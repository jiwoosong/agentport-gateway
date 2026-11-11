"""Outbound adapters. Endpoints are operator-registered; callers cannot override them."""

import asyncio
import os
import time

import httpx


class UncertainExecution(Exception):
    """An external action may have happened; do not automatically repeat it."""


class Komodo:
    def __init__(self):
        self.url = os.environ.get("KOMODO_URL", "").rstrip("/")

    async def call(self, area: str, operation: str, params: dict):
        if not self.url:
            raise RuntimeError("Komodo is not configured")
        async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
            response = await client.post(
                f"{self.url}/{area}",
                headers={
                    "X-API-KEY": os.environ["KOMODO_API_KEY"],
                    "X-API-SECRET": os.environ["KOMODO_API_SECRET"],
                },
                json={"type": operation, "params": params},
            )
            response.raise_for_status()
            return response.json()

    async def start(self, stack_id: str):
        return await self.execute("StartStack", stack_id)

    async def stop(self, stack_id: str):
        return await self.execute("StopStack", stack_id)

    async def update(self, stack_id: str):
        return await self.execute("DeployStack", stack_id)

    async def execute(self, operation: str, stack_id: str):
        value = await self.call("execute", operation, {"stack": stack_id})
        deadline = time.monotonic() + 300
        while value.get("status") != "Complete":
            if time.monotonic() > deadline:
                raise UncertainExecution("Komodo operation did not finish")
            uid = value.get("_id", {}).get("$oid") or value.get("id")
            if not uid:
                raise UncertainExecution("Komodo returned no operation ID")
            await asyncio.sleep(0.5)
            value = await self.call("read", "GetUpdate", {"id": uid})
        if value.get("success") is not True:
            raise RuntimeError("Komodo operation failed; inspect its audit log")
        return {"status": "Complete", "success": True}  # Raw deployment logs may contain secrets.


async def ready(url: str, timeout: int):
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
        while time.monotonic() < deadline:
            try:
                if (await client.get(url)).is_success:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
    raise TimeoutError("Service readiness deadline exceeded")
