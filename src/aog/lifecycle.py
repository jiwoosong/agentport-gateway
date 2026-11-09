import asyncio
import time

from sqlalchemy import select

from aog import adapters
from aog.models import Service
from aog.store import Resource, Store


class Lifecycle:
    def __init__(self, store: Store, backend=None, health=None):
        self.store = store
        self.backend = backend or adapters.Komodo()
        self.health = health or adapters.ready

    async def acquire(self, service_id: str, service: Service, key: str, run_id: str):
        deadline = time.monotonic() + service.timeout_seconds
        while not self.store.acquire(service_id, key, run_id, service.concurrency):
            if time.monotonic() > deadline:
                raise TimeoutError("Service capacity unavailable")
            await asyncio.sleep(0.2)
        if service.lifecycle == "manual":
            return
        if self.store.claim_start(service_id):
            try:
                await self.backend.start(service.stack_id)
                await self.health(service.health_url, service.timeout_seconds)
                self.store.phase(service_id, "running")
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
            services = list(db.scalars(select(Resource).where(Resource.kind == "service")))
        for row in services:
            service = Service.model_validate(row.document)
            if service.lifecycle == "always_on" and service.enabled:
                if self.store.claim_start(row.id):
                    try:
                        await self.backend.start(service.stack_id)
                        await self.health(service.health_url, service.timeout_seconds)
                        self.store.phase(row.id, "running")
                    except Exception:
                        self.store.phase(row.id, "error")
            elif service.lifecycle == "on_demand" and self.store.claim_stop(row.id, service.idle_seconds):
                try:
                    await self.backend.stop(service.stack_id)
                    self.store.phase(row.id, "stopped")
                except Exception:
                    self.store.phase(row.id, "error")

    async def deploy_release(self, row: Resource, release: str, actor):
        from fastapi import HTTPException

        service = Service.model_validate(row.document)
        target = service.releases.get(release)
        if not target or not service.stack_id or not service.health_url:
            raise HTTPException(422, "Register a release stack and readiness URL first")
        if not self.store.claim_stop(row.id, 0, "updating"):
            raise HTTPException(409, "Active jobs must drain before deployment")
        self.store.audit(actor.subject, "release.begin", row.id, {"release": release})
        try:
            await self.backend.stop(service.stack_id)
            await self.backend.update(target)
            await self.health(service.health_url, service.timeout_seconds)
            self.store.save(
                "service",
                {**row.document, "stack_id": target, "current_release": release},
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
                    await self.backend.start(service.stack_id)
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
