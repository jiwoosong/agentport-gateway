import json
import os
import sqlite3
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from aog.migration import create_backup, perform_migration
from aog.models import Node, Principal, Service, Workflow
from aog.store import Lease, Resource, Run, Runtime, RuntimeTarget, SandboxBinding, Store


class Catalog:
    def __init__(self, fail_after_commit=False):
        self.tools = {}
        self.calls = []
        self.fail_after_commit = fail_after_commit

    async def migrated_tool(self, source_id, owner):
        return self.tools.get((source_id, owner))

    async def save(self, document, actor, migration_source_id):
        self.calls.append((document, actor, migration_source_id))
        tool = SimpleNamespace(id="native-" + migration_source_id)
        self.tools[migration_source_id, actor.subject] = tool
        if self.fail_after_commit:
            self.fail_after_commit = False
            raise RuntimeError("Lost native catalog response")
        return tool


def legacy_store(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/old.db")
    store.init()
    document = Service(name="Converter", endpoint="http://converter/convert", stack_id="image-stack",
                       lifecycle="on_demand", health_url="http://converter/health",
                       project_id="team", visibility="project").model_dump()
    with store.tx() as db:
        db.add(Resource(id="old-service", kind="service", owner="alice", project_id="team",
                        visibility="project", revision=4, document=document))
        db.flush()
        db.add(Runtime(service_id="old-service", phase="running", last_used=123))
        db.add(RuntimeTarget(target=store.target_identity("service", document), runtime_id="old-service"))
        db.add(SandboxBinding(target="old-lock", service_id="old-service"))
        db.add(Resource(id="legacy-agent", kind="agent", owner="alice", revision=2,
                        document={"name": "Author", "service_id": "old-service", "model": "unknown"}))
        db.add(Run(id="historical", owner="alice", status="completed", inputs={"file": "retained"},
                   principal={"subject": "alice"}, spec={"service_id": "old-service"}, result={"url": "retained"}))
    return store


async def test_migration_preserves_ids_ownership_runtime_and_historical_results(tmp_path):
    store, catalog = legacy_store(tmp_path), Catalog()
    report = await perform_migration(store, catalog)
    assert report == {"migrated_services": 1, "archived_agents": 1, "archived_services": 0,
                      "resumed_aliases": 0, "unchanged_aliases": 0,
                      "published_workflows": 0, "workflows_requiring_review": 0}
    actor = Principal(subject="alice", teams=["team"])
    alias = store.resource("old-service", actor)
    assert alias.kind == "service_alias" and alias.revision == 5
    assert alias.document["phase"] == "completed" and alias.document["tool_id"] == "native-old-service"
    runtime_id = alias.document["runtime_id"]
    runtime = store.resource(runtime_id, actor)
    assert (runtime.kind, runtime.owner, runtime.project_id, runtime.visibility) == ("runtime", "alice", "team", "project")
    assert runtime.document["target_id"] == "image-stack"
    assert store.runtime_id("native-old-service") == runtime_id
    with store.tx() as db:
        assert db.get(Runtime, "old-service") is None
        assert db.get(Runtime, runtime_id).last_used == 123
        assert db.scalar(select(RuntimeTarget)).runtime_id == runtime_id
        assert db.get(SandboxBinding, "old-lock") is None
        archive = db.get(Resource, "legacy-agent")
        assert archive.kind == "agent_archive" and archive.document["enabled"] is False
        assert archive.document["legacy_definition"]["model"] == "unknown"
        historical = db.get(Run, "historical")
        assert historical.spec == {"service_id": "old-service"}
        assert historical.result == {"url": "retained"} and historical.status == "completed"
    assert catalog.calls[0][1].subject == "alice"
    again = await perform_migration(store, catalog)
    assert again["unchanged_aliases"] == 1 and again["archived_agents"] == 0
    assert len(catalog.calls) == 1


async def test_migration_recovers_native_commit_without_duplicate_registration(tmp_path):
    store, catalog = legacy_store(tmp_path), Catalog(fail_after_commit=True)
    with pytest.raises(RuntimeError, match="Lost native"):
        await perform_migration(store, catalog)
    actor = Principal(subject="alice", teams=["team"])
    alias = store.resource("old-service", actor)
    assert alias.document["phase"] == "pending"
    runtime_id = alias.document["runtime_id"]
    report = await perform_migration(store, catalog)
    assert report["resumed_aliases"] == 1 and len(catalog.calls) == 1
    assert store.runtime_id("native-old-service") == runtime_id
    with store.tx() as db:
        assert len(list(db.scalars(select(Resource).where(Resource.kind == "runtime")))) == 1


async def test_migration_publishes_compatible_workflows_and_leaves_archived_agent_refs_for_review(tmp_path):
    store, catalog = legacy_store(tmp_path), Catalog()
    actor = Principal(subject="alice", teams=["team"])
    compatible = store.save("workflow", Workflow(name="Existing tool graph", project_id="team",
        nodes=[Node(id="convert", type="tool", target="old-service")]).model_dump(), actor)
    store.save("workflow", Workflow(name="Ambiguous agent graph", project_id="team",
        nodes=[Node(id="author", type="agent", target="legacy-agent")]).model_dump(), actor)
    catalog.publish_resource = AsyncMock()
    report = await perform_migration(store, catalog)
    assert report["published_workflows"] == report["workflows_requiring_review"] == 1
    published, owner = catalog.publish_resource.await_args.args
    assert published.id == compatible.id and owner.subject == actor.subject
    assert store.resource(compatible.id, actor).document["nodes"][0]["target"] == "old-service"


async def test_ambiguous_legacy_model_is_archived_without_guessing_protocol_or_auth(tmp_path):
    store, catalog = legacy_store(tmp_path), Catalog()
    with store.tx() as db:
        row = db.get(Resource, "old-service")
        row.document = {**row.document, "kind": "model", "transport": "openai"}
    report = await perform_migration(store, catalog)
    assert report["archived_services"] == 1 and not catalog.calls
    with store.tx() as db:
        archived = db.get(Resource, "old-service")
        assert archived.kind == "service_archive" and archived.document["enabled"] is False
        assert archived.document["legacy_definition"]["transport"] == "openai"
        assert db.get(Resource, archived.document["runtime_id"]).kind == "runtime"
    again = await perform_migration(store, catalog)
    assert again["archived_services"] == 0 and not catalog.calls


@pytest.mark.parametrize("blocker", ["running", "needs_attention", "lease", "starting"])
async def test_migration_requires_drain_before_any_writes(tmp_path, blocker):
    store = legacy_store(tmp_path)
    with store.tx() as db:
        if blocker in {"running", "needs_attention"}:
            db.add(Run(id="pending", owner="alice", status=blocker, inputs={}, principal={}, spec={}))
        elif blocker == "lease":
            db.add(Lease(id="session-lease", service_id="old-service", run_id="historical"))
        else:
            db.get(Runtime, "old-service").phase = "starting"
    catalog = Catalog()
    with pytest.raises(RuntimeError, match="Migration requires"):
        await perform_migration(store, catalog)
    with store.tx() as db:
        assert db.get(Resource, "old-service").kind == "service"
        assert db.get(Resource, "legacy-agent").kind == "agent"
    assert not catalog.calls


def test_private_backup_includes_committed_wal_artifacts_and_effective_secrets(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    source = data / "orchestration.db"
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE preserved(value TEXT)")
        db.execute("INSERT INTO preserved VALUES ('committed')")
        db.commit()
        (tmp_path / ".env").write_text("AUTH_ENCRYPTION_SECRET=from-file\n")
        (data / "artifacts").mkdir()
        (data / "artifacts/private.txt").write_text("result")
        (data / "worker-signing.key").write_bytes(b"signing-key")
        backup = create_backup(root=tmp_path, environment={"AUTH_ENCRYPTION_SECRET": "effective-value"})
    with sqlite3.connect(backup / "databases/aog_database_url.sqlite") as db:
        assert db.execute("SELECT value FROM preserved").fetchone() == ("committed",)
    assert (backup / "local/data/artifacts/private.txt").read_text() == "result"
    assert (backup / "local/data/worker-signing.key").read_bytes() == b"signing-key"
    assert json.loads((backup / "configuration/effective-environment.json").read_text())["AUTH_ENCRYPTION_SECRET"] == "effective-value"
    manifest = json.loads((backup / "manifest.json").read_text())
    assert manifest["status"] == "completed" and manifest["external_backups_required"]
    assert "effective-value" not in (backup / "manifest.json").read_text()
    assert backup.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in backup.rglob("*") if path.is_file())
    with pytest.raises(FileExistsError):
        create_backup(backup, root=tmp_path, environment={})


def test_postgres_backup_keeps_password_out_of_process_arguments(tmp_path, monkeypatch):
    monkeypatch.setattr("aog.migration.shutil.which", lambda _: "/usr/bin/pg_dump")
    calls = []

    def dump(arguments, **kwargs):
        calls.append((arguments, kwargs))
        Path(arguments[-1]).write_bytes(b"native pg_dump")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("aog.migration.subprocess.run", dump)
    backup = create_backup(root=tmp_path, environment={"DATABASE_URL": "postgresql+psycopg://user:secret@db:5432/cf?sslmode=require"})
    args, kwargs = calls[0]
    assert "secret" not in " ".join(args) and kwargs["env"]["PGPASSWORD"] == "secret"
    assert kwargs["env"]["PGSSLMODE"] == "require"
    assert (backup / "databases/database_url.dump").read_bytes() == b"native pg_dump"


def test_backup_includes_configured_artifacts_tls_and_referenced_auth_environment(tmp_path):
    artifacts = tmp_path / "custom-results"
    artifacts.mkdir()
    (artifacts / "result.txt").write_text("retained")
    key = tmp_path / "client.key"
    key.write_text("private TLS key")
    config = tmp_path / "openshell.json"
    config.write_text(json.dumps({"dgx": {"key_path": str(key), "client_credentials": {
        "client_secret_env": "DGX_CLIENT_SECRET"}}}))
    backup = create_backup(root=tmp_path, environment={"AOG_ARTIFACT_ROOT": str(artifacts),
        "AOG_OPENSHELL_CONFIG": str(config), "DGX_CLIENT_SECRET": "retained secret"})
    assert (backup / "local/aog_artifact_root/result.txt").read_text() == "retained"
    saved = list((backup / "configuration/referenced-files").glob("*-client.key"))
    assert len(saved) == 1 and saved[0].read_text() == "private TLS key"
    effective = json.loads((backup / "configuration/effective-environment.json").read_text())
    assert effective["DGX_CLIENT_SECRET"] == "retained secret"
    assert "retained secret" not in (backup / "manifest.json").read_text()


def test_operator_postgres_archive_is_private_and_does_not_require_pg_dump(tmp_path, monkeypatch):
    archive = tmp_path / "operator.dump"
    archive.write_bytes(b"PGDMPoperator-archive")
    monkeypatch.setattr("aog.migration.shutil.which", lambda _: None)
    url = "postgresql+psycopg://user:password@postgres/agentport"
    backup = create_backup(root=tmp_path, postgres_backup=archive, environment={
        "DATABASE_URL": url, "AOG_DATABASE_URL": url, "DBOS_SYSTEM_DATABASE_URL": url})
    for key in ("database_url", "aog_database_url", "dbos_system_database_url"):
        saved = backup / f"databases/{key}.dump"
        assert saved.read_bytes() == archive.read_bytes() and saved.stat().st_mode & 0o777 == 0o600
    manifest = json.loads((backup / "manifest.json").read_text())
    assert manifest["postgres_backup"] == "operator-provided"


@pytest.mark.parametrize("content", [b"", b"PGDMP", b"not-a-custom-archive"])
def test_invalid_operator_postgres_archive_rejected_before_backup(tmp_path, content):
    archive = tmp_path / "operator.dump"
    archive.write_bytes(content)
    with pytest.raises(RuntimeError, match="archive"):
        create_backup("backup", root=tmp_path, postgres_backup=archive,
                      environment={"DATABASE_URL": "postgresql://user@postgres/agentport"})
    assert not (tmp_path / "backup").exists()


def test_one_operator_archive_cannot_back_up_multiple_postgres_databases(tmp_path):
    archive = tmp_path / "operator.dump"
    archive.write_bytes(b"PGDMPoperator-archive")
    with pytest.raises(RuntimeError, match="distinct"):
        create_backup(root=tmp_path, postgres_backup=archive, environment={
            "DATABASE_URL": "postgresql://user@postgres/contextforge",
            "AOG_DATABASE_URL": "postgresql://user@postgres/orchestration"})


def test_failed_backup_is_marked_incomplete_and_does_not_start_migration(tmp_path):
    with pytest.raises(RuntimeError, match="In-memory"):
        create_backup("failed", root=tmp_path, environment={"AOG_DATABASE_URL": "sqlite:///:memory:"})
    manifest = json.loads((tmp_path / "failed/manifest.json").read_text())
    assert manifest["status"] == "incomplete"


def test_cli_migration_backs_up_before_app_start_and_uses_migration_lifespan(tmp_path, monkeypatch, capsys):
    from aog.cli import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["aog", "migrate", "--offline"])
    calls = []

    @asynccontextmanager
    async def lifespan(app):
        assert os.environ["AOG_MIGRATING"] == "1"
        assert list((tmp_path / "data/backups").glob("*/manifest.json"))
        calls.append("migration-only")
        yield

    app = SimpleNamespace(router=SimpleNamespace(lifespan_context=lifespan),
                          state=SimpleNamespace(migration_report={"migrated_services": 0}))
    monkeypatch.setitem(sys.modules, "aog.app", SimpleNamespace(app=app))
    monkeypatch.delenv("AOG_MIGRATING", raising=False)
    main()
    assert calls == ["migration-only"] and '"migrated_services": 0' in capsys.readouterr().out
    assert "AOG_MIGRATING" not in os.environ


def test_cli_requires_operator_offline_confirmation(tmp_path, monkeypatch):
    from aog.cli import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["aog", "migrate"])
    with pytest.raises(SystemExit):
        main()
    assert not (tmp_path / "data").exists()
