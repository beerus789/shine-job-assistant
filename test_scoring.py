import config
from scoring import (
    Job,
    contains_phrase,
    parse_experience,
    preliminary_job_priority,
    score_job,
)


def test_human_editable_settings_are_loaded():
    assert "python" in config.REQUIRED_SKILLS
    assert "fastapi" in config.PREFERRED_SKILLS
    assert "technical support" in config.BLOCKED_KEYWORDS


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


def test_strong_match_is_accepted():
    result = score_job(make_job())
    assert result.accepted
    assert result.score >= 60


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


def test_internship_is_rejected_for_midlevel_profile():
    result = score_job(make_job(title="Python Backend Developer Internship"))
    assert not result.accepted
