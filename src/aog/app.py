import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

from dbos import DBOS, SetWorkflowID
from mcpgateway.main import app
from sqlalchemy import select
from starlette.routing import Match, Mount

from aog import engine
from aog.api import router
from aog.engine import Engine, run_graph
from aog.mcp import AuthenticatedMCP, server
from aog.store import Run, Store

original_lifespan = app.router.lifespan_context
mcp_application = server.streamable_http_app(
    streamable_http_path="/", stateless_http=True, json_response=True
)


class MetadataOnlyErrors(logging.Filter):
    def filter(self, record):
        # SDK exceptions (e.g. JSON Schema) can embed complete input/output values.
        if record.exc_info:
            record.msg = "Execution error: %s"
            record.args = (record.exc_info[0].__name__,)
            record.exc_info = None
            record.exc_text = None
        return True


class OrchestrationMCPMiddleware:
    """Keep scoped MCP grants out of ContextForge's unrelated global JWT middleware.

    Only this exact mount is dispatched. AuthenticatedMCP still verifies every
    caller, and its tools use the same current ContextForge account/RBAC checks.
    """

    def __init__(self, app):
        self.next = app
        self.mount = Mount("/orchestration/v1/mcp", app=AuthenticatedMCP(mcp_application))

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            match, child_scope = self.mount.matches(scope)
            if match == Match.FULL:
                scope = {**scope, **child_scope}
                await self.mount.handle(scope, receive, send)
                return
        await self.next(scope, receive, send)


async def maintain():
    while True:
        try:
            await engine.current().lifecycle.sweep()
            with engine.current().store.tx() as db:
                queued = list(db.scalars(select(Run).where(Run.status == "queued")))
            for run in queued:
                with SetWorkflowID(run.id):
                    await DBOS.start_workflow_async(run_graph, run.id, run.spec, run.inputs, run.principal)
        except Exception as exc:
            # Keep maintenance alive, but never log request bodies or credential-bearing URLs.
            logging.getLogger("aog").error("Maintenance error: %s", type(exc).__name__)
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(application):
    async with original_lifespan(application):
        store = Store(os.environ.get("AOG_DATABASE_URL", "sqlite:///data/orchestration.db"))
        store.init()
        engine.engine = Engine(store)
        DBOS(
            config={
                "name": "agentport",
                "application_version": "0.1.0",
                "log_level": "ERROR",
                "system_database_url": os.environ.get("DBOS_SYSTEM_DATABASE_URL", "sqlite:///data/dbos.db"),
            }
        )
        logging.getLogger("dbos").addFilter(MetadataOnlyErrors())
        DBOS.launch()
        task = asyncio.create_task(maintain())
        try:
            async with mcp_application.router.lifespan_context(mcp_application):
                yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            DBOS.destroy(workflow_completion_timeout_sec=2)


app.router.lifespan_context = lifespan
app.include_router(router)
app.add_middleware(OrchestrationMCPMiddleware)
