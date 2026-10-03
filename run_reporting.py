"""Shared, side-effect-free counters for job reports and run status.

A discovered card is not necessarily a fully evaluated job. Keep discovery,
detail coverage, and application outcomes separate so a budget limit or a
failed page cannot be presented as a completed evaluation.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any


_HELD_STATUSES = {"retry_cooldown", "manual_review_pending", "manual_only"}
_SCORED_STATUSES = {
    "accepted", "rejected", "shortlisted", "applied", "already_applied",
    "already_seen", "run_limit", "daily_limit", "both_application_limits",
    "daily_or_run_limit", "role_family_limit",
}


def _status_group(row: Mapping[str, Any]) -> str:
    return str(row.get("status", "unknown")).split(":", 1)[0].strip()


def detail_was_evaluated(row: Mapping[str, Any]) -> bool:
    """Read explicit detail evidence, with a fallback for older report rows.

    Application failures can occur *after* successful scoring. In particular,
    ``needs_review`` is not sufficient evidence of a detail-fetch failure.
    New rows should always supply a boolean ``detail_evaluated`` field.
    """
    explicit = row.get("detail_evaluated")
    if isinstance(explicit, bool):
        return explicit
    group = _status_group(row)
    if group in {"pre_filtered", "not_evaluated", "detail_failed"} | _HELD_STATUSES:
        return False
    if group == "needs_review":
        status = str(row.get("status", "")).lower()
        if "job-detail extraction" in status or "job detail extraction" in status:
            return False
        return row.get("accepted") is True
    return group in _SCORED_STATUSES or row.get("accepted") is True


def _coverage_group(row: Mapping[str, Any]) -> str:
    if detail_was_evaluated(row):
        return "evaluated"
    group = _status_group(row)
    if group == "pre_filtered":
        return "pre_filtered"
    if group in _HELD_STATUSES:
        return "held"
    if group == "already_seen":
        return "already_seen"
    if group in {"needs_review", "detail_failed"}:
        return "detail_failed"
    # An unknown or pending row must not silently count as scored.
    return "not_evaluated"


def application_limit_status(
    applications_this_run: int,
    applications_today: int,
    maximum_per_run: int,
    maximum_per_day: int,
) -> tuple[str, str] | None:
    """Identify the exact application cap that prevents the next submission."""
    if min(applications_this_run, applications_today, maximum_per_run, maximum_per_day) < 0:
        raise ValueError("application counts and limits cannot be negative")

    run_reached = applications_this_run >= maximum_per_run
    day_reached = applications_today >= maximum_per_day
    if run_reached and day_reached:
        return (
            "both_application_limits",
            f"per-run limit ({maximum_per_run}) and daily limit ({maximum_per_day}) reached",
        )
    if run_reached:
        return "run_limit", f"per-run limit ({maximum_per_run}) reached"
    if day_reached:
        return "daily_limit", f"daily limit ({maximum_per_day}) reached"
    return None


def build_run_summary(
    rows: Sequence[Mapping[str, Any]],
    search_metrics: Sequence[Mapping[str, Any]] | None = None,
    *,
    discovered_count: int | None = None,
) -> dict[str, Any]:
    """Count reported jobs, full-detail coverage, and failed search pages.

    ``discovered_count`` is useful while reporting a partially completed run.
    Otherwise, discovery totals come from per-query unique additions when
    present, falling back to the number of rows. Held retry/manual-review jobs
    are reported separately from jobs left unknown by the detail budget.
    """
    metrics = search_metrics or []
    if discovered_count is not None and discovered_count < 0:
        raise ValueError("discovered_count cannot be negative")
    if discovered_count is None:
        discovered_count = sum(
            int(metric.get("unique_jobs_added", 0)) for metric in metrics
        )
    discovered_count = max(discovered_count, len(rows))

    coverage = Counter(_coverage_group(row) for row in rows)
    # Keep the historical short status groups (`not_evaluated`, `needs_review`)
    # in the summary; each full reason remains available on its individual row.
    statuses = Counter(_status_group(row) for row in rows)
    pre_filter_reasons = Counter(
        str(row.get("status", "")).partition(":")[2].strip()
        for row in rows
        if _status_group(row) == "pre_filtered"
    )
    pages = [page for metric in metrics for page in metric.get("pages", [])]
    failed_pages = sum(page.get("status") == "failed" for page in pages)
    cards_found = 0
    for metric in metrics:
        if "cards_found" in metric:
            cards_found += int(metric["cards_found"])
        else:
            cards_found += sum(
                int(page.get("cards_found", 0)) for page in metric.get("pages", [])
            )
    application_failures = sum(
        _status_group(row) in {"needs_review", "application_failed"}
        and detail_was_evaluated(row)
        for row in rows
    )
    early_rows = [
        row for row in rows
        if row.get("early_applicant", row.get("is_early_applicant", False)) is True
    ]
    return {
        "discovered_in_this_run": discovered_count,
        "reported_in_this_run": len(rows),
        "unreported_in_this_run": discovered_count - len(rows),
        "evaluated_in_this_run": coverage["evaluated"],
        "pre_filtered_in_this_run": coverage["pre_filtered"],
        "not_evaluated_in_this_run": coverage["not_evaluated"],
        "detail_failed_in_this_run": coverage["detail_failed"],
        "held_in_this_run": coverage["held"],
        "already_seen_in_this_run": coverage["already_seen"],
        "application_failed_in_this_run": application_failures,
        "run_limit_in_this_run": statuses["run_limit"],
        "daily_limit_in_this_run": statuses["daily_limit"],
        "both_application_limits_in_this_run": statuses["both_application_limits"],
        "legacy_ambiguous_limit_in_this_run": statuses["daily_or_run_limit"],
        "role_family_limit_in_this_run": statuses["role_family_limit"],
        "pre_filter_reason_counts": dict(sorted(pre_filter_reasons.items())),
        "accepted_in_this_run": sum(
            row.get("accepted") is True and detail_was_evaluated(row) for row in rows
        ),
        "early_applicant_in_this_run": len(early_rows),
        "early_applicant_evaluated_in_this_run": sum(
            detail_was_evaluated(row) for row in early_rows
        ),
        "cards_found": cards_found,
        "search_pages_failed": failed_pages,
        "discovery_status": (
            "partial_failure" if failed_pages and discovered_count
            else "failed" if failed_pages
            else "complete"
        ),
        "status_counts": dict(sorted(statuses.items())),
    }


def final_run_status(summary: Mapping[str, Any], *, interrupted: bool = False) -> str:
    """Prevent a finished workflow from hiding failed or unevaluated work.

    A fatal exception remains the caller's responsibility and should be saved
    as ``failed``. This helper describes a run that reached its normal end,
    or a deliberately interrupted run whose partial results were retained.
    """
    if any(summary.get(key, 0) for key in (
        "search_pages_failed", "detail_failed_in_this_run", "application_failed_in_this_run",
    )):
        return "partial_failure"
    if interrupted or any(summary.get(key, 0) for key in (
        "not_evaluated_in_this_run", "unreported_in_this_run",
    )):
        return "incomplete"
    return "complete"
