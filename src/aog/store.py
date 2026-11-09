"""Extension-owned tables. ContextForge tables are never written here."""

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
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from aog.models import Principal


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
                db.execute(text("UPDATE aog_runs SET cancel_requested = TRUE WHERE status = 'cancel_requested'"))

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
        project = document.get("project_id")
        if project and project not in actor.teams and not actor.is_admin:
            raise HTTPException(403, "Project membership required")
        if rid:
            self.resource(rid, actor, kind, write=True)
        with self.tx() as db:
            if rid and kind == "service":
                db.execute(
                    update(Runtime).where(Runtime.service_id == rid).values(last_used=Runtime.last_used)
                )
                state = db.get(Runtime, rid)
                if db.scalar(select(Lease.id).where(Lease.service_id == rid).limit(1)):
                    raise HTTPException(409, "Service has active leases")
                if state.phase in {"starting", "stopping", "updating"} and not deployment:
                    raise HTTPException(409, "Service has an active deployment operation")
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
                if kind == "service":
                    db.add(Runtime(service_id=row.id))
            db.add(Audit(actor=actor.subject, action=f"{kind}.save", resource_id=row.id))
            db.flush()
            return row

    def run(self, run_id: str, actor: Principal):
        with self.tx() as db:
            run = db.get(Run, run_id)
            # Runs are private even if their definition was shared.
            if not run or (run.owner != actor.subject and not actor.is_admin):
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
        with self.tx() as db:
            # UPDATE serializes with idle-stop and other acquisitions, including SQLite.
            db.execute(update(Runtime).where(Runtime.service_id == service_id).values(last_used=time.time()))
            state = db.get(Runtime, service_id)
            if state.phase in {"stopping", "updating", "error"}:
                return False
            if db.get(Lease, lease_id):
                return True
            active = list(
                db.scalars(
                    select(Lease.id).where(Lease.service_id == service_id, ~Lease.id.like("session:%"))
                )
            )
            if len(active) >= capacity:
                return False
            db.add(Lease(id=lease_id, service_id=service_id, run_id=run_id))
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
        with self.tx() as db:
            return (
                db.execute(
                    update(Runtime)
                    .where(Runtime.service_id == service_id, Runtime.phase == "stopped")
                    .values(phase="starting")
                ).rowcount
                == 1
            )

    def phase(self, service_id: str, value: str | None = None):
        with self.tx() as db:
            if value is not None:
                db.execute(update(Runtime).where(Runtime.service_id == service_id).values(phase=value))
            return db.get(Runtime, service_id).phase

    def claim_stop(self, service_id: str, idle_seconds: int, phase="stopping"):
        with self.tx() as db:
            busy = exists(select(Lease.id).where(Lease.service_id == service_id))
            return (
                db.execute(
                    update(Runtime)
                    .where(
                        Runtime.service_id == service_id,
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
