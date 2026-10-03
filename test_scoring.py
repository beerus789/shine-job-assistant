import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

import config
import bot
from scoring import (
    Job,
    contains_phrase,
    has_early_applicant_badge,
    parse_experience,
    parse_required_experience,
    preliminary_job_priority,
    preliminary_job_rejection_reason,
    score_job,
)


def test_discovery_skips_timed_out_page_and_continues(tmp_path, monkeypatch):
    class FakeReadyState:
        async def json_value(self):
            return "ready"

        async def dispose(self):
            return None

    class FakePage:
        def __init__(self):
            self.url = ""
            self.visited = []
            self.context = SimpleNamespace(browser=None)
            self.readiness_checks = []

        def is_closed(self):
            return False

        async def goto(self, url, **kwargs):
            self.visited.append(url)
            if "first-query" in url:
                self.url = "about:blank"
                raise bot.PlaywrightTimeoutError("slow navigation")
            self.url = url

        async def wait_for_timeout(self, delay_ms):
            return None

        async def wait_for_function(self, expression, **kwargs):
            self.readiness_checks.append(self.url)
            return FakeReadyState()

    async def fake_extract_jobs(page):
        return [make_job(url=page.url)]

    monkeypatch.setattr(bot, "ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(bot, "extract_jobs", fake_extract_jobs)
    monkeypatch.setattr(config, "SEARCH_QUERIES", ["first query", "second query"])
    page = FakePage()

    jobs, metrics = asyncio.run(bot.discover(page, 1, 1_000, 0, 0))

    assert [job.url for job in jobs] == [
        "https://www.shine.com/job-search/second-query-jobs"
    ]
    assert page.visited == [
        "https://www.shine.com/job-search/first-query-jobs",
        "https://www.shine.com/job-search/second-query-jobs",
    ]
    assert page.readiness_checks == [page.visited[1]]
    assert metrics[0]["navigation_timeouts"] == 1
    assert metrics[0]["pages_visited"] == 1
    assert metrics[0]["pages"][0]["status"] == "failed"
    assert "timed out before reaching" in metrics[0]["pages"][0]["error"]
    assert metrics[1]["pages_visited"] == 1
    assert metrics[1]["pages"][0]["status"] == "ready"
    diagnostics = json.loads((tmp_path / "search-diagnostics.json").read_text())
    assert diagnostics["status"] == "partial_failure"
    assert diagnostics["failed_pages"] == 1
    assert diagnostics["unique_jobs"] == 1


def test_human_editable_settings_are_loaded():
    assert "python" in config.REQUIRED_SKILLS
    assert "fastapi" in config.PREFERRED_SKILLS
    assert "technical support" in config.BLOCKED_KEYWORDS
    assert len(config.SEARCH_QUERIES) == 10


def test_phrase_matching_uses_token_boundaries():
    assert contains_phrase("Building a RAG-based application", "rag")
    assert contains_phrase("Designing a REST   API service", "rest api")
    assert not contains_phrase("Python storage engineer", "rag")
    assert not contains_phrase("JavaScript backend developer", "java")
    assert not contains_phrase("CPython runtime developer", "python")
    assert not contains_phrase("anything", "")


def make_job(**overrides):
    values = {
        "title": "Python Backend Developer",
        "company": "Example",
        "url": "https://example.test/job/1",
        "text": "Python FastAPI Django PostgreSQL Docker AWS 2 to 5 Yrs",
        "skills": ("Python", "FastAPI", "Django", "Docker", "AWS"),
        "min_experience": 2,
        "max_experience": 5,
    }
    values.update(overrides)
    return Job(**values)


def test_preliminary_filter_keeps_incomplete_backend_card():
    job = make_job(title="Backend Engineer", text="", skills=())
    assert preliminary_job_priority(job) is not None


def test_preliminary_filter_rejects_blocked_title_and_excess_experience():
    assert preliminary_job_priority(make_job(title="Python Technical Support")) is None
    assert preliminary_job_priority(make_job(min_experience=7, max_experience=10)) is None


def test_preliminary_filter_reports_specific_skip_reason():
    blocked = make_job(title="Python Data Engineer")
    experience = make_job(min_experience=7, max_experience=10)
    unrelated = make_job(
        title="Senior Accountant",
        text="Payroll reconciliation and finance reporting",
        skills=("Accounting",),
    )

    assert "data engineer" in preliminary_job_rejection_reason(blocked)
    assert "7 years" in preliminary_job_rejection_reason(experience)
    reason = preliminary_job_rejection_reason(unrelated)
    assert "title similarity" in reason
    assert "closest configured title" in reason


def test_preliminary_filter_rejects_unrelated_search_noise():
    assert preliminary_job_priority(
        make_job(
            title="Senior Accountant",
            text="Payroll reconciliation and finance reporting",
            skills=("Accounting",),
        )
    ) is None


def test_resume_skill_density_improves_detail_priority():
    strong_ai = make_job(
        title="Artificial Intelligence Engineer - Python & Agentic AI",
        text="Python backend Agentic AI LLM OpenAI APIs",
        skills=("Python", "Agentic AI", "LLM", "OpenAI"),
    )
    generic = make_job(
        title="Software Engineer II",
        text="Python backend",
        skills=("Python",),
    )

    assert preliminary_job_priority(strong_ai) > preliminary_job_priority(generic)


def test_strong_match_is_accepted():
    result = score_job(make_job())
    assert result.accepted
    assert result.score >= 60


@pytest.mark.parametrize("label", [
    "Be An Early Applicant",
    "EARLY APPLICANT",
    "  be\tAn\u00a0Early   Applicant  ",
    "Python Backend Developer\nBe An Early Applicant\nPython FastAPI",
])
def test_early_applicant_badge_recognition(label):
    assert has_early_applicant_badge(label)


@pytest.mark.parametrize("text", [
    "",
    "We encourage early applicants to apply",
    "Early Applicant Tracking Engineer",
    "Be An Early Applicants",
    "Not an Early Applicant",
])
def test_early_applicant_badge_does_not_match_arbitrary_prose(text):
    assert not has_early_applicant_badge(text)


@pytest.mark.parametrize("overrides", [
    {"title": "Backend Developer", "text": "Ruby Rails", "skills": ("Ruby",)},
    {"title": "Accountant", "text": "Python spreadsheets", "skills": ("Python",)},
    {"min_experience": config.MAX_REQUIRED_EXPERIENCE + 1},
    {"title": "Python Technical Support"},
])
def test_early_applicant_badge_never_bypasses_eligibility_rules(overrides):
    job = make_job(**overrides)
    result = score_job(job)
    assert not result.accepted
    assert score_job(replace(job, is_early_applicant=True)) == result


def test_early_applicant_badge_preserves_fit_scores():
    job = make_job()
    early_job = replace(job, is_early_applicant=True)
    assert not job.is_early_applicant
    assert score_job(early_job) == score_job(job)
    assert preliminary_job_priority(early_job) == preliminary_job_priority(job)


def test_missing_python_is_rejected():
    result = score_job(
        make_job(title="Backend Developer", text="Ruby Rails", skills=("Ruby",))
    )
    assert not result.accepted


def test_blocked_role_is_rejected():
    result = score_job(make_job(title="Python Technical Support"))
    assert not result.accepted


def test_experience_over_limit_is_rejected():
    result = score_job(make_job(min_experience=7, max_experience=10))
    assert not result.accepted


def test_parse_experience():
    assert parse_experience("2 to 6 Yrs") == (2, 6)


def test_full_description_experience_patterns_are_parsed_safely():
    assert parse_required_experience(
        "7+ years of professional software engineering experience"
    ) == (7, None)
    assert parse_required_experience("Experience: 3-5 years") == (3, 5)
    assert parse_required_experience("Experience: 35 years") == (None, None)
    assert parse_required_experience(
        "For more than 20 years, the company has served global clients"
    ) == (None, None)


def test_unrelated_sdet_role_is_rejected():
    result = score_job(
        make_job(
            title="Software Development Engineer in Test",
            text="Python Selenium API testing 3 to 6 Yrs",
            skills=("Python", "Selenium", "API testing"),
        )
    )
    assert not result.accepted


def test_sde_iii_python_backend_role_is_accepted():
    result = score_job(
        make_job(
            title="Software Development Engineer III - Backend",
            text="Python FastAPI microservices 4 to 6 Yrs",
            skills=("Python", "FastAPI", "microservices"),
            min_experience=4,
            max_experience=6,
        )
    )
    assert result.accepted


def test_role_requiring_more_than_resume_experience_is_rejected():
    result = score_job(
        make_job(
            title="Senior Python Backend Engineer",
            text="Python FastAPI backend 5 to 9 Yrs",
            min_experience=5,
            max_experience=9,
        )
    )
    assert not result.accepted


def test_explicit_three_to_six_year_description_overrides_single_value_card():
    card_job = make_job(min_experience=6, max_experience=6)
    detailed_job = bot.merge_job_details(
        card_job,
        "Python backend role requiring 3 to 6 years of production experience.",
        ["Python", "FastAPI"],
        "6 Yrs",
    )

    assert (detailed_job.min_experience, detailed_job.max_experience) == (3, 6)
    assert score_job(detailed_job).accepted


def test_explicit_six_plus_description_is_not_treated_as_three_to_six():
    card_job = make_job(min_experience=3, max_experience=6)
    detailed_job = bot.merge_job_details(
        card_job,
        "Python backend role requiring at least 6 years of experience.",
        ["Python", "FastAPI"],
        "3 to 6 Yrs",
    )

    assert (detailed_job.min_experience, detailed_job.max_experience) == (6, None)
    assert not score_job(detailed_job).accepted


def test_single_minimum_in_description_does_not_weaken_card_minimum():
    card_job = make_job(min_experience=5, max_experience=8)
    detailed_job = bot.merge_job_details(
        card_job,
        "Python backend role requiring at least 4 years of experience.",
        ["Python", "FastAPI"],
        "5 to 8 Yrs",
    )
    assert (detailed_job.min_experience, detailed_job.max_experience) == (5, 8)
    assert not score_job(detailed_job).accepted


def test_role_family_uses_role_title_and_token_boundaries():
    assert bot.role_family(
        make_job(title="Python Storage Engineer", text="storage backend systems", skills=())
    ) == "other"
    assert bot.role_family(
        make_job(title="Python Backend Engineer", text="RAG retrieval and LLM", skills=())
    ) == "backend"
    assert bot.role_family(
        make_job(title="SDE-3 Backend Engineer", text="Python", skills=())
    ) == "sde"


def test_internship_is_rejected_for_midlevel_profile():
    result = score_job(make_job(title="Python Backend Developer Internship"))
    assert not result.accepted


def test_misleading_ai_title_with_vlsi_description_is_rejected():
    result = score_job(
        make_job(
            title="AI Agentic AI Engineer - VLSI",
            text="Python Agentic AI role purpose is to architect VLSI and hardware products",
            skills=("Python", "Agentic AI"),
        )
    )

    assert not result.accepted
    assert result.reasons == ("blocked title keyword: vlsi",)


def test_neighboring_blocked_roles_in_description_do_not_reject_backend_job():
    result = score_job(make_job(
        title="Python Backend Engineer",
        text=(
            "Build backend APIs with Python and FastAPI. Collaborate with the "
            "DevOps engineer, Android team, and data engineering group."
        ),
        skills=("Python", "FastAPI", "PostgreSQL"),
    ))

    assert result.accepted


@pytest.mark.parametrize("phrase", ["Java only", ".NET only", "PHP only", "unpaid internship"])
def test_explicit_description_disqualifiers_still_reject(phrase):
    result = score_job(make_job(text=f"This role is {phrase}.", skills=("Python",)))
    assert not result.accepted
    assert result.reasons == (f"blocked description phrase: {phrase.lower()}",)
