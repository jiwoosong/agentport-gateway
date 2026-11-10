import json
import tomllib

from aog import observability as module


def test_relay_window_omits_all_untrusted_payloads(tmp_path):
    config, log = module.configure_relay(tmp_path)
    parsed = tomllib.loads(config.read_text())
    component = parsed["components"][0]["config"]
    assert component["enable_full_payloads"] is False
    assert [s["type"] for s in component["atof"]["sinks"]] == ["file"]
    assert component["atof"]["sinks"][0]["output_directory"] == str(log.parent)
    log.write_text("prior-window\n")
    cursor = module.relay_cursor(log)
    event = dict(
        atof_version="0.1",
        kind="scope",
        category="tool",
        scope_category="start",
        name="sensitive-filename",
        metadata={"token": "secret"},
        input="private prompt",
    )
    with log.open("a") as f:
        f.write(json.dumps(event) + "\n")
        f.write(json.dumps({**event, "scope_category": "end"}) + "\n")
        f.write("partial")
    result = module.relay_summary(log, cursor)
    assert result["counts"]["tool"] == {"started": 1, "ended": 1}
    assert result["invalid_records"] == 0
    assert "secret" not in json.dumps(result)
    assert "sensitive" not in json.dumps(result)
    assert "private prompt" not in json.dumps(result)


def test_relay_missing_rotation_and_size_limit(tmp_path, monkeypatch):
    log = tmp_path / "events.jsonl"
    assert module.relay_summary(log, (None, 0))["available"] is False
    log.write_text("old data\n" * 10)
    cursor = module.relay_cursor(log)
    log.write_text("{}\n")
    assert module.relay_summary(log, cursor)["rotated"] is True
    monkeypatch.setattr(module, "MAX_BYTES", 10)
    log.write_bytes(b"x" * 20)
    assert module.relay_summary(log, (None, 0))["truncated"] is True
