"""Independent Core API and durable worker; gateways are external integrations."""
import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

from dbos import DBOS, SetWorkflowID
from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import select

from aog import engine
from aog.backends import get_backend
from aog.bridge import read_message, rpc_response, verify_message
from aog.store import Run, Store


class MetadataOnlyErrors(logging.Filter):
    def filter(self, record):
        if record.exc_info:
            record.msg = "Execution error: %s"
            record.args = (record.exc_info[0].__name__,)
            record.exc_info = None
            record.exc_text = None
        return True


async def maintain():
    while True:
        try:
            await engine.current().lifecycle.sweep()
            with engine.current().store.tx() as db:
                queued = list(db.scalars(select(Run).where(Run.status == "queued")))
            for run in queued:
                with SetWorkflowID(run.id):
                    await DBOS.start_workflow_async(engine.run_graph, run.id, run.spec, run.inputs, run.principal)
        except Exception as exc:
            logging.getLogger("aog").error("Maintenance error: %s", type(exc).__name__)
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(application):
    store = Store(os.environ.get("AOG_DATABASE_URL", "sqlite:///data/orchestration.db"))
    store.init()
    backend = get_backend()
    if os.environ.get("AOG_MIGRATING") == "1":
        application.state.migration_report = await backend.migrate()
        yield
        return
    await backend.prepare()
    application.state.gateway_capabilities = await backend.capabilities()
    engine.engine = engine.Engine(store, catalog=backend.catalog(store), backend=backend)
    DBOS(config={
        "name": "agentport", "application_version": "agentport-2", "log_level": "ERROR",
        "system_database_url": os.environ.get("DBOS_SYSTEM_DATABASE_URL", "sqlite:///data/dbos.db"),
    })
    logging.getLogger("dbos").addFilter(MetadataOnlyErrors())
    DBOS.launch()
    task = asyncio.create_task(maintain())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        DBOS.destroy(workflow_completion_timeout_sec=2)


def create_app():
    from aog.api import router
    from aog.http_limits import BodyLimit
    from aog.management import router as management_router
    application = FastAPI(title="AgentPort Core", lifespan=lifespan)
    application.add_middleware(BodyLimit)
    application.include_router(management_router, prefix="/orchestration/v1")
    application.include_router(router)

    @application.get("/health")
    async def health():
        return {"ok": engine.engine is not None, "component": "agentport-core", "contract_version": 1}

    @application.post("/internal/agentport/v1/dispatch")
    async def dispatch_message(request: Request):
        from aog.dispatch import dispatch
        body = await read_message(request)
        operation, arguments = verify_message(body, request.headers, "core")
        try:
            if operation != "dispatch":
                raise HTTPException(403, "Unknown gateway callback")
            value = await dispatch(**arguments)
            return rpc_response(body, value, "core")
        except HTTPException as exc:
            return rpc_response(body, {"detail": exc.detail}, "core", status=exc.status_code)
        except Exception:
            return rpc_response(body, {"detail": "Core operation failed; inspect job state"}, "core", status=502)

    return application


app = create_app()
