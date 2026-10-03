"""Shine job discovery, scoring, application, and audit-report workflow.

The automation confirms every successful application from Shine's visible
``Applied`` state. Unsupported questions, redirects, and timeouts are isolated
to one job and written to the manual-review queue.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os
import random
import re
import tempfile
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from contextlib import contextmanager

from dotenv import load_dotenv
from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Response,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

import config
from detail_progress import DetailProgress
from run_reporting import application_limit_status, build_run_summary, final_run_status
from scoring import (
    Job,
    ScoreResult,
    has_early_applicant_badge,
    contains_phrase,
    normalize,
    parse_experience,
    parse_required_experience,
    preliminary_job_priority,
    preliminary_job_rejection_reason,
    score_job,
)

BASE_URL = "https://www.shine.com"
ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state"
ARTIFACT_DIR = ROOT / "artifacts"
HISTORY_FILE = STATE_DIR / "history.json"
ATTEMPTS_FILE = STATE_DIR / "attempts.json"
REPORT_FILE = ARTIFACT_DIR / "latest.csv"
SCORED_AND_APPLIED_FILE = ARTIFACT_DIR / "scored-and-applied.json"
MANUAL_REVIEW_FILE = ARTIFACT_DIR / "manual-review.json"
SEARCH_CARD_SELECTOR = 'article[itemprop="itemListElement"], article[class*="result-card_card__"], div.jdbigCard'


# ---------------------------------------------------------------------------
# Environment and user-supplied application facts
# ---------------------------------------------------------------------------


class ManualReviewRequired(RuntimeError):
    """A job needs a truthful answer or a website flow the bot does not know."""


class DiscoveryError(RuntimeError):
    """A search failed to expose reliable results; it was not an empty search."""


def require_open_browser(page: Page) -> None:
    """A dead session is a run failure, not a failure of every remaining job."""
    browser = page.context.browser
    if page.is_closed() or (browser is not None and not browser.is_connected()):
        raise RuntimeError("Browser session closed; run stopped before attempting more jobs")


@dataclass(frozen=True)
class ApplicationAnswers:
    experience_years: int | None
    experience_months: int | None
    current_salary_lpa: int | None
    expected_salary_lpa: int | None
    notice_period_days: int | None

    @classmethod
    def from_environment(cls) -> "ApplicationAnswers":
        return cls(
            experience_years=env_optional_int("CANDIDATE_EXPERIENCE_YEARS"),
            experience_months=env_optional_int("CANDIDATE_EXPERIENCE_MONTHS"),
            current_salary_lpa=env_optional_int("CURRENT_SALARY_LPA"),
            expected_salary_lpa=env_optional_int("EXPECTED_SALARY_LPA"),
            notice_period_days=env_optional_int("NOTICE_PERIOD_DAYS"),
        )


@dataclass(frozen=True)
class ApplicationOutcome:
    """A job result together with the evidence used to confirm it."""

    status: str
    job_id: str
    confirmation_method: str
    verified_at: str
    response_status: int | None = None
    response_job_id: str | None = None

    def confirmation_record(self) -> dict:
        return {
            "method": self.confirmation_method,
            "job_id": self.job_id,
            "verified_at": self.verified_at,
            "response_status": self.response_status,
            "response_job_id": self.response_job_id,
        }


_QUESTION_PREFIX = r"^\s*(?:\d+[.)]\s*)?"
EXPERIENCE_FIELD_PATTERN = re.compile(
    _QUESTION_PREFIX
    + r"(?:total\s+)?(?:work\s+)?experience(?:\s+in\s+years)?\s*\*?$",
    re.I,
)
CURRENT_SALARY_FIELD_PATTERN = re.compile(
    _QUESTION_PREFIX
    + r"(?:(?:what\s+is\s+your\s+)?current\s+(?:annual\s+)?(?:salary|ctc)|"
    r"total\s+annual\s+salary).*",
    re.I,
)
EXPECTED_SALARY_FIELD_PATTERN = re.compile(
    _QUESTION_PREFIX
    + r"(?:what\s+is\s+your\s+)?expected\s+(?:annual\s+)?(?:salary|ctc).*",
    re.I,
)
NOTICE_PERIOD_FIELD_PATTERN = re.compile(
    _QUESTION_PREFIX + r"(?:what\s+is\s+your\s+)?notice\s+period.*",
    re.I,
)


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true/false, yes/no, on/off, or 1/0")


def atomic_write_text(path: Path, text: str) -> None:
    """Write a same-directory temporary file, then atomically replace the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def atomic_write_json(path: Path, payload: object) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))


@contextmanager
def single_instance_run_lock(path: Path):
    """Prevent concurrent runs from racing duplicate and daily-limit state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            raise RuntimeError(
                "Another Shine automation process is already using the workspace state"
            ) from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def env_optional_int(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a whole number") from exc
    if parsed < 0:
        raise RuntimeError(f"{name} cannot be negative")
    return parsed


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def error_screenshot_path(title: str, url: str) -> Path:
    """Return a stable, collision-resistant screenshot path for one job."""

    title_slug = slugify(title)[:45] or "job"
    job_key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    return ARTIFACT_DIR / f"error-{title_slug}-{job_key}.png"


# ---------------------------------------------------------------------------
# Persistent history and duplicate protection
# ---------------------------------------------------------------------------


def load_history() -> dict[str, dict]:
    if not HISTORY_FILE.exists():
        return {}
    return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))


def save_history(history: dict[str, dict]) -> None:
    atomic_write_json(HISTORY_FILE, history)


def load_attempts() -> dict[str, dict]:
    if not ATTEMPTS_FILE.exists():
        return {}
    return json.loads(ATTEMPTS_FILE.read_text(encoding="utf-8"))


def save_attempts(attempts: dict[str, dict]) -> None:
    atomic_write_json(ATTEMPTS_FILE, attempts)


def history_entry_is_verified(item: dict) -> bool:
    """Return whether a saved success has job-specific confirmation evidence."""

    if item.get("status") not in {"applied", "already_applied"}:
        return False
    confirmation = item.get("confirmation")
    if not isinstance(confirmation, dict):
        return False
    return all(
        str(confirmation.get(field, "")).strip()
        for field in ("method", "job_id", "verified_at")
    )


def history_entry_hold_status(item: dict | None) -> tuple[str, str] | None:
    """Hold legacy apparent successes until a human verifies the Shine state."""
    if not item:
        return None
    if history_entry_is_verified(item):
        return "already_seen", "verified application already exists in history"
    if item.get("status") in {"applied", "already_applied"}:
        return (
            "manual_review_pending",
            "legacy application record has no confirmation; verify before retrying",
        )
    return None


def failure_is_transient(reason: str) -> bool:
    """Return whether a failure is safe to retry after a cooldown."""
    normalized = reason.lower()
    return any(
        signal in normalized
        for signal in (
            "timeout",
            "timed out",
            "exceeded",
            "net::",
            "connection",
            "temporarily unavailable",
        )
    )


def record_failed_attempt(
    attempts: dict[str, dict],
    job: Job,
    reason: str,
    now: datetime,
    retry_delay_hours: int,
    maximum_transient_attempts: int,
) -> dict:
    """Record cooldown/manual-only state without polluting success history."""
    previous = attempts.get(job.url, {})
    attempt_count = int(previous.get("attempt_count", 0)) + 1
    transient = failure_is_transient(reason)
    manual_only = not transient or attempt_count >= maximum_transient_attempts
    retry_after = (
        None
        if manual_only
        else (now + timedelta(hours=retry_delay_hours)).isoformat()
    )
    entry = {
        "title": job.title,
        "company": job.company,
        "status": "manual_only" if manual_only else "retry_scheduled",
        "failure_reason": reason,
        "last_attempted_at": now.isoformat(),
        "retry_after": retry_after,
        "attempt_count": attempt_count,
    }
    attempts[job.url] = entry
    return entry


def attempt_hold_status(entry: dict | None, now: datetime) -> tuple[str, str] | None:
    """Describe why an unresolved job must not be processed in this run."""
    if not entry:
        return None
    if entry.get("status") == "manual_only":
        return "manual_review_pending", "job requires manual completion"

    retry_after_text = str(entry.get("retry_after") or "")
    if not retry_after_text:
        return None
    try:
        retry_after = datetime.fromisoformat(retry_after_text)
    except ValueError:
        return "manual_review_pending", "invalid retry state requires manual review"
    if retry_after.tzinfo is None:
        retry_after = retry_after.astimezone()
    if now < retry_after:
        return "retry_cooldown", f"automatic retry is paused until {retry_after.isoformat()}"
    return None


def applications_today(history: dict[str, dict]) -> int:
    today = date.today().isoformat()
    return sum(
        1
        for item in history.values()
        # Count unverified legacy successes conservatively for the daily cap:
        # they are held for human review, but may still be real submissions.
        if item.get("status") in {"applied", "already_applied"}
        and item.get("applied_at", "").startswith(today)
    )


# ---------------------------------------------------------------------------
# Shine login and job discovery
# ---------------------------------------------------------------------------


def _is_shine_url(url: str) -> bool:
    """Accept only HTTPS pages owned by shine.com or one of its subdomains."""
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (
        hostname == "shine.com" or hostname.endswith(".shine.com")
    )


async def login(page: Page, email: str, password: str, navigation_timeout_ms: int) -> None:
    await page.goto(
        f"{BASE_URL}/pages/myshine/login",
        wait_until="domcontentloaded",
        timeout=navigation_timeout_ms,
    )
    password_tab = page.get_by_role("button", name="Login Via Password", exact=True)
    if await password_tab.count() == 1:
        await password_tab.click()

    await page.get_by_label("Email", exact=True).fill(email)
    await page.get_by_label("Password", exact=True).fill(password)
    await page.get_by_role("button", name="Log In", exact=True).click()
    # Waiting for the current page's load state returns immediately because the
    # login page is already loaded. Wait for Shine's actual redirect instead.
    try:
        await page.wait_for_url(
            "**/dashboard",
            wait_until="domcontentloaded",
            timeout=navigation_timeout_ms,
        )
    except PlaywrightTimeoutError as exc:
        text = (await page.locator("body").inner_text()).lower()
        if "captcha" in text or "one time password" in text or "enter otp" in text:
            raise RuntimeError("Shine requires CAPTCHA/OTP. Complete verification manually.") from exc
        raise RuntimeError("Login did not reach the dashboard. Check credentials or finish verification manually.") from exc

    page_text = (await page.locator("body").inner_text()).lower()
    if "captcha" in page_text or "one time password" in page_text or "enter otp" in page_text:
        raise RuntimeError("Shine requires CAPTCHA/OTP. Complete it manually, then rerun.")
    if "/login" in page.url:
        raise RuntimeError("Login did not complete. Check credentials or finish any verification manually.")


async def wait_for_search_results(page: Page, timeout_ms: int) -> str:
    """Wait for hydrated cards or a real empty result, never just DOMContentLoaded."""
    try:
        state = await page.wait_for_function(
            r"""selector => {
              const visible = e => e.getClientRects().length > 0;
              const cards = [...document.querySelectorAll(selector)].filter(visible);
              const ready = cards.filter(e => !e.closest('[inert], [aria-busy="true"]')
                && !e.className.includes('is-placeholder'));
              const text = document.body?.innerText || '';
              if (/verify (?:you are|you're) human|access denied|unusual traffic|complete the captcha/i.test(text)) return 'blocked';
              if (ready.some(e => e.querySelector('a[href*="/jobs/"]'))) return 'ready';
              if (!cards.length && /\b0 jobs found\b|\bno jobs found\b|\bno jobs match(?:ed|ing)?\b/i.test(text)) return 'empty';
              return false;
            }""",
            arg=SEARCH_CARD_SELECTOR,
            timeout=timeout_ms,
        )
    except PlaywrightTimeoutError as exc:
        raise DiscoveryError(
            "Search results did not become ready; layout changed or loading stalled"
        ) from exc
    result = await state.json_value()
    await state.dispose()
    if result == "blocked":
        raise DiscoveryError("Shine blocked the search or requires human verification")
    return result


async def extract_jobs(page: Page) -> list[Job]:
    """Read legacy and redesigned Shine cards; links may have no visible text."""
    cards = await page.locator(SEARCH_CARD_SELECTOR).evaluate_all(
        """cards => cards.filter(card => card.getClientRects().length > 0
          && !card.closest('[inert], [aria-busy="true"]')
          && !card.className.includes('is-placeholder')).map(card => {
          const link = card.querySelector('a[href*="/jobs/"]');
          const title = card.querySelector('[itemprop="name"], h2, h3')?.textContent?.trim() || link?.textContent?.trim() || '';
          const company = card.querySelector('[class*="result-card_company__"], .jdTruncationCompany, [class*="CompanyName"], [class*="companyName"]')?.textContent?.trim() || '';
          const skills = [...card.querySelectorAll('[class*="result-card_skills-item__"], li')]
            .map(x => x.textContent.replace(/^[\\s·]+/, '').trim()).filter(Boolean);
          const earlyLabels = new Set(['early applicant', 'be an early applicant']);
          const earlyApplicant = [...card.querySelectorAll('span, p, div, li, [class*="badge"], [class*="chip"]')]
            .some(element => earlyLabels.has((element.innerText || element.textContent || '')
              .replaceAll(String.fromCharCode(160), ' ').trim().toLowerCase()
              .split(' ').filter(Boolean).join(' ')));
          return {title, company, url: link?.href || '', text: card.innerText || '', skills, earlyApplicant};
        }).filter(x => x.title && x.url)"""
    )
    jobs: dict[str, Job] = {}
    for card in cards:
        resolved_url = urljoin(BASE_URL, card["url"])
        # Search results must never introduce an off-site application URL.
        parsed = urlsplit(resolved_url)
        if not _is_shine_url(resolved_url) or not re.fullmatch(r"/jobs/.+/\d+/?", parsed.path):
            continue
        # Tracking parameters and fragments must not create duplicate applications.
        resolved_url = parsed._replace(path=parsed.path.rstrip("/"), query="", fragment="").geturl()
        minimum, maximum = parse_experience(card["text"])
        jobs[resolved_url] = Job(
                title=card["title"],
                company=card["company"],
                url=resolved_url,
                text=card["text"],
                skills=tuple(card["skills"]),
                min_experience=minimum,
                max_experience=maximum,
                is_early_applicant=(
                    card["earlyApplicant"] or has_early_applicant_badge(card["text"])
                    or (resolved_url in jobs and jobs[resolved_url].is_early_applicant)
                ),
        )
    return list(jobs.values())


async def discover(
    page: Page,
    max_pages: int,
    navigation_timeout_ms: int,
    delay_min_seconds: int,
    delay_max_seconds: int,
) -> tuple[list[Job], list[dict]]:
    """Search at a moderate pace and record each query's unique contribution."""
    discovered: dict[str, Job] = {}
    search_metrics: list[dict] = []
    navigation_count = 0
    for query in sorted(config.SEARCH_QUERIES):
        metric = {
            "query": query,
            "pages_visited": 0,
            "cards_found": 0,
            "unique_jobs_added": 0,
            "pages": [],
        }
        base_slug = f"{slugify(query)}-jobs"
        previous_page_urls: set[str] | None = None
        for page_number in range(1, max_pages + 1):
            require_open_browser(page)
            if navigation_count:
                delay_ms = random.randint(delay_min_seconds, delay_max_seconds) * 1_000
                await page.wait_for_timeout(delay_ms)
            suffix = "" if page_number == 1 else f"-{page_number}"
            target_url = f"{BASE_URL}/job-search/{base_slug}{suffix}"
            page_metric = {"page": page_number, "url": target_url}
            metric["pages"].append(page_metric)
            metric["pages_visited"] += 1
            navigation_count += 1
            try:
                started = asyncio.get_running_loop().time()
                try:
                    response = await page.goto(
                        target_url,
                        wait_until="domcontentloaded",
                        timeout=navigation_timeout_ms,
                    )
                    if response is not None and response.status >= 400:
                        raise DiscoveryError(f"Search returned HTTP {response.status}")
                except PlaywrightTimeoutError:
                    metric["navigation_timeouts"] = metric.get("navigation_timeouts", 0) + 1
                    if not _same_job_path(target_url, page.url):
                        raise DiscoveryError("Search navigation timed out before reaching its destination")
                if not _same_job_path(target_url, page.url):
                    raise DiscoveryError(f"Search redirected to {page.url}")
                state = await wait_for_search_results(page, navigation_timeout_ms)
                page_jobs = await extract_jobs(page) if state == "ready" else []
                if state == "ready" and not page_jobs:
                    raise DiscoveryError("Cards are visible but no valid Shine job links could be extracted")
                page_urls = {job.url for job in page_jobs}
                if page_urls and page_urls == previous_page_urls:
                    raise DiscoveryError("Pagination repeated the previous page; stopping this search")
                previous_page_urls = page_urls
                page_metric.update(status=state, cards_found=len(page_jobs), seconds=round(asyncio.get_running_loop().time() - started, 2))
            except Exception as exc:
                require_open_browser(page)
                page_metric.update(status="failed", error=str(exc).strip() or type(exc).__name__)
                print(f"Search failed: {query}, page {page_number}: {page_metric['error']}", flush=True)
                break
            unique_before = len(discovered)
            for job in page_jobs:
                previous = discovered.get(job.url)
                if previous is not None and previous.is_early_applicant:
                    job = replace(job, is_early_applicant=True)
                discovered[job.url] = job
            metric["cards_found"] += len(page_jobs)
            metric["unique_jobs_added"] += len(discovered) - unique_before
            print(f"Search: {query}, page {page_number}: {len(page_jobs)} jobs ({len(discovered) - unique_before} new)", flush=True)
            if state == "empty":
                break
        search_metrics.append(metric)
    failed_pages = sum(p["status"] == "failed" for m in search_metrics for p in m["pages"])
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    diagnostic_path = ARTIFACT_DIR / "search-diagnostics.json"
    atomic_write_json(diagnostic_path, {
        "generated_at": datetime.now().astimezone().isoformat(),
        "status": "partial_failure" if discovered and failed_pages else "failed" if failed_pages else "complete",
        "unique_jobs": len(discovered),
        "failed_pages": failed_pages,
        "search_metrics": search_metrics,
    })
    if not discovered and failed_pages:
        raise DiscoveryError(f"Job discovery failed; see {diagnostic_path}. Previous job reports were preserved.")
    return list(discovered.values()), search_metrics


def merge_job_details(
    job: Job,
    description: str,
    detail_skills: list[str],
    highlights: str,
) -> Job:
    """Replace incomplete card fields with content from the actual job page."""
    highlight_minimum, highlight_maximum = parse_experience(highlights)
    description_minimum, description_maximum = parse_required_experience(description)
    # A complete range in the full description is more informative than a
    # compact card value such as "6 Yrs". A single minimum ("6+ years") is
    # different: keep the stricter of it and the card's stated minimum.
    if description_minimum is not None and description_maximum is not None:
        minimum = description_minimum
        maximum = description_maximum
    else:
        minimum_candidates = [
            value for value in (highlight_minimum, description_minimum)
            if value is not None
        ]
        minimum = max(minimum_candidates) if minimum_candidates else job.min_experience
        maximum = highlight_maximum
        if description_minimum is not None and description_minimum == minimum:
            maximum = description_maximum
        if maximum is None and description_minimum is None:
            maximum = job.max_experience
    if maximum is not None and minimum is not None and maximum < minimum:
        maximum = None
    unique_skills = tuple(dict.fromkeys(skill.strip() for skill in detail_skills if skill.strip()))
    return Job(
        title=job.title,
        company=job.company,
        url=job.url,
        text="\n".join(part for part in (description.strip(), highlights.strip()) if part),
        skills=unique_skills,
        min_experience=minimum,
        max_experience=maximum,
        is_early_applicant=job.is_early_applicant,
    )


async def extract_job_detail(
    page: Page, job: Job, navigation_timeout_ms: int
) -> Job:
    """Load one Shine job page and extract its description, skills, and experience."""
    await page.goto(job.url, wait_until="domcontentloaded", timeout=navigation_timeout_ms)
    if not _is_shine_url(page.url):
        raise ManualReviewRequired(
            f"Job detail redirected outside Shine ({page.url}); the external page was not used"
        )
    if not _same_job_path(job.url, page.url):
        raise ManualReviewRequired(f"Job detail redirected to {page.url}")

    description_heading = page.get_by_role("heading", name="Job Description", exact=True)
    await description_heading.wait_for(state="visible", timeout=navigation_timeout_ms)
    if await description_heading.count() != 1:
        raise RuntimeError("Expected one Job Description heading")
    description_scope = description_heading.locator("xpath=following-sibling::*[1]")
    if await description_scope.count() != 1:
        raise RuntimeError("Job Description content was not found")
    description = (await description_scope.inner_text()).strip()
    if not description:
        raise RuntimeError("Job Description is empty")

    highlights = ""
    highlights_heading = page.get_by_role("heading", name="Key Highlights", exact=True)
    if await highlights_heading.count() == 1:
        highlights_scope = highlights_heading.locator("xpath=following-sibling::*[1]")
        if await highlights_scope.count() == 1:
            highlights = (await highlights_scope.inner_text()).strip()

    detail_skills: list[str] = []
    skills_label = page.get_by_text("SKILLS", exact=True)
    if await skills_label.count() == 1:
        skills_scope = skills_label.locator("xpath=following-sibling::*[1]")
        if await skills_scope.count() == 1:
            detail_skills = await skills_scope.locator("a, li").all_inner_texts()
            if not detail_skills:
                detail_skills = (await skills_scope.inner_text()).splitlines()

    return merge_job_details(job, description, detail_skills, highlights)


def select_detail_candidates(
    jobs: list[Job], maximum: int, *, previous_checks: dict[str, float] | None = None,
) -> tuple[list[Job], dict[str, str]]:
    """Reach unseen candidates before revisiting recently checked descriptions.

    Within the same coverage group, prefer Early Applicant cards and then fit.
    The badge never exempts a job from title/experience or final scoring rules.
    """
    if maximum < 0:
        raise ValueError("Detail limit cannot be negative")
    previous_checks = previous_checks or {}
    ranked_candidates: list[tuple[int, Job]] = []
    skipped: dict[str, str] = {}
    for job in jobs:
        rejection_reason = preliminary_job_rejection_reason(job)
        priority = preliminary_job_priority(job)
        if rejection_reason is not None or priority is None:
            skipped[job.url] = rejection_reason or "preliminary rules excluded this card"
        else:
            ranked_candidates.append((priority, job))

    ranked_candidates.sort(key=lambda pair: (
        previous_checks.get(pair[1].url, 0),
        not pair[1].is_early_applicant,
        -pair[0],
        pair[1].url,
    ))
    selected = [job for _, job in ranked_candidates[:maximum]]
    for _, job in ranked_candidates[maximum:]:
        skipped[job.url] = f"detail-scoring limit reached ({maximum} jobs)"
    return selected, skipped


async def score_detailed_jobs(
    page: Page,
    jobs: list[Job],
    maximum: int,
    navigation_timeout_ms: int,
    detail_timeout_seconds: int,
    *,
    previous_checks: dict[str, float] | None = None,
) -> tuple[list[tuple[ScoreResult, Job]], dict[str, str]]:
    """Score only enriched jobs and surface detail failures for manual review."""
    selected, skipped = select_detail_candidates(jobs, maximum, previous_checks=previous_checks)
    enriched: dict[str, Job] = {}
    failures: dict[str, str] = {}

    for index, job in enumerate(selected, 1):
        require_open_browser(page)
        started = asyncio.get_running_loop().time()
        try:
            enriched[job.url] = await asyncio.wait_for(
                extract_job_detail(page, job, navigation_timeout_ms),
                timeout=detail_timeout_seconds,
            )
        except TimeoutError:
            failures[job.url] = (
                f"job-detail extraction exceeded {detail_timeout_seconds} seconds"
            )
        except Exception as exc:
            require_open_browser(page)
            failures[job.url] = f"job-detail extraction failed: {str(exc).strip() or type(exc).__name__}"
        elapsed = asyncio.get_running_loop().time() - started
        outcome = "needs review" if job.url in failures else "read"
        print(f"Details {index}/{len(selected)}: {outcome} in {elapsed:.1f}s — {job.title}", flush=True)

    scored: list[tuple[ScoreResult, Job]] = []
    statuses: dict[str, str] = {}
    for job in jobs:
        if job.url in enriched:
            detailed_job = enriched[job.url]
            scored.append((score_job(detailed_job), detailed_job))
        elif job.url in failures:
            reason = failures[job.url]
            scored.append((ScoreResult(0, False, (reason,)), job))
            statuses[job.url] = f"needs_review: {reason}"
        else:
            reason = skipped.get(job.url, "not selected for detail scoring")
            scored.append((ScoreResult(0, False, (reason,)), job))
            status_prefix = (
                "pre_filtered"
                if preliminary_job_rejection_reason(job) is not None
                else "not_evaluated"
            )
            statuses[job.url] = f"{status_prefix}: {reason}"

    scored.sort(key=lambda pair: (
        pair[0].accepted, pair[0].accepted and pair[1].is_early_applicant, pair[0].score,
    ), reverse=True)
    return scored, statuses


def role_family(job: Job) -> str:
    # Classify from the title, not the entire description: incidental mentions
    # in a long posting must not consume a different role family's quota.
    title = normalize(job.title)
    if re.search(
        r"\bsoftware development engineer\b|\bsde\s*(?:1|2|3|i|ii|iii)\b",
        title,
    ):
        return "sde"
    if any(
        contains_phrase(title, term)
        for term in ("genai", "generative ai", "rag", "llm", "langchain", "agentic ai")
    ):
        return "genai_rag"
    if any(contains_phrase(title, term) for term in ("fastapi", "django", "flask")):
        return "framework_backend"
    if contains_phrase(title, "backend") or contains_phrase(title, "back end"):
        return "backend"
    if contains_phrase(title, "software engineer"):
        return "software_engineering"
    return "other"


# ---------------------------------------------------------------------------
# Supported application cards and manual-review boundaries
# ---------------------------------------------------------------------------


def _option_number(text: str) -> float | None:
    """Return one comparable number from a simple card option."""
    normalized = text.lower().replace(",", "")
    match = re.search(r"\d+(?:\.\d+)?", normalized)
    if not match:
        return None
    number = float(match.group())
    # Salary cards sometimes use a full rupee amount rather than lakhs.
    if number >= 100_000:
        return number / 100_000
    return number


def choose_option_index(options: list[str], target: int, field_name: str) -> int | None:
    """Choose a card option without relying on its position in Shine's list."""
    normalized = [re.sub(r"\s+", " ", option.strip().lower()) for option in options]

    if field_name == "notice period":
        for index, option in enumerate(normalized):
            if target == 0 and any(word in option for word in ("immediate", "not serving")):
                return index
            day_match = re.search(r"(\d+)\s*days?", option)
            month_match = re.search(r"(\d+)\s*months?", option)
            option_days = (
                int(day_match.group(1))
                if day_match
                else int(month_match.group(1)) * 30
                if month_match
                else None
            )
            if option_days == target:
                return index

    # Prefer an exact numeric option such as "4 yrs" or "19 LPA".
    for index, option in enumerate(normalized):
        numbers = [float(value) for value in re.findall(r"\d+(?:\.\d+)?", option.replace(",", ""))]
        if len(numbers) == 1:
            value = _option_number(option)
            if value is not None and value == target:
                return index

    # Then accept a range card, e.g. "15-20 LPA" for a value of 19.
    for index, option in enumerate(normalized):
        numbers = [float(value) for value in re.findall(r"\d+(?:\.\d+)?", option)]
        if len(numbers) >= 2 and numbers[0] < target <= numbers[1]:
            return index
    for index, option in enumerate(normalized):
        numbers = [float(value) for value in re.findall(r"\d+(?:\.\d+)?", option)]
        if len(numbers) >= 2 and numbers[0] <= target < numbers[1]:
            return index
    return None


async def _first_visible(locator: Locator) -> Locator | None:
    count = await locator.count()
    visible: list[Locator] = []
    for index in range(count):
        candidate = locator.nth(index)
        if await candidate.is_visible():
            visible.append(candidate)
    if not visible:
        return None
    return visible[0]


async def _find_field_label(scope: Locator, pattern: re.Pattern[str]) -> Locator | None:
    label = await _first_visible(scope.locator("label").filter(has_text=pattern))
    if label is not None:
        return label
    return await _first_visible(scope.get_by_text(pattern))


async def _select_custom_option(
    page: Page,
    root: Locator,
    target: int,
    field_name: str,
) -> None:
    headers = root.locator('div[class*="customSelect_selectHeader"]')
    header_count = await headers.count()
    if header_count != 1:
        raise ManualReviewRequired(
            f"{field_name} card changed: expected one selector header, found {header_count}"
        )
    await headers.click()

    options = root.locator('div[class*="customSelect_option"]')
    option_count = await options.count()
    if option_count == 0:
        # Some component libraries render the opened list at the end of body.
        options = page.locator('div[class*="customSelect_option"]:visible')
        option_count = await options.count()
    option_texts = [text.strip() for text in await options.all_inner_texts()]
    selected_index = choose_option_index(option_texts, target, field_name)
    if selected_index is None:
        raise ManualReviewRequired(
            f"No truthful {field_name} option matched {target}; available options: "
            + ", ".join(option_texts[:12])
        )
    if selected_index >= option_count:
        raise ManualReviewRequired(f"{field_name} option list changed while selecting")
    await options.nth(selected_index).click()


async def _select_native_option(
    select: Locator, target: int, field_name: str
) -> None:
    option_texts = await select.locator("option").all_inner_texts()
    selected_index = choose_option_index(option_texts, target, field_name)
    if selected_index is None:
        raise ManualReviewRequired(f"No truthful {field_name} option matched {target}")
    await select.select_option(index=selected_index)


async def _field_container(label: Locator, minimum_custom_selects: int = 1) -> Locator:
    # Shine questionnaire cards render a label in one list item and its
    # dropdown in the immediately following list item. Resolve that pair first
    # so a later field cannot accidentally reuse the modal's first dropdown.
    paired_list_item = label.locator(
        "xpath=ancestor::li[1]/following-sibling::li[1]"
        "[.//select or .//div[contains(@class,'customSelect_customSelect')]][1]"
    )
    if await paired_list_item.count():
        return paired_list_item

    custom_xpath = (
        "ancestor::div[count(.//div[contains(@class,'customSelect_customSelect')]) "
        f">= {minimum_custom_selects}][1]"
    )
    container = label.locator(f"xpath={custom_xpath}")
    if await container.count() == 0:
        container = label.locator("xpath=ancestor::div[.//select][1]")
    if await container.count() == 0:
        raise ManualReviewRequired("A known application field is present, but its selector card changed")
    return container


async def _fill_single_known_field(
    page: Page,
    scope: Locator,
    pattern: re.Pattern[str],
    target: int | None,
    environment_name: str,
    field_name: str,
) -> bool:
    label = await _find_field_label(scope, pattern)
    if label is None:
        return False
    if target is None:
        raise ManualReviewRequired(
            f"{field_name.title()} is required, but {environment_name} is empty"
        )
    container = await _field_container(label)
    custom_selects = container.locator('div[class*="customSelect_customSelect"]')
    custom_count = await custom_selects.count()
    if custom_count:
        await _select_custom_option(page, custom_selects.nth(0), target, field_name)
        return True
    selects = container.locator("select")
    select_count = await selects.count()
    if select_count:
        await _select_native_option(selects.nth(0), target, field_name)
        return True
    raise ManualReviewRequired(f"The {field_name} control is not a supported dropdown")


async def complete_known_application_fields(
    page: Page, scope: Locator, answers: ApplicationAnswers
) -> int:
    """Fill only facts supplied by the user; never infer employer answers."""
    handled = 0
    experience_label = await _find_field_label(scope, EXPERIENCE_FIELD_PATTERN)
    if experience_label is not None:
        if answers.experience_years is None or answers.experience_months is None:
            raise ManualReviewRequired(
                "Experience is required, but CANDIDATE_EXPERIENCE_YEARS or "
                "CANDIDATE_EXPERIENCE_MONTHS is empty"
            )
        container = await _field_container(experience_label, minimum_custom_selects=2)
        custom_selects = container.locator('div[class*="customSelect_customSelect"]')
        custom_count = await custom_selects.count()
        if custom_count >= 2:
            await _select_custom_option(
                page, custom_selects.nth(0), answers.experience_years, "experience years"
            )
            await _select_custom_option(
                page, custom_selects.nth(1), answers.experience_months, "experience months"
            )
            handled += 2
        else:
            selects = container.locator("select")
            select_count = await selects.count()
            if select_count < 2:
                raise ManualReviewRequired("The experience card layout changed")
            await _select_native_option(selects.nth(0), answers.experience_years, "experience years")
            await _select_native_option(selects.nth(1), answers.experience_months, "experience months")
            handled += 2

    known_fields = (
        (
            CURRENT_SALARY_FIELD_PATTERN,
            answers.current_salary_lpa,
            "CURRENT_SALARY_LPA",
            "current salary",
        ),
        (
            EXPECTED_SALARY_FIELD_PATTERN,
            answers.expected_salary_lpa,
            "EXPECTED_SALARY_LPA",
            "expected salary",
        ),
        (
            NOTICE_PERIOD_FIELD_PATTERN,
            answers.notice_period_days,
            "NOTICE_PERIOD_DAYS",
            "notice period",
        ),
    )
    for pattern, target, environment_name, field_name in known_fields:
        if await _fill_single_known_field(
            page, scope, pattern, target, environment_name, field_name
        ):
            handled += 1
    return handled


def _is_supported_application_field_label(value: str) -> bool:
    """Match only labels the bot can safely fill from explicit user facts."""
    label = re.sub(r"\s+", " ", value).strip()
    return any(
        pattern.fullmatch(label)
        for pattern in (
            EXPERIENCE_FIELD_PATTERN,
            CURRENT_SALARY_FIELD_PATTERN,
            EXPECTED_SALARY_FIELD_PATTERN,
            NOTICE_PERIOD_FIELD_PATTERN,
        )
    )


async def _application_scope(page: Page) -> Locator | None:
    candidates = page.locator(
        '[role="dialog"]:visible, form:visible, div[class*="modal"]:visible, '
        'div[class*="Modal"]:visible'
    )
    count = await candidates.count()
    for index in range(count):
        candidate = candidates.nth(index)
        text = (await candidate.inner_text()).lower()
        if any(
            signal in text
            for signal in (
                "current salary",
                "total annual salary",
                "expected salary",
                "experience",
                "notice period",
                "screening question",
                "submit application",
                "answer the following",
            )
        ):
            return candidate
    return None


async def _unknown_required_controls(scope: Locator) -> list[str]:
    controls = scope.locator(
        'input:not([type="hidden"]):not([type="submit"]):not([type="button"]), '
        "textarea, select, [role=radio], [role=checkbox]"
    )
    details: list[str] = []
    count = await controls.count()
    for index in range(count):
        control = controls.nth(index)
        if not await control.is_visible() or await control.is_disabled():
            continue
        description = await control.evaluate(
            """element => {
              const id = element.id;
              const label = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
              const container = element.closest('label, [class*="question"], [class*="field"]');
              return (label?.innerText || element.getAttribute('aria-label') ||
                      element.getAttribute('placeholder') || container?.innerText || '')
                .replace(/\\s+/g, ' ').trim().slice(0, 180);
            }"""
        )
        if not _is_supported_application_field_label(description or ""):
            details.append(description or "unlabelled required control")

    # Custom question cards do not always use input elements. Treat any other
    # visible field label or question group as manual rather than guessing.
    labels = scope.locator('label:visible, legend:visible, [class*="question"]:visible')
    label_count = await labels.count()
    for index in range(label_count):
        candidate = labels.nth(index)
        tag_name = await candidate.evaluate("element => element.tagName.toLowerCase()")
        if tag_name not in {"label", "legend"}:
            interactive_children = candidate.locator(
                'input, textarea, select, [role="radio"], [role="checkbox"], '
                'div[class*="customSelect_customSelect"]'
            )
            if await interactive_children.count() == 0:
                # Shine's modal heading class contains "question" even though
                # the heading is not an application field.
                continue
        description = re.sub(r"\s+", " ", (await candidate.inner_text()).strip())
        if description and not any(
            _is_supported_application_field_label(line)
            for line in description.splitlines()
        ):
            details.append(description[:180])
    return list(dict.fromkeys(details))


def _same_job_path(before: str, after: str) -> bool:
    first = urlsplit(before)
    second = urlsplit(after)
    return (first.netloc.lower(), first.path.rstrip("/")) == (
        second.netloc.lower(),
        second.path.rstrip("/"),
    )


async def _visible_exact_button(scope: Locator, names: tuple[str, ...]) -> Locator | None:
    for name in names:
        locator = scope.get_by_role("button", name=name, exact=True)
        button = await _first_visible(locator)
        if button is not None:
            return button
    return None


async def _reject_new_application_tabs(
    page: Page,
    pages_before_click: tuple[Page, ...],
    observed_pages: list[Page] | None = None,
) -> None:
    """Close any new tab and require manual review without interacting with it."""
    candidates = list(observed_pages or []) + list(page.context.pages)
    new_pages: list[Page] = []
    for candidate in candidates:
        if candidate in pages_before_click or candidate in new_pages:
            continue
        new_pages.append(candidate)
    if not new_pages:
        return

    popup = new_pages[0]
    popup_url = popup.url
    try:
        await popup.close()
    except Exception:
        pass

    if not _is_shine_url(popup_url):
        raise ManualReviewRequired(
            f"Application opened an external website ({popup_url or 'unknown URL'}); "
            "the external page was not used"
        )
    raise ManualReviewRequired(
        f"Application opened a separate Shine tab ({popup_url}); complete it manually"
    )


def _matches_job_apply_response(response: Response, job_id: str) -> bool:
    """Match only Shine's application response for the current job ID."""

    parsed = urlsplit(response.url)
    if not _is_shine_url(response.url):
        return False
    if not parsed.path.rstrip("/").endswith("/job-apply"):
        return False
    if response.request.method.upper() != "POST":
        return False
    try:
        payload = response.request.post_data_json
    except Exception:
        return False
    return isinstance(payload, dict) and str(payload.get("job_id", "")) == str(job_id)


async def _validate_job_apply_response(response: Response, job_id: str) -> tuple[int, str]:
    """Require a successful response body for exactly the requested job."""

    if response.status not in {200, 201}:
        raise ManualReviewRequired(
            f"Shine application API returned HTTP {response.status} for the current job"
        )
    try:
        payload = await response.json()
    except Exception as exc:
        raise ManualReviewRequired(
            "Shine application API returned an unreadable confirmation"
        ) from exc
    response_job_id = str(payload.get("job_id", "")) if isinstance(payload, dict) else ""
    if response_job_id != str(job_id):
        raise ManualReviewRequired(
            "Shine application confirmation referenced a different job ID"
        )
    return response.status, response_job_id


def _current_job_applied_button(page: Page) -> Locator:
    """Locate the disabled Applied button in the primary job-detail card only."""

    return page.locator('button[class*="jdCard_jdBtn"]').filter(
        has_text=re.compile(r"^\s*Applied\s*$", re.I)
    )


async def _current_job_is_applied(page: Page) -> bool:
    button = _current_job_applied_button(page)
    if await button.count() != 1:
        return False
    return await button.is_visible() and await button.is_disabled()


async def _verify_persisted_application(
    page: Page,
    job: Job,
    navigation_timeout_ms: int,
    authentication_timeout_ms: int,
) -> None:
    """Reload the job and require its primary card to remain Applied."""

    await page.goto(job.url, wait_until="domcontentloaded", timeout=navigation_timeout_ms)
    if not _is_shine_url(page.url) or not _same_job_path(job.url, page.url):
        raise ManualReviewRequired(
            f"Could not recheck the applied job because it redirected to {page.url}"
        )
    await page.get_by_text("skills matched with your profile", exact=False).wait_for(
        state="visible", timeout=authentication_timeout_ms
    )
    applied = _current_job_applied_button(page)
    try:
        await applied.wait_for(state="visible", timeout=authentication_timeout_ms)
    except Exception as exc:
        raise ManualReviewRequired(
            "Shine accepted the application request but Applied did not persist after reload"
        ) from exc
    if await applied.count() != 1 or not await applied.is_disabled():
        raise ManualReviewRequired(
            "Shine accepted the application request but the current job is not persistently Applied"
        )


async def _wait_for_application_response(
    response_future: asyncio.Future[Response], timeout_ms: int
) -> Response | None:
    if response_future.done():
        return response_future.result()
    try:
        return await asyncio.wait_for(
            asyncio.shield(response_future),
            timeout=max(timeout_ms, 0) / 1_000,
        )
    except TimeoutError:
        return None


async def apply_to_job(
    page: Page,
    job: Job,
    navigation_timeout_ms: int,
    authentication_timeout_ms: int,
    apply_timeout_ms: int,
    answers: ApplicationAnswers,
) -> ApplicationOutcome:
    if not _is_shine_url(job.url):
        raise ManualReviewRequired(
            f"Job URL is outside the Shine portal ({job.url}); no external page was opened"
        )
    await page.goto(
        job.url,
        wait_until="domcontentloaded",
        timeout=navigation_timeout_ms,
    )
    if not _is_shine_url(page.url):
        raise ManualReviewRequired(
            f"Job navigation left the Shine portal ({page.url}); the external page was not used"
        )
    if not _same_job_path(job.url, page.url):
        raise ManualReviewRequired(f"Job navigation redirected to {page.url}")

    # Shine initially renders the signed-out version of a job page and then
    # hydrates the authenticated state. Do not click until that transition is
    # complete; otherwise Apply can redirect back to login.
    authenticated_profile = page.get_by_text(
        "skills matched with your profile", exact=False
    )
    await authenticated_profile.wait_for(
        state="visible", timeout=authentication_timeout_ms
    )

    job_id = urlsplit(job.url).path.rstrip("/").rsplit("/", 1)[-1]
    if await _current_job_is_applied(page):
        return ApplicationOutcome(
            status="already_applied",
            job_id=job_id,
            confirmation_method="persisted_current_job_button",
            verified_at=datetime.now().astimezone().isoformat(),
        )

    button = page.locator(f'button[id="id_apply_{job_id}"]')
    count = await button.count()
    if count != 1:
        raise RuntimeError(f"Expected one primary Apply button; found {count}")

    response_future: asyncio.Future[Response] = asyncio.get_running_loop().create_future()
    pages_before_click = tuple(page.context.pages)
    observed_pages: list[Page] = []
    unsafe_navigation_urls: list[str] = []

    def observe_application_response(response: Response) -> None:
        if response_future.done():
            return
        if _matches_job_apply_response(response, job_id):
            response_future.set_result(response)

    def observe_application_page(candidate: Page) -> None:
        if candidate not in pages_before_click and candidate not in observed_pages:
            observed_pages.append(candidate)

    def observe_application_navigation(frame) -> None:
        if frame != page.main_frame:
            return
        destination = frame.url
        if not _is_shine_url(destination) or not _same_job_path(job.url, destination):
            unsafe_navigation_urls.append(destination)

    async def require_safe_application_destination() -> None:
        await _reject_new_application_tabs(
            page,
            pages_before_click,
            observed_pages,
        )
        if unsafe_navigation_urls:
            destination = unsafe_navigation_urls[-1]
            if not _is_shine_url(destination):
                raise ManualReviewRequired(
                    f"Application redirected to an external website ({destination}); "
                    "the external page was not used"
                )
            raise ManualReviewRequired(f"Application redirected to {destination}")
        if not _is_shine_url(page.url):
            raise ManualReviewRequired(
                f"Application redirected to an external website ({page.url}); "
                "the external page was not used"
            )
        if not _same_job_path(job.url, page.url):
            raise ManualReviewRequired(f"Application redirected to {page.url}")

    async def confirmed_outcome(response: Response) -> ApplicationOutcome:
        response_status, response_job_id = await _validate_job_apply_response(
            response, job_id
        )
        # A submission script can redirect or open an employer tab after the API
        # response arrives. Give that browser work a chance to run and reject it
        # before the exact-job reload below could hide what happened.
        await page.wait_for_timeout(500)
        await require_safe_application_destination()
        await _verify_persisted_application(
            page,
            job,
            navigation_timeout_ms,
            authentication_timeout_ms,
        )
        # Keep the event sentinels active through a short settled-state window
        # after reload. This catches popups or delayed redirects that a one-time
        # URL check before reload would miss.
        await page.wait_for_timeout(500)
        await require_safe_application_destination()
        return ApplicationOutcome(
            status="applied",
            job_id=job_id,
            confirmation_method="shine_api_and_persisted_current_job_button",
            verified_at=datetime.now().astimezone().isoformat(),
            response_status=response_status,
            response_job_id=response_job_id,
        )

    page.on("response", observe_application_response)
    page.on("framenavigated", observe_application_navigation)
    page.context.on("page", observe_application_page)
    try:
        await button.click()
        deadline = asyncio.get_running_loop().time() + apply_timeout_ms / 1_000
        submitted_forms: set[tuple[str, ...]] = set()

        while asyncio.get_running_loop().time() < deadline:
            # Check the origin before reading or clicking anything on the result page.
            await require_safe_application_destination()

            response = await _wait_for_application_response(response_future, 0)
            if response is not None:
                return await confirmed_outcome(response)

            body = (await page.locator("body").inner_text()).lower()
            if any(
                signal in body
                for signal in (
                    "screening question",
                    "answer the following",
                    "additional questions",
                    "employer questions",
                )
            ):
                raise ManualReviewRequired("Employer screening questions require manual review")

            scope = await _application_scope(page)
            if scope is None:
                # A questionnaire may arrive after an asynchronous profile/API
                # check. Keep watching for it as well as the submission response.
                await page.wait_for_timeout(200)
                continue

            # Track question labels rather than selected values or validation
            # messages: an unchanged form must never be submitted twice while
            # its original request is still pending.
            question_labels: list[str] = []
            for pattern in (
                EXPERIENCE_FIELD_PATTERN,
                CURRENT_SALARY_FIELD_PATTERN,
                EXPECTED_SALARY_FIELD_PATTERN,
                NOTICE_PERIOD_FIELD_PATTERN,
            ):
                label = await _find_field_label(scope, pattern)
                if label is not None:
                    question_labels.append(
                        re.sub(r"\s+", " ", (await label.inner_text()).strip().lower())
                    )
            form_key = tuple(question_labels)
            if form_key in submitted_forms:
                await page.wait_for_timeout(200)
                continue

            handled = await complete_known_application_fields(page, scope, answers)
            unknown_controls = await _unknown_required_controls(scope)
            if unknown_controls:
                raise ManualReviewRequired(
                    "Unfamiliar application questions: " + "; ".join(unknown_controls[:3])
                )
            if handled == 0:
                raise ManualReviewRequired("An unfamiliar application form requires manual answers")

            submit = await _visible_exact_button(
                scope,
                (
                    "Submit Application",
                    "Submit and apply",
                    "Submit & Apply",
                    "Submit",
                    "Save & Apply",
                    "Continue",
                    "Next",
                    "Apply Now",
                ),
            )
            if submit is None:
                raise ManualReviewRequired(
                    "Known fields were filled, but no supported submit button was found"
                )
            if asyncio.get_running_loop().time() >= deadline:
                break
            submitted_forms.add(form_key)
            await submit.click()

        await require_safe_application_destination()
        response = await _wait_for_application_response(response_future, 0)
        if response is not None:
            return await confirmed_outcome(response)
        raise ManualReviewRequired(
            "Application timed out without a matching Shine application response"
            + (" after submitting the supported form once" if submitted_forms else " or a supported form")
        )
    finally:
        page.remove_listener("response", observe_application_response)
        page.remove_listener("framenavigated", observe_application_navigation)
        page.context.remove_listener("page", observe_application_page)


_CONFIRMED_APPLICATION_STATUSES = {"applied", "already_applied", "already_seen"}


def _report_status(row: dict) -> tuple[str, str]:
    status = str(row.get("status", "unknown")).strip()
    group, separator, detail = status.partition(":")
    return group.strip(), detail.strip() if separator else ""


def _report_reason(row: dict) -> str:
    group, detail = _report_status(row)
    if group == "shortlisted":
        return "Dry-run preview only; no application was submitted."
    if group == "rejected":
        reasons = str(row.get("reasons", "")).strip()
        return f"Did not meet the matching rules: {reasons}" if reasons else "Did not meet the matching rules."
    if group == "pre_filtered":
        return detail or "Skipped by preliminary title or experience rules; the full description was not checked."
    if group == "not_evaluated":
        return detail or "The full description was not checked in this run."
    if group == "needs_review":
        return detail or "The application outcome needs manual verification."
    if group in {"run_limit", "daily_limit", "both_application_limits", "daily_or_run_limit"}:
        return detail or "An application limit prevented this application."
    if group == "role_family_limit":
        return "The application limit for this role family was reached."
    if group == "retry_cooldown":
        reasons = str(row.get("reasons", "")).strip()
        return detail or reasons or "An automatic retry is paused until its cooldown expires."
    if group == "manual_review_pending":
        reasons = str(row.get("reasons", "")).strip()
        return detail or reasons or "This job needs manual verification or completion."
    if detail:
        return detail
    return f"No confirmed application was recorded (status: {group or 'unknown'})."


def _report_next_step(row: dict) -> str:
    group, _ = _report_status(row)
    if group == "shortlisted":
        return "This was a preview. Set DRY_RUN=false only when ready to submit eligible applications."
    if group == "rejected":
        return "Review the job and matching reasons; change rules only if the job genuinely fits your profile."
    if group == "pre_filtered":
        return "If suitable, review title/experience pre-filters; this job's full description was not read."
    if group == "not_evaluated":
        return "Let a later run check it, or raise MAX_DETAIL_JOBS_PER_RUN; it was not fully scored."
    if group == "needs_review":
        return "Open the job URL and artifacts/manual-review.json; verify the status and finish manually if appropriate."
    if group in {"run_limit", "daily_limit", "both_application_limits", "daily_or_run_limit", "role_family_limit"}:
        return "Review MAX_APPLICATIONS_PER_RUN and MAX_APPLICATIONS_PER_DAY in .env; otherwise wait for the blocking limit to reset."
    if group == "retry_cooldown":
        return "Wait until retry_after in state/attempts.json, or open the job URL and handle it manually."
    if group == "manual_review_pending":
        return "Open the exact job on Shine, verify whether it is already applied, and complete it manually if not."
    return "Open the job URL and inspect its status and reason before deciding whether to apply manually."


def unapplied_report_rows(rows: list[dict]) -> list[dict]:
    """Return only jobs without a confirmed application, with clear guidance."""
    report_rows: list[dict] = []
    for row in rows:
        group, _ = _report_status(row)
        if group in _CONFIRMED_APPLICATION_STATUSES:
            continue
        report_row = dict(row)
        report_row["reason_not_applied"] = _report_reason(row)
        report_row["recommended_action"] = _report_next_step(row)
        report_rows.append(report_row)
    return report_rows


def write_report(rows: list[dict]) -> int:
    """Replace the per-run CSV with only jobs lacking confirmed applications."""
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    fields = ["score", "accepted", "status", "title", "company", "experience", "url", "reasons",
              "reason_not_applied", "recommended_action", "detail_evaluated", "is_early_applicant"]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    report_rows = unapplied_report_rows(rows)
    writer.writerows(report_rows)
    atomic_write_text(REPORT_FILE, buffer.getvalue())
    return len(report_rows)


def archive_unreferenced_error_screenshots(manual_jobs: list[dict]) -> int:
    """Move legacy or resolved screenshots out of the active artifact folder.

    Files directly under ``artifacts`` should correspond to the current manual
    queue. Older diagnostics remain recoverable under ``stale-screenshots``.
    """

    referenced_names = {
        Path(str(item["screenshot"])).name
        for item in manual_jobs
        if item.get("screenshot")
    }
    stale_paths = [
        path
        for path in ARTIFACT_DIR.glob("error-*.png")
        if path.name not in referenced_names
    ]
    if not stale_paths:
        return 0

    archive_dir = ARTIFACT_DIR / "stale-screenshots"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived_count = 0
    for source in stale_paths:
        destination = archive_dir / source.name
        counter = 1
        while destination.exists():
            destination = archive_dir / f"{source.stem}-{counter}{source.suffix}"
            counter += 1
        try:
            source.replace(destination)
            archived_count += 1
        except OSError:
            # Artifact housekeeping must not turn a completed job run into a failure.
            continue
    return archived_count


def write_json_reports(
    rows: list[dict],
    dry_run: bool,
    history: dict[str, dict],
    attempts: dict[str, dict] | None = None,
    search_metrics: list[dict] | None = None,
) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().astimezone().isoformat()
    attempts = attempts or {}
    search_metrics = search_metrics or []

    applied_jobs = [
        {
            "title": item.get("title", ""),
            "company": item.get("company", ""),
            "url": url,
            "score": item.get("score"),
            "status": item.get("status", "applied"),
            "applied_at": item.get("applied_at", ""),
            "confirmation": item.get("confirmation"),
        }
        for url, item in sorted(history.items())
        if history_entry_is_verified(item)
    ]
    summary = build_run_summary(rows, search_metrics)
    summary["applied_jobs_in_history"] = len(applied_jobs)

    scored_payload = {
        "generated_at": generated_at,
        "mode": "dry_run" if dry_run else "live",
        "status": final_run_status(summary),
        "summary": summary,
        "how_to_read": {
            "score": "Higher means a closer resume match; 60 or more can qualify.",
            "accepted": "True means the job passed the resume rules.",
            "status": "Shows whether the job was applied, shortlisted, rejected, limited, or needs review.",
            "detail_evaluated": "True only when this run read and scored the full description.",
            "is_early_applicant": "The search card carried the Early Applicant badge; this affects order, not eligibility.",
        },
        "applied_jobs": applied_jobs,
        "scored_jobs": rows,
        "search_metrics": search_metrics,
    }
    atomic_write_json(SCORED_AND_APPLIED_FILE, scored_payload)

    existing_items: dict[str, dict] = {}
    if MANUAL_REVIEW_FILE.exists():
        try:
            existing = json.loads(MANUAL_REVIEW_FILE.read_text(encoding="utf-8"))
            existing_items = {
                item["url"]: item
                for item in existing.get("jobs", [])
                if item.get("url")
            }
        except (json.JSONDecodeError, OSError):
            existing_items = {}

    # A URL in successful history is no longer an unresolved manual item.
    for applied_url, history_item in history.items():
        if history_entry_is_verified(history_item):
            existing_items.pop(applied_url, None)

    for url, item in existing_items.items():
        attempt = attempts.get(url)
        if attempt:
            item.update(
                {
                    "automation_status": attempt.get("status"),
                    "attempt_count": attempt.get("attempt_count", 0),
                    "last_attempted_at": attempt.get("last_attempted_at"),
                    "retry_after": attempt.get("retry_after"),
                }
            )

    # Old versions could write "applied" before proving that Shine accepted
    # the exact job. Never silently treat those records as success.
    for url, item in history.items():
        if history_entry_is_verified(item):
            continue
        existing_items[url] = {
            "detected_at": generated_at,
            "title": item.get("title", ""),
            "company": item.get("company", ""),
            "url": url,
            "score": item.get("score"),
            "experience": item.get("experience", ""),
            "failure_reason": (
                "Legacy success record has no job-specific confirmation evidence"
            ),
            "automation_status": "manual_only",
            "attempt_count": attempts.get(url, {}).get("attempt_count", 0),
            "last_attempted_at": item.get("applied_at") or generated_at,
            "retry_after": None,
            "screenshot": None,
            "manual_action": (
                "Open the exact Shine job and verify that its main button is disabled and "
                "says Applied. If it is not, apply manually."
            ),
        }

    for row in rows:
        status = str(row.get("status", ""))
        if not status.startswith("needs_review"):
            continue
        title = str(row.get("title", ""))
        screenshot_path = error_screenshot_path(
            title,
            str(row.get("url", "")),
        )
        existing_items[str(row.get("url", ""))] = {
            "detected_at": generated_at,
            "title": title,
            "company": row.get("company", ""),
            "url": row.get("url", ""),
            "score": row.get("score"),
            "experience": row.get("experience", ""),
            "failure_reason": status.removeprefix("needs_review:").strip(),
            "automation_status": attempts.get(str(row.get("url", "")), {}).get(
                "status", "manual_only"
            ),
            "attempt_count": attempts.get(str(row.get("url", "")), {}).get(
                "attempt_count", 1
            ),
            "last_attempted_at": attempts.get(str(row.get("url", "")), {}).get(
                "last_attempted_at", generated_at
            ),
            "retry_after": attempts.get(str(row.get("url", "")), {}).get(
                "retry_after"
            ),
            "screenshot": (
                f"artifacts/{screenshot_path.name}" if screenshot_path.exists() else None
            ),
            "manual_action": (
                "Open the job URL, confirm it still matches your resume, sign in if needed, "
                "answer any employer questions truthfully, and click the main Apply button."
            ),
        }

    manual_jobs = sorted(
        existing_items.values(), key=lambda item: item.get("detected_at", ""), reverse=True
    )
    archived_screenshot_count = archive_unreferenced_error_screenshots(manual_jobs)
    manual_payload = {
        "generated_at": generated_at,
        "unresolved_count": len(manual_jobs),
        "archived_stale_screenshot_count": archived_screenshot_count,
        "stale_screenshot_directory": "artifacts/stale-screenshots",
        "instructions": (
            "These jobs were not confirmed as applied. Review each failure_reason and URL, "
            "then apply manually only if the job is still suitable."
        ),
        "jobs": manual_jobs,
    }
    atomic_write_json(MANUAL_REVIEW_FILE, manual_payload)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _is_expected_browser_shutdown_error(error: Exception) -> bool:
    """Return whether Playwright is reporting an already-closed browser."""

    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "connection closed while reading from the driver",
            "target page, context or browser has been closed",
            "browser has been closed",
        )
    )


async def close_browser_resources(context: BrowserContext, browser: Browser) -> None:
    """Close Playwright resources without failing when Chromium closed first.

    A user can close the visible browser, or Chromium can exit after the work is
    complete. In either case the Playwright transport may already be gone when
    the ``finally`` block runs. Unknown cleanup errors are still raised.
    """

    cleanup_error: Exception | None = None
    try:
        await context.close()
    except Exception as error:
        if not _is_expected_browser_shutdown_error(error):
            cleanup_error = error

    try:
        if browser.is_connected():
            await browser.close()
    except Exception as error:
        if cleanup_error is None and not _is_expected_browser_shutdown_error(error):
            cleanup_error = error

    if cleanup_error is not None:
        raise cleanup_error


async def run() -> None:
    with single_instance_run_lock(STATE_DIR / "bot-run.lock"):
        await _run_locked()


async def _run_locked() -> None:
    # Clear the previous run's CSV immediately, including when config validation
    # or the daily-cap check exits before browser startup.
    write_report([])
    load_dotenv(ROOT / ".env")
    email = os.getenv("SHINE_EMAIL", "")
    password = os.getenv("SHINE_PASSWORD", "")
    dry_run = env_bool("DRY_RUN", True)
    headless = env_bool("HEADLESS", False)
    tracing_enabled = env_bool("ENABLE_TRACING", False)
    max_per_run = env_int("MAX_APPLICATIONS_PER_RUN", 20)
    max_per_day = env_int("MAX_APPLICATIONS_PER_DAY", 20)
    max_per_role_family = env_int(
        "MAX_APPLICATIONS_PER_ROLE_FAMILY",
        config.MAX_APPLICATIONS_PER_ROLE_FAMILY,
    )
    max_pages = env_int("MAX_PAGES_PER_SEARCH", 3)
    search_delay_min_seconds = env_int("SEARCH_DELAY_MIN_SECONDS", 2)
    search_delay_max_seconds = env_int("SEARCH_DELAY_MAX_SECONDS", 5)
    action_delay = env_int("ACTION_DELAY_SECONDS", 5)
    navigation_timeout_ms = env_int("NAVIGATION_TIMEOUT_SECONDS", 30) * 1_000
    authentication_timeout_ms = env_int("AUTH_TIMEOUT_SECONDS", 15) * 1_000
    apply_timeout_ms = env_int("APPLY_TIMEOUT_SECONDS", 15) * 1_000
    per_job_timeout_seconds = env_int("PER_JOB_TIMEOUT_SECONDS", 45)
    detail_timeout_seconds = env_int("DETAIL_TIMEOUT_SECONDS", 20)
    max_detail_jobs = env_int("MAX_DETAIL_JOBS_PER_RUN", 250)
    retry_delay_hours = env_int("MANUAL_RETRY_DELAY_HOURS", 72)
    maximum_transient_attempts = env_int("MAX_TRANSIENT_ATTEMPTS", 2)
    answers = ApplicationAnswers.from_environment()

    if max_pages < 1 or max_detail_jobs < 1:
        raise RuntimeError("MAX_PAGES_PER_SEARCH and MAX_DETAIL_JOBS_PER_RUN must be positive")
    if min(navigation_timeout_ms, authentication_timeout_ms, apply_timeout_ms,
           per_job_timeout_seconds, detail_timeout_seconds) <= 0:
        raise RuntimeError("Navigation, authentication, application, and detail timeouts must be positive")
    if min(max_per_run, max_per_day, max_per_role_family) < 0:
        raise RuntimeError("Application limits cannot be negative")
    if search_delay_min_seconds < 0 or search_delay_min_seconds > search_delay_max_seconds:
        raise RuntimeError(
            "SEARCH_DELAY_MIN_SECONDS must be non-negative and no greater than "
            "SEARCH_DELAY_MAX_SECONDS"
        )
    if action_delay < 0:
        raise RuntimeError("ACTION_DELAY_SECONDS cannot be negative")
    if retry_delay_hours < 1 or maximum_transient_attempts < 1:
        raise RuntimeError(
            "MANUAL_RETRY_DELAY_HOURS and MAX_TRANSIENT_ATTEMPTS must be positive"
        )

    if not dry_run and (not email or not password):
        raise RuntimeError("SHINE_EMAIL and SHINE_PASSWORD are required when DRY_RUN=false")

    history = load_history()
    attempts = load_attempts()
    attempts_changed = False
    for applied_url, history_item in history.items():
        if history_entry_is_verified(history_item) and attempts.pop(applied_url, None) is not None:
            attempts_changed = True
    if attempts_changed and not dry_run:
        save_attempts(attempts)
    applications_today_at_start = applications_today(history)
    remaining_today = max(0, max_per_day - applications_today_at_start)
    rows: list[dict] = []
    initial_limit = application_limit_status(
        0, applications_today_at_start, max_per_run, max_per_day
    )
    if not dry_run and initial_limit is not None:
        limit_status, limit_reason = initial_limit
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_json(ARTIFACT_DIR / "run-status.json", {
            "generated_at": datetime.now().astimezone().isoformat(),
            "status": "limit_reached",
            "phase": "preflight_limits",
            "mode": "live",
            "discovered_jobs": 0,
            "evaluated_jobs": 0,
            "application_limits": {
                "blocking_status": limit_status,
                "blocking_reason": limit_reason,
                "maximum_per_run": max_per_run,
                "maximum_per_day": max_per_day,
                "applications_today_at_start": applications_today_at_start,
                "remaining_today": remaining_today,
            },
            "summary": build_run_summary([], [], discovered_count=0),
            "error": "",
        })
        print(f"Application run not started: {limit_reason}; no browser session started")
        return
    role_family_counts: dict[str, int] = {}
    search_metrics: list[dict] = []
    discovered_count = 0
    phase = "starting_browser"
    progress = DetailProgress(STATE_DIR / "detail-progress.json", "dry_run" if dry_run else "live")

    def save_completed_progress() -> None:
        # Persist scheduling only after reports. A crash must not advance past
        # jobs whose scoring/application outcome has not yet been recorded.
        progress.mark_checked(
            row["url"] for row in rows
            if row["detail_evaluated"] or str(row["status"]).startswith("needs_review: job-detail")
        )

    def save_run_status(status: str, error: str = "") -> None:
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        summary = build_run_summary(rows, search_metrics, discovered_count=discovered_count)
        atomic_write_json(ARTIFACT_DIR / "run-status.json", {
            "generated_at": datetime.now().astimezone().isoformat(),
            "status": status, "phase": phase,
            "mode": "dry_run" if dry_run else "live",
            "discovered_jobs": discovered_count,
            "evaluated_jobs": summary["evaluated_in_this_run"],
            "application_limits": {
                "maximum_per_run": max_per_run,
                "maximum_per_day": max_per_day,
                "applications_today_at_start": applications_today_at_start,
                "remaining_today": remaining_today,
            },
            "summary": summary, "error": error,
        })

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        context: BrowserContext = await browser.new_context()
        page = await context.new_page()
        trace_started = False
        trace_has_failure = False
        run_failed = False
        try:
            if not dry_run:
                phase = "login"
                save_run_status("running")
                await login(page, email, password, navigation_timeout_ms)
            if tracing_enabled:
                await context.tracing.start(
                    screenshots=True,
                    snapshots=True,
                    sources=True,
                )
                trace_started = True
            phase = "discovery"
            save_run_status("running")
            jobs, search_metrics = await discover(
                page,
                max_pages,
                navigation_timeout_ms,
                search_delay_min_seconds,
                search_delay_max_seconds,
            )
            discovered_count = len(jobs)
            trace_has_failure = any(
                p.get("status") == "failed" for metric in search_metrics for p in metric.get("pages", [])
            )
            run_started_at = datetime.now().astimezone()
            active_jobs: list[Job] = []
            held_jobs: list[tuple[Job, str, str]] = []
            for job in jobs:
                history_hold = history_entry_hold_status(history.get(job.url))
                if history_hold is not None:
                    held_jobs.append((job, history_hold[0], history_hold[1]))
                    continue
                hold = attempt_hold_status(attempts.get(job.url), run_started_at)
                if hold is None:
                    active_jobs.append(job)
                else:
                    hold_status, hold_reason = hold
                    held_jobs.append((job, hold_status, hold_reason))

            phase = "detail_scoring"
            save_run_status("running")
            ranked, detail_statuses = await score_detailed_jobs(
                page,
                active_jobs,
                max_detail_jobs,
                navigation_timeout_ms,
                detail_timeout_seconds,
                previous_checks=progress.checked_at,
            )
            trace_has_failure = trace_has_failure or any(
                status.startswith("needs_review:")
                for status in detail_statuses.values()
            )
            for job, hold_status, hold_reason in held_jobs:
                ranked.append((ScoreResult(0, False, (hold_reason,)), job))
                detail_statuses[job.url] = hold_status

            applied_this_run = 0
            phase = "applications"
            save_run_status("running")
            for result, job in ranked:
                require_open_browser(page)
                fatal_session_error: Exception | None = None
                detail_evaluated = job.url not in detail_statuses
                status = detail_statuses.get(job.url, "rejected")
                if result.accepted:
                    family = role_family(job)
                    if job.url in history and history_entry_is_verified(history[job.url]):
                        status = "already_seen"
                    elif dry_run:
                        status = "shortlisted"
                    elif (
                        limit := application_limit_status(
                            applied_this_run,
                            applications_today_at_start + applied_this_run,
                            max_per_run,
                            max_per_day,
                        )
                    ) is not None:
                        status = f"{limit[0]}: {limit[1]}"
                    elif role_family_counts.get(family, 0) >= max_per_role_family:
                        status = "role_family_limit"
                    else:
                        try:
                            application_outcome = await asyncio.wait_for(
                                apply_to_job(
                                    page,
                                    job,
                                    navigation_timeout_ms,
                                    authentication_timeout_ms,
                                    apply_timeout_ms,
                                    answers,
                                ),
                                timeout=per_job_timeout_seconds,
                            )
                            application_status = application_outcome.status
                            status = application_status
                            history[job.url] = {
                                "title": job.title,
                                "company": job.company,
                                "applied_at": (
                                    datetime.now().astimezone().isoformat()
                                    if application_status == "applied"
                                    else ""
                                ),
                                "score": result.score,
                                "status": application_status,
                                "confirmation": application_outcome.confirmation_record(),
                            }
                            save_history(history)
                            if attempts.pop(job.url, None) is not None:
                                save_attempts(attempts)
                            if application_status == "applied":
                                applied_this_run += 1
                                role_family_counts[family] = role_family_counts.get(family, 0) + 1
                                await asyncio.sleep(action_delay)
                        except TimeoutError:
                            status = (
                                "needs_review: application exceeded the "
                                f"{per_job_timeout_seconds}-second per-job timeout"
                            )
                            ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
                            try:
                                await asyncio.wait_for(
                                    page.screenshot(
                                        path=error_screenshot_path(job.title, job.url)
                                    ),
                                    timeout=5,
                                )
                            except Exception:
                                pass
                        except Exception as exc:  # capture per-job failures in the report
                            try:
                                require_open_browser(page)
                            except RuntimeError as session_error:
                                fatal_session_error = session_error
                            reason = (
                                "Browser closed during the application; outcome unconfirmed. "
                                "Verify the exact job manually before retrying."
                                if fatal_session_error else str(exc).strip() or type(exc).__name__
                            )
                            status = f"needs_review: {reason}"
                            ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
                            try:
                                await asyncio.wait_for(
                                    page.screenshot(
                                        path=error_screenshot_path(job.title, job.url)
                                    ),
                                    timeout=5,
                                )
                            except Exception:
                                pass

                if status.startswith("needs_review:"):
                    trace_has_failure = True
                    failure_reason = status.removeprefix("needs_review:").strip()
                    if not dry_run:
                        record_failed_attempt(
                            attempts,
                            job,
                            failure_reason,
                            datetime.now().astimezone(),
                            retry_delay_hours,
                            maximum_transient_attempts,
                        )
                        save_attempts(attempts)

                rows.append(
                    {
                        "score": result.score,
                        "accepted": result.accepted,
                        "status": status,
                        "title": job.title,
                        "company": job.company,
                        "experience": f"{job.min_experience}-{job.max_experience}",
                        "url": job.url,
                        "reasons": "; ".join(result.reasons),
                        "detail_evaluated": detail_evaluated,
                        "is_early_applicant": job.is_early_applicant,
                    }
                )
                # Save the current non-applied queue before the next browser
                # action can fail. Successful rows are intentionally omitted.
                write_report(rows)
                if status in {"applied", "already_applied"} or status.startswith("needs_review:"):
                    write_json_reports(rows, dry_run, history, attempts, search_metrics)
                if fatal_session_error is not None:
                    raise fatal_session_error
            written_rows = write_report(rows)
            write_json_reports(rows, dry_run, history, attempts, search_metrics)
            save_completed_progress()
            phase = "finished"
            save_run_status(final_run_status(build_run_summary(rows, search_metrics)))
        except Exception as exc:
            run_failed = True
            if rows:
                write_report(rows)
                write_json_reports(rows, dry_run, history, attempts, search_metrics)
                save_completed_progress()
            save_run_status("failed", str(exc).strip() or type(exc).__name__)
            raise
        finally:
            if trace_started:
                try:
                    if trace_has_failure or run_failed:
                        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
                        await context.tracing.stop(path=ARTIFACT_DIR / "trace.zip")
                    else:
                        await context.tracing.stop()
                except Exception:
                    # Trace diagnostics must never hide the original run result.
                    pass
            try:
                await close_browser_resources(context, browser)
            except Exception:
                # If the workflow already failed, preserve that original error
                # instead of replacing it with a secondary cleanup exception.
                if not run_failed:
                    raise

    summary = build_run_summary(rows, search_metrics)
    print(f"Wrote {written_rows} not-confirmed-as-applied job rows to {REPORT_FILE}; "
          f"{summary['evaluated_in_this_run']} fully scored, "
          f"{summary['not_evaluated_in_this_run']} outside the detail budget; "
          f"run status: {final_run_status(summary)}")
    print("DRY RUN: no applications were submitted" if dry_run else "Application run complete")


if __name__ == "__main__":
    asyncio.run(run())
