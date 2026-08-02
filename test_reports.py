import asyncio
import json
from datetime import datetime, timedelta, timezone

import bot
import pytest
from scoring import score_job


def test_card_option_matching_uses_value_not_position():
    assert bot.choose_option_index(["In Years", "3 yrs", "4 yrs", "5 yrs"], 4, "experience years") == 2
    assert bot.choose_option_index(["10-15 LPA", "15-20 LPA", "20-25 LPA"], 19, "expected salary") == 1
    assert bot.choose_option_index(["15 Days", "1 Month", "2 Months"], 30, "notice period") == 1


def test_numbered_known_application_fields_are_recognized():
    assert bot.EXPECTED_SALARY_FIELD_PATTERN.search(
        "1. What is your expected annual CTC?*"
    )
    assert bot.NOTICE_PERIOD_FIELD_PATTERN.search("2. What is your notice period?*")
    assert bot.CURRENT_SALARY_FIELD_PATTERN.search("3) Current annual salary*")
    assert bot.EXPERIENCE_FIELD_PATTERN.search("4. Total work experience*")


def test_redirect_comparison_ignores_query_and_fragment_only():
    job = "https://www.shine.com/jobs/python-backend/example/123"
    assert bot._same_job_path(job, job + "?source=search#apply")
    assert not bot._same_job_path(job, "https://www.shine.com/pages/application-form")


def test_only_https_shine_urls_are_allowed():
    assert bot._is_shine_url("https://www.shine.com/jobs/example/123")
    assert bot._is_shine_url("https://jobs.shine.com/example")
    assert not bot._is_shine_url("http://www.shine.com/jobs/example/123")
    assert not bot._is_shine_url("https://shine.com.example.org/jobs/123")
    assert not bot._is_shine_url("https://external-employer.example/apply")


def test_browser_cleanup_ignores_driver_disconnect_race():
    class FakeContext:
        async def close(self):
            return None

    class FakeBrowser:
        close_attempted = False

        def is_connected(self):
            return True

        async def close(self):
            self.close_attempted = True
            raise Exception(
                "Browser.close: Connection closed while reading from the driver"
            )

    browser = FakeBrowser()
    asyncio.run(bot.close_browser_resources(FakeContext(), browser))

    assert browser.close_attempted


def test_browser_cleanup_skips_an_already_disconnected_browser():
    class FakeContext:
        async def close(self):
            return None

    class FakeBrowser:
        close_attempted = False

        def is_connected(self):
            return False

        async def close(self):
            self.close_attempted = True

    browser = FakeBrowser()
    asyncio.run(bot.close_browser_resources(FakeContext(), browser))

    assert not browser.close_attempted


def test_browser_cleanup_preserves_unexpected_errors():
    class FakeContext:
        async def close(self):
            return None

    class FakeBrowser:
        def is_connected(self):
            return True

        async def close(self):
            raise RuntimeError("unexpected cleanup failure")

    with pytest.raises(RuntimeError, match="unexpected cleanup failure"):
        asyncio.run(bot.close_browser_resources(FakeContext(), FakeBrowser()))


def test_error_screenshots_are_unique_for_jobs_with_the_same_title(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(bot, "ARTIFACT_DIR", tmp_path)

    first = bot.error_screenshot_path(
        "Python Backend Developer", "https://www.shine.com/jobs/example/1"
    )
    second = bot.error_screenshot_path(
        "Python Backend Developer", "https://www.shine.com/jobs/example/2"
    )

    assert first != second
    assert first.name.startswith("error-python-backend-developer-")


def test_only_unreferenced_error_screenshots_are_archived(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "ARTIFACT_DIR", tmp_path)
    current = tmp_path / "error-current-job-123.png"
    stale = tmp_path / "error-stale-job-456.png"
    unrelated = tmp_path / "profile.png"
    current.write_bytes(b"current")
    stale.write_bytes(b"stale")
    unrelated.write_bytes(b"unrelated")

    archived = bot.archive_unreferenced_error_screenshots(
        [{"screenshot": f"artifacts/{current.name}"}]
    )

    assert archived == 1
    assert current.exists()
    assert not stale.exists()
    assert (tmp_path / "stale-screenshots" / stale.name).exists()
    assert unrelated.exists()


def test_full_job_details_replace_misleading_card_content():
    card_job = bot.Job(
        title="Backend Engineer",
        company="Example",
        url="https://www.shine.com/jobs/backend/example/123",
        text="Python FastAPI card keywords",
        skills=("Python", "FastAPI"),
        min_experience=2,
        max_experience=5,
    )
    detailed = bot.merge_job_details(
        card_job,
        description="Build Java and Spring services.",
        detail_skills=["Java", "Spring"],
        highlights="3 to 6 Yrs",
    )

    assert "card keywords" not in detailed.text
    assert detailed.skills == ("Java", "Spring")
    assert (detailed.min_experience, detailed.max_experience) == (3, 6)
    assert not score_job(detailed).accepted


def test_full_job_details_can_rescue_an_incomplete_card():
    card_job = bot.Job(
        title="Backend Engineer",
        company="Example",
        url="https://www.shine.com/jobs/backend/example/456",
        text="Short card without technologies",
        min_experience=2,
        max_experience=4,
    )
    detailed = bot.merge_job_details(
        card_job,
        description="Build Python FastAPI backend microservices.",
        detail_skills=["Python", "FastAPI", "PostgreSQL", "Docker", "Redis"],
        highlights="2 to 4 Yrs",
    )

    assert score_job(detailed).accepted


def test_manual_question_is_never_retried_automatically():
    attempts = {}
    job = bot.Job(
        title="Backend Engineer",
        company="Example",
        url="https://www.shine.com/jobs/backend/example/789",
        text="Python backend",
    )
    now = datetime(2026, 8, 2, 1, 30, tzinfo=timezone.utc)
    entry = bot.record_failed_attempt(
        attempts,
        job,
        "Employer screening questions require manual review",
        now,
        retry_delay_hours=72,
        maximum_transient_attempts=2,
    )

    assert entry["status"] == "manual_only"
    assert entry["retry_after"] is None
    assert bot.attempt_hold_status(entry, now)[0] == "manual_review_pending"


def test_transient_failure_retries_once_after_cooldown():
    attempts = {}
    job = bot.Job(
        title="Backend Engineer",
        company="Example",
        url="https://www.shine.com/jobs/backend/example/790",
        text="Python backend",
    )
    now = datetime(2026, 8, 2, 1, 30, tzinfo=timezone.utc)
    first = bot.record_failed_attempt(
        attempts,
        job,
        "application exceeded the 45-second timeout",
        now,
        retry_delay_hours=72,
        maximum_transient_attempts=2,
    )

    assert first["status"] == "retry_scheduled"
    assert bot.attempt_hold_status(first, now)[0] == "retry_cooldown"
    after_cooldown = now + timedelta(hours=73)
    assert bot.attempt_hold_status(first, after_cooldown) is None

    second = bot.record_failed_attempt(
        attempts,
        job,
        "connection timeout",
        after_cooldown,
        retry_delay_hours=72,
        maximum_transient_attempts=2,
    )
    assert second["attempt_count"] == 2
    assert second["status"] == "manual_only"


def test_json_reports_separate_scored_and_manual_jobs(tmp_path, monkeypatch):
    scored_path = tmp_path / "scored-and-applied.json"
    manual_path = tmp_path / "manual-review.json"
    artifact_dir = tmp_path / "artifacts"
    monkeypatch.setattr(bot, "ARTIFACT_DIR", artifact_dir)
    monkeypatch.setattr(bot, "SCORED_AND_APPLIED_FILE", scored_path)
    monkeypatch.setattr(bot, "MANUAL_REVIEW_FILE", manual_path)

    rows = [
        {
            "score": 90,
            "accepted": True,
            "status": "shortlisted",
            "title": "Python Backend Engineer",
            "company": "Example",
            "experience": "3-6",
            "url": "https://example.test/job/1",
            "reasons": "strong match",
        },
        {
            "score": 80,
            "accepted": True,
            "status": "needs_review: screening questions",
            "title": "RAG Engineer",
            "company": "Example AI",
            "experience": "3-5",
            "url": "https://example.test/job/2",
            "reasons": "strong match",
        },
    ]
    history = {
        "https://example.test/applied": {
            "title": "FastAPI Developer",
            "company": "Applied Co",
            "status": "applied",
            "applied_at": "2026-08-02T00:00:00+05:30",
            "score": 88,
        }
    }

    search_metrics = [
        {
            "query": "python backend developer",
            "pages_visited": 1,
            "cards_found": 20,
            "unique_jobs_added": 12,
        }
    ]
    bot.write_json_reports(rows, True, history, search_metrics=search_metrics)

    scored = json.loads(scored_path.read_text(encoding="utf-8"))
    manual = json.loads(manual_path.read_text(encoding="utf-8"))
    assert scored["summary"]["evaluated_in_this_run"] == 2
    assert scored["summary"]["applied_jobs_in_history"] == 1
    assert len(scored["scored_jobs"]) == 2
    assert scored["search_metrics"] == search_metrics
    assert manual["unresolved_count"] == 1
    assert manual["jobs"][0]["failure_reason"] == "screening questions"
