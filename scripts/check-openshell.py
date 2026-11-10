"""Read-only identity/phase check; requires the optional OpenShell SDK and operator mTLS config.

Run: uv run --extra openshell python scripts/check-openshell.py service.json
"""

import asyncio
import json
import sys
from pathlib import Path

from aog.models import Service
from aog.openshell import OpenShell


async def main():
    service = Service.model_validate_json(Path(sys.argv[1]).read_text())
    if not service.openshell:
        raise SystemExit("Definition has no OpenShell target")
    try:
        status = await OpenShell().execute(service.openshell, "status", service.timeout_seconds)
    except Exception:
        raise SystemExit(
            "OpenShell verification failed; inspect identity, mTLS and gateway configuration"
        ) from None
    print(json.dumps(status))


if __name__ == "__main__":
    asyncio.run(main())
