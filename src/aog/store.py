"""Extension-owned tables. ContextForge tables are never written here."""

import hashlib
import time
import uuid
from contextlib import contextmanager

from fastapi import HTTPException
from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    create_engine,
    exists,
    false,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from aog.models import AgentProfile, Principal, ProviderConnection, RuntimeDefinition, Service


class Base(DeclarativeBase):
    pass


class Resource(Base):
    __tablename__ = "aog_resources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    owner: Mapped[str] = mapped_column(String(255), index=True)
    project_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    document: Mapped[dict] = mapped_column(JSON)


class Run(Base):
    __tablename__ = "aog_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner: Mapped[str] = mapped_column(String(255), index=True)
    project_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    spec: Mapped[dict] = mapped_column(JSON)
    inputs: Mapped[dict] = mapped_column(JSON)
    principal: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Step(Base):
    __tablename__ = "aog_steps"
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("aog_runs.id"), index=True)
    node_id: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32))
    output: Mapped[dict] = mapped_column(JSON, default=dict)


class Runtime(Base):
    __tablename__ = "aog_runtimes"
    service_id: Mapped[str] = mapped_column(ForeignKey("aog_resources.id"), primary_key=True)
    phase: Mapped[str] = mapped_column(String(32), default="stopped")
    last_used: Mapped[float] = mapped_column(Float, default=time.time)


class Lease(Base):
    __tablename__ = "aog_leases"
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    service_id: Mapped[str] = mapped_column(ForeignKey("aog_resources.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)


class SandboxBinding(Base):
    __tablename__ = "aog_sandbox_bindings"
    target: Mapped[str] = mapped_column(String(64), primary_key=True)
    service_id: Mapped[str] = mapped_column(ForeignKey("aog_resources.id"), unique=True)


class RuntimeBinding(Base):
    __tablename__ = "aog_runtime_bindings"
    # Native ContextForge tool IDs do not belong to aog_resources.
    service_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    runtime_id: Mapped[str] = mapped_column(ForeignKey("aog_resources.id"), index=True)


class RuntimeTarget(Base):
    __tablename__ = "aog_runtime_targets"
    target: Mapped[str] = mapped_column(String(64), primary_key=True)
    runtime_id: Mapped[str] = mapped_column(ForeignKey("aog_resources.id"), unique=True)


class SchemaMigration(Base):
    __tablename__ = "aog_schema_migrations"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[float] = mapped_column(Float, default=time.time)


class Audit(Base):
    __tablename__ = "aog_audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex)
    at: Mapped[float] = mapped_column(Float, default=time.time)
    actor: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(100))
    resource_id: Mapped[str] = mapped_column(String(200))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)  # Metadata only; no prompts/secrets.


class Store:
    def __init__(self, url: str):
        self.engine = create_engine(
            url,
            **(
                {"connect_args": {"check_same_thread": False, "timeout": 30}}
                if url.startswith("sqlite")
                else {}
            ),
        )

    def init(self):
        Base.metadata.create_all(self.engine)
        # Additive migration for databases made by the initial development snapshot.
        if "cancel_requested" not in {c["name"] for c in inspect(self.engine).get_columns("aog_runs")}:
            with self.engine.begin() as db:
                db.execute(
                    text("ALTER TABLE aog_runs ADD COLUMN cancel_requested BOOLEAN NOT NULL DEFAULT FALSE")
                )
                db.execute(
                    text("UPDATE aog_runs SET cancel_requested = TRUE WHERE status = 'cancel_requested'")
                )
        with self.tx() as db:
            if not db.get(SchemaMigration, 2):
                targets = {}
                for row in db.scalars(select(Resource).where(Resource.kind.in_(["service", "runtime"]))):
                    target = self.target_identity(row.kind, row.document)
                    if target:
                        if target in targets:
                            raise RuntimeError("Duplicate deployment targets require operator reconciliation before migration")
                        targets[target] = row.id
                        db.add(RuntimeTarget(target=target, runtime_id=row.id))
                db.add(SchemaMigration(version=2))

    @staticmethod
    def target_identity(kind: str, document: dict) -> str | None:
        target = document.get("openshell")
        if target:
            parts = ["openshell", target["gateway"], target["workspace"], target["sandbox"]]
        elif kind == "runtime" and document.get("backend") == "komodo":
            parts = ["komodo", document.get("backend_ref") or "default", document["target_id"]]
        elif kind == "service" and document.get("stack_id"):
            parts = ["komodo", "default", document["stack_id"]]
        else:
            return None
        return hashlib.sha256("\0".join(parts).encode()).hexdigest()

    def register_runtime(self, definition: RuntimeDefinition | dict, actor: Principal, rid: str | None = None):
        document = RuntimeDefinition.model_validate(definition).model_dump()
        return self.save("runtime", document, actor, rid)

    def runtime_id(self, service_id: str) -> str:
        with self.tx() as db:
            binding = db.get(RuntimeBinding, service_id)
            return binding.runtime_id if binding else service_id

    def runtime_definition(self, service_id: str) -> RuntimeDefinition | None:
        runtime_id = self.runtime_id(service_id)
        with self.tx() as db:
            row = db.get(Resource, runtime_id)
            return RuntimeDefinition.model_validate(row.document) if row and row.kind == "runtime" else None

    def service_projection(self, service_id: str, service: Service) -> Service:
        definition = self.runtime_definition(service_id)
        return definition.service_projection(service) if definition else service

    def bind_runtime(self, service_id: str, runtime_id: str, actor: Principal, *, claimed_runtime_id: str | None = None):
        if not actor.is_admin:
            raise HTTPException(403, "Platform administrator required for runtime bindings")
        self.resource(runtime_id, actor, "runtime")
        old_id = self.runtime_id(service_id)
        with self.tx() as db:
            if claimed_runtime_id is not None:
                if old_id != claimed_runtime_id:
                    raise HTTPException(409, "Configuration claim no longer matches the runtime binding")
                db.execute(update(Runtime).where(Runtime.service_id == old_id).values(last_used=Runtime.last_used))
                old = db.get(Runtime, old_id)
                if not old or old.phase != "updating" or db.scalar(select(Lease.id).where(Lease.service_id == old_id).limit(1)):
                    raise HTTPException(409, "Configuration update requires its exclusive drained runtime claim")
            elif old_id != runtime_id:
                db.execute(update(Runtime).where(Runtime.service_id == old_id).values(last_used=time.time()))
                old = db.get(Runtime, old_id)
                if db.scalar(select(Lease.id).where(Lease.service_id == old_id).limit(1)):
                    raise HTTPException(409, "Existing runtime has active leases")
                if old and old.phase in {"starting", "stopping", "updating"}:
                    raise HTTPException(409, "Existing runtime has an active deployment operation")
            binding = db.get(RuntimeBinding, service_id)
            if binding:
                binding.runtime_id = runtime_id
            else:
                db.add(RuntimeBinding(service_id=service_id, runtime_id=runtime_id))
            db.add(Audit(actor=actor.subject, action="runtime.bind", resource_id=service_id,
                         detail={"runtime_id": runtime_id}))

    @contextmanager
    def tx(self):
        with Session(self.engine, expire_on_commit=False) as db, db.begin():
            yield db

    def resource(self, rid: str, actor: Principal, kind: str | None = None, write=False):
        with self.tx() as db:
            row = db.get(Resource, rid)
            if (
                not row
                or (kind and row.kind != kind)
                or not actor.sees(row.owner, row.project_id, row.visibility)
            ):
                raise HTTPException(404, "Resource not found")
            if write and not (actor.is_admin or row.owner == actor.subject):
                raise HTTPException(403, "Owner permission required")
            if actor.agent_id and row.kind == "service" and rid not in (actor.allowed_service_ids or []):
                raise HTTPException(404, "Resource not found")
            return row

    def list_resources(self, actor: Principal, kind: str):
        with self.tx() as db:
            return [
                r
                for r in db.scalars(select(Resource).where(Resource.kind == kind))
                if actor.sees(r.owner, r.project_id, r.visibility)
            ]

    def save(self, kind: str, document: dict, actor: Principal, rid: str | None = None, *, deployment=False):
        if actor.public_only:
            raise HTTPException(403, "Public-only tokens cannot manage private Core resources")
        owner = self.resource(rid, actor, kind, write=True).owner if rid else actor.subject
        if kind == "runtime":
            if not actor.is_admin:
                raise HTTPException(403, "Platform administrator required for runtime definitions")
            document = RuntimeDefinition.model_validate(document).model_dump()
        elif kind == "provider":
            document = ProviderConnection.model_validate(document).model_dump()
            credential = self.resource(document["credential_ref"], actor, "credential")
            if credential.owner != owner:
                raise HTTPException(403, "Provider credential belongs to another user")
        elif kind == "agent" and document.get("schema_version") == 2:
            document = AgentProfile.model_validate(document).model_dump()
            self.resource(document["runtime_id"], actor, "runtime")
            if document["provider_connection_id"]:
                provider = self.resource(document["provider_connection_id"], actor, "provider")
                if provider.owner != owner:
                    raise HTTPException(403, "Provider connection belongs to another user")
            else:
                login = self.resource(document["native_login_ref"], actor, "credential")
                if login.owner != owner:
                    raise HTTPException(403, "Native login belongs to another user")
        project = document.get("project_id")
        if project and project not in actor.teams and not actor.is_admin:
            raise HTTPException(403, "Project membership required")
        if rid:
            self.resource(rid, actor, kind, write=True)
        with self.tx() as db:
            if rid and kind in {"service", "runtime"}:
                runtime_id = self.runtime_id(rid)
                db.execute(
                    update(Runtime).where(Runtime.service_id == runtime_id).values(last_used=Runtime.last_used)
                )
                state = db.get(Runtime, runtime_id)
                if db.scalar(select(Lease.id).where(Lease.service_id == runtime_id).limit(1)):
                    raise HTTPException(409, "Service has active leases")
                if state.phase in {"starting", "stopping", "updating"} and not deployment:
                    raise HTTPException(409, "Service has an active deployment operation")
                old = db.get(Resource, rid).document
                target_changed = self.target_identity(kind, old) != self.target_identity(kind, document)
                sandbox_changed = old.get("openshell") != document.get("openshell")
                if (target_changed or sandbox_changed) and state.phase != "stopped" and not deployment:
                    raise HTTPException(409, "Stop and reconcile the runtime before changing deployment binding")
            row = db.get(Resource, rid) if rid else None
            if row:
                row.document = document
                row.revision += 1
                row.project_id, row.visibility = project, document.get("visibility", "private")
            else:
                row = Resource(
                    id=uuid.uuid4().hex,
                    kind=kind,
                    document=document,
                    owner=actor.subject,
                    project_id=project,
                    visibility=document.get("visibility", "private"),
                )
                db.add(row)
                db.flush()
                if kind in {"service", "runtime"}:
                    db.add(Runtime(service_id=row.id))
            if kind in {"service", "runtime"}:
                target_binding = db.scalar(select(RuntimeTarget).where(RuntimeTarget.runtime_id == row.id))
                if target_binding:
                    db.delete(target_binding)
                    db.flush()
                target_identity = self.target_identity(kind, document)
                if target_identity:
                    db.add(RuntimeTarget(target=target_identity, runtime_id=row.id))
                    try:
                        db.flush()
                    except IntegrityError:
                        raise HTTPException(409, "Deployment target is already registered") from None
            if kind == "service":
                binding = db.scalar(select(SandboxBinding).where(SandboxBinding.service_id == row.id))
                if binding:
                    db.delete(binding)
                    db.flush()
                target = document.get("openshell")
                if target:
                    # Unique DB constraint prevents two service lease pools for one sandbox.
                    identity = "\0".join(target[k] for k in ("gateway", "workspace", "sandbox"))
                    db.add(
                        SandboxBinding(
                            target=hashlib.sha256(identity.encode()).hexdigest(), service_id=row.id
                        )
                    )
                    try:
                        db.flush()
                    except IntegrityError:
                        raise HTTPException(409, "Sandbox is already bound to another service") from None
            db.add(Audit(actor=actor.subject, action=f"{kind}.save", resource_id=row.id))
            db.flush()
            return row

    def run(self, run_id: str, actor: Principal):
        with self.tx() as db:
            run = db.get(Run, run_id)
            # Runs are private even if their definition was shared.
            if not run or not actor.sees(run.owner, run.project_id, "private"):
                raise HTTPException(404, "Run not found")
            if (
                actor.agent_id
                and run.id != actor.parent_run_id
                and (
                    run.principal.get("parent_run_id") != actor.parent_run_id
                    or run.principal.get("agent_id") != actor.agent_id
                )
            ):
                raise HTTPException(404, "Run not found")
            return run

    def acquire(self, service_id: str, lease_id: str, run_id: str, capacity: int) -> bool:
        runtime_id = self.runtime_id(service_id)
        with self.tx() as db:
            # UPDATE serializes with idle-stop and other acquisitions, including SQLite.
            db.execute(update(Runtime).where(Runtime.service_id == runtime_id).values(last_used=time.time()))
            state = db.get(Runtime, runtime_id)
            if state is None:
                raise HTTPException(409, "Runtime is not registered")
            binding = db.get(RuntimeBinding, service_id)
            if (binding.runtime_id if binding else service_id) != runtime_id:
                return False
            if state.phase in {"stopping", "updating", "error"}:
                return False
            lease = db.get(Lease, lease_id)
            if lease:
                if lease.service_id != runtime_id or lease.run_id != run_id:
                    raise HTTPException(409, "Lease ID already belongs to another execution")
                return True
            runtime = db.get(Resource, runtime_id)
            if runtime and runtime.kind == "runtime":
                definition = RuntimeDefinition.model_validate(runtime.document)
                if not definition.enabled:
                    raise HTTPException(403, "Runtime disabled")
                capacity = definition.concurrency
            active = list(
                db.scalars(
                    select(Lease.id).where(Lease.service_id == runtime_id, ~Lease.id.like("session:%"))
                )
            )
            if len(active) >= capacity:
                return False
            db.add(Lease(id=lease_id, service_id=runtime_id, run_id=run_id))
            return True

    def release(self, lease_id: str):
        with self.tx() as db:
            lease = db.get(Lease, lease_id)
            if lease:
                db.execute(
                    update(Runtime)
                    .where(Runtime.service_id == lease.service_id)
                    .values(last_used=time.time())
                )
                db.delete(lease)

    def claim_start(self, service_id: str):
        runtime_id = self.runtime_id(service_id)
        with self.tx() as db:
            return (
                db.execute(
                    update(Runtime)
                    .where(Runtime.service_id == runtime_id, Runtime.phase == "stopped")
                    .values(phase="starting")
                ).rowcount
                == 1
            )

    def phase(self, service_id: str, value: str | None = None):
        runtime_id = self.runtime_id(service_id)
        with self.tx() as db:
            if value is not None:
                db.execute(update(Runtime).where(Runtime.service_id == runtime_id).values(phase=value))
            state = db.get(Runtime, runtime_id)
            if state is None:
                raise HTTPException(409, "Runtime is not registered")
            return state.phase

    def claim_stop(self, service_id: str, idle_seconds: int, phase="stopping"):
        runtime_id = self.runtime_id(service_id)
        with self.tx() as db:
            busy = exists(select(Lease.id).where(Lease.service_id == runtime_id))
            return (
                db.execute(
                    update(Runtime)
                    .where(
                        Runtime.service_id == runtime_id,
                        ~busy,
                        Runtime.phase.in_(["running", "stopped"]),
                        Runtime.last_used <= time.time() - idle_seconds,
                    )
                    .values(phase=phase)
                ).rowcount
                == 1
            )

    def status(self, run_id: str, status: str, result: dict | None = None):
        with self.tx() as db:
            run = db.get(Run, run_id)
            if status == "cancel_requested":
                run.cancel_requested = True
            attention = db.scalar(
                select(Step.id).where(Step.run_id == run_id, Step.status == "needs_attention").limit(1)
            )
            if attention and status not in {"completed", "failed", "cancelled"}:
                run.status = "needs_attention"
            elif run.cancel_requested and status not in {"completed", "failed", "cancelled"}:
                run.status = "cancel_requested"
            else:
                run.status = status
            if result is not None:
                run.result = result

    def step(self, run_id: str, node_id: str, status: str | None = None, output: dict | None = None):
        key = f"{run_id}:{node_id}"
        with self.tx() as db:
            row = db.get(Step, key)
            if status:
                if row:
                    row.status, row.output = status, output or {}
                else:
                    row = Step(id=key, run_id=run_id, node_id=node_id, status=status, output=output or {})
                    db.add(row)
            return row

    def audit(self, actor: str, action: str, rid: str, detail: dict | None = None):
        with self.tx() as db:
            db.add(Audit(actor=actor, action=action, resource_id=rid, detail=detail or {}))
