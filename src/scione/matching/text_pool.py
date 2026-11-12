"""Lossless wire deduplication for source quotations and repeated lab claim bodies."""

import json
from collections import Counter


def _wire_size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _pool_claim_bodies(payload):
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, list):
        return payload
    counts = Counter()
    bodies = {}
    for capability in capabilities:
        for claim in capability.get("claims", []):
            if not isinstance(claim, dict) or "id" not in claim or len(claim) < 2:
                continue
            body = {key: value for key, value in claim.items() if key != "id"}
            key = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            counts[key] += 1
            bodies.setdefault(key, body)
    references = {
        key: f"claim_body_{index + 1:03d}"
        for index, (key, count) in enumerate(counts.items()) if count > 1
    }
    if not references:
        return payload

    pooled = {**payload, "capabilities": []}
    for capability in capabilities:
        if "claims" not in capability:
            pooled["capabilities"].append(capability)
            continue
        claims = []
        for claim in capability["claims"]:
            body = {key: value for key, value in claim.items() if key != "id"}
            key = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            reference = references.get(key) if "id" in claim else None
            claims.append(
                {"id": claim["id"], "claim_body_ref": reference} if reference else claim
            )
        pooled["capabilities"].append({**capability, "claims": claims})
    pooled["claim_body_table"] = {
        reference: bodies[key] for key, reference in references.items()
    }
    # Charge the new table and every reference before deciding this is useful.
    return pooled if _wire_size(payload) - _wire_size(pooled) >= 4000 else payload


def _pool_source_text(payload):
    counts = Counter()

    def collect(value):
        if isinstance(value, str) and len(value) > 200:
            counts[value] += 1
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(payload)
    repeated = {
        text: f"text_{i + 1:03d}" for i, (text, count) in enumerate(counts.items()) if count > 1
    }
    if sum((counts[text] - 1) * len(text) for text in repeated) < 4000:
        return payload

    def replace(value):
        if isinstance(value, str) and value in repeated:
            return {"text_ref": repeated[value]}
        if isinstance(value, dict):
            return {k: replace(v) for k, v in value.items()}
        if isinstance(value, list):
            return [replace(v) for v in value]
        return value

    result = replace(payload)
    result["source_text_table"] = {identifier: text for text, identifier in repeated.items()}
    return result


def pool_matching_text(payload):
    result = _pool_source_text(_pool_claim_bodies(payload))
    if expand_matching_text(result) != payload:
        raise ValueError("Matching pooling must round-trip every original input value.")
    return result


def expand_matching_text(payload):
    table = payload.get("source_text_table", {})
    bodies = payload.get("claim_body_table", {})

    def expand(value):
        if isinstance(value, dict) and set(value) == {"text_ref"}:
            identifier = value["text_ref"]
            if identifier not in table:
                raise ValueError("Missing source quotation reference.")
            return table[identifier]
        if isinstance(value, dict) and "claim_body_ref" in value:
            if set(value) != {"id", "claim_body_ref"}:
                raise ValueError("Invalid lab claim body reference.")
            identifier = value["claim_body_ref"]
            if identifier not in bodies:
                raise ValueError("Missing lab claim body reference.")
            body = bodies[identifier]
            if not isinstance(body, dict) or "id" in body or "claim_body_ref" in body:
                raise ValueError("Invalid shared lab claim body.")
            return {"id": expand(value["id"]), **expand(body)}
        if isinstance(value, dict):
            return {k: expand(v) for k, v in value.items()}
        if isinstance(value, list):
            return [expand(v) for v in value]
        return value

    return expand({
        k: v for k, v in payload.items()
        if k not in {"source_text_table", "claim_body_table"}
    })
