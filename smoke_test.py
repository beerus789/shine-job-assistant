"""Read-only smoke checks for the current public Shine page structure.

This script never enters credentials and never clicks Apply. It is intentionally
separate from the deterministic unit suite because live website checks can fail
when Shine is unavailable or changes its frontend.
"""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from playwright.async_api import async_playwright

import config
from bot import (
    BASE_URL,
    ROOT,
    extract_job_detail,
    extract_jobs,
    slugify,
)
from scoring import preliminary_job_priority


async def run_smoke_test() -> None:
    load_dotenv(ROOT / ".env")
    timeout_ms = int(os.getenv("NAVIGATION_TIMEOUT_SECONDS", "30")) * 1_000
    query = os.getenv("SMOKE_TEST_QUERY", "python backend developer").strip().lower()
    if query not in config.SEARCH_QUERIES:
        raise RuntimeError("SMOKE_TEST_QUERY must be present in search-queries.txt")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        try:
            await page.goto(
                f"{BASE_URL}/job-search/{slugify(query)}-jobs",
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
            jobs = await extract_jobs(page)
            if not jobs:
                raise RuntimeError("Search-card extraction returned no jobs")

            candidates = [
                job for job in jobs if preliminary_job_priority(job) is not None
            ]
            if not candidates:
                raise RuntimeError("Search page returned no detail candidates")
            candidate = max(candidates, key=lambda job: preliminary_job_priority(job) or 0)
            detailed = await extract_job_detail(page, candidate, timeout_ms)
            if len(detailed.text) < 100:
                raise RuntimeError("Job-description extraction returned too little text")

            job_id = candidate.url.rstrip("/").rsplit("/", 1)[-1]
            apply_button = page.locator(f'button[id="id_apply_{job_id}"]')
            if await apply_button.count() != 1:
                raise RuntimeError("Primary Apply selector no longer finds exactly one button")

            await page.goto(
                f"{BASE_URL}/pages/myshine/login",
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
            password_tab = page.get_by_role(
                "button", name="Login Via Password", exact=True
            )
            if await password_tab.count() == 1:
                await password_tab.click()
            await page.get_by_label("Email", exact=True).wait_for(
                state="visible", timeout=timeout_ms
            )
            if await page.get_by_label("Email", exact=True).count() != 1:
                raise RuntimeError("Login Email selector changed")
            if await page.get_by_label("Password", exact=True).count() != 1:
                raise RuntimeError("Login Password selector changed")
            if await page.get_by_role("button", name="Log In", exact=True).count() != 1:
                raise RuntimeError("Login submit selector changed")

            print(
                "Smoke test passed: "
                f"{len(jobs)} cards, {len(detailed.text)} detail characters, "
                f"{len(detailed.skills)} skills"
            )
        finally:
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(run_smoke_test())
