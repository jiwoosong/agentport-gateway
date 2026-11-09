"""Portable graph semantics. No eval, model router, or implicit history forwarding."""

from copy import deepcopy
from typing import Any

from aog.models import Node


def layers(nodes: list[Node]) -> list[list[Node]]:
    ids = {n.id for n in nodes}
    if len(ids) != len(nodes):
        raise ValueError("Duplicate node ID")
    reserved = {
        f"{n.id}-{role}-{turn}"
        for n in nodes
        if n.type == "review"
        for role in ("author", "review")
        for turn in range(n.max_rounds)
    }
    release_ids = [
        f"{n.id[:32]}-release-{role}-{turn}"
        for n in nodes
        if n.type == "review"
        for role in ("author", "review")
        for turn in range(n.max_rounds)
    ]
    if len(set(release_ids)) != len(release_ids):
        raise ValueError("Review node prefixes must be unique within 32 characters")
    reserved.update(release_ids)
    if reserved & ids:
        raise ValueError("Node ID conflicts with a review substep")
    for node in nodes:
        if set(node.depends_on) - ids or node.id in node.depends_on:
            raise ValueError(f"Invalid dependencies: {node.id}")
        if node.type in {"tool", "model", "agent", "review"} and not node.target:
            raise ValueError(f"Target required: {node.id}")
        if node.type == "review" and not node.reviewer:
            raise ValueError("Review requires a reviewer agent")
    done: set[str] = set()
    result = []
    while len(done) < len(ids):
        ready = [n for n in nodes if n.id not in done and set(n.depends_on) <= done]
        if not ready:
            raise ValueError("Cycles are not allowed; use a bounded review node")
        # References must be to ancestors, not an unrelated concurrently running node.
        for node in ready:
            ancestors = set(node.depends_on)
            todo = list(ancestors)
            by_id = {n.id: n for n in nodes}
            while todo:
                for dep in by_id[todo.pop()].depends_on:
                    if dep not in ancestors:
                        ancestors.add(dep)
                        todo.append(dep)
            for ref in references([node.inputs, node.when]):
                parts = ref.split(".")
                if parts[0] not in {"input", "nodes"}:
                    raise ValueError(f"Unknown reference root: {ref}")
                if parts[0] == "nodes" and (len(parts) < 2 or parts[1] not in ancestors):
                    raise ValueError(f"Reference is not an ancestor: {ref}")
        result.append(ready)
        done.update(n.id for n in ready)
    return result


def references(value: Any):
    if isinstance(value, dict):
        if "$ref" in value:
            if set(value) != {"$ref"} or not isinstance(value["$ref"], str):
                raise ValueError("A reference must be a single string $ref field")
            yield value["$ref"]
        else:
            for child in value.values():
                yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


def resolve(value: Any, inputs: dict, outputs: dict):
    if isinstance(value, dict):
        if "$ref" in value:
            result: Any = {"input": inputs, "nodes": outputs}
            for part in value["$ref"].split("."):
                if isinstance(result, list) and part.isdigit():
                    result = result[int(part)]
                elif isinstance(result, dict) and part in result:
                    result = result[part]
                else:
                    raise ValueError(f"Missing input reference: {value['$ref']}")
            return deepcopy(result)
        return {k: resolve(v, inputs, outputs) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, inputs, outputs) for v in value]
    return deepcopy(value)


def condition(values: dict) -> bool:
    if set(values) != {"left", "op", "right"}:
        raise ValueError("Condition requires left, op, right")
    left, right = values["left"], values["right"]
    match values["op"]:
        case "eq":
            return left == right
        case "ne":
            return left != right
        case "in":
            return left in right
        case "gt":
            return left > right
        case "lt":
            return left < right
        case _:
            raise ValueError("Unsupported condition operator")
