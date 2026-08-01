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


def parse_experience(text: str) -> tuple[int | None, int | None]:
    """Extract a Shine experience range such as ``2 to 6 Yrs``."""
    match = EXPERIENCE_RE.search(text)
    if not match:
        single = re.search(r"(?<!\d)(\d+)\s*Yrs", text, re.I)
        value = int(single.group(1)) if single else None
        return value, value
    return int(match.group("minimum")), int(match.group("maximum"))
