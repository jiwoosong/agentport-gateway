"""Deterministic ACP wire fixture. No model, provider account, or network access."""

import json
import sys

for line in sys.stdin:
    message = json.loads(line)
    method, params = message.get("method"), message.get("params", {})
    if "id" not in message:
        continue
    if method == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}, "authMethods": []}
    elif method == "session/new":
        result = {"sessionId": "native-fixture"}
    elif method == "session/prompt":
        print(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": params["sessionId"],
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {
                                "type": "text",
                                "text": '{"approved":true,"feedback":"checked fixture"}',
                            },
                        },
                    },
                }
            ),
            flush=True,
        )
        result = {"stopReason": "end_turn"}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
