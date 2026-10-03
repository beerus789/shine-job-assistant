# Fixes from the September diagnostic

The 30 September read-only diagnostic found that Shine's **Be An Early
Applicant** badge appeared on many cards but had no effect on selection. It
also found that a small full-description limit could repeatedly leave relevant
jobs unchecked, reports described every row as evaluated, and a search with
some failed pages could still appear complete.

## How candidate selection works now

The bot extracts the badge as a separate `is_early_applicant` value. It orders
full-description checks by jobs that have not been checked, then jobs checked
longest ago. Within the same check history, it gives the Early Applicant badge
priority, followed by the normal title and skill match. After scoring, eligible
Early Applicant jobs are considered before other eligible jobs, then ordered by
score. The badge changes order only; required Python, role, experience, blocked
keyword, and score rules still apply.

`state/detail-progress.json` keeps recent check timestamps for up to seven days.
It stores no job scores and never confirms an application. Live runs, dry runs,
and discovery audits use separate queues. A changed matching policy starts a
fresh queue. The bot saves progress after writing the matching report; stopping
midway through detail checks may mean some pages are checked again.

## How to read a run

`artifacts/scored-and-applied.json` separates discovered, reported, and fully
scored job counts. Each job has a `detail_evaluated` flag and the
`is_early_applicant` badge flag. `pre_filtered` means only the search-card
filter ran. `not_evaluated` means the full job description remains unchecked.

`artifacts/run-status.json` reports:

- `complete`: configured work finished without a detected search, detail, or
  application failure; application limits can still leave suitable jobs
  unapplied.
- `incomplete`: some discovered jobs remain outside the detail-page budget.
- `partial_failure`: at least one search page, detail check, or application
  failed. This status takes precedence when a failure and unchecked jobs occur
  together.
- `failed`: a fatal error stopped the run.

`artifacts/search-diagnostics.json` lists each page's result so a partial
search is visible even when other queries succeeded. The read-only
`audit_discovery.py` command uses its own detail-check queue and reports which
descriptions it fully read.

The 4 August audit in [document 07](07-DISCOVERY-AUDIT-2026-08-04.md) and the
30 September diagnostic in the local `artifacts/diagnostics` folder are dated
snapshots. Shine listings and badge counts can change over time.

On 3 October 2026, the full local test suite passed **115 tests**. The public
page smoke check found 8 badge-bearing cards among 20 inspected. A bounded dry
run visited all 30 configured search pages, found 600 card instances and 458
unique jobs, and detected the badge on 92 jobs. It fully scored 20 jobs, all
badge-bearing; 16 passed the match rules. The run correctly reported
`incomplete` because the detail budget left 248 candidates unchecked. It had no
failed search pages and submitted no applications. This is a changing search
snapshot, not a promise about later results.

## Verification boundaries

Automated tests cover card extraction and badge preservation, candidate
rotation, matching-policy changes, expired or damaged progress data, accurate
coverage counters, and partial search failures. The application tests use a
local simulated server. A successful simulation does not verify current Shine
login or a real application submission; the read-only smoke test checks the
current public page selectors without signing in or clicking Apply.

[Back to Start Here](../README.md)
