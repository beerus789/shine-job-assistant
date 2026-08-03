"""Read-only multi-page audit for Shine discovery and detailed scoring.

This command never signs in and never calls the application workflow. It is a
repeatable way to see which jobs were accepted, rejected, or not evaluated.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright

import bot


AUDIT_REPORT_FILE = bot.ARTIFACT_DIR / "discovery-audit.json"


def build_audit_payload(ranked, statuses: dict[str, str], search_metrics: list[dict]) -> dict:
    jobs: list[dict] = []
    status_counts: Counter[str] = Counter()
    for result, job in ranked:
        if result.accepted:
            status = "accepted"
        else:
            status = statuses.get(job.url, "rejected")
        status_counts[status.split(":", 1)[0]] += 1
        jobs.append(
            {
                "score": result.score,
                "accepted": result.accepted,
                "status": status,
                "title": job.title,
                "company": job.company,
                "experience": [job.min_experience, job.max_experience],
                "url": job.url,
                "reasons": list(result.reasons),
            }
        )

    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "mode": "read_only_discovery_audit",
        "safety": "No login and no application actions are performed.",
        "summary": {
            "cards_found": sum(item.get("cards_found", 0) for item in search_metrics),
            "unique_jobs": len(jobs),
            "accepted": sum(item["accepted"] for item in jobs),
            "status_counts": dict(sorted(status_counts.items())),
        },
        "search_metrics": search_metrics,
        "jobs": jobs,
    }


async def run() -> None:
    load_dotenv(bot.ROOT / ".env")
    max_pages = bot.env_int("MAX_PAGES_PER_SEARCH", 3)
    max_detail_jobs = bot.env_int("MAX_DETAIL_JOBS_PER_RUN", 250)
    navigation_timeout_ms = bot.env_int("NAVIGATION_TIMEOUT_SECONDS", 30) * 1_000
    detail_timeout_seconds = bot.env_int("DETAIL_TIMEOUT_SECONDS", 20)
    delay_min = bot.env_int("SEARCH_DELAY_MIN_SECONDS", 2)
    delay_max = bot.env_int("SEARCH_DELAY_MAX_SECONDS", 5)
    headless = bot.env_bool("HEADLESS", True)

    if max_pages < 1 or max_detail_jobs < 1:
        raise RuntimeError("Audit page and detail limits must both be positive")
    if delay_min < 0 or delay_min > delay_max:
        raise RuntimeError("Search delay range is invalid")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()
        try:
            jobs, search_metrics = await bot.discover(
                page,
                max_pages,
                navigation_timeout_ms,
                delay_min,
                delay_max,
            )
            ranked, statuses = await bot.score_detailed_jobs(
                page,
                jobs,
                max_detail_jobs,
                navigation_timeout_ms,
                detail_timeout_seconds,
            )
            payload = build_audit_payload(ranked, statuses, search_metrics)
            bot.ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
            AUDIT_REPORT_FILE.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            summary = payload["summary"]
            print(
                "Discovery audit complete: "
                f"{summary['cards_found']} cards, "
                f"{summary['unique_jobs']} unique jobs, "
                f"{summary['accepted']} accepted"
            )
            print(f"Report: {Path(AUDIT_REPORT_FILE).resolve()}")
        finally:
            await bot.close_browser_resources(context, browser)

if __name__ == "__main__":
    asyncio.run(run())
