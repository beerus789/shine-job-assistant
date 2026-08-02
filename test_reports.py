import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bot
import pytest
from scoring import score_job


def test_card_option_matching_uses_value_not_position():
    assert bot.choose_option_index(["In Years", "3 yrs", "4 yrs", "5 yrs"], 4, "experience years") == 2
    assert bot.choose_option_index(["10-15 LPA", "15-20 LPA", "20-25 LPA"], 19, "expected salary") == 1
    assert bot.choose_option_index(["15 Days", "1 Month", "2 Months"], 30, "notice period") == 1


def test_daily_count_ignores_unverified_legacy_success():
    today = datetime.now().astimezone().isoformat()
    history = {
        "verified": {
            "status": "applied",
            "applied_at": today,
            "confirmation": {
                "method": "shine_api_and_persisted_current_job_button",
                "job_id": "123",
                "verified_at": today,
            },
        },
        "legacy": {"status": "applied", "applied_at": today},
    }

    assert bot.applications_today(history) == 1


def test_numbered_known_application_fields_are_recognized():
    assert bot.EXPECTED_SALARY_FIELD_PATTERN.search(
        "1. What is your expected annual CTC?*"
    )
    assert bot.NOTICE_PERIOD_FIELD_PATTERN.search("2. What is your notice period?*")
    assert bot.CURRENT_SALARY_FIELD_PATTERN.search("3) Current annual salary*")
    assert bot.EXPERIENCE_FIELD_PATTERN.search("4. Total work experience*")


class FakeApplicationRequest:
    method = "POST"

    def __init__(self, job_id):
        self.post_data_json = {"job_id": job_id}


class FakeApplicationResponse:
    url = "https://www.shine.com/api/v2/candidate/example/job-apply/"

    def __init__(self, request_job_id, response_job_id, status=201):
        self.request = FakeApplicationRequest(request_job_id)
        self.status = status
        self._response_job_id = response_job_id

    async def json(self):
        return {"job_id": self._response_job_id}


def test_application_response_must_match_the_current_job_id():
    matching = FakeApplicationResponse("123", 123)
    wrong_request = FakeApplicationResponse("999", 999)

    assert bot._matches_job_apply_response(matching, "123")
    assert not bot._matches_job_apply_response(wrong_request, "123")


def test_failed_or_wrong_application_response_is_not_confirmation():
    with pytest.raises(bot.ManualReviewRequired, match="HTTP 500"):
        asyncio.run(
            bot._validate_job_apply_response(
                FakeApplicationResponse("123", 123, status=500), "123"
            )
        )

    with pytest.raises(bot.ManualReviewRequired, match="different job ID"):
        asyncio.run(
            bot._validate_job_apply_response(
                FakeApplicationResponse("123", 456), "123"
            )
        )


def test_similar_job_applied_button_cannot_confirm_the_current_job():
    async def scenario():
        async with bot.async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.set_content(
                    '<aside><button class="jobApplyBtnNova" disabled>Applied</button></aside>'
                )
                assert not await bot._current_job_is_applied(page)

                await page.set_content(
                    '<main><button class="jdCard_jdBtn__current" disabled>Applied</button></main>'
                    '<aside><button class="jobApplyBtnNova" disabled>Applied</button></aside>'
                )
                assert await bot._current_job_is_applied(page)

                await page.set_content(
                    '<main><button class="jdCard_jdBtn__one" disabled>Applied</button>'
                    '<button class="jdCard_jdBtn__two" disabled>Applied</button></main>'
                )
                assert not await bot._current_job_is_applied(page)
            finally:
                await browser.close()

    asyncio.run(scenario())


def _run_fake_shine_application(
    monkeypatch,
    response_status,
    *,
    persist_after_success=True,
    redirect_after_success=False,
    popup_after_persisted_reload=False,
    questionnaire=False,
    answers=None,
):
    state = {"applied": False, "request_count": 0, "last_payload": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_GET(self):
            if self.path in {"/redirect", "/popup"}:
                html = b"<html><body>redirected application</body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
                return
            if self.path != "/jobs/backend/example/123":
                self.send_error(404)
                return
            persisted_behavior = ""
            if state["applied"]:
                primary = (
                    '<button class="jdCard_jdBtn__current" disabled>Applied</button>'
                )
                if popup_after_persisted_reload:
                    persisted_behavior = (
                        "<script>setTimeout(() => window.open('/popup', '_blank'), 100);"
                        "</script>"
                    )
            else:
                apply_action = (
                    "openQuestionnaire()" if questionnaire else "applyCurrentJob()"
                )
                primary = (
                    '<button id="id_apply_123" class="jdCard_jdBtn__current" '
                    f'onclick="{apply_action}">Apply</button>'
                )
            questionnaire_markup = ""
            if questionnaire and not state["applied"]:
                questionnaire_markup = """
                  <style>.customSelect_option { display: none; }</style>
                  <div id="applicationDialog" role="dialog" style="display:none">
                    <ul>
                      <li><label>1. What is your expected annual CTC?*</label></li>
                      <li><div id="expected" class="customSelect_customSelect">
                        <div class="customSelect_selectHeader" onclick="openSelect(this)">Choose</div>
                        <div class="customSelect_option" onclick="chooseOption(this)">15-20 LPA</div>
                        <div class="customSelect_option" onclick="chooseOption(this)">20-25 LPA</div>
                      </div></li>
                      <li><label>2. What is your notice period?*</label></li>
                      <li><div id="notice" class="customSelect_customSelect">
                        <div class="customSelect_selectHeader" onclick="openSelect(this)">Choose</div>
                        <div class="customSelect_option" onclick="chooseOption(this)">15 Days</div>
                        <div class="customSelect_option" onclick="chooseOption(this)">1 Month</div>
                        <div class="customSelect_option" onclick="chooseOption(this)">2 Months</div>
                      </div></li>
                      <li><label>3. Current annual salary*</label></li>
                      <li><div id="current" class="customSelect_customSelect">
                        <div class="customSelect_selectHeader" onclick="openSelect(this)">Choose</div>
                        <div class="customSelect_option" onclick="chooseOption(this)">10-15 LPA</div>
                        <div class="customSelect_option" onclick="chooseOption(this)">15-20 LPA</div>
                      </div></li>
                      <li><label>4. Total work experience*</label></li>
                      <li>
                        <div id="years" class="customSelect_customSelect">
                          <div class="customSelect_selectHeader" onclick="openSelect(this)">Years</div>
                          <div class="customSelect_option" onclick="chooseOption(this)">3 yrs</div>
                          <div class="customSelect_option" onclick="chooseOption(this)">4 yrs</div>
                          <div class="customSelect_option" onclick="chooseOption(this)">5 yrs</div>
                        </div>
                        <div id="months" class="customSelect_customSelect">
                          <div class="customSelect_selectHeader" onclick="openSelect(this)">Months</div>
                          <div class="customSelect_option" onclick="chooseOption(this)">0 months</div>
                          <div class="customSelect_option" onclick="chooseOption(this)">6 months</div>
                          <div class="customSelect_option" onclick="chooseOption(this)">11 months</div>
                        </div>
                      </li>
                    </ul>
                    <button onclick="submitQuestionnaire()">Submit and apply</button>
                  </div>
                """
            html = f"""
                <html><body>
                  <div>skills matched with your profile</div>
                  <main>{primary}</main>
                  <aside><button class="jobApplyBtnNova" disabled>Applied</button></aside>
                  {questionnaire_markup}
                  {persisted_behavior}
                  <script>
                    function openQuestionnaire() {{
                      document.getElementById('applicationDialog').style.display = 'block';
                    }}
                    function openSelect(header) {{
                      header.parentElement.querySelectorAll('.customSelect_option')
                        .forEach(option => option.style.display = 'block');
                    }}
                    function chooseOption(option) {{
                      const select = option.parentElement;
                      select.dataset.value = option.textContent.trim();
                      select.querySelector('.customSelect_selectHeader').textContent =
                        option.textContent.trim();
                      select.querySelectorAll('.customSelect_option')
                        .forEach(candidate => candidate.style.display = 'none');
                    }}
                    async function submitPayload(payload) {{
                      const response = await fetch('/api/v2/candidate/test/job-apply/', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify(payload)
                      }});
                      if (response.ok) {{
                        const button = document.getElementById('id_apply_123');
                        button.removeAttribute('id');
                        button.textContent = 'Applied';
                        button.disabled = true;
                        {"setTimeout(() => location.href = '/redirect', 100);" if redirect_after_success else ""}
                      }}
                    }}
                    async function applyCurrentJob() {{
                      await submitPayload({{job_id: '123'}});
                    }}
                    async function submitQuestionnaire() {{
                      await submitPayload({{
                        job_id: '123',
                        expected: document.getElementById('expected').dataset.value,
                        notice: document.getElementById('notice').dataset.value,
                        current: document.getElementById('current').dataset.value,
                        years: document.getElementById('years').dataset.value,
                        months: document.getElementById('months').dataset.value
                      }});
                    }}
                  </script>
                </body></html>
            """.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def do_POST(self):
            if self.path != "/api/v2/candidate/test/job-apply/":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            request_payload = json.loads(self.rfile.read(length) or b"{}")
            state["request_count"] += 1
            state["last_payload"] = request_payload
            if response_status in {200, 201} and persist_after_success:
                state["applied"] = True
            response_payload = json.dumps(
                {"job_id": int(request_payload.get("job_id", 0))}
            ).encode("utf-8")
            self.send_response(response_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_payload)))
            self.end_headers()
            self.wfile.write(response_payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(bot, "_is_shine_url", lambda _url: True)
    job = bot.Job(
        title="Backend Engineer",
        company="Example",
        url=f"http://127.0.0.1:{server.server_port}/jobs/backend/example/123",
        text="Python backend",
    )

    async def scenario():
        async with bot.async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            try:
                return await bot.apply_to_job(
                    page,
                    job,
                    navigation_timeout_ms=5_000,
                    authentication_timeout_ms=5_000,
                    apply_timeout_ms=5_000,
                    answers=answers
                    or bot.ApplicationAnswers(None, None, None, None, None),
                )
            finally:
                await bot.close_browser_resources(context, browser)

    try:
        return asyncio.run(scenario()), state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_apply_to_job_requires_matching_response_and_persisted_primary_button(
    monkeypatch,
):
    outcome, state = _run_fake_shine_application(monkeypatch, 201)

    assert outcome.status == "applied"
    assert outcome.response_status == 201
    assert outcome.response_job_id == "123"
    assert state["applied"] is True
    assert state["request_count"] == 1
    assert state["last_payload"] == {"job_id": "123"}


def test_apply_to_job_rejects_failed_application_response(monkeypatch):
    with pytest.raises(bot.ManualReviewRequired, match="HTTP 500"):
        _run_fake_shine_application(monkeypatch, 500)


def test_apply_to_job_rejects_success_response_that_does_not_persist(monkeypatch):
    with pytest.raises(bot.ManualReviewRequired, match="did not persist after reload"):
        _run_fake_shine_application(
            monkeypatch,
            201,
            persist_after_success=False,
        )


def test_apply_to_job_rejects_redirect_after_success_response(monkeypatch):
    with pytest.raises(bot.ManualReviewRequired, match="redirected to"):
        _run_fake_shine_application(
            monkeypatch,
            201,
            redirect_after_success=True,
        )


def test_apply_to_job_rejects_popup_during_persisted_reload(monkeypatch):
    with pytest.raises(bot.ManualReviewRequired, match="separate Shine tab"):
        _run_fake_shine_application(
            monkeypatch,
            201,
            popup_after_persisted_reload=True,
        )


def test_apply_to_job_completes_supported_questionnaire_once(monkeypatch):
    outcome, state = _run_fake_shine_application(
        monkeypatch,
        201,
        questionnaire=True,
        answers=bot.ApplicationAnswers(4, 6, 18, 22, 30),
    )

    assert outcome.status == "applied"
    assert state["request_count"] == 1
    assert state["last_payload"] == {
        "job_id": "123",
        "expected": "20-25 LPA",
        "notice": "1 Month",
        "current": "15-20 LPA",
        "years": "4 yrs",
        "months": "6 months",
    }


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
            "confirmation": {
                "method": "shine_api_and_persisted_current_job_button",
                "job_id": "123",
                "response_status": 201,
                "verified_at": "2026-08-02T00:00:01+05:30",
            },
        },
        "https://example.test/already-applied": {
            "title": "Django Developer",
            "company": "Existing Co",
            "status": "already_applied",
            "applied_at": "",
            "score": 82,
            "confirmation": {
                "method": "persisted_current_job_button",
                "job_id": "456",
                "verified_at": "2026-08-02T00:00:02+05:30",
            },
        },
        "https://example.test/legacy-unverified": {
            "title": "Legacy Python Developer",
            "company": "Legacy Co",
            "status": "applied",
            "applied_at": "2026-08-01T00:00:00+05:30",
            "score": 80,
        },
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
    assert scored["summary"]["applied_jobs_in_history"] == 2
    assert len(scored["scored_jobs"]) == 2
    applied_confirmation = next(
        item["confirmation"]
        for item in scored["applied_jobs"]
        if item["status"] == "applied"
    )
    assert applied_confirmation["response_status"] == 201
    assert scored["search_metrics"] == search_metrics
    assert manual["unresolved_count"] == 2
    assert {item["failure_reason"] for item in manual["jobs"]} == {
        "screening questions",
        "Legacy success record has no job-specific confirmation evidence",
    }
