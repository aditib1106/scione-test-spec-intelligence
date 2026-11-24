"""Manual clarification prose is escaped and flows without losing long notes."""

import re
from copy import deepcopy
from html import unescape

from scione.structured.pdf import _clarification_log


def test_clarification_log_wraps_full_plain_text_and_identifies_earlier_assessment():
    text = "<script>START & customer</script> " + "Complete wording. " * 210 + " END_MARKER"
    payload = {
        "comparison_id": "export-id",
        "machine_comparison_id": "current-machine",
        "customer_clarifications": {
            "req_gloss": {
                "version": 2,
                "events": [
                    {
                        "event_id": "question-one",
                        "question_id": "question-one",
                        "event_kind": "question",
                        "comparison_id": "earlier-machine",
                        "text": text,
                        "actor": "immutable-id-not-a-label",
                        "actor_label": "Customer coordinator",
                        "contact": "Customer <engineering>",
                        "created_at": "2026-11-24T15:00:00+00:00",
                    },
                    {
                        "event_id": "reply-one",
                        "question_id": "question-one",
                        "event_kind": "customer_reply",
                        "comparison_id": "current-machine",
                        "text": "Please report each site separately.",
                        "actor": "Lab & reviewer",
                        "contact": "Customer team",
                        "created_at": "2026-11-24T15:05:00+00:00",
                    },
                ],
            },
        },
    }
    before = deepcopy(payload)
    html = _clarification_log(payload, "req_gloss")
    assert "Question 1 · Earlier assessment" in html
    assert "Customer reply 1" in html
    assert "&lt;script&gt;START &amp; customer&lt;/script&gt;" in html
    assert "Customer &lt;engineering&gt;" in html
    assert "Lab &amp; reviewer" in html
    assert "Customer coordinator" in html
    assert "immutable-id-not-a-label" not in html
    assert "END_MARKER" in html
    visible = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))
    assert visible.count("Complete wording.") == 210
    assert "<script>" not in html and "<table>" not in html
    assert payload == before
    assert _clarification_log(payload, "req_other") == ""
