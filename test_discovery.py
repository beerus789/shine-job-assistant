"""Browser regressions for the September 2026 Shine result-card redesign."""
import asyncio
import json

import pytest

import bot


def card(job_id, tracking="", early=False):
    # The real card's overlay link is empty; its title lives in a separate h3.
    badge = "<span>Be An Early Applicant</span>" if early else ""
    return f'''<article itemprop="itemListElement" class="result-card_card__newHash">
      {badge}
      <a href="https://www.shine.com/jobs/python/example/{job_id}{tracking}"
         aria-label="Python Backend Developer at Example"></a>
      <span class="result-card_company__newHash">Example</span>
      <h3 itemprop="name">Python Backend Developer</h3>
      <span>3 to 5 Yrs</span>
      <span class="result-card_skills-item__newHash">Python</span>
      <span class="result-card_skills-item__newHash"><span>·</span>FastAPI</span>
    </article>'''


async def with_browser(scenario):
    async with bot.async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await scenario(page)
        finally:
            await browser.close()


def test_extract_redesigned_and_legacy_cards_with_canonical_duplicates():
    async def scenario(page):
        await page.set_content(
            card(123, "?position_in_results=1")
            + card(123, "?position_in_results=7#tracking")
            + '''<div class="jdbigCard"><a href="https://www.shine.com/jobs/python/legacy/124">Python Developer</a>
              <span class="jdTruncationCompany">Legacy</span><ul><li>Django</li></ul>2 to 4 Yrs</div>'''
            + card(125).replace("www.shine.com", "employer.example")
        )
        jobs = await bot.extract_jobs(page)
        assert len(jobs) == 2
        assert jobs[0].title == "Python Backend Developer"
        assert jobs[0].company == "Example"
        assert jobs[0].skills == ("Python", "FastAPI")
        assert jobs[0].url == "https://www.shine.com/jobs/python/example/123"
        assert (jobs[0].min_experience, jobs[0].max_experience) == (3, 5)
        assert jobs[1].company == "Legacy"
    asyncio.run(with_browser(scenario))


def test_extraction_does_not_silently_truncate_after_thirty_cards():
    async def scenario(page):
        await page.set_content("".join(card(n) for n in range(35)))
        assert len(await bot.extract_jobs(page)) == 35
    asyncio.run(with_browser(scenario))


def test_extracts_early_applicant_badge_and_preserves_it_across_duplicate_cards():
    async def scenario(page):
        await page.set_content(card(123, early=True) + card(123))
        jobs = await bot.extract_jobs(page)
        assert len(jobs) == 1
        assert jobs[0].is_early_applicant is True
    asyncio.run(with_browser(scenario))


def test_unknown_experience_screening_question_is_not_whitelisted_as_known_field():
    async def scenario(page):
        await page.set_content('''<form>
          <div class="field">
            <label for="kafka">How many years of experience do you have with Kafka?</label>
            <input id="kafka" required>
          </div>
        </form>''')
        unknown = await bot._unknown_required_controls(page.locator("form"))
        assert unknown == ["How many years of experience do you have with Kafka?"]
    asyncio.run(with_browser(scenario))


def test_supported_profile_fields_are_not_marked_as_unknown_controls():
    async def scenario(page):
        await page.set_content('''<form>
          <div class="field">
            <label for="experience">Total work experience</label>
            <input id="experience" required>
          </div>
          <div class="field">
            <label for="salary">Expected salary</label>
            <select id="salary"><option>15 LPA</option></select>
          </div>
        </form>''')
        unknown = await bot._unknown_required_controls(page.locator("form"))
        assert unknown == []
    asyncio.run(with_browser(scenario))


def test_waits_for_hydration_before_reading_placeholder_cards():
    async def scenario(page):
        await page.set_content(f'<div id="results" inert aria-busy="true">{card(123)}</div>')
        await page.evaluate('''() => setTimeout(() => {
          const results = document.querySelector('#results');
          results.innerHTML = results.innerHTML.replaceAll('123', '456');
          results.removeAttribute('inert'); results.setAttribute('aria-busy', 'false');
        }, 600)''')
        assert await bot.extract_jobs(page) == []
        assert await bot.wait_for_search_results(page, 2500) == "ready"
        assert (await bot.extract_jobs(page))[0].url.endswith("/456")
    asyncio.run(with_browser(scenario))


@pytest.mark.parametrize("html, expected", [
    ("<main>0 Jobs Found</main>", "empty"),
    ("<main>No jobs found</main>", "empty"),
    ("<main>Verify you are human</main>", "blocked"),
    ("<main>20 Jobs Found, but unsupported layout</main>", "timeout"),
])
def test_empty_search_is_distinct_from_blocked_or_broken_page(html, expected):
    async def scenario(page):
        await page.set_content(html)
        if expected == "empty":
            assert await bot.wait_for_search_results(page, 250) == "empty"
        else:
            with pytest.raises(bot.DiscoveryError):
                await bot.wait_for_search_results(page, 250)
    asyncio.run(with_browser(scenario))


def test_discovery_reaches_three_pages_and_deduplicates_tracking(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(bot.config, "SEARCH_QUERIES", {"python backend"})
    async def scenario(page):
        async def serve(route):
            path = bot.urlsplit(route.request.url).path
            number = 2 if path.endswith("-2") else 3 if path.endswith("-3") else 1
            await route.fulfill(body=card(100 + number) + card(200, f"?position_in_results={number}"))
        await page.route("https://www.shine.com/**", serve)
        jobs, metrics = await bot.discover(page, 3, 2000, 0, 0)
        assert len(jobs) == 4
        assert metrics[0]["pages_visited"] == 3
        assert metrics[0]["cards_found"] == 6
        assert [p["status"] for p in metrics[0]["pages"]] == ["ready"] * 3
        assert json.loads((tmp_path / "search-diagnostics.json").read_text())["failed_pages"] == 0
    asyncio.run(with_browser(scenario))


def test_all_failed_searches_raise_and_preserve_previous_report(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(bot.config, "SEARCH_QUERIES", {"python"})
    prior = tmp_path / "scored-and-applied.json"
    prior.write_text('{"previous": true}')
    async def scenario(page):
        await page.route("https://www.shine.com/**", lambda route: route.fulfill(status=403, body="Access denied"))
        with pytest.raises(bot.DiscoveryError, match="Previous job reports were preserved"):
            await bot.discover(page, 3, 1000, 0, 0)
        diagnostic = json.loads((tmp_path / "search-diagnostics.json").read_text())
        assert diagnostic["status"] == "failed"
        assert diagnostic["failed_pages"] == 1
        assert "HTTP 403" in diagnostic["search_metrics"][0]["pages"][0]["error"]
        assert json.loads(prior.read_text()) == {"previous": True}
    asyncio.run(with_browser(scenario))


def test_repeated_pagination_stops_instead_of_counting_same_page(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(bot.config, "SEARCH_QUERIES", {"python"})
    async def scenario(page):
        await page.route("https://www.shine.com/**", lambda route: route.fulfill(body=card(123)))
        jobs, metrics = await bot.discover(page, 3, 1000, 0, 0)
        assert len(jobs) == 1
        assert metrics[0]["pages_visited"] == 2
        assert "Pagination repeated" in metrics[0]["pages"][1]["error"]
    asyncio.run(with_browser(scenario))


def test_closed_browser_stops_before_marking_jobs_as_manual_failures():
    async def scenario(page):
        await page.close()
        job = bot.Job(title="Python Developer", company="Example", url="https://www.shine.com/jobs/python/example/123", text="Python FastAPI")
        with pytest.raises(RuntimeError, match="Browser session closed"):
            await bot.score_detailed_jobs(page, [job], 10, 1000, 1)
    asyncio.run(with_browser(scenario))
