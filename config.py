"""Load the job-matching policy from human-editable text files.

Users should edit files in ``job_settings`` rather than changing Python code.
Each file accepts one value per line. Blank lines and lines beginning with ``#``
are ignored.
"""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SETTINGS_DIRECTORY = PROJECT_ROOT / "job_settings"


def _load_terms(filename: str) -> frozenset[str]:
    """Read, normalize, and validate one list-style configuration file."""
    path = SETTINGS_DIRECTORY / filename
    if not path.is_file():
        raise RuntimeError(f"Missing job settings file: {path}")

    terms = {
        line.strip().lower()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if not terms:
        raise RuntimeError(f"Job settings file cannot be empty: {path}")
    return frozenset(terms)


# Human-editable matching lists. See job_settings/README.md.
TARGET_TITLES = _load_terms("target-titles.txt")
SEARCH_QUERIES = _load_terms("search-queries.txt")
REQUIRED_SKILLS = _load_terms("required-skills.txt")
PREFERRED_SKILLS = _load_terms("preferred-skills.txt")
BLOCKED_KEYWORDS = _load_terms("blocked-keywords.txt")
ROLE_SIGNALS = _load_terms("role-signals.txt")


# Resume and safety thresholds. These change infrequently and are kept together
# so the scoring formula remains explicit and reviewable.
CANDIDATE_YEARS_EXPERIENCE = 4
MAX_REQUIRED_EXPERIENCE = 4
MINIMUM_SCORE = 60
MAX_APPLICATIONS_PER_ROLE_FAMILY = 2


# Scoring weights add to 100. A blocked keyword or missing required skill
# rejects a job regardless of its numerical score.
TITLE_WEIGHT = 45
REQUIRED_SKILLS_WEIGHT = 30
PREFERRED_SKILLS_WEIGHT = 20
EXPERIENCE_WEIGHT = 5
