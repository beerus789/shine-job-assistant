"""Coverage rotation must expose unseen jobs without relaxing matching rules."""

import asyncio
from datetime import datetime, timedelta, timezone
import json

import pytest

import bot
import config
from detail_progress import DetailProgress, matching_policy_fingerprint
from scoring import Job, preliminary_job_priority


NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def make_job(number, *, early=False, **overrides):
    values = {
        "title": "Python Backend Developer",
        "company": "Example",
        "url": f"https://www.shine.com/jobs/python/example/{number}",
        "text": "Python FastAPI Django PostgreSQL Docker AWS",
        "skills": ("Python", "FastAPI", "Django", "PostgreSQL", "Docker", "AWS"),
        "min_experience": 2,
        "max_experience": 4,
        "is_early_applicant": early,
    }
    values.update(overrides)
    return Job(**values)


def test_unchanged_jobs_rotate_past_small_cap_after_journal_reload(tmp_path):
    path = tmp_path / "state" / "detail-progress.json"
    jobs = [make_job(number, early=number < 2) for number in range(6)]
    cohorts = []

    for run_number in range(3):
        current = NOW + timedelta(hours=run_number)
        journal = DetailProgress(path, "live", now=current)
        selected, skipped = bot.select_detail_candidates(
            jobs, 2, previous_checks=journal.checked_at
        )
        urls = {job.url for job in selected}
        assert len(urls) == 2
        assert all(urls.isdisjoint(prior) for prior in cohorts)
        assert len(skipped) == 4
        cohorts.append(urls)
        journal.mark_checked(urls, now=current)

    assert set.union(*cohorts) == {job.url for job in jobs}
    reloaded = DetailProgress(path, "live", now=NOW + timedelta(hours=3))
    assert len(reloaded.checked_at) == 6
    selected, _ = bot.select_detail_candidates(jobs, 2, previous_checks=reloaded.checked_at)
    assert {job.url for job in selected} == cohorts[0]


def test_rotation_precedes_badge_then_badge_precedes_fit_score():
    fresh_early = make_job(1, early=True, title="Backend Engineer", text="", skills=())
    fresh_strong = make_job(2)
    old_plain = make_job(3)
    recent_early = make_job(4, early=True)
    assert preliminary_job_priority(fresh_strong) > preliminary_job_priority(fresh_early)

    selected, _ = bot.select_detail_candidates(
        [recent_early, old_plain, fresh_strong, fresh_early],
        4,
        previous_checks={
            old_plain.url: (NOW - timedelta(days=2)).timestamp(),
            recent_early.url: (NOW - timedelta(hours=1)).timestamp(),
        },
    )
    assert selected == [fresh_early, fresh_strong, old_plain, recent_early]


@pytest.mark.parametrize("previously_checked", [False, True])
def test_fit_breaks_equal_badge_and_coverage_ties(previously_checked):
    weak = make_job(1, early=True, title="Backend Engineer", text="", skills=())
    strong = make_job(2, early=True)
    previous_checks = (
        {weak.url: NOW.timestamp(), strong.url: NOW.timestamp()}
        if previously_checked else {}
    )
    selected, _ = bot.select_detail_candidates(
        [weak, strong], 2, previous_checks=previous_checks
    )
    assert selected == [strong, weak]


def test_accepted_early_applicant_precedes_higher_score_plain_job(monkeypatch):
    early = make_job(1, early=True)
    plain = make_job(2)
    monkeypatch.setattr(bot, "require_open_browser", lambda page: None)

    async def use_card_as_detail(page, job, timeout):
        return job

    monkeypatch.setattr(bot, "extract_job_detail", use_card_as_detail)
    monkeypatch.setattr(
        bot, "score_job",
        lambda job: bot.ScoreResult(70 if job.is_early_applicant else 95, True, ("accepted",)),
    )
    ranked, _ = asyncio.run(
        bot.score_detailed_jobs(None, [plain, early], 2, 1, 1)
    )
    assert [(job.is_early_applicant, result.score) for result, job in ranked] == [
        (True, 70), (False, 95)
    ]


@pytest.mark.parametrize("overrides", [
    {"title": "Python Technical Support"},
    {"min_experience": config.MAX_REQUIRED_EXPERIENCE + 1},
    {"title": "Senior Accountant", "text": "Payroll accounting", "skills": ("Accounting",)},
])
def test_early_badge_does_not_bypass_preliminary_rejection(overrides):
    unsuitable = make_job(1, early=True, **overrides)
    suitable = make_job(2)
    selected, skipped = bot.select_detail_candidates([unsuitable, suitable], 1)
    assert selected == [suitable]
    assert skipped[unsuitable.url].startswith((
        "blocked title phrase",
        "card minimum experience",
        "no backend/AI role signal",
    ))


def test_live_dry_run_and_audit_coverage_are_independent(tmp_path):
    path = tmp_path / "detail-progress.json"
    for number, mode in enumerate(("dry_run", "audit", "live")):
        journal = DetailProgress(path, mode, now=NOW)
        assert journal.checked_at == {}
        journal.mark_checked([make_job(number).url], now=NOW)

    for number, mode in enumerate(("dry_run", "audit", "live")):
        journal = DetailProgress(path, mode, now=NOW)
        assert journal.checked_at == {make_job(number).url: NOW.timestamp()}


@pytest.mark.parametrize("setting, changed", [
    ("REQUIRED_SKILLS", frozenset({"python", "fastapi"})),
    ("TARGET_TITLES", frozenset({"a different target title"})),
    ("MAX_REQUIRED_EXPERIENCE", config.MAX_REQUIRED_EXPERIENCE + 1),
    ("MINIMUM_SCORE", config.MINIMUM_SCORE + 1),
])
def test_changed_matching_policy_restarts_coverage(tmp_path, monkeypatch, setting, changed):
    path = tmp_path / "detail-progress.json"
    job = make_job(1)
    original = DetailProgress(path, "live", now=NOW)
    original.mark_checked([job.url], now=NOW)
    monkeypatch.setattr(config, setting, changed)

    updated = DetailProgress(path, "live", now=NOW)
    assert updated.policy != original.policy
    assert updated.checked_at == {}
    updated.mark_checked([job.url], now=NOW)
    assert DetailProgress(path, "live", now=NOW).checked_at == {job.url: NOW.timestamp()}


def test_expired_invalid_naive_and_future_timestamps_do_not_suppress_jobs(tmp_path):
    path = tmp_path / "detail-progress.json"
    valid = make_job(1).url
    boundary = make_job(2).url
    entries = {
        valid: (NOW - timedelta(hours=1)).isoformat(),
        boundary: (NOW - timedelta(days=7)).isoformat(),
        make_job(3).url: (NOW - timedelta(days=7, seconds=1)).isoformat(),
        make_job(4).url: (NOW + timedelta(seconds=1)).isoformat(),
        make_job(5).url: NOW.replace(tzinfo=None).isoformat(),
        make_job(6).url: "not a date",
        make_job(7).url: None,
        make_job(8).url: {"bad": "type"},
        make_job(9).url: NOW.timestamp(),
    }
    path.write_text(json.dumps({
        "version": 1,
        "modes": {"live": {"policy": matching_policy_fingerprint(), "checked_at": entries}},
    }), encoding="utf-8")

    journal = DetailProgress(path, "live", now=NOW)
    assert journal.checked_at == {
        valid: (NOW - timedelta(hours=1)).timestamp(),
        boundary: (NOW - timedelta(days=7)).timestamp(),
    }
    fresh_url = make_job(10).url
    journal.mark_checked([fresh_url], now=NOW)
    reloaded = DetailProgress(path, "live", now=NOW)
    assert set(reloaded.checked_at) == {valid, boundary, fresh_url}


@pytest.mark.parametrize("document", [
    "not JSON",
    "null",
    "[]",
    '{"version": 2, "modes": {}}',
    '{"version": 1, "modes": []}',
    '{"version": 1, "modes": {"live": []}}',
])
def test_unusable_journal_starts_fresh_and_can_be_replaced(tmp_path, document):
    path = tmp_path / "detail-progress.json"
    path.write_text(document, encoding="utf-8")
    journal = DetailProgress(path, "live", now=NOW)
    assert journal.checked_at == {}
    url = make_job(1).url
    journal.mark_checked([url], now=NOW)
    assert DetailProgress(path, "live", now=NOW).checked_at == {url: NOW.timestamp()}


def test_detail_merge_preserves_card_badge_when_description_omits_it():
    card = make_job(1, early=True)
    detailed = bot.merge_job_details(
        card,
        "Build Python FastAPI services with 3 years of backend experience.",
        ["Python", "FastAPI"],
        "3 to 4 Yrs",
    )
    assert detailed.is_early_applicant
    assert detailed.min_experience == 3
    assert "Early Applicant" not in detailed.text
