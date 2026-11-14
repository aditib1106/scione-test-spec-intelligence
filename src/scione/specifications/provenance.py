"""Accept only literal, metadata-only synthetic-method disclosure as a notice.

This is deliberately narrower than classifying arbitrary model review prose.
Scientific conditions, unknown wording and mixed warnings remain review issues.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from pydantic import ValidationError

from scione.ingestion.method_inventory import literal_method_designations
from scione.providers.json_output import loads_exact
from scione.schemas import IngestedDocument
from scione.source_text import citation_contains
from scione.specifications.models import (
    CustomerDocumentNotice,
    CustomerDocumentNoticeAudit,
    CustomerExtraction,
    CustomerExtractionRun,
)

_PROVENANCE_WORDS = frozenset(word.casefold() for word in """
a an all any this these that the are is were was be been being
method methods id ids identifier identifiers designation designations
test tests reference references referenced cited entry entries bank
synthetic fictional fictitious example examples benchmark sample samples benchmarking
customer quality specification requirement requirements entities acceptance limits
prepared extraction normalization
no not none official officially published standard standards
equivalence equivalent claimed claim claims asserted asserts assertion
declared declares declaring declaration document source printed provided
of as and or in for from only with by between here where to
used uses use stated retained exactly implied
conditions locations timing remain remains dwell pass buyer set client defined
specified supplied dataset evaluation checks limits exposure corrosion wash history
scales release govern governs memo s scione ai scioneai
les seuils ci dessus sont propres au client identifiants de méthode fictifs fictif
methodenkennungen sind fiktiv kundenbedingungen und grenzwerte bleiben maßgeblich
""".split())

_CHINESE_PROVENANCE_PHRASES = (
    "亦不宣称方法等效", "并非正式发布标准", "模拟方法编号", "正式标准文件",
    "方法等效声明", "以上编号均为", "本文件引用", "匹配测试路径", "本数据集",
    "交叉切割附着", "总干膜厚", "编号仅为", "对应", "方法库", "的", "或",
    "色差", "光泽", "湿热", "及", "并非", "正式标准", "均为", "模拟", "合成",
)
_SOURCE_ERROR = "Provenance notice is not a literal quotation on its physical source page."
_AFFIRMATIVE_ERROR = (
    "Provenance notice does not contain only an affirmative synthetic-method disclosure."
)
_CONTENT_ERROR = "Provenance notice contains other content that still requires review."
_SOFTWARE_NOTICE_ERRORS = (_SOURCE_ERROR, _AFFIRMATIVE_ERROR, _CONTENT_ERROR)
_REPRESENTATIVE_NOTICE = re.compile(
    r"No real company or person is named; all limits are representative[.]?", re.I
)


def _representative_notice(quote: str) -> bool:
    return bool(_REPRESENTATIVE_NOTICE.fullmatch(" ".join(quote.split())))

# Match whole metadata sentences, not words that could also occur in a real
# requirement. In particular, "no standard procedure text is reproduced" is a
# publication disclaimer, whereas "no standard procedure is required" is not.
_DEMONSTRATION_METADATA_SENTENCE = re.compile(
    r"(?:^|(?<=[.;]))\s*(?:"
    r"demonstration data only|"
    r"(?:the\s+)?customer requirements below are original fictional examples|"
    r"method identifiers are references only|"
    r"no standard procedure text is reproduced"
    r")\s*(?=[.;]|$)",
    re.I,
)


def metadata_only_synthetic_quote(quote: str, file_name: str = "") -> bool:
    """Recognize provenance/ownership concepts, never quantitative requirements.

    This check is independent of source verification. An unlocated pure-metadata
    proposal is unused and audited; it cannot become a verified source notice.
    Unknown scientific words, numbers, operators and real concerns fail closed.
    """
    if _representative_notice(quote):
        return True
    text = quote
    # A filename-matching document ID in a literal provenance footer is metadata,
    # not a scientific number. Other numbers remain disallowed below.
    for identifier in re.findall(
        r"\b[A-Z]{2,4}-(?:[A-Z]{2,4}-)?\d{2,5}\b", Path(file_name).name
    ):
        text = text.replace(identifier, "document")
    for method in sorted(literal_method_designations(text), key=len, reverse=True):
        text = re.sub(re.escape(method).replace(r"\ ", r"\s+"), "method", text, flags=re.I)
    text = re.sub(r"\b[A-Z]{3}-\d{2}\b", "method", text)
    text = unicodedata.normalize("NFKC", text).casefold()
    affirmative = bool(re.search(
        r"\b(?:synthetic|fictional|fictitious|fictifs?|fiktiv)\b|模拟方法编号|合成方法", text
    ))
    # Determine the affirmative disclosure before masking known metadata
    # sentences. All other text still passes the unchanged strict checks below.
    text = _DEMONSTRATION_METADATA_SENTENCE.sub(" document ", text)
    # A number mentioned only as the subject of an explicit data-ownership
    # statement is not a requested threshold or a contradiction. Other numeric
    # clauses remain disallowed, including must/unknown/comparison predicates.
    ownership = re.compile(
        r"(?:the\s+)?[+-]?\d+(?:[.,]\d+)?\s*[a-zµμω]+"
        r"(?:\s+and\s+other\s+limits)?\s+(?:are|remain)\s+"
        r"(?:buyer[- ]set|customer[- ](?:set|defined)|buyer[- ]specified)\.?"
    )
    text = ";".join(
        " limits are customer defined " if ownership.fullmatch(part.strip()) else part
        for part in text.split(";")
    )
    for phrase in sorted(_CHINESE_PROVENANCE_PHRASES, key=len, reverse=True):
        text = text.replace(phrase, " ")
    without_benign_negations = re.sub(
        r"\bno\s+(?:official\s+)?equivalence\s+(?:is\s+)?"
        r"(?:claimed|asserted|declared|implied)\b|"
        r"\bno\s+method\s+is\s+asserted\s+to\s+be\s+a\s+published\s+or\s+"
        r"officially\s+equivalent\s+standard\b|"
        r"\bnot\s+(?:an?\s+)?(?:official\s+)?(?:published\s+)?standards?\b", "", text,
    )
    if (
        not affirmative
        or re.search(r"\bnot\s+(?:all|synthetic|fictional|fictitious)\b", text)
        or re.search(r"\d|[%°±≤≥=<>]", text)
        or re.search(r"\b(?:no|not)\b", without_benign_negations)
    ):
        return False
    words = set(re.findall(r"[^\W\d_]+", text, re.UNICODE))
    if not words <= _PROVENANCE_WORDS:
        return False
    return True


def provenance_source_issues(
    notice: CustomerDocumentNotice, document: IngestedDocument
) -> list[str]:
    """Source status and metadata-only classification are separate checks."""
    page = next((page for page in document.pages if page.page_number == notice.page), None)
    if page is None or not citation_contains(page.text, notice.quote):
        return [_SOURCE_ERROR]
    if not metadata_only_synthetic_quote(notice.quote, document.file_name):
        return [_CONTENT_ERROR]
    return []


def filter_document_notices(
    extraction: CustomerExtraction, document: IngestedDocument, *, call_index: int
) -> tuple[CustomerExtraction, list[CustomerDocumentNoticeAudit]]:
    """Keep source-verified notices; audit unused metadata; review mixed concerns."""
    result = extraction.model_copy(deep=True)
    result.document_notices = []
    audits = []
    for index, notice in enumerate(extraction.document_notices, 1):
        issues = provenance_source_issues(notice, document)
        metadata = metadata_only_synthetic_quote(notice.quote, document.file_name)
        if _representative_notice(notice.quote) and issues:
            metadata = False  # This additional closed template requires its actual source.
        status = (
            "source_verified" if not issues else
            "unused_unverified_metadata" if metadata else "needs_review"
        )
        audits.append(CustomerDocumentNoticeAudit(
            call_index=call_index, notice_index=index, proposal=notice.model_copy(deep=True),
            status=status, issues=issues,
        ))
        if not issues:
            result.document_notices.append(notice.model_copy(deep=True))
        elif not metadata:
            result.review_issues.append(
                "Document notice needs review: " + " ".join(issues) + " " + notice.quote
            )
    return result, audits


def nonblocking_notice_issues(
    run: CustomerExtractionRun, document: IngestedDocument
) -> set[str]:
    """Reclassify only historical software errors tied to actual typed proposals.

    Ordinary citation/atomic warnings and free-form model concerns are untouched.
    Historical raw responses stay immutable; this is a current review projection.
    """
    ignored = set()
    for call in run.calls:
        if call.purpose != "extraction":
            continue
        try:
            payload = loads_exact(call.raw_response)
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("document_notices"), list):
            continue
        for proposed in payload["document_notices"]:
            try:
                notice = CustomerDocumentNotice.model_validate(proposed)
            except ValidationError:
                continue
            if _representative_notice(notice.quote) and provenance_source_issues(notice, document):
                continue
            if metadata_only_synthetic_quote(notice.quote, document.file_name):
                for error in _SOFTWARE_NOTICE_ERRORS:
                    candidate = "Document notice needs review: " + error + " " + notice.quote
                    if candidate in run.extraction.review_issues:
                        ignored.add(candidate)
    return ignored
