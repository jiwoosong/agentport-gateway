"""Import a portable JSON/YAML definition using an existing ContextForge token."""

import argparse
import getpass
import os
from pathlib import Path

import httpx
import yaml

parser = argparse.ArgumentParser()
parser.add_argument("collection", choices=["services", "agents", "workflows"])
parser.add_argument("file", type=Path)
parser.add_argument("--gateway", default="http://127.0.0.1:4444")
args = parser.parse_args()
value = yaml.safe_load(args.file.read_text())
if "definition" in value:
    value = value["definition"]
token = os.environ.get("AGENTPORT_TOKEN") or getpass.getpass("ContextForge token: ")
with httpx.Client(trust_env=False, timeout=30) as client:
    result = client.post(
        args.gateway.rstrip("/") + "/orchestration/v1/" + args.collection,
        headers={"Authorization": "Bearer " + token},
        json=value,
    )
    result.raise_for_status()
    print(result.json()["id"])
