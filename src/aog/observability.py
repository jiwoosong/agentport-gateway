"""Local Hermes Relay export; only fixed-category counts cross the worker boundary.

ATOF payloads/names/metadata are intentionally never returned to the gateway.
These are observations in a worker time window, not durable workflow checkpoints.
"""

import json
from pathlib import Path

MAX_BYTES = 2_000_000


def configure_relay(root: Path):
    directory = root.resolve() / "telemetry"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = directory / "plugins.toml"
    content = f"""version = 1
[policy]
unknown_component = "error"
unknown_field = "error"
unsupported_value = "error"
[[components]]
kind = "observability"
enabled = true
[components.config]
version = 4
enable_full_payloads = false
[components.config.atof]
enabled = true
[[components.config.atof.sinks]]
type = "file"
output_directory = {json.dumps(str(directory))}
filename = "events.jsonl"
mode = "append"
"""
    if not config.exists() or config.read_text() != content:
        config.write_text(content)
        config.chmod(0o600)
    return config, directory / "events.jsonl"


def relay_cursor(path: Path):
    try:
        stat = path.stat()
        return stat.st_ino, stat.st_size
    except FileNotFoundError:
        return None, 0


def relay_summary(path: Path, cursor):
    counts = {category: {"started": 0, "ended": 0} for category in ("agent", "turn", "tool", "llm")}
    try:
        with path.open("rb") as source:
            import os

            stat = os.fstat(source.fileno())
            rotated = cursor[0] is not None and (stat.st_ino != cursor[0] or stat.st_size < cursor[1])
            source.seek(0 if rotated else cursor[1])
            data = source.read(MAX_BYTES + 1)
    except OSError:
        return {"source": "nemo-relay", "available": False}
    truncated = len(data) > MAX_BYTES
    data = data[:MAX_BYTES]
    # Ignore an unfinished record; never infer successful execution from a partial log.
    lines = data.split(b"\n")[:-1]
    invalid = 0
    for line in lines:
        try:
            event = json.loads(line)
            if not isinstance(event, dict) or event.get("atof_version") != "0.1":
                invalid += 1
                continue
            category = event.get("category")
            phase = {"start": "started", "end": "ended"}.get(str(event.get("scope_category")))
            if event.get("kind") == "scope" and isinstance(category, str) and category in counts and phase:
                counts[category][phase] += 1
        except (ValueError, TypeError):
            invalid += 1
    return {
        "source": "nemo-relay",
        "available": True,
        "scope": "worker_window",
        "counts": counts,
        "truncated": truncated,
        "rotated": rotated,
        "invalid_records": invalid,
    }
