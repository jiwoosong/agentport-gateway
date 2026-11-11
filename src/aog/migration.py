"""Offline, resumable migration into the native catalog; private operator backups."""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.engine import make_url

from aog.models import Principal, RuntimeDefinition, Service, Workflow
from aog.store import Audit, Lease, Resource, Run, Runtime, RuntimeBinding, RuntimeTarget, SandboxBinding

TERMINAL = {"completed", "failed", "cancelled"}
DATABASES = {
    "DATABASE_URL": "sqlite:///data/contextforge.db",
    "AOG_DATABASE_URL": "sqlite:///data/orchestration.db",
    "DBOS_SYSTEM_DATABASE_URL": "sqlite:///data/dbos.db",
}
CONFIG_KEYS = {
    "AOG_GATEWAY_BRIDGE_SECRET", "AOG_GATEWAY_BACKEND", "AOG_GATEWAY_URL", "AOG_CORE_URL",
    "JWT_SECRET_KEY", "AUTH_ENCRYPTION_SECRET", "PLATFORM_ADMIN_EMAIL",
    "PLATFORM_ADMIN_PASSWORD", "DEFAULT_USER_PASSWORD", "BASIC_AUTH_PASSWORD",
    "AUTH_REQUIRED", "MCP_CLIENT_AUTH_ENABLED", "EMAIL_AUTH_ENABLED",
    "KOMODO_URL", "KOMODO_API_KEY", "KOMODO_API_SECRET", "MCPGATEWAY_A2A_ENABLED",
    "LLM_API_ENABLED", "VALIDATION_MAX_RPC_PARAM_SIZE",
}


def assert_drained(store):
    """Unknown external effects must be resolved, never replayed by migration."""
    with store.tx() as db:
        if db.scalar(select(Run.id).where(Run.status.not_in(TERMINAL)).limit(1)):
            raise RuntimeError("Migration requires all runs terminal, including approval and uncertain runs")
        if db.scalar(select(Lease.id).limit(1)):
            raise RuntimeError("Migration requires all runtime/session leases released")
        if db.scalar(select(Runtime.service_id).where(Runtime.phase.not_in({"running", "stopped"})).limit(1)):
            raise RuntimeError("Migration requires stable running/stopped deployment states")


def _runtime(service):
    return RuntimeDefinition(
        name=service.name, project_id=service.project_id, visibility=service.visibility,
        enabled=service.enabled,
        backend="openshell" if service.openshell else "komodo" if service.stack_id else "external",
        target_id=service.stack_id, openshell=service.openshell,
        lifecycle=service.lifecycle, health_url=service.health_url,
        idle_seconds=service.idle_seconds, timeout_seconds=service.timeout_seconds,
        concurrency=service.concurrency, location=service.location, egress_policy=service.egress_policy,
        releases=service.releases, current_release=service.current_release, rollback_safe=service.rollback_safe,
    ).model_dump()


async def perform_migration(store, catalog):
    """No engine is constructed here. A pending alias makes cross-DB commits resumable."""
    assert_drained(store)
    report = {"migrated_services": 0, "resumed_aliases": 0, "unchanged_aliases": 0,
              "archived_agents": 0, "archived_services": 0,
              "published_workflows": 0, "workflows_requiring_review": 0}
    with store.tx() as db:
        originals = list(db.scalars(select(Resource).where(Resource.kind == "service")))
        # Validate the entire legacy batch before its first write.
        definitions = {row.id: _runtime(Service.model_validate(row.document)) for row in originals}
        for row in originals:
            runtime_id = uuid.uuid5(uuid.NAMESPACE_URL, "agentport:legacy-runtime:" + row.id).hex
            definition = definitions[row.id]
            if db.get(Resource, runtime_id):
                raise RuntimeError("Migration runtime ID collision requires operator reconciliation")
            db.add(Resource(id=runtime_id, kind="runtime", owner=row.owner, project_id=row.project_id,
                            visibility=row.visibility, revision=1, document=definition))
            db.flush()
            previous = db.get(Runtime, row.id)
            state = {"phase": previous.phase, "last_used": previous.last_used} if previous else {"phase": "stopped"}
            db.execute(delete(Runtime).where(Runtime.service_id == row.id))
            db.add(Runtime(service_id=runtime_id, **state))
            db.execute(update(RuntimeBinding).where(RuntimeBinding.runtime_id == row.id).values(runtime_id=runtime_id))
            claim = db.scalar(select(RuntimeTarget).where(RuntimeTarget.runtime_id == row.id))
            target = store.target_identity("runtime", definition)
            if claim:
                claim.runtime_id = runtime_id
            elif target:
                db.add(RuntimeTarget(target=target, runtime_id=runtime_id))
            db.execute(delete(SandboxBinding).where(SandboxBinding.service_id == row.id))
            ambiguous_model = row.document.get("kind") == "model" or row.document.get("transport") == "openai"
            row.kind = "service_archive" if ambiguous_model else "service_alias"
            row.document = {"schema_version": 2, "phase": "archived" if ambiguous_model else "pending",
                            "runtime_id": runtime_id, "tool_id": None, "legacy_definition": row.document}
            if ambiguous_model:
                row.document = {**row.document, "enabled": False,
                                "reason": "Select an explicit model API protocol, authentication and harness before reuse"}
                report["archived_services"] += 1
            row.revision += 1
            db.add(Audit(actor=row.owner, action="migration.service.archive" if ambiguous_model else "migration.service.stage", resource_id=row.id,
                         detail={"runtime_id": runtime_id}))
        for row in db.scalars(select(Resource).where(Resource.kind == "agent")):
            if row.document.get("schema_version", 1) != 1:
                continue
            row.kind = "agent_archive"
            row.document = {"schema_version": 2, "enabled": False,
                            "reason": "Select an explicit harness, runtime and provider or native login before reuse",
                            "legacy_definition": row.document}
            row.revision += 1
            db.add(Audit(actor=row.owner, action="migration.agent.archive", resource_id=row.id))
            report["archived_agents"] += 1
        aliases = list(db.scalars(select(Resource).where(Resource.kind == "service_alias")))
    original_ids = {row.id for row in originals}
    for alias in aliases:
        if alias.document["phase"] == "completed":
            report["unchanged_aliases"] += 1
            continue
        actor = Principal(subject=alias.owner, teams=[alias.project_id] if alias.project_id else [], is_admin=True)
        # Native catalog metadata/name survives a crash after its commit but before this DB's commit.
        tool = await catalog.migrated_tool(alias.id, alias.owner)
        if tool is None:
            tool = await catalog.save(
                {**alias.document["legacy_definition"], "runtime_id": alias.document["runtime_id"]},
                actor, migration_source_id=alias.id,
            )
        store.bind_runtime(tool.id, alias.document["runtime_id"], actor)
        with store.tx() as db:
            row = db.get(Resource, alias.id)
            row.document = {**row.document, "tool_id": tool.id, "phase": "completed"}
            db.add(Audit(actor=alias.owner, action="migration.service.complete", resource_id=alias.id,
                         detail={"tool_id": tool.id}))
        report["migrated_services" if alias.id in original_ids else "resumed_aliases"] += 1
    with store.tx() as db:
        workflows = list(db.scalars(select(Resource).where(Resource.kind == "workflow")))
        unavailable = set(db.scalars(select(Resource.id).where(
            Resource.kind.in_(["agent_archive", "service_archive"])) ))
    for row in workflows:
        definition = Workflow.model_validate(row.document)
        targets = {reference for node in definition.nodes for reference in (node.target, node.reviewer) if reference}
        if targets.intersection(unavailable):
            report["workflows_requiring_review"] += 1
            continue
        actor = Principal(subject=row.owner, teams=[row.project_id] if row.project_id else [], is_admin=True)
        await catalog.publish_resource(row, actor)
        report["published_workflows"] += 1
    return report


def _sqlite_backup(source, destination):
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)
    destination.chmod(0o600)


def _postgres_backup(url, destination):
    if not shutil.which("pg_dump"):
        raise RuntimeError("PostgreSQL backup requires pg_dump installed by the operator")
    environment = os.environ.copy()
    for field, variable in (("host", "PGHOST"), ("port", "PGPORT"), ("username", "PGUSER"),
                            ("password", "PGPASSWORD"), ("database", "PGDATABASE")):
        value = getattr(url, field)
        if value is not None:
            environment[variable] = str(value)
    supported = {"sslmode", "sslcert", "sslkey", "sslrootcert"}
    if set(url.query) - supported:
        raise RuntimeError("Unsupported PostgreSQL URL options; use an operator database backup")
    environment.update({"PG" + key.upper(): str(value) for key, value in url.query.items()})
    result = subprocess.run(["pg_dump", "--format=custom", "--file", str(destination)],
                            env=environment, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError("pg_dump failed; inspect database access locally before migration")
    destination.chmod(0o600)


def create_backup(output=None, *, root=None, environment=None, postgres_backup=None):
    """Copy local state privately; callers must stop writers for cross-database coherence."""
    root = Path(root or Path.cwd()).resolve()
    environment = dict(os.environ if environment is None else environment)
    archive = None
    if postgres_backup:
        databases = {make_url(environment.get(key, default)) for key, default in DATABASES.items()}
        postgres = {url.set(drivername="postgresql") for url in databases if url.drivername.startswith("postgresql")}
        if len(postgres) != 1:
            raise RuntimeError("One operator archive requires exactly one distinct configured PostgreSQL database")
        archive = Path(postgres_backup)
        archive = archive if archive.is_absolute() else root / archive
        if not archive.is_file() or archive.stat().st_size <= 5:
            raise RuntimeError("Operator PostgreSQL archive must be a nonempty pg_dump custom-format file")
        with archive.open("rb") as handle:
            if handle.read(5) != b"PGDMP":
                raise RuntimeError("Operator PostgreSQL archive must have the PGDMP custom-format header")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = Path(output) if output else root / "data/backups" / f"agentport-{stamp}-{uuid.uuid4().hex[:8]}"
    if not destination.is_absolute():
        destination = root / destination
    destination = destination.resolve()
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    manifest = {"format_version": 1, "created_at": stamp, "status": "incomplete", "files": {},
                "absent_databases": [], "postgres_backup": "operator-provided" if archive else "pg_dump",
                "external_backups_required": [
                    "Gateway JWT/encryption master keys and host .env (not present in the Core container)",
                    "Stop central/worker writers for a coherent cross-database and file snapshot",
                    "Remote worker homes, job databases and mounted volumes",
                    "OpenShell gateway database AND encryption key, sandbox volumes and provider credentials",
                    "Komodo database, Periphery deployment files and Docker volumes",
                ]}
    database_sources = set()

    def save_file(source, target):
        if source.is_symlink():
            manifest["external_backups_required"].append("Symlink target: " + str(source))
            return
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with source.open("rb") as handle:
            sqlite = handle.read(16) == b"SQLite format 3\0"
        if sqlite:
            _sqlite_backup(source, target)
        else:
            shutil.copyfile(source, target)
            target.chmod(0o600)

    try:
        for key, default in DATABASES.items():
            url = make_url(environment.get(key, default))
            target = destination / "databases" / (key.lower() + (".sqlite" if url.drivername.startswith("sqlite") else ".dump"))
            target.parent.mkdir(mode=0o700, exist_ok=True)
            if url.drivername.startswith("sqlite"):
                if not url.database or url.database == ":memory:":
                    raise RuntimeError("In-memory databases cannot be backed up for migration")
                source = Path(url.database)
                source = source if source.is_absolute() else root / source
                database_sources.add(source.resolve())
                if source.exists():
                    _sqlite_backup(source, target)
                else:
                    manifest["absent_databases"].append(key)
            elif url.drivername.startswith("postgresql"):
                if archive:
                    shutil.copyfile(archive, target)
                    target.chmod(0o600)
                else:
                    _postgres_backup(url, target)
            else:
                raise RuntimeError("Only SQLite and PostgreSQL operator backups are supported")
        roots = [(root / "data", "local/data")]
        for key in ("AOG_ARTIFACT_ROOT", "AOG_WORKER_ROOT"):
            if environment.get(key):
                configured = Path(environment[key])
                configured = (configured if configured.is_absolute() else root / configured).resolve()
                if not configured.is_relative_to(root / "data"):
                    roots.append((configured, "local/" + key.lower()))
        for source_root, backup_root in roots:
            if source_root.is_symlink():
                manifest["external_backups_required"].append("Symlink target: " + str(source_root))
                continue
            for folder, directories, files in os.walk(source_root, followlinks=False):
                base = Path(folder)
                manifest["external_backups_required"].extend(
                    "Symlink target: " + str(base / name) for name in directories if (base / name).is_symlink())
                directories[:] = [name for name in directories if base / name != root / "data/backups"
                                  and not (base / name).is_symlink()
                                  and not destination.is_relative_to(base / name)]
                for name in files:
                    source = base / name
                    if source.resolve() in database_sources or name.endswith(("-wal", "-shm")):
                        continue
                    save_file(source, destination / backup_root / source.relative_to(source_root))
        if (root / ".env").exists():
            save_file(root / ".env", destination / "configuration/.env")
        effective = {key: value for key, value in environment.items()
                     if key in CONFIG_KEYS or key in DATABASES or key.startswith("AOG_")}

        def referenced_configuration(value):
            if isinstance(value, list):
                for item in value:
                    referenced_configuration(item)
            elif isinstance(value, dict):
                for key, item in value.items():
                    if key.endswith("_env") and isinstance(item, str) and item in environment:
                        effective[item] = environment[item]
                    elif key in {"ca_path", "cert_path", "key_path"} and isinstance(item, str):
                        path = Path(item)
                        path = path if path.is_absolute() else root / path
                        if path.is_file():
                            name = hashlib.sha256(str(path).encode()).hexdigest()[:16] + "-" + path.name
                            save_file(path, destination / "configuration/referenced-files" / name)
                        manifest["external_backups_required"].append("Restore configuration file path: " + str(path))
                    elif key == "home" and isinstance(item, str):
                        manifest["external_backups_required"].append("Native login home: " + item)
                    referenced_configuration(item)

        for key in ("AOG_OPENSHELL_CONFIG", "AOG_NATIVE_LOGIN_PROFILES", "AOG_WORKER_SIGNING_KEY_PATH"):
            if environment.get(key):
                source = Path(environment[key])
                source = source if source.is_absolute() else root / source
                if source.is_file():
                    save_file(source, destination / "configuration" / key.lower())
                    if key != "AOG_WORKER_SIGNING_KEY_PATH":
                        referenced_configuration(json.loads(source.read_text()))
                else:
                    manifest["external_backups_required"].append("Missing configuration: " + key)
        configuration = destination / "configuration/effective-environment.json"
        configuration.parent.mkdir(mode=0o700, exist_ok=True)
        configuration.touch(mode=0o600)
        configuration.write_text(json.dumps(effective, indent=2) + "\n")
        for path in destination.rglob("*"):
            if path.is_file():
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                manifest["files"][str(path.relative_to(destination))] = digest.hexdigest()
        manifest["status"] = "completed"
    finally:
        path = destination / "manifest.json"
        path.touch(mode=0o600)
        path.write_text(json.dumps(manifest, indent=2) + "\n")
    return destination
