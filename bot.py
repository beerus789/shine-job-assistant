"""Shine job discovery, scoring, application, and audit-report workflow.

The automation confirms every successful application from Shine's visible
``Applied`` state. Unsupported questions, redirects, and timeouts are isolated
to one job and written to the manual-review queue.
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from dotenv import load_dotenv
from playwright.async_api import BrowserContext, Locator, Page, async_playwright

import config
from scoring import Job, parse_experience, score_job

BASE_URL = "https://www.shine.com"
ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state"
ARTIFACT_DIR = ROOT / "artifacts"
HISTORY_FILE = STATE_DIR / "history.json"
REPORT_FILE = ARTIFACT_DIR / "latest.csv"
SCORED_AND_APPLIED_FILE = ARTIFACT_DIR / "scored-and-applied.json"
MANUAL_REVIEW_FILE = ARTIFACT_DIR / "manual-review.json"


# ---------------------------------------------------------------------------
# Environment and user-supplied application facts
# ---------------------------------------------------------------------------


class ManualReviewRequired(RuntimeError):
    """A job needs a truthful answer or a website flow the bot does not know."""


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


def env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


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


# ---------------------------------------------------------------------------
# Persistent history and duplicate protection
# ---------------------------------------------------------------------------


def load_history() -> dict[str, dict]:
    if not HISTORY_FILE.exists():
        return {}
    return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))


def save_history(history: dict[str, dict]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, indent=2, sort_keys=True), encoding="utf-8")


def applications_today(history: dict[str, dict]) -> int:
    today = date.today().isoformat()
    return sum(1 for item in history.values() if item.get("applied_at", "").startswith(today))


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
    await page.wait_for_url(
        "**/dashboard",
        wait_until="domcontentloaded",
        timeout=navigation_timeout_ms,
    )

    page_text = (await page.locator("body").inner_text()).lower()
    if "captcha" in page_text or "one time password" in page_text or "enter otp" in page_text:
        raise RuntimeError("Shine requires CAPTCHA/OTP. Complete it manually, then rerun.")
    if "/login" in page.url:
        raise RuntimeError("Login did not complete. Check credentials or finish any verification manually.")


async def extract_jobs(page: Page) -> list[Job]:
    cards = await page.locator("div.jdbigCard").evaluate_all(
        """cards => cards.slice(0, 30).map(card => {
          const link = card.querySelector('a[href*="/jobs/"]');
          const title = link?.textContent?.trim() || '';
          const company = card.querySelector('.jdTruncationCompany, [class*="CompanyName"], [class*="companyName"]')?.textContent?.trim() || '';
          const skills = [...card.querySelectorAll('li')].map(x => x.textContent.trim()).filter(Boolean);
          return {title, company, url: link?.href || '', text: card.innerText || '', skills};
        }).filter(x => x.title && x.url)"""
    )
    jobs: list[Job] = []
    for card in cards:
        resolved_url = urljoin(BASE_URL, card["url"])
        # Search results must never introduce an off-site application URL.
        if not _is_shine_url(resolved_url):
            continue
        minimum, maximum = parse_experience(card["text"])
        jobs.append(
            Job(
                title=card["title"],
                company=card["company"],
                url=resolved_url,
                text=card["text"],
                skills=tuple(card["skills"]),
                min_experience=minimum,
                max_experience=maximum,
            )
        )
    return jobs


async def discover(page: Page, max_pages: int, navigation_timeout_ms: int) -> list[Job]:
    discovered: dict[str, Job] = {}
    for query in sorted(config.SEARCH_QUERIES):
        base_slug = f"{slugify(query)}-jobs"
        for page_number in range(1, max_pages + 1):
            suffix = "" if page_number == 1 else f"-{page_number}"
            await page.goto(
                f"{BASE_URL}/job-search/{base_slug}{suffix}",
                wait_until="domcontentloaded",
                timeout=navigation_timeout_ms,
            )
            for job in await extract_jobs(page):
                discovered[job.url] = job
    return list(discovered.values())


def role_family(job: Job) -> str:
    text = job.searchable_text.lower()
    if any(term in text for term in ("genai", "generative ai", "rag", "llm", "langchain", "agentic ai")):
        return "genai_rag"
    if "software development engineer" in text or re.search(r"\bsde\s*(?:2|3|ii|iii)\b", text):
        return "sde"
    if any(term in text for term in ("fastapi", "django", "flask")):
        return "framework_backend"
    if "backend" in text or "back end" in text:
        return "backend"
    if "software engineer" in text:
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
    experience_pattern = re.compile(
        r"^(?:total\s+)?(?:work\s+)?experience(?:\s+in\s+years)?\s*\*?$", re.I
    )
    experience_label = await _find_field_label(scope, experience_pattern)
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
            re.compile(
                r"^(?:(?:what\s+is\s+your\s+)?current\s+(?:annual\s+)?(?:salary|ctc)|"
                r"total\s+annual\s+salary).*",
                re.I,
            ),
            answers.current_salary_lpa,
            "CURRENT_SALARY_LPA",
            "current salary",
        ),
        (
            re.compile(r"^(?:what\s+is\s+your\s+)?expected\s+(?:annual\s+)?(?:salary|ctc).*", re.I),
            answers.expected_salary_lpa,
            "EXPECTED_SALARY_LPA",
            "expected salary",
        ),
        (
            re.compile(r"^(?:what\s+is\s+your\s+)?notice\s+period.*", re.I),
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
    known = re.compile(r"salary|ctc|experience|notice\s*period", re.I)
    for index in range(count):
        control = controls.nth(index)
        if not await control.is_visible() or await control.is_disabled():
            continue
        description = await control.evaluate(
            """element => {
              const id = element.id;
              const label = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
              const container = element.closest('label, [class*="question"], [class*="field"]');
              return [label?.innerText, element.getAttribute('aria-label'),
                      element.getAttribute('placeholder'), container?.innerText]
                .filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim().slice(0, 180);
            }"""
        )
        if not known.search(description or ""):
            details.append(description or "unlabelled required control")

    # Custom question cards do not always use input elements. Treat any other
    # visible field label or question group as manual rather than guessing.
    labels = scope.locator('label:visible, legend:visible, [class*="question"]:visible')
    label_count = await labels.count()
    for index in range(label_count):
        description = re.sub(r"\s+", " ", (await labels.nth(index).inner_text()).strip())
        if description and not known.search(description):
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
    page: Page, pages_before_click: tuple[Page, ...]
) -> None:
    """Close any new tab and require manual review without interacting with it."""
    new_pages = [candidate for candidate in page.context.pages if candidate not in pages_before_click]
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


async def apply_to_job(
    page: Page,
    job: Job,
    navigation_timeout_ms: int,
    authentication_timeout_ms: int,
    apply_timeout_ms: int,
    answers: ApplicationAnswers,
) -> str:
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

    # Shine initially renders the signed-out version of a job page and then
    # hydrates the authenticated state. Do not click until that transition is
    # complete; otherwise Apply can redirect back to login.
    authenticated_profile = page.get_by_text(
        "skills matched with your profile", exact=False
    )
    await authenticated_profile.wait_for(
        state="visible", timeout=authentication_timeout_ms
    )

    applied_button = page.get_by_role("button", name="Applied", exact=True)
    if await applied_button.count() == 1:
        return "already_applied"

    job_id = job.url.rstrip("/").rsplit("/", 1)[-1]
    button = page.locator(f'button[id="id_apply_{job_id}"]')
    count = await button.count()
    if count != 1:
        raise RuntimeError(f"Expected one primary Apply button; found {count}")
    pages_before_click = tuple(page.context.pages)
    await button.click()

    for _ in range(3):
        await page.wait_for_timeout(500)
        await _reject_new_application_tabs(page, pages_before_click)
        # Check the origin before reading or clicking anything on the result page.
        if not _is_shine_url(page.url):
            raise ManualReviewRequired(
                f"Application redirected to an external website ({page.url}); "
                "the external page was not used"
            )
        if not _same_job_path(job.url, page.url):
            raise ManualReviewRequired(f"Application redirected to {page.url}")
        if await applied_button.count() == 1 and await applied_button.is_visible():
            return "applied"

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
            try:
                await applied_button.wait_for(state="visible", timeout=apply_timeout_ms)
                return "applied"
            except Exception as exc:
                raise ManualReviewRequired(
                    "Shine did not confirm Applied and no supported application form appeared"
                ) from exc

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
            ("Submit Application", "Submit", "Save & Apply", "Continue", "Next", "Apply Now"),
        )
        if submit is None:
            raise ManualReviewRequired("Known fields were filled, but no supported submit button was found")
        await submit.click()

    raise ManualReviewRequired("Application did not finish after three form steps")


def write_report(rows: list[dict]) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    fields = ["score", "accepted", "status", "title", "company", "experience", "url", "reasons"]
    with REPORT_FILE.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_json_reports(
    rows: list[dict], dry_run: bool, history: dict[str, dict]
) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().astimezone().isoformat()

    applied_jobs = [
        {
            "title": item.get("title", ""),
            "company": item.get("company", ""),
            "url": url,
            "score": item.get("score"),
            "status": item.get("status", "applied"),
            "applied_at": item.get("applied_at", ""),
        }
        for url, item in sorted(history.items())
        if item.get("status", "applied") == "applied"
        or item.get("applied_at")
    ]
    status_counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status", "unknown"))
        status_counts[status] = status_counts.get(status, 0) + 1

    scored_payload = {
        "generated_at": generated_at,
        "mode": "dry_run" if dry_run else "live",
        "summary": {
            "evaluated_in_this_run": len(rows),
            "accepted_in_this_run": sum(bool(row.get("accepted")) for row in rows),
            "applied_jobs_in_history": len(applied_jobs),
            "status_counts": status_counts,
        },
        "how_to_read": {
            "score": "Higher means a closer resume match; 60 or more can qualify.",
            "accepted": "True means the job passed the resume rules.",
            "status": "Shows whether the job was applied, shortlisted, rejected, limited, or needs review.",
        },
        "applied_jobs": applied_jobs,
        "scored_jobs": rows,
    }
    SCORED_AND_APPLIED_FILE.write_text(
        json.dumps(scored_payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

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
    for applied_url in history:
        existing_items.pop(applied_url, None)

    for row in rows:
        status = str(row.get("status", ""))
        if not status.startswith("needs_review"):
            continue
        title = str(row.get("title", ""))
        screenshot_name = f"error-{slugify(title)[:50]}.png"
        screenshot_path = ARTIFACT_DIR / screenshot_name
        existing_items[str(row.get("url", ""))] = {
            "detected_at": generated_at,
            "title": title,
            "company": row.get("company", ""),
            "url": row.get("url", ""),
            "score": row.get("score"),
            "experience": row.get("experience", ""),
            "failure_reason": status.removeprefix("needs_review:").strip(),
            "screenshot": (
                f"artifacts/{screenshot_name}" if screenshot_path.exists() else None
            ),
            "manual_action": (
                "Open the job URL, confirm it still matches your resume, sign in if needed, "
                "answer any employer questions truthfully, and click the main Apply button."
            ),
        }

    manual_jobs = sorted(
        existing_items.values(), key=lambda item: item.get("detected_at", ""), reverse=True
    )
    manual_payload = {
        "generated_at": generated_at,
        "unresolved_count": len(manual_jobs),
        "instructions": (
            "These jobs were not confirmed as applied. Review each failure_reason and URL, "
            "then apply manually only if the job is still suitable."
        ),
        "jobs": manual_jobs,
    }
    MANUAL_REVIEW_FILE.write_text(
        json.dumps(manual_payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def run() -> None:
    load_dotenv(ROOT / ".env")
    email = os.getenv("SHINE_EMAIL", "")
    password = os.getenv("SHINE_PASSWORD", "")
    dry_run = env_bool("DRY_RUN", True)
    headless = env_bool("HEADLESS", False)
    max_per_run = env_int("MAX_APPLICATIONS_PER_RUN", 5)
    max_per_day = env_int("MAX_APPLICATIONS_PER_DAY", 10)
    max_pages = env_int("MAX_PAGES_PER_SEARCH", 2)
    action_delay = env_int("ACTION_DELAY_SECONDS", 3)
    navigation_timeout_ms = env_int("NAVIGATION_TIMEOUT_SECONDS", 30) * 1_000
    authentication_timeout_ms = env_int("AUTH_TIMEOUT_SECONDS", 15) * 1_000
    apply_timeout_ms = env_int("APPLY_TIMEOUT_SECONDS", 15) * 1_000
    per_job_timeout_seconds = env_int("PER_JOB_TIMEOUT_SECONDS", 45)
    answers = ApplicationAnswers.from_environment()

    if not dry_run and (not email or not password):
        raise RuntimeError("SHINE_EMAIL and SHINE_PASSWORD are required when DRY_RUN=false")

    history = load_history()
    remaining_today = max(0, max_per_day - applications_today(history))
    application_budget = min(max_per_run, remaining_today)
    rows: list[dict] = []
    if not dry_run and application_budget == 0:
        print("Daily application limit reached; no browser session started")
        return
    role_family_counts: dict[str, int] = {}

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        context: BrowserContext = await browser.new_context()
        page = await context.new_page()
        try:
            if not dry_run:
                await login(page, email, password, navigation_timeout_ms)
            jobs = await discover(page, max_pages, navigation_timeout_ms)
            ranked = sorted(((score_job(job), job) for job in jobs), key=lambda pair: pair[0].score, reverse=True)

            applied_this_run = 0
            for result, job in ranked:
                status = "rejected"
                if result.accepted:
                    family = role_family(job)
                    if job.url in history:
                        status = "already_seen"
                    elif dry_run:
                        status = "shortlisted"
                    elif applied_this_run >= application_budget:
                        status = "daily_or_run_limit"
                    elif role_family_counts.get(family, 0) >= config.MAX_APPLICATIONS_PER_ROLE_FAMILY:
                        status = "role_family_limit"
                    else:
                        try:
                            application_status = await asyncio.wait_for(
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
                            }
                            save_history(history)
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
                                        path=ARTIFACT_DIR
                                        / f"error-{slugify(job.title)[:50]}.png"
                                    ),
                                    timeout=5,
                                )
                            except Exception:
                                pass
                        except Exception as exc:  # capture per-job failures in the report
                            reason = str(exc).strip() or type(exc).__name__
                            status = f"needs_review: {reason}"
                            ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
                            try:
                                await asyncio.wait_for(
                                    page.screenshot(
                                        path=ARTIFACT_DIR
                                        / f"error-{slugify(job.title)[:50]}.png"
                                    ),
                                    timeout=5,
                                )
                            except Exception:
                                pass

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
                    }
                )
            write_report(rows)
            write_json_reports(rows, dry_run, history)
        finally:
            await context.close()
            await browser.close()

    print(f"Wrote {len(rows)} evaluated jobs to {REPORT_FILE}")
    print("DRY RUN: no applications were submitted" if dry_run else "Application run complete")


if __name__ == "__main__":
    asyncio.run(run())
