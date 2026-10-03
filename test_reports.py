import asyncio
import csv
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bot
import audit_discovery
import pytest
from scoring import score_job


def test_card_option_matching_uses_value_not_position():
    assert bot.choose_option_index(["In Years", "3 yrs", "4 yrs", "5 yrs"], 4, "experience years") == 2
    assert bot.choose_option_index(["10-15 LPA", "15-20 LPA", "20-25 LPA"], 19, "expected salary") == 1
    assert bot.choose_option_index(["15 Days", "1 Month", "2 Months"], 30, "notice period") == 1


def test_daily_count_conservatively_includes_unverified_legacy_success():
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

    assert bot.applications_today(history) == 2


def test_exhausted_daily_limit_is_reported_without_starting_browser(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    artifact_dir = tmp_path / "artifacts"
    monkeypatch.setattr(bot, "STATE_DIR", state_dir)
    monkeypatch.setattr(bot, "ATTEMPTS_FILE", state_dir / "attempts.json")
    monkeypatch.setattr(bot, "HISTORY_FILE", state_dir / "history.json")
    monkeypatch.setattr(bot, "ARTIFACT_DIR", artifact_dir)
    monkeypatch.setattr(bot, "REPORT_FILE", artifact_dir / "latest.csv")
    monkeypatch.setattr(bot, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "load_history", lambda: {
        "https://www.shine.com/jobs/python/example/1": {
            "status": "applied",
            "applied_at": datetime.now().astimezone().isoformat(),
        }
    })
    monkeypatch.setattr(bot, "load_attempts", lambda: {})
    monkeypatch.setattr(bot, "async_playwright", lambda: pytest.fail("browser must not start"))
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("SHINE_EMAIL", "person@example.com")
    monkeypatch.setenv("SHINE_PASSWORD", "not-a-real-password")
    monkeypatch.setenv("MAX_APPLICATIONS_PER_RUN", "20")
    monkeypatch.setenv("MAX_APPLICATIONS_PER_DAY", "1")

    asyncio.run(bot.run())

    status = json.loads((artifact_dir / "run-status.json").read_text(encoding="utf-8"))
    assert status["status"] == "limit_reached"
    assert status["phase"] == "preflight_limits"
    assert status["application_limits"]["blocking_status"] == "daily_limit"
    assert status["application_limits"]["applications_today_at_start"] == 1
    assert (artifact_dir / "latest.csv").is_file()


def test_legacy_applied_history_is_held_for_manual_verification():
    assert bot.history_entry_hold_status({"status": "applied"}) == (
        "manual_review_pending",
        "legacy application record has no confirmation; verify before retrying",
    )
    assert bot.history_entry_hold_status({"status": "rejected"}) is None


def test_boolean_environment_values_fail_closed_on_typos(monkeypatch):
    monkeypatch.delenv("DRY_RUN", raising=False)
    assert bot.env_bool("DRY_RUN", True) is True
    monkeypatch.setenv("DRY_RUN", "false")
    assert bot.env_bool("DRY_RUN", True) is False
    monkeypatch.setenv("DRY_RUN", "treu")
    with pytest.raises(RuntimeError, match="DRY_RUN must be true/false"):
        bot.env_bool("DRY_RUN", True)


def test_latest_csv_is_reset_and_contains_only_unapplied_jobs(tmp_path, monkeypatch):
    report_path = tmp_path / "latest.csv"
    report_path.write_text("stale previous run", encoding="utf-8")
    monkeypatch.setattr(bot, "ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(bot, "REPORT_FILE", report_path)

    assert bot.write_report([]) == 0
    with report_path.open(encoding="utf-8", newline="") as stream:
        assert list(csv.DictReader(stream)) == []

    rows = [
        {
            "score": 85,
            "accepted": True,
            "status": "applied",
            "title": "Python Backend Engineer",
            "company": "Applied Co",
            "experience": "3-6",
            "url": "https://example.test/applied",
            "reasons": "strong match",
            "detail_evaluated": True,
            "is_early_applicant": False,
        },
        {
            "score": 82,
            "accepted": True,
            "status": "already_applied",
            "title": "Django Developer",
            "company": "Previously Applied Co",
            "experience": "3-6",
            "url": "https://example.test/already-applied",
            "reasons": "strong match",
            "detail_evaluated": True,
            "is_early_applicant": False,
        },
        {
            "score": 81,
            "accepted": True,
            "status": "already_seen",
            "title": "FastAPI Developer",
            "company": "Already Seen Co",
            "experience": "3-6",
            "url": "https://example.test/already-seen",
            "reasons": "verified application in history",
            "detail_evaluated": False,
            "is_early_applicant": False,
        },
        {
            "score": 35,
            "accepted": False,
            "status": "rejected",
            "title": "Java Engineer",
            "company": "Rejected Co",
            "experience": "3-6",
            "url": "https://example.test/rejected",
            "reasons": "required skill missing: python",
            "detail_evaluated": True,
            "is_early_applicant": False,
        },
        {
            "score": 80,
            "accepted": True,
            "status": "needs_review: screening questions",
            "title": "RAG Engineer",
            "company": "Review Co",
            "experience": "3-5",
            "url": "https://example.test/review",
            "reasons": "strong match",
            "detail_evaluated": True,
            "is_early_applicant": True,
        },
        {
            "score": 82,
            "accepted": True,
            "status": "shortlisted",
            "title": "Django Developer",
            "company": "Preview Co",
            "experience": "3-5",
            "url": "https://example.test/preview",
            "reasons": "strong match",
            "detail_evaluated": True,
            "is_early_applicant": False,
        },
    ]

    assert bot.write_report(rows) == 3
    with report_path.open(encoding="utf-8", newline="") as stream:
        report_rows = list(csv.DictReader(stream))

    assert [row["company"] for row in report_rows] == [
        "Rejected Co",
        "Review Co",
        "Preview Co",
    ]
    assert "required skill missing: python" in report_rows[0]["reason_not_applied"]
    assert "matching reasons" in report_rows[0]["recommended_action"]
    assert report_rows[1]["reason_not_applied"] == "screening questions"
    assert "manual-review.json" in report_rows[1]["recommended_action"]
    assert "Dry-run preview only" in report_rows[2]["reason_not_applied"]


def test_latest_csv_explains_precise_application_caps_and_prefilter_rules(tmp_path, monkeypatch):
    report_path = tmp_path / "latest.csv"
    monkeypatch.setattr(bot, "ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(bot, "REPORT_FILE", report_path)
    rows = [
        {
            "score": 82, "accepted": True,
            "status": "run_limit: per-run limit (20) reached",
            "title": "Python Backend Engineer", "company": "Run Cap Co",
            "experience": "3-5", "url": "https://example.test/run-cap",
            "reasons": "strong match", "detail_evaluated": True,
        },
        {
            "score": 82, "accepted": True,
            "status": "daily_limit: daily limit (100) reached",
            "title": "Python Backend Engineer", "company": "Daily Cap Co",
            "experience": "3-5", "url": "https://example.test/day-cap",
            "reasons": "strong match", "detail_evaluated": True,
        },
        {
            "score": 0, "accepted": False,
            "status": "pre_filtered: blocked title phrase: data engineer",
            "title": "Python Data Engineer", "company": "Skipped Co",
            "experience": "3-5", "url": "https://example.test/skipped",
            "reasons": "blocked title phrase: data engineer", "detail_evaluated": False,
        },
    ]

    assert bot.write_report(rows) == 3
    with report_path.open(encoding="utf-8", newline="") as stream:
        report_rows = list(csv.DictReader(stream))

    assert "per-run limit (20) reached" in report_rows[0]["reason_not_applied"]
    assert "daily limit (100) reached" in report_rows[1]["reason_not_applied"]
    assert "blocked title phrase: data engineer" in report_rows[2]["reason_not_applied"]
    assert "full description was not read" in report_rows[2]["recommended_action"]


def test_run_state_lock_rejects_a_second_process_owner(tmp_path):
    lock = tmp_path / "bot-run.lock"
    with bot.single_instance_run_lock(lock):
        with pytest.raises(RuntimeError, match="Another Shine automation process"):
            with bot.single_instance_run_lock(lock):
                pass


def test_atomic_json_write_replaces_file_with_complete_document(tmp_path):
    destination = tmp_path / "state.json"
    bot.atomic_write_json(destination, {"complete": True, "count": 3})
    assert json.loads(destination.read_text(encoding="utf-8")) == {
        "complete": True,
        "count": 3,
    }
    assert list(tmp_path.iterdir()) == [destination]


def test_read_only_audit_separates_accepted_and_not_evaluated_jobs():
    accepted_job = bot.Job(
        title="Python Backend Developer",
        company="Accepted Co",
        url="https://www.shine.com/jobs/backend/accepted/1",
        text="Python FastAPI backend",
        min_experience=2,
        max_experience=4,
    )
    limited_job = bot.Job(
        title="Backend Engineer",
        company="Limited Co",
        url="https://www.shine.com/jobs/backend/limited/2",
        text="Python backend",
    )
    payload = audit_discovery.build_audit_payload(
        [
            (bot.ScoreResult(85, True, ("strong match",)), accepted_job),
            (bot.ScoreResult(0, False, ("detail limit",)), limited_job),
        ],
        {limited_job.url: "not_evaluated: detail-scoring limit reached (1 jobs)"},
        [
            {
                "query": "python backend",
                "pages_visited": 3,
                "cards_found": 60,
                "unique_jobs_added": 2,
            }
        ],
    )

    assert payload["mode"] == "read_only_discovery_audit"
    assert payload["summary"]["cards_found"] == 60
    assert payload["summary"]["accepted"] == 1
    assert payload["summary"]["evaluated_in_this_run"] == 1
    assert payload["status"] == "incomplete"
    assert payload["summary"]["status_counts"] == {
        "accepted": 1,
        "not_evaluated": 1,
    }


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
    questionnaire_delay_ms=0,
    response_delay_ms=0,
    apply_timeout_ms=5_000,
    job_url_suffix="",
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
            if self.path.split("?", 1)[0] != "/jobs/backend/example/123":
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
                      setTimeout(() => {{
                        document.getElementById('applicationDialog').style.display = 'block';
                      }}, {questionnaire_delay_ms});
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
            if response_delay_ms:
                threading.Event().wait(response_delay_ms / 1_000)
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
        url=f"http://127.0.0.1:{server.server_port}/jobs/backend/example/123{job_url_suffix}",
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
                    apply_timeout_ms=apply_timeout_ms,
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


def test_apply_to_job_waits_for_a_delayed_questionnaire(monkeypatch):
    outcome, state = _run_fake_shine_application(
        monkeypatch,
        201,
        questionnaire=True,
        questionnaire_delay_ms=1_200,
        answers=bot.ApplicationAnswers(4, 6, 18, 22, 30),
    )

    assert outcome.status == "applied"
    assert state["request_count"] == 1
    assert state["last_payload"]["expected"] == "20-25 LPA"


def test_apply_to_job_does_not_resubmit_a_pending_questionnaire(monkeypatch):
    outcome, state = _run_fake_shine_application(
        monkeypatch,
        201,
        questionnaire=True,
        response_delay_ms=6_000,
        apply_timeout_ms=10_000,
        answers=bot.ApplicationAnswers(4, 6, 18, 22, 30),
    )

    assert outcome.status == "applied"
    assert state["request_count"] == 1


def test_apply_to_job_ignores_query_and_fragment_when_matching_job_id(monkeypatch):
    outcome, state = _run_fake_shine_application(
        monkeypatch,
        201,
        job_url_suffix="?source=search#apply",
    )

    assert outcome.job_id == "123"
    assert state["last_payload"] == {"job_id": "123"}
    assert state["request_count"] == 1


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


def test_full_description_overrides_misleading_lower_experience_card():
    card_job = bot.Job(
        title="Python FastAPI Agentic AI Platforms",
        company="Example",
        url="https://www.shine.com/jobs/backend/example/457",
        text="Python FastAPI 1 to 5 Yrs",
        skills=("Python", "FastAPI"),
        min_experience=1,
        max_experience=5,
    )
    detailed = bot.merge_job_details(
        card_job,
        description=(
            "We require 7+ years of professional software engineering experience "
            "building Python FastAPI backend services."
        ),
        detail_skills=["Python", "FastAPI", "PostgreSQL"],
        highlights="1 to 5 Yrs",
    )

    assert (detailed.min_experience, detailed.max_experience) == (7, None)
    assert not score_job(detailed).accepted


def test_detail_limit_is_reported_as_not_evaluated():
    job = bot.Job(
        title="Python Backend Developer",
        company="Example",
        url="https://www.shine.com/jobs/backend/example/458",
        text="Python FastAPI backend 2 to 4 Yrs",
        skills=("Python", "FastAPI"),
        min_experience=2,
        max_experience=4,
    )

    ranked, statuses = asyncio.run(
        bot.score_detailed_jobs(
            page=None,
            jobs=[job],
            maximum=0,
            navigation_timeout_ms=1,
            detail_timeout_seconds=1,
        )
    )

    assert ranked[0][0].score == 0
    assert statuses[job.url].startswith("not_evaluated: detail-scoring limit reached")


def test_excess_experience_card_is_reported_as_pre_filtered():
    job = bot.Job(
        title="Senior Python Backend Engineer",
        company="Example",
        url="https://www.shine.com/jobs/backend/example/459",
        text="Python backend 7 to 10 Yrs",
        skills=("Python",),
        min_experience=7,
        max_experience=10,
    )

    _, statuses = asyncio.run(
        bot.score_detailed_jobs(
            page=None,
            jobs=[job],
            maximum=250,
            navigation_timeout_ms=1,
            detail_timeout_seconds=1,
        )
    )

    assert statuses[job.url].startswith(
        "pre_filtered: card minimum experience 7 years"
    )


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
            "detail_evaluated": True,
            "is_early_applicant": True,
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
    assert scored["summary"]["discovered_in_this_run"] == 12
    assert scored["summary"]["early_applicant_evaluated_in_this_run"] == 1
    assert scored["status"] == "partial_failure"
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


def test_run_records_uncertain_application_before_stopping_on_browser_close(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    job = bot.Job(
        title="Python Backend Engineer",
        company="Example",
        url="https://www.shine.com/jobs/backend/example/321",
        text="Python backend",
    )
    state_dir = tmp_path / "state"
    artifact_dir = tmp_path / "artifacts"
    monkeypatch.setattr(bot, "STATE_DIR", state_dir)
    monkeypatch.setattr(bot, "HISTORY_FILE", state_dir / "history.json")
    monkeypatch.setattr(bot, "ATTEMPTS_FILE", state_dir / "attempts.json")
    monkeypatch.setattr(bot, "ARTIFACT_DIR", artifact_dir)
    monkeypatch.setattr(bot, "REPORT_FILE", artifact_dir / "latest.csv")
    monkeypatch.setattr(bot, "SCORED_AND_APPLIED_FILE", artifact_dir / "scored.json")
    monkeypatch.setattr(bot, "MANUAL_REVIEW_FILE", artifact_dir / "manual-review.json")
    monkeypatch.setattr(bot, "load_dotenv", lambda *args, **kwargs: None)
    async def fake_discover(*args, **kwargs):
        return [job], [{"unique_jobs_added": 1, "pages": [{"status": "ready"}]}]

    async def fake_score(*args, **kwargs):
        return [(bot.ScoreResult(80, True, ("eligible",)), job)], {}

    async def fake_apply(page, *args, **kwargs):
        page.context.browser.connected = False
        raise RuntimeError("browser disconnected during confirmation")

    class FakePage:
        def __init__(self, context):
            self.context = context

        def is_closed(self):
            return False

        async def screenshot(self, **kwargs):
            return None

    class FakeContext:
        def __init__(self, browser):
            self.browser = browser

        async def new_page(self):
            return FakePage(self)

        async def close(self):
            return None

    class FakeBrowser:
        connected = True

        async def new_context(self):
            return FakeContext(self)

        def is_connected(self):
            return self.connected

    class FakePlaywright:
        async def __aenter__(self):
            self.browser = FakeBrowser()
            self.chromium = SimpleNamespace(launch=self.launch)
            return self

        async def launch(self, **kwargs):
            return self.browser

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(bot, "async_playwright", FakePlaywright)
    monkeypatch.setattr(bot, "login", lambda *args, **kwargs: asyncio.sleep(0))
    monkeypatch.setattr(bot, "discover", fake_discover)
    monkeypatch.setattr(bot, "score_detailed_jobs", fake_score)
    monkeypatch.setattr(bot, "apply_to_job", fake_apply)
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("HEADLESS", "true")
    monkeypatch.setenv("ENABLE_TRACING", "false")
    monkeypatch.setenv("SHINE_EMAIL", "test@example.com")
    monkeypatch.setenv("SHINE_PASSWORD", "test-password")
    monkeypatch.setenv("MAX_APPLICATIONS_PER_RUN", "1")
    monkeypatch.setenv("MAX_APPLICATIONS_PER_DAY", "1")
    monkeypatch.setenv("MAX_DETAIL_JOBS_PER_RUN", "1")

    with pytest.raises(RuntimeError, match="Browser session closed"):
        asyncio.run(bot.run())

    report = json.loads((artifact_dir / "scored.json").read_text(encoding="utf-8"))
    manual = json.loads((artifact_dir / "manual-review.json").read_text(encoding="utf-8"))
    status = json.loads((artifact_dir / "run-status.json").read_text(encoding="utf-8"))
    assert report["scored_jobs"][0]["status"].startswith("needs_review:")
    assert report["scored_jobs"][0]["accepted"] is True
    assert manual["jobs"][0]["automation_status"] == "manual_only"
    assert "outcome unconfirmed" in manual["jobs"][0]["failure_reason"]
    assert status["status"] == "failed"
    assert not (state_dir / "history.json").exists()
