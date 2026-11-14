"""Locate source-proved chunk notes without approving their scientific content.

Original model warnings and provider responses remain immutable. Regional method
questions still block their known method owners and remain visible once at the
document level, including alternatives without their own requested-test row.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from scione.catalogs.normalization import normalized_name
from scione.ingestion.method_inventory import literal_method_designations
from scione.schemas import IngestedDocument
from scione.source_text import citation_contains
from scione.specifications.models import CustomerExtractionRun
from scione.specifications.source_chunking import SOURCE_CHUNK_POLICY

_REGIONAL = re.compile(
    r"Page (?P<page>[1-9]\d*) regional annex lists accepted method equivalents "
    r"\((?P<pairs>[^()]+)\) for tests whose full conditions are on context page [1-9]\d*, "
    r"which is outside this chunk['’]s primary pages; these equivalence clauses could not "
    r"be attached to test rows emitted here[.]?", re.I,
)
_REGIONAL_OTHER_PRIMARY = re.compile(
    r"Page (?P<page>[1-9]\d*) regional annex lists accepted method equivalents "
    r"\((?P<pairs>[^()]+)\)[.] These are equivalence declarations for tests whose "
    r"substantive rows appear on other pages not supplied as primary here, so no "
    r"separate test rows were emitted for them[.]?", re.I,
)
_REGIONAL_UNATTACHED = re.compile(
    r"Page (?P<page>[1-9]\d*) lists accepted method equivalents "
    r"\((?P<pairs>[^()]+)\) for tests whose substantive rows are on other pages "
    r"not supplied here; they could not be attached to those tests in this chunk[.]?", re.I,
)
_APPROVAL = re.compile(
    r"Page (?P<page>[1-9]\d*) approval-package table \((?P<packages>[^()]+)\) "
    r"is administrative workflow, not independently requested tests; the referenced "
    r"test packages \(e[.]g[.] (?P<examples>[^()]+)\) are summaries of tests detailed "
    r"elsewhere in the document[.]?", re.I,
)
_APPROVAL_OTHER_SECTIONS = re.compile(
    r"Page (?P<page>[1-9]\d*) lists approval packages \((?P<packages>[^()]+)\); "
    r"these are package summaries referencing other sections, not new requested "
    r"tests on the primary pages[.]?", re.I,
)
_APPROVAL_SUMMARY = re.compile(
    r"Page (?P<page>[1-9]\d*) approval-package table \((?P<packages>[^()]+)\) "
    r"summarizes tests defined elsewhere and was not emitted as new test rows[.]?", re.I,
)
_BAKE_SCOPE = re.compile(
    r"Page (?P<page>[1-9]\d*) states '(?P<quote>Adhesion, stone chip and humidity "
    r"are tested at all three bake conditions; other tests at nominal bake unless stated)', "
    r"but the corresponding adhesion, stone-chip and humidity test rows are printed on "
    r"context pages \(Section 3[.]2/4[.]x index on page [1-9]\d*\) not included as primary "
    r"rows in this chunk; applicability of the three-bake condition to those tests "
    r"could not be resolved here[.]?", re.I,
)
_WEATHERING_WORKFLOW = re.compile(
    r"Section 4[.]2 weathering note on page (?P<page>[1-9]\d*) "
    r"\('(?P<quote>Series release can be granted on accelerated results; outdoor "
    r"exposure continues after release and the release is withdrawn if a checkpoint fails)'\) "
    r"is a release-workflow clause, retained here rather than as a test row; confirm "
    r"whether it should be captured as a shared instruction in a full-document extraction[.]?",
    re.I,
)
_REGIONAL_FLORIDA = re.compile(
    r"Page (?P<page>[1-9]\d*) Europe row states '(?P<quote>Florida exposure "
    r"\(Section 4[.]2\) is sufficient)' as a cross-reference to weathering tests defined "
    r"on other pages; no separate test row was emitted for it[.]?", re.I,
)
_METADATA_VARIANCE = (
    "Source-window extractions returned differing document metadata; "
    "the first window's metadata was retained and must be checked against the source."
)


@dataclass(frozen=True)
class ChunkReviewProjection:
    document_issues: tuple[str, ...]
    row_targets: dict[str, frozenset[str]]
    source_locations: dict[str, dict] = field(default_factory=dict)


def _key(code: str) -> str:
    return re.sub(r"\s+", "", code).casefold()


def _located(text: str, fragment: str) -> bool:
    return citation_contains(text.casefold(), fragment.casefold())


def _heading(text: str, title: str) -> bool:
    return bool(re.search(
        r"(?mi)^\s*(?:\d+(?:\.\d+)*[.)]?\s+)?" + re.escape(title) + r"\s*$", text
    ))


def _column_text(text: str, *, first: bool) -> str:
    """Locate table labels/tokens, not a contiguous scientific quotation.

    Geometric PDF text uses repeated horizontal spaces between columns. Removing
    the other columns can reconnect wrapped labels; this cannot prove a method
    relationship, limit, applicability or capability. All such questions remain
    in the document note and the known method owners' review queue.
    """
    return "\n".join(
        re.split(r"[ \t]{2,}", line.strip())[0 if first else -1]
        for line in text.splitlines() if line.strip()
    )


def _weathering_owners(run: CustomerExtractionRun) -> frozenset[str]:
    """Locate explicit weathering titles, without assessing their conditions."""
    return frozenset(
        row.id for row in run.extraction.requirements
        if re.search(
            r"\b(?:weathering|xenon|outdoor|fluorescent)\b|"
            r"\b(?:desert|humid tropical|dry heat)\b.*\bexposure\b",
            normalized_name(row.original_test_name),
        )
    )


def _metadata_variance_pages(run, pages):
    """Prove title detail differences and one physically printed controlling revision.

    Differing identifiers, controlling revisions or superseded revisions remain
    global concerns. This only relocates a system-generated document-control note.
    """
    metadata = []
    for call in run.calls:
        if call.purpose != "extraction" or call.request is None:
            continue
        try:
            item = json.loads(call.raw_response).get("document")
        except (ValueError, AttributeError, TypeError):
            return ()
        if not isinstance(item, dict) or not all(
            isinstance(item.get(key), str) and item[key].strip()
            for key in ("title", "identifier", "revision")
        ):
            return ()
        metadata.append(item)
    if len(metadata) < 2 or len({item["identifier"] for item in metadata}) != 1:
        return ()
    revisions = [re.fullmatch(
        r"(?P<current>[A-Za-z0-9.-]+)(?:\s*\(supersedes Rev\s+"
        r"(?P<previous>[A-Za-z0-9.-]+)\))?", item["revision"], re.I,
    ) for item in metadata]
    if any(match is None for match in revisions):
        return ()
    if len({match["current"].casefold() for match in revisions}) != 1:
        return ()
    previous = {match["previous"].casefold() for match in revisions if match["previous"]}
    if len(previous) > 1:
        return ()
    full_revision = max((item["revision"] for item in metadata), key=len)
    identifier = metadata[0]["identifier"]
    source_pages = tuple(page for page, text in pages.items() if (
        _located(text, identifier)
        and _located(text, "Revision " + full_revision)
        and all(_located(text, item["title"]) for item in metadata)
    ))
    return source_pages


def _mentioned_pages(text: str) -> set[int]:
    """Read explicit page references only; section numbers are not page numbers."""
    result = set()
    for match in re.finditer(r"\bpages?\s+([1-9]\d*)(?:\s*[-–]\s*([1-9]\d*))?", text, re.I):
        start = int(match[1])
        end = int(match[2] or start)
        if end < start or end - start > 100:
            return set()
        result.update(range(start, end + 1))
    return result


def _ordered_reference_words(text: str, fragment: str) -> bool:
    """Locate a quoted cross-reference across interleaved table columns only.

    This is layout provenance, never proof of a quantitative/semantic relation.
    """
    source = iter(re.findall(r"[^\W_]+", normalized_name(text)))
    words = re.findall(r"[^\W_]+", normalized_name(fragment))
    return len(words) >= 3 and all(any(word == item for item in source) for word in words)


def _chunk_coverage_locations(run, manifest, pages):
    """Keep child-window coverage notes at document scope after a proved merge.

    These notes say what a child *did not emit*, not that every merged row is
    incorrect. The saved partition and original request must prove the window
    restriction. Physical index/annex text locates the mentioned reference; it
    does not establish completeness, shared-condition applicability, equivalence,
    or lab support. Such questions remain explicit document review obligations.
    Scientific ambiguity and row-specific defects never enter this route.
    """
    chunks = manifest.get("input_partitioning", {}).get("chunks")
    if not isinstance(chunks, list) or len(chunks) < 2:
        return {}
    windows = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            return {}
        primary, supplied = chunk.get("primary_pages"), chunk.get("supplied_pages")
        if not all(isinstance(value, list) and value and all(
            type(page) is int and page in pages for page in value
        ) for value in (primary, supplied)) or not set(primary) <= set(supplied):
            return {}
        windows.append((set(primary), set(supplied)))
    if set().union(*(primary for primary, _ in windows)) != set(pages):
        return {}
    locations = {}
    for call in run.calls:
        if call.purpose != "extraction" or call.request is None:
            continue
        window = next((item for item in windows if item[0] == set(call.focus_pages)), None)
        if window is None:
            continue
        primary, supplied = window
        received = {int(value) for value in re.findall(
            r"=== PAGE ([1-9]\d*) ===", call.request.user_prompt
        )}
        if not supplied <= received:
            continue
        try:
            comments = json.loads(call.raw_response).get("review_issues", [])
        except (ValueError, AttributeError, TypeError):
            continue
        for issue in comments:
            if not isinstance(issue, str) or issue not in run.extraction.review_issues:
                continue
            text = " ".join(issue.split())
            if re.search(r"\breq_[\w-]+\b", text):
                continue
            if not re.search(r"\b(?:context|primary)\b", text, re.I) or not re.search(
                r"\b(?:not|no)\b.{0,100}\b(?:emitted|target rows|new test row)\b|"
                r"\bno\s+(?:separate\s+)?(?:test\s+)?rows?\b.{0,100}\bemitted\b|"
                r"\brather than\s+(?:separate|new)\s+test rows\b",
                text, re.I,
            ):
                continue
            # Never move a value, unit, applicability, citation, or interpretation
            # defect out of its existing review route just because it also says
            # that a child did not emit a row.
            if re.search(
                r"\b(?:ambiguous|ambiguity|uncertain|unclear|conflict\w*|missing|"
                r"invented|hallucinat\w*|incorrect|wrong|absent|unresolved|unverified|"
                r"acceptance|criteri(?:on|a)|unit|temperature|pressure|duration|tolerance|"
                r"limit|not specified|not stated|not explicitly|"
                r"not found|cannot resolve|could not resolve|not resolved)\b", text, re.I,
            ):
                continue
            mentioned = _mentioned_pages(text)
            context = supplied - primary
            if not context or not mentioned <= supplied:
                continue
            # Some original notes name the annex page but refer to other
            # context pages collectively. The saved request supplies those
            # pages explicitly; do not invent their ownership from prose.
            located_pages = mentioned or supplied
            physical = "\n".join(pages[page] for page in sorted(located_pages))
            index_note = bool(re.search(r"\bindex\b", text, re.I) and re.search(
                r"(?mi)^\s*(?:\d+(?:\.\d+)*\s+)?(?:test item|test method|test) index\s*$",
                physical,
            ))
            annex_note = bool(re.search(r"\b(?:regional|annex|equivalents?)\b", text, re.I)
                              and _heading(physical, "Regional annex"))
            fragments = re.findall(r"(?<!\w)'([^']+)'|\"([^\"]+)\"", text)
            quotes = [next(value for value in item if value) for item in fragments]
            reference_note = bool(
                re.search(r"\bcross-reference\b", text, re.I)
                and _heading(physical, "Regional annex") and quotes
                and all(_ordered_reference_words(physical, quote) for quote in quotes)
            )
            if not (index_note or annex_note or reference_note):
                continue
            codes = {_key(code) for code in literal_method_designations(text)}
            actual_codes = {_key(code) for code in literal_method_designations(
                physical + "\n" + _column_text(physical, first=False)
            )}
            if not codes <= actual_codes:
                continue
            remainder = text
            for code in sorted(literal_method_designations(text), key=len, reverse=True):
                remainder = re.sub(re.escape(code), "METHOD", remainder, flags=re.I)
            remainder = re.sub(
                r"\b(?:pages?|sections?)\s+[1-9]\d*(?:\.\d+)*(?:(?:\s*[-–,/]\s*|"
                r"\s+and\s+)[1-9]\d*(?:\.\d+)*)*", "REFERENCE", remainder, flags=re.I,
            )
            if re.search(r"\d|[°%±≤≥<>]", remainder):
                continue
            locations[issue] = {
                "policy_version": "chunk_document_context_v3",
                "source_page": min(located_pages), "source_pages": sorted(located_pages),
                "primary_pages": sorted(primary), "context_pages": sorted(context),
                "location_kind": "saved_window_and_physical_reference",
                "establishes_method_equivalence": False,
                "establishes_limit_or_coverage": False,
                "document_review_required": True,
            }
    return locations


def source_chunk_review_projection(
    run: CustomerExtractionRun, document: IngestedDocument
) -> ChunkReviewProjection:
    """Require saved chunk policy, actual provider comment and physical page proof."""
    empty = ChunkReviewProjection((), {})
    if run.document_id != document.document_id:
        return empty
    try:
        manifest = json.loads(run.raw_response)
        if manifest.get("input_partitioning", {}).get("policy") != SOURCE_CHUNK_POLICY:
            return empty
    except (ValueError, AttributeError, TypeError):
        return empty
    providers = []
    for call in run.calls:
        if call.purpose != "extraction" or call.request is None:
            continue
        try:
            payload = json.loads(call.raw_response)
        except (ValueError, TypeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("review_issues"), list):
            providers.append((payload["review_issues"], call.request.user_prompt))
    pages = {page.page_number: page.text for page in document.pages}
    document_issues = []
    row_targets = {}
    source_locations = {}
    coverage_locations = _chunk_coverage_locations(run, manifest, pages)
    for issue in run.extraction.review_issues:
        text = " ".join(issue.split())
        regional = (_REGIONAL.fullmatch(text) or _REGIONAL_OTHER_PRIMARY.fullmatch(text)
                    or _REGIONAL_UNATTACHED.fullmatch(text))
        approval = (_APPROVAL.fullmatch(text) or _APPROVAL_OTHER_SECTIONS.fullmatch(text)
                    or _APPROVAL_SUMMARY.fullmatch(text))
        bake = _BAKE_SCOPE.fullmatch(text)
        weathering = _WEATHERING_WORKFLOW.fullmatch(text)
        florida = _REGIONAL_FLORIDA.fullmatch(text)
        matched = regional or approval or bake or weathering or florida
        if matched is None:
            if issue in coverage_locations:
                document_issues.append(issue)
                source_locations[issue] = coverage_locations[issue]
                # Alternatives remain actionable on their existing method owners;
                # an index-only omission remains a document completeness question.
                if re.search(r"\bequivalents?\b", issue, re.I):
                    mentions = {_key(code) for code in literal_method_designations(issue)}
                    row_targets[issue] = frozenset(
                        row.id for row in run.extraction.requirements if any(
                            _key(method.code) in mentions for method in row.requested_methods
                        )
                    )
                elif (re.search(r"\bcross-reference\b", issue, re.I)
                      and re.search(r"\bweathering\b", issue, re.I)):
                    row_targets[issue] = _weathering_owners(run)
            continue
        page = int(matched["page"])
        comment_sources = [
            source for comments, source in providers
            if issue in comments and f"=== PAGE {page} ===" in source
        ]
        if page not in pages or not comment_sources:
            continue
        source_pages = [page]
        if bake or weathering or florida:
            quote_located = _located(pages[page], matched["quote"])
            florida_layout = bool(florida and all(_located(pages[page], part) for part in (
                "Florida exposure", "(Section 4.2) is", "sufficient",
            )))
            if not quote_located and not florida_layout:
                continue
            if bake:
                names = {row.id: normalized_name(row.original_test_name)
                         for row in run.extraction.requirements}
                families = [frozenset(key for key, name in names.items() if family in name)
                            for family in ("adhesion", "stone chip", "humidity")]
                if not all(families):
                    continue
                targets = frozenset().union(*families)
            else:
                if weathering and not _heading(pages[page], "Weathering"):
                    continue
                if florida and not _heading(pages[page], "Regional annex"):
                    continue
                targets = _weathering_owners(run)
                if not targets:
                    continue
            row_targets[issue] = targets
            layout_located = not quote_located
        elif regional:
            if not _heading(pages[page], "Regional annex"):
                continue
            codes = literal_method_designations(regional["pairs"])
            source_codes = {_key(code) for code in literal_method_designations(pages[page])}
            column_codes = {
                _key(code) for code in literal_method_designations(
                    _column_text(pages[page], first=False)
                )
            }
            if not codes or any(_key(code) not in source_codes | column_codes for code in codes):
                continue
            layout_located = any(_key(code) not in source_codes for code in codes)
            # Check the comment's closed relationship grammar, not equivalence.
            # Unknown conditions or extra scientific prose do not fit it.
            pairs = regional["pairs"]
            for code in sorted(set(codes), key=len, reverse=True):
                pairs = re.sub(re.escape(code), "METHOD", pairs, flags=re.I)
            if not all(re.fullmatch(
                r"METHOD (?:for|accepted with correlation data to) METHOD", part.strip(), re.I
            ) for part in pairs.split(";")):
                continue
            mentions = {_key(code) for code in codes}
            row_targets[issue] = frozenset(
                row.id for row in run.extraction.requirements
                if any(_key(method.code) in mentions for method in row.requested_methods)
            )
        else:
            previous = (
                pages.get(page - 1, "") if any(
                    f"=== PAGE {page - 1} ===" in source for source in comment_sources
                ) else ""
            )
            heading_source = pages[page] + "\n" + previous
            if not _heading(heading_source, "Approval types and test packages"):
                continue
            if not all(_located(pages[page], label) for label in (
                "Approval type", "Trigger", "Test package", "Documents",
            )):
                continue
            fragments = [part.strip() for part in approval["packages"].split(",")]
            # A continuation's full-system label can be on the immediately
            # preceding approval page, but only if this original call received
            # that page and its explicit approval heading.
            package_source = pages[page]
            if (
                approval.re is _APPROVAL_OTHER_SECTIONS
                and _heading(previous, "Approval types and test packages")
            ):
                package_source = previous + "\n" + pages[page]
                source_pages = [page - 1, page]
            labels = _column_text(package_source, first=True)
            if not all(fragment and (
                _located(package_source, fragment) or _located(labels, fragment)
            ) for fragment in fragments):
                continue
            layout_located = any(not _located(package_source, fragment) for fragment in fragments)
            if examples_text := approval.groupdict().get("examples"):
                examples = [part.strip() for part in examples_text.split(",")]
                if not all(fragment and _located(pages[page], fragment) for fragment in examples):
                    continue
        document_issues.append(issue)
        source_locations[issue] = {
            "policy_version": "chunk_document_context_v2",
            "source_page": page,
            "source_pages": source_pages,
            "location_kind": "layout_column_tokens" if layout_located else "literal_page_text",
            "establishes_method_equivalence": False,
            "establishes_limit_or_coverage": False,
            "document_review_required": True,
        }
    if _METADATA_VARIANCE in run.extraction.review_issues:
        metadata_pages = _metadata_variance_pages(run, pages)
        if metadata_pages:
            document_issues.append(_METADATA_VARIANCE)
            source_locations[_METADATA_VARIANCE] = {
                "policy_version": "chunk_document_context_v2",
                "source_page": metadata_pages[0], "source_pages": list(metadata_pages),
                "location_kind": "literal_document_control",
                "establishes_method_equivalence": False,
                "establishes_limit_or_coverage": False,
                "document_review_required": True,
            }
    return ChunkReviewProjection(
        tuple(dict.fromkeys(document_issues)), row_targets, source_locations
    )
