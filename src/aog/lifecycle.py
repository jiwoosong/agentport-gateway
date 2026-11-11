import asyncio
import time

from sqlalchemy import select

from aog import adapters
from aog.models import RuntimeDefinition, Service
from aog.openshell import OpenShell
from aog.store import Resource, Store


class Lifecycle:
    def __init__(self, store: Store, backend=None, health=None):
        self.store = store
        self.backend = backend or adapters.Komodo()
        self.health = health or adapters.ready
        self.openshell = OpenShell()

    async def control(self, service: Service | RuntimeDefinition, operation: str):
        if service.openshell:
            return await self.openshell.execute(service.openshell, operation, service.timeout_seconds)
        target_id = service.target_id if isinstance(service, RuntimeDefinition) else service.stack_id
        if not target_id:
            raise ValueError("This runtime is controlled by its operator")
        return await getattr(self.backend, operation)(target_id)

    async def _start_claimed(self, service_id: str, service: Service):
        """Complete a startup after the caller has acquired the database claim."""
        await self.control(service, "start")
        await self.health(service.health_url, service.timeout_seconds)
        self.store.phase(service_id, "running")

    async def acquire(self, service_id: str, service: Service, key: str, run_id: str, *, validate=None):
        service = self.store.service_projection(service_id, service)
        deadline = time.monotonic() + service.timeout_seconds
        while not self.store.acquire(service_id, key, run_id, service.concurrency):
            if time.monotonic() > deadline:
                raise TimeoutError("Service capacity unavailable")
            await asyncio.sleep(0.2)
        if validate:
            await validate()
        if service.lifecycle == "manual":
            return
        if self.store.claim_start(service_id):
            try:
                await self._start_claimed(service_id, service)
            except Exception:
                # A start command with an unknown outcome requires operator reconciliation.
                self.store.phase(service_id, "error")
                raise
        while self.store.phase(service_id) != "running":
            if self.store.phase(service_id) == "error" or time.monotonic() > deadline:
                raise RuntimeError("Service startup needs operator attention")
            await asyncio.sleep(0.2)

    async def sweep(self):
        with self.store.tx() as db:
            services = list(db.scalars(select(Resource).where(Resource.kind.in_(["service", "runtime"]))))
        for row in services:
            # The runtime is the lifecycle unit, even when several native tools reference it.
            if row.kind == "service" and self.store.runtime_id(row.id) != row.id:
                continue
            service = (
                RuntimeDefinition.model_validate(row.document)
                if row.kind == "runtime"
                else Service.model_validate(row.document)
            )
            if service.lifecycle == "always_on" and service.enabled:
                if self.store.claim_start(row.id):
                    try:
                        await self._start_claimed(row.id, service)
                    except Exception:
                        self.store.phase(row.id, "error")
            elif (
                service.lifecycle == "on_demand"
                and self.store.phase(row.id) == "running"
                and self.store.claim_stop(row.id, service.idle_seconds)
            ):
                try:
                    await self.control(service, "stop")
                    self.store.phase(row.id, "stopped")
                except Exception:
                    self.store.phase(row.id, "error")

    async def deploy_release(self, row: Resource, release: str, actor):
        from fastapi import HTTPException

        service = (
            RuntimeDefinition.model_validate(row.document)
            if row.kind == "runtime"
            else Service.model_validate(row.document)
        )
        if service.openshell:
            raise HTTPException(409, "OpenShell updates require a separately provisioned replacement sandbox")
        target = service.releases.get(release)
        old_target = service.target_id if isinstance(service, RuntimeDefinition) else service.stack_id
        if not target or not old_target or not service.health_url:
            raise HTTPException(422, "Register a release stack and readiness URL first")
        if not self.store.claim_stop(row.id, 0, "updating"):
            raise HTTPException(409, "Active jobs must drain before deployment")
        self.store.audit(actor.subject, "release.begin", row.id, {"release": release})
        try:
            await self.control(service, "stop")
            await self.backend.update(target)
            await self.health(service.health_url, service.timeout_seconds)
            self.store.save(
                row.kind,
                {
                    **row.document,
                    "target_id" if row.kind == "runtime" else "stack_id": target,
                    "current_release": release,
                },
                actor,
                row.id,
                deployment=True,
            )
            self.store.phase(row.id, "running")
            self.store.audit(actor.subject, "release.completed", row.id, {"release": release})
            return {"release": release, "rolled_back": False}
        except Exception:
            if service.rollback_safe:
                try:
                    # A failed deployment might still be running: confirm it stopped first.
                    await self.backend.stop(target)
                    await self.control(service, "start")
                    await self.health(service.health_url, service.timeout_seconds)
                    self.store.phase(row.id, "running")
                    self.store.audit(actor.subject, "release.rolled_back", row.id, {"release": release})
                    return {"release": service.current_release, "rolled_back": True}
                except Exception:
                    pass
            self.store.phase(row.id, "error")
            raise HTTPException(
                502, "Deployment needs operator recovery; no database rollback was attempted"
            ) from None
