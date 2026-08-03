"""Deterministic, explainable scoring for discovered Shine jobs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

import config


def normalize(value: str) -> str:
    """Normalize wording while retaining common technology symbols."""
    return re.sub(r"[^a-z0-9+#.]+", " ", value.lower()).strip()


def contains_phrase(text: str, phrase: str) -> bool:
    """Match complete normalized tokens instead of arbitrary substrings.

    Substring matching makes ``rag`` match ``storage`` and can turn an
    unrelated role into a false positive. The explicit boundaries still allow
    flexible whitespace in multi-word phrases and retain technology symbols
    supported by :func:`normalize`, such as ``+``, ``#``, and ``.``.
    """
    phrase_tokens = [re.escape(token) for token in normalize(phrase).split()]
    if not phrase_tokens:
        return False

    pattern = (
        r"(?<![a-z0-9+#.])"
        + r"\s+".join(phrase_tokens)
        + r"(?![a-z0-9+#.])"
    )
    return re.search(pattern, normalize(text)) is not None


@dataclass(frozen=True)
class Job:
    title: str
    company: str
    url: str
    text: str
    skills: tuple[str, ...] = ()
    min_experience: int | None = None
    max_experience: int | None = None

    @property
    def searchable_text(self) -> str:
        return " ".join((self.title, self.text, *self.skills))


@dataclass(frozen=True)
class ScoreResult:
    score: int
    accepted: bool
    reasons: tuple[str, ...]


def _title_similarity(title: str) -> float:
    actual = normalize(title)
    return max(
        SequenceMatcher(None, actual, normalize(target)).ratio()
        for target in config.TARGET_TITLES
    )


def preliminary_job_priority(job: Job) -> int | None:
    """Rank safe detail-page candidates without making a final decision.

    Search cards are incomplete, so missing skills never reject a job here.
    Only an unsuitable title or an excessive minimum-experience requirement is
    considered strong enough to avoid the extra detail-page request.
    """
    if any(contains_phrase(job.title, keyword) for keyword in config.BLOCKED_KEYWORDS):
        return None
    if job.min_experience is not None and job.min_experience > config.MAX_REQUIRED_EXPERIENCE:
        return None

    title_similarity = _title_similarity(job.title)
    role_hits = sum(
        contains_phrase(job.searchable_text, signal) for signal in config.ROLE_SIGNALS
    )
    # Shine's broad searches can return accountants, technicians, and other
    # unrelated roles. Keep an incomplete but relevant card, while preventing
    # unrelated titles from consuming the limited full-description budget.
    if not role_hits and title_similarity < 0.58:
        return None

    required_hits = sum(
        contains_phrase(job.searchable_text, skill) for skill in config.REQUIRED_SKILLS
    )
    preferred_hits = sum(
        contains_phrase(job.searchable_text, skill) for skill in config.PREFERRED_SKILLS
    )

    priority = round(title_similarity * 100)
    priority += 20 * required_hits
    priority += min(36, 6 * preferred_hits)
    priority += min(20, 4 * role_hits)
    return priority


def score_job(job: Job) -> ScoreResult:
    """Apply hard rejection rules, then calculate an explainable 0-100 score."""
    corpus = normalize(job.searchable_text)
    reasons: list[str] = []

    blocked = sorted(k for k in config.BLOCKED_KEYWORDS if contains_phrase(corpus, k))
    if blocked:
        return ScoreResult(0, False, (f"blocked keyword: {', '.join(blocked)}",))

    missing = sorted(k for k in config.REQUIRED_SKILLS if not contains_phrase(corpus, k))
    if missing:
        return ScoreResult(0, False, (f"missing required skill: {', '.join(missing)}",))

    role_signals = sorted(
        signal for signal in config.ROLE_SIGNALS if contains_phrase(corpus, signal)
    )
    if not role_signals:
        return ScoreResult(0, False, ("missing backend/AI role signal",))

    if job.min_experience is not None and job.min_experience > config.MAX_REQUIRED_EXPERIENCE:
        return ScoreResult(
            0,
            False,
            (f"minimum experience {job.min_experience} exceeds limit",),
        )

    title_points = round(config.TITLE_WEIGHT * _title_similarity(job.title))
    required_points = config.REQUIRED_SKILLS_WEIGHT
    preferred_hits = sorted(
        skill for skill in config.PREFERRED_SKILLS if contains_phrase(corpus, skill)
    )
    preferred_ratio = min(1.0, len(preferred_hits) / 5)
    preferred_points = round(config.PREFERRED_SKILLS_WEIGHT * preferred_ratio)
    experience_points = config.EXPERIENCE_WEIGHT
    score = min(100, title_points + required_points + preferred_points + experience_points)

    reasons.extend(
        (
            f"title={title_points}/{config.TITLE_WEIGHT}",
            f"required={required_points}/{config.REQUIRED_SKILLS_WEIGHT}",
            f"preferred={preferred_points}/{config.PREFERRED_SKILLS_WEIGHT}"
            + (f" ({', '.join(preferred_hits)})" if preferred_hits else ""),
            f"experience={experience_points}/{config.EXPERIENCE_WEIGHT}",
            f"role signals={', '.join(role_signals)}",
        )
    )
    return ScoreResult(score, score >= config.MINIMUM_SCORE, tuple(reasons))


EXPERIENCE_RE = re.compile(r"(?P<minimum>\d+)\s*(?:to|-)\s*(?P<maximum>\d+)\s*Yrs", re.I)

REQUIRED_EXPERIENCE_PATTERNS = (
    re.compile(
        r"(?:experience(?:\s+required)?|required\s+experience)\s*[:\-]\s*"
        r"(?P<minimum>\d+)\s*(?:to|-)\s*(?P<maximum>\d+)\s*(?:years?|yrs?)",
        re.I,
    ),
    re.compile(
        r"(?P<minimum>\d+)\s*(?:to|-)\s*(?P<maximum>\d+)\s*(?:years?|yrs?)"
        r"(?:\s+of)?(?:\s+[a-z][a-z0-9/+.-]*){0,8}\s+experience",
        re.I,
    ),
    re.compile(
        r"(?:minimum(?:\s+of)?|at\s+least)\s*(?P<minimum>\d+)\+?\s*"
        r"(?:years?|yrs?)",
        re.I,
    ),
    re.compile(
        r"(?P<minimum>\d+)\+\s*(?:years?|yrs?)(?:\s+of)?"
        r"(?:\s+[a-z][a-z0-9/+.-]*){0,8}\s+experience",
        re.I,
    ),
    re.compile(
        r"(?P<minimum>\d+)\s+(?:years?|yrs?)\s+of"
        r"(?:\s+[a-z][a-z0-9/+.-]*){0,8}\s+experience",
        re.I,
    ),
)


def parse_experience(text: str) -> tuple[int | None, int | None]:
    """Extract a Shine experience range such as ``2 to 6 Yrs``."""
    match = EXPERIENCE_RE.search(text)
    if not match:
        single = re.search(r"(?<!\d)(\d+)\s*Yrs", text, re.I)
        value = int(single.group(1)) if single else None
        return value, value
    return int(match.group("minimum")), int(match.group("maximum"))


def parse_required_experience(text: str) -> tuple[int | None, int | None]:
    """Extract explicit experience requirements from a full description.

    Shine's summary range can disagree with wording such as ``7+ years of
    professional software engineering experience`` in the description. Values
    above 15 are ignored because broken typography can collapse ``3-5`` into
    ``35`` and a company's age is not a candidate requirement.
    """

    requirements: list[tuple[int, int | None]] = []
    for pattern in REQUIRED_EXPERIENCE_PATTERNS:
        for match in pattern.finditer(text):
            minimum = int(match.group("minimum"))
            maximum_text = match.groupdict().get("maximum")
            maximum = int(maximum_text) if maximum_text else None
            if minimum > 15 or (maximum is not None and maximum > 15):
                continue
            if maximum is not None and maximum < minimum:
                continue
            requirements.append((minimum, maximum))

    if not requirements:
        return None, None
    return max(requirements, key=lambda item: item[0])
