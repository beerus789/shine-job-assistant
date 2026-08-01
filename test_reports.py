import json

import bot


def test_card_option_matching_uses_value_not_position():
    assert bot.choose_option_index(["In Years", "3 yrs", "4 yrs", "5 yrs"], 4, "experience years") == 2
    assert bot.choose_option_index(["10-15 LPA", "15-20 LPA", "20-25 LPA"], 19, "expected salary") == 1
    assert bot.choose_option_index(["15 Days", "1 Month", "2 Months"], 30, "notice period") == 1


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

    bot.write_json_reports(rows, True, history)

    scored = json.loads(scored_path.read_text(encoding="utf-8"))
    manual = json.loads(manual_path.read_text(encoding="utf-8"))
    assert scored["summary"]["evaluated_in_this_run"] == 2
    assert scored["summary"]["applied_jobs_in_history"] == 1
    assert len(scored["scored_jobs"]) == 2
    assert manual["unresolved_count"] == 1
    assert manual["jobs"][0]["failure_reason"] == "screening questions"
