import pytest

from run_reporting import (
    application_limit_status,
    build_run_summary,
    detail_was_evaluated,
    final_run_status,
)


def test_diagnostic_sample_reports_twenty_evaluated_not_411():
    rows = (
        [{"status": "shortlisted", "accepted": True}] * 18
        + [{"status": "rejected", "accepted": False}] * 2
        + [{"status": "pre_filtered: unsuitable title", "accepted": False}] * 232
        + [{"status": "not_evaluated: detail-scoring limit reached", "accepted": False}] * 159
    )
    summary = build_run_summary(rows, [{"cards_found": 600, "unique_jobs_added": 411}])

    assert summary["discovered_in_this_run"] == 411
    assert summary["reported_in_this_run"] == 411
    assert summary["evaluated_in_this_run"] == 20
    assert summary["accepted_in_this_run"] == 18
    assert summary["pre_filtered_in_this_run"] == 232
    assert summary["not_evaluated_in_this_run"] == 159
    assert summary["cards_found"] == 600
    assert final_run_status(summary) == "incomplete"


def test_failure_after_scoring_is_application_failure_not_detail_failure():
    rows = [
        {"status": "needs_review: unsupported question", "accepted": True, "detail_evaluated": True},
        {"status": "needs_review: job-detail extraction exceeded 20 seconds", "accepted": False, "detail_evaluated": False},
    ]
    summary = build_run_summary(rows)

    assert summary["evaluated_in_this_run"] == 1
    assert summary["detail_failed_in_this_run"] == 1
    assert summary["application_failed_in_this_run"] == 1
    assert final_run_status(summary) == "partial_failure"


@pytest.mark.parametrize("row, expected", [
    ({"status": "needs_review: questionnaire", "accepted": True}, True),
    ({"status": "needs_review: job-detail extraction failed", "accepted": False}, False),
    ({"status": "needs_review: unsupported question", "detail_evaluated": True}, True),
    ({"status": "rejected", "accepted": False}, True),
    ({"status": "rejected", "detail_evaluated": False}, False),
    ({"status": "manual_review_pending", "accepted": False}, False),
    ({"status": "retry_cooldown", "accepted": False}, False),
    ({"status": "pending"}, False),
])
def test_explicit_detail_evidence_and_legacy_fallback(row, expected):
    assert detail_was_evaluated(row) is expected


def test_partial_search_failure_survives_successful_job_processing():
    summary = build_run_summary(
        [{"status": "applied", "accepted": True, "detail_evaluated": True}],
        [{"unique_jobs_added": 1, "pages": [
            {"status": "ready", "cards_found": 1},
            {"status": "failed", "error": "HTTP 403"},
        ]}],
    )
    assert summary["search_pages_failed"] == 1
    assert summary["cards_found"] == 1
    assert summary["discovery_status"] == "partial_failure"
    assert final_run_status(summary) == "partial_failure"


def test_true_empty_search_is_complete_and_failed_search_is_distinct():
    empty = build_run_summary([], [{"pages": [{"status": "empty", "cards_found": 0}]}])
    failed = build_run_summary([], [{"pages": [{"status": "failed"}]}])
    assert empty["discovery_status"] == "complete"
    assert final_run_status(empty) == "complete"
    assert failed["discovery_status"] == "failed"
    assert final_run_status(failed) == "partial_failure"


def test_pending_discovered_jobs_and_interruption_cannot_look_complete():
    summary = build_run_summary([{"status": "shortlisted", "accepted": True}], discovered_count=3)
    assert summary["unreported_in_this_run"] == 2
    assert final_run_status(summary) == "incomplete"
    assert final_run_status(build_run_summary([]), interrupted=True) == "incomplete"


def test_early_badge_counts_show_how_many_were_fully_scored():
    summary = build_run_summary([
        {"status": "shortlisted", "accepted": True, "early_applicant": True},
        {"status": "not_evaluated: detail limit", "is_early_applicant": True},
        {"status": "rejected", "early_applicant": False},
    ])
    assert summary["early_applicant_in_this_run"] == 2
    assert summary["early_applicant_evaluated_in_this_run"] == 1


def test_held_jobs_and_application_budgets_have_distinct_coverage():
    summary = build_run_summary([
        {"status": "retry_cooldown", "accepted": False},
        {"status": "manual_review_pending", "accepted": False},
        {"status": "run_limit: per-run limit (20) reached", "accepted": True},
        {"status": "daily_limit: daily limit (100) reached", "accepted": True},
        {"status": "both_application_limits: both caps reached", "accepted": True},
        {"status": "role_family_limit", "accepted": True},
    ])
    assert summary["held_in_this_run"] == 2
    assert summary["evaluated_in_this_run"] == 4
    assert summary["run_limit_in_this_run"] == 1
    assert summary["daily_limit_in_this_run"] == 1
    assert summary["both_application_limits_in_this_run"] == 1
    assert summary["role_family_limit_in_this_run"] == 1
    assert summary["not_evaluated_in_this_run"] == 0
    assert final_run_status(summary) == "complete"


@pytest.mark.parametrize(("in_run", "today", "per_run", "per_day", "expected"), [
    (0, 0, 20, 100, None),
    (20, 20, 20, 100, ("run_limit", "per-run limit (20) reached")),
    (3, 100, 20, 100, ("daily_limit", "daily limit (100) reached")),
    (20, 20, 20, 20, (
        "both_application_limits",
        "per-run limit (20) and daily limit (20) reached",
    )),
    (0, 0, 0, 100, ("run_limit", "per-run limit (0) reached")),
])
def test_application_limit_status_identifies_exact_blocking_cap(
    in_run, today, per_run, per_day, expected
):
    assert application_limit_status(in_run, today, per_run, per_day) == expected


def test_summary_explains_pre_filter_reasons():
    summary = build_run_summary([
        {"status": "pre_filtered: blocked title phrase: data engineer"},
        {"status": "pre_filtered: blocked title phrase: data engineer"},
        {"status": "pre_filtered: card minimum experience 7 years exceeds configured maximum 4 years"},
    ])
    assert summary["pre_filter_reason_counts"] == {
        "blocked title phrase: data engineer": 2,
        "card minimum experience 7 years exceeds configured maximum 4 years": 1,
    }


def test_skipping_verified_history_does_not_claim_full_scoring_or_incomplete():
    summary = build_run_summary([
        {"status": "already_seen", "detail_evaluated": False, "is_early_applicant": True},
        {"status": "already_seen", "detail_evaluated": True, "accepted": True},
    ])
    assert summary["already_seen_in_this_run"] == 1
    assert summary["evaluated_in_this_run"] == 1
    assert summary["early_applicant_in_this_run"] == 1
    assert summary["early_applicant_evaluated_in_this_run"] == 0
    assert summary["not_evaluated_in_this_run"] == 0
    assert final_run_status(summary) == "complete"


def test_reported_rows_are_not_lost_with_incomplete_discovery_metadata():
    summary = build_run_summary([{"status": "rejected"}], [{"unique_jobs_added": 0}])
    assert summary["discovered_in_this_run"] == 1
    assert summary["unreported_in_this_run"] == 0
    with pytest.raises(ValueError, match="negative"):
        build_run_summary([], discovered_count=-1)


def test_failures_take_precedence_over_detail_limit():
    summary = build_run_summary([
        {"status": "not_evaluated: detail limit", "detail_evaluated": False},
        {"status": "needs_review: unsupported dropdown", "detail_evaluated": True},
    ])
    assert final_run_status(summary) == "partial_failure"
