"""Lossless wire deduplication for repeated long source quotations."""

from collections import Counter


def pool_matching_text(payload):
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
    if expand_matching_text(result) != payload:
        raise ValueError("Source text pooling must round-trip every original input value.")
    return result


def expand_matching_text(payload):
    table = payload.get("source_text_table", {})

    def expand(value):
        if isinstance(value, dict) and set(value) == {"text_ref"}:
            identifier = value["text_ref"]
            if identifier not in table:
                raise ValueError("Missing source quotation reference.")
            return table[identifier]
        if isinstance(value, dict):
            return {k: expand(v) for k, v in value.items()}
        if isinstance(value, list):
            return [expand(v) for v in value]
        return value

    return expand({k: v for k, v in payload.items() if k != "source_text_table"})
