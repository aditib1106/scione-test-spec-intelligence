"""Business views keep scientific facts while withholding inference artifacts."""

from __future__ import annotations

from fastapi.encoders import jsonable_encoder

from scione.auth.models import current_principal


def lab_request() -> bool:
    principal = current_principal()
    return principal is not None and principal.role == "lab"


def business_view(payload):
    """Return a detached view; never rewrite stored model/audit artifacts."""
    data = jsonable_encoder(payload)
    if not lab_request():
        return data

    def clean(value):
        if isinstance(value, list):
            return [clean(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "extraction" in value and "run_id" in value:
            return {
                key: clean(value[key])
                for key in ("run_id", "document_id", "started_at", "extraction")
                if key in value
            }
        return {
            key: clean(item)
            for key, item in value.items()
            if key not in {
                "raw_response", "raw_response_kind", "provider_request_id",
                "generation_settings", "grounding_actions", "wire_format_actions",
                "inventory_audit", "notice_audits", "policy_snapshot", "input_snapshot",
                "parsed_response", "normalization_actions", "source_review_audits",
            }
        }

    result = clean(data)
    if isinstance(result, dict) and result.get("failure"):
        failure = result["failure"]
        result["failure"] = {
            key: failure[key]
            for key in ("occurred_at", "previous_run_id", "message")
            if key in failure
        }
    return result


def public_matching_overview(payload):
    if not lab_request():
        return payload
    return {
        key: payload[key]
        for key in (
            "source_run_id", "report_input_fingerprint", "input_fingerprint", "current_run_id"
        )
        if key in payload
    }


def current_actor() -> str:
    principal = current_principal()
    return (
        principal.user_id
        if principal is not None and principal.user_id != "local"
        else "local_reviewer"
    )
