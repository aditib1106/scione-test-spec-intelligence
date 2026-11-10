"""Bounded historical projections plus a precise future operation ledger.

Historical extraction files store summed provider latency, not user operation
wall time. Those values are never substituted into operation-duration charts.
Latest saved comparison outcomes describe capability coverage, never accuracy.
"""

from __future__ import annotations

import json
import logging
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from scione.library.store import comparisons, documents, requirements, runs
from scione.matching.store import matching_runs, upgrade_matching_schema
from scione.monitoring.store import MonitoringStore
from scione.specifications.store import SpecStoreError

OUTCOMES = ("fully_matched", "partially_matched", "not_matched", "needs_information", "excluded")
LOGGER = logging.getLogger(__name__)


@dataclass
class ReadBudget:
    remaining: int = 8 * 1024 * 1024
    truncated: bool = False

    def read_json(self, workspace, path):
        if path.is_symlink() or not path.is_file():
            return None
        size = path.stat().st_size
        if size > min(self.remaining, 2 * 1024 * 1024):
            self.truncated = True
            return None
        self.remaining -= size
        try:
            value = json.loads(workspace.specs._read_bytes(path))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError, SpecStoreError):
            return None


def _date(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (AttributeError, ValueError, TypeError):
        return None


def _positive(value):
    return (
        value if isinstance(value, (float, int)) and math.isfinite(value) and value >= 0 else None
    )


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lo, hi = math.floor(index), math.ceil(index)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo), 1)


def _historical(workspace, since, limit, budget):
    """Load only bounded metadata/JSON projections; never full matching payloads."""
    rows = []
    store = workspace.library.store
    with store.engine.connect() as connection:
        source_rows = connection.execute(
            select(
                runs.c.run_id,
                runs.c.document_id,
                runs.c.created_at,
                select(func.count())
                .select_from(requirements)
                .where(requirements.c.run_id == runs.c.run_id)
                .scalar_subquery()
                .label("test_count"),
            )
            .where(runs.c.created_at >= since)
            .order_by(runs.c.created_at.desc())
            .limit(limit)
        ).mappings()
        for row in source_rows:
            data = budget.read_json(
                workspace,
                workspace.specs._run_directory(row["document_id"], row["run_id"]) / "run.json",
            )
            doc = budget.read_json(
                workspace, workspace.specs._document_directory(row["document_id"]) / "document.json"
            )
            rows.append(
                {
                    "id": "historical-extraction-" + row["run_id"],
                    "kind": "extraction",
                    "document_id": row["document_id"],
                    "run_id": row["run_id"],
                    "started_at": row["created_at"],
                    "finished_at": None,
                    "status": "success",
                    "duration_ms": None,
                    "duration_kind": "unavailable",
                    "historical": True,
                    "provider_latency_ms": _positive(data.get("latency_ms")) if data else None,
                    "provider_calls": (
                        0
                        if data.get("raw_response_kind") == "manual_reference"
                        else len(data.get("calls", [])) or 1
                    )
                    if data
                    else None,
                    "page_count": doc.get("page_count") if doc else None,
                    "test_count": row["test_count"],
                }
            )
    upgrade_matching_schema(store)
    with store.engine.connect() as connection:
        metadata_rows = (
            connection.execute(
                select(
                    matching_runs.c.run_id,
                    matching_runs.c.document_id,
                    matching_runs.c.created_at,
                    matching_runs.c.status,
                )
                .where(
                    matching_runs.c.created_at >= since,
                    matching_runs.c.workspace_id == workspace.labs.workspace_id,
                )
                .order_by(matching_runs.c.created_at.desc())
                .limit(limit)
            )
            .mappings()
            .all()
        )
        # MySQL filesort can retain the complete multi-megabyte JSON argument
        # even when SELECT projects only one path. Sort small IDs first, then
        # extract scalar paths without ORDER BY or loading provider payloads.
        projections = {}
        if metadata_rows:
            projected = connection.execute(
                select(
                    matching_runs.c.run_id,
                    matching_runs.c.payload["finished_at"].as_string().label("finished_at"),
                    matching_runs.c.payload["latency_ms"].as_float().label("provider_latency_ms"),
                ).where(
                    matching_runs.c.workspace_id == workspace.labs.workspace_id,
                    matching_runs.c.run_id.in_([row["run_id"] for row in metadata_rows]),
                )
            ).mappings()
            projections = {row["run_id"]: dict(row) for row in projected}
        for metadata_row in metadata_rows:
            row = {**metadata_row, **projections[metadata_row["run_id"]]}
            start, end = _date(row["created_at"]), _date(row["finished_at"])
            duration = max(0, (end - start).total_seconds() * 1000) if start and end else None
            rows.append(
                {
                    "id": "historical-matching-" + row["run_id"],
                    "kind": "matching",
                    "document_id": row["document_id"],
                    "run_id": row["run_id"],
                    "started_at": row["created_at"],
                    "finished_at": row["finished_at"],
                    "status": "success" if row["status"] == "completed" else "failure",
                    "duration_ms": duration,
                    "duration_kind": "historical_run_wall"
                    if duration is not None
                    else "unavailable",
                    "historical": True,
                    "provider_latency_ms": _positive(row["provider_latency_ms"]),
                    # A planned batch count is not the number actually sent.
                    # Older artifacts do not carry one reliable scalar count.
                    "provider_calls": None,
                    "page_count": None,
                    "test_count": None,
                }
            )
    # Older failed extractions have no SQL run. Only the latest failure per
    # bounded recent document is available; the response states this limitation.
    with store.engine.connect() as connection:
        identifiers = (
            connection.execute(
                select(documents.c.document_id)
                .order_by(documents.c.updated_at.desc())
                .limit(min(limit, 50))
            )
            .scalars()
            .all()
        )
    for identifier in identifiers:
        failure = budget.read_json(
            workspace, workspace.specs._document_directory(identifier) / "latest_failure.json"
        )
        if failure and failure.get("occurred_at", "") >= since:
            rows.append(
                {
                    "id": "historical-failure-"
                    + identifier
                    + "-"
                    + str(failure.get("attempt_id") or failure["occurred_at"]),
                    "kind": "extraction",
                    "document_id": identifier,
                    "run_id": failure.get("attempt_id"),
                    "started_at": failure["occurred_at"],
                    "finished_at": None,
                    "status": "failure",
                    "duration_ms": None,
                    "duration_kind": "unavailable",
                    "historical": True,
                    "provider_latency_ms": None,
                    "provider_calls": None,
                    "page_count": None,
                    "test_count": None,
                }
            )
    return rows


def _covered_by_event(row, events):
    for event in events:
        if event["status"] == "cache_reuse":
            continue
        if event["kind"] != row["kind"] or event["document_id"] != row["document_id"]:
            continue
        if event.get("run_id") and event["run_id"] == row.get("run_id"):
            return True
        if event["status"] != row["status"]:
            continue
        start, end, when = (
            _date(event["started_at"]),
            _date(event.get("finished_at")),
            _date(row["started_at"]),
        )
        if (
            start
            and when
            and start - timedelta(seconds=2) <= when
            and (end is not None and when <= end + timedelta(seconds=2))
        ):
            return True
    return False


def _latest_comparisons(workspace, since, limit, budget):
    outcomes = Counter({name: 0 for name in OUTCOMES})
    source = Counter(needs_review=0, total_tests=0)
    seen = set()
    with workspace.library.store.engine.connect() as connection:
        rows = (
            connection.execute(
                select(
                    comparisons.c.comparison_id,
                    comparisons.c.document_id,
                )
                .where(comparisons.c.created_at >= since)
                .order_by(comparisons.c.created_at.desc(), comparisons.c.comparison_id.desc())
                .limit(limit)
            )
            .mappings()
            .all()
        )
        projected = {}
        if rows:
            values = connection.execute(
                select(
                    comparisons.c.comparison_id,
                    comparisons.c.payload["summary"].label("summary"),
                ).where(comparisons.c.comparison_id.in_([row["comparison_id"] for row in rows]))
            ).mappings()
            projected = {row["comparison_id"]: row["summary"] for row in values}
    for row in rows:
        if row["document_id"] in seen:
            continue
        seen.add(row["document_id"])
        value = projected.get(row["comparison_id"])
        summary = value if isinstance(value, dict) else {}
        outcomes.update({key: max(0, int(summary.get(key, 0))) for key in OUTCOMES})
        if len(seen) > 20 or budget.remaining <= 0:
            budget.truncated = True
            continue
        with workspace.library.store.engine.connect() as connection:
            size_fn = (
                func.octet_length if workspace.library.store.backend == "mysql" else func.length
            )
            estimated = connection.execute(
                select(size_fn(comparisons.c.payload)).where(
                    comparisons.c.comparison_id == row["comparison_id"]
                )
            ).scalar_one()
            if estimated and estimated > min(budget.remaining, 2 * 1024 * 1024):
                budget.truncated = True
                continue
            data = connection.execute(
                select(comparisons.c.payload["requirements"]).where(
                    comparisons.c.comparison_id == row["comparison_id"]
                )
            ).scalar_one()
        budget.remaining -= estimated or 0
        if isinstance(data, list):
            source["total_tests"] += len(data)
            source["needs_review"] += sum(
                item.get("source_review_state") == "needs_attention" for item in data
            )
    return outcomes, source, len(rows) >= limit


def collect_metrics(labs, *, scope="current", days=30, limit=100, now=None):
    """labs is a server-resolved sequence of (lab_id, lab_name, Workspace)."""
    current = now or datetime.now(UTC)
    since = (current - timedelta(days=days)).isoformat()
    budget = ReadBudget()
    recent, notices = [], []
    outcomes = Counter({name: 0 for name in OUTCOMES})
    review = Counter(needs_review=0, total_tests=0)
    truncated = len(labs) > 20
    unavailable = 0
    for lab_id, lab_name, workspace in labs[:20]:
        phase = "operation_ledger"
        try:
            events = MonitoringStore(workspace.library.store).listing(since, limit=limit + 1)
            truncated |= len(events) > limit
            events = events[:limit]
            for row in events:
                if (
                    row["status"] == "running"
                    and _date(row["started_at"])
                    and current - _date(row["started_at"]) > timedelta(hours=2)
                ):
                    row["status"] = "incomplete"
                row.update(
                    historical=False,
                    duration_kind="operation_wall"
                    if row["duration_ms"] is not None
                    else "unavailable",
                )
            recent.extend({**row, "lab_id": lab_id, "lab_name": lab_name} for row in events)
            phase = "historical_runs"
            history = _historical(workspace, since, limit + 1, budget)
            truncated |= len(history) > limit
            recent.extend(
                {**row, "lab_id": lab_id, "lab_name": lab_name}
                for row in ([item for item in history if not _covered_by_event(item, events)])
            )
            phase = "comparisons"
            counts, pending, limited = _latest_comparisons(workspace, since, limit, budget)
            outcomes.update(counts)
            review.update(pending)
            truncated |= limited
        except (SQLAlchemyError, OSError, ValueError, RuntimeError) as exc:
            unavailable += 1
            args = getattr(getattr(exc, "orig", None), "args", ())
            code = args[0] if args and isinstance(args[0], int) else None
            LOGGER.warning(
                "Monitoring unavailable phase=%s type=%s driver_code=%s",
                phase,
                type(exc).__name__,
                code,
            )
            notices.append(f"Metrics are temporarily unavailable for {lab_name}.")
    recent.sort(key=lambda item: item["started_at"], reverse=True)
    truncated |= len(recent) > limit or budget.truncated
    recent = recent[:limit]
    metrics = {}
    for kind in ("extraction", "matching"):
        values = [row for row in recent if row["kind"] == kind]
        durations = [
            row["duration_ms"]
            for row in values
            if row["status"] in {"success", "failure"} and row["duration_ms"] is not None
        ]
        metrics[kind] = {
            "count": len(values),
            **{
                status: sum(row["status"] == status for row in values)
                for status in ("success", "failure", "running", "incomplete", "cache_reuse")
            },
            "duration_samples": len(durations),
            "median_ms": _percentile(durations, 0.5),
            "p95_ms": _percentile(durations, 0.95),
        }
    volume = {
        "documents": len({(row["lab_id"], row["document_id"]) for row in recent}),
        "pages": sum(
            row["page_count"] or 0
            for row in recent
            if row["kind"] == "extraction" and row["status"] == "success"
        ),
        "tests": sum(
            row["test_count"] or 0
            for row in recent
            if row["kind"] == "extraction" and row["status"] == "success"
        ),
    }
    daily = defaultdict(list)
    for row in recent:
        daily[row["started_at"][:10]].append(row)
    trend = []
    for day, values in sorted(daily.items()):
        durations = [
            row["duration_ms"]
            for row in values
            if row["duration_ms"] is not None and row["status"] != "cache_reuse"
        ]
        trend.append(
            {
                "date": day,
                "extraction_count": sum(row["kind"] == "extraction" for row in values),
                "matching_count": sum(row["kind"] == "matching" for row in values),
                "success": sum(row["status"] == "success" for row in values),
                "failure": sum(row["status"] == "failure" for row in values),
                "pages": sum(
                    row["page_count"] or 0
                    for row in values
                    if row["kind"] == "extraction" and row["status"] == "success"
                ),
                "tests": sum(
                    row["test_count"] or 0
                    for row in values
                    if row["kind"] == "extraction" and row["status"] == "success"
                ),
                "mean_operation_ms": round(sum(durations) / len(durations), 1)
                if durations
                else None,
            }
        )
    notices += [
        "Operation speed and matching coverage are not model accuracy.",
        "Historical extraction durations were not recorded; provider time is a separate sum "
        "and excludes application work.",
        "Historical matching durations describe saved run wall time; new events measure "
        "handler operation time.",
        "Historical failed extractions include the latest saved failure per recent document; "
        "older failures may be unavailable.",
    ]
    if truncated:
        notices.append(
            "This view is bounded to recent records and a small metadata-read budget; "
            "some older records are not included."
        )
    return {
        "version": "business_metrics_v1",
        "generated_at": current.isoformat(),
        "scope": scope,
        "days": days,
        "coverage": {
            "lab_count": min(len(labs), 20),
            "total_lab_count": len(labs),
            "failure_history_complete": not unavailable
            and not any(row["historical"] for row in recent),
            "unavailable_lab_count": unavailable,
            "truncated": truncated,
            "notices": notices,
        },
        "summary": {
            **metrics,
            "volume": volume,
            "outcomes": dict(outcomes),
            "source_review": {
                **review,
                "review_rate": round(review["needs_review"] / review["total_tests"], 4)
                if review["total_tests"]
                else None,
            },
        },
        "daily": trend,
        "recent": recent[:30],
    }
