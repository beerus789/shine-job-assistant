# Tech-Lead Review: Rules, Safety, and Failure Modes

**Review date:** 3 October 2026
**Scope:** matching policy, experience extraction, search coverage, application
state, retry/reporting behavior, configuration, and the latest supplied run log.
This was a local code/report review; no authenticated job pages were opened and
no applications were submitted.

## Executive summary

The supplied run did apply across several role types. The saved report shows
141 full-description evaluations, 87 eligible jobs, 20 confirmed applications,
67 jobs stopped by the daily/run limit, and zero recorded application failures.
The main reason suitable jobs were left unapplied was the global run cap and
the absence of category quotas—not a failed Apply click. Four search pages also
returned HTTP 503, so the search was incomplete.

The review fixed several correctness and safety defects described below. The
full local suite now passes **127 tests**. Live UI behavior remains unverified;
the unit tests cannot guarantee that Shine's current selectors and workflow
still match.

## Matching and run rules observed

- The candidate profile remains **4 years**; `MAX_REQUIRED_EXPERIENCE` remains
  **4**. A clear **3–6 years** range includes a four-year candidate; a **6+**
  minimum does not.
- Python is mandatory. At least one backend/applied-AI signal is mandatory.
  The minimum score is 60. Blocked role terms still reject a listing.
- The local ignored `.env` was set to live mode, with a 20-per-run cap, a
  100-per-day cap, a 20-per-role-family cap, and a 250-detail-page cap. The
  repository example defaults to 20 per day. I did not alter `.env` or read
  credential values.
- The app ranks all eligible roles together. It does not reserve a number of
  applications for each search phrase or category. SDE2 and SDE3 also share an
  `sde` role family.

## Findings, remediations, and consequences

### Fixed

| Severity | Finding | Change made | If left unfixed |
|---|---|---|---|
| Critical | `env_bool()` treated every unrecognized value as false. A typo in `DRY_RUN` could silently turn a preview into live mode. | Boolean values are now parsed strictly; unknown values raise an error before browser startup. | A misspelling could unexpectedly submit applications. |
| High | Range parsing could reinterpret the upper endpoint of `3–6 years` as a separate six-year minimum. The summary card could also override a clear range in the full description. | Experience ranges are parsed without double-counting their upper endpoint; a complete range in the full description wins over a single-value card. A single `6+` minimum remains strict. | Suitable four-year candidates could be rejected, while careless relaxation could also admit genuine `6+` requirements. |
| High | Unverified legacy `applied` history was shown for review but was not held out of the application loop. | Such records now become `manual_review_pending`; dated legacy success records count conservatively toward the daily cap. | An older “applied” row without confirmation could be submitted again, or the daily cap could undercount applications. |
| High | Two launches could read the same history and daily count before either saved a result. | Added a cross-process OS lock shared by the bot, audit, and smoke workflows. A second concurrent run exits before browsing. | Concurrent launches could duplicate an application or exceed configured limits. |
| High | The unknown-question check treated any label containing “experience” as a supported profile field. | Only exact known profile-field labels are whitelisted. For example, “years of experience with Kafka?” now goes to manual review. | An employer screening question might be overlooked and an incomplete or inappropriate response submitted. |
| Medium | `role_family()` used substring checks against full descriptions; `rag` could match `storage`, and incidental description text could change a job's quota category. | Role families now use token-aware matching on the title. | A job could consume the wrong family's cap, starving other roles or misreporting category counts. |
| Medium | State and report JSON/CSV files were overwritten in place. An interrupted write could leave truncated files. | History, attempts, JSON reports, and CSV reports now use same-directory temporary files and atomic replacement. | A crash during a write could corrupt duplicate protection or make reports unreadable. |
| Medium | Timing/retry values could accept negative delay or zero retry attempts. The code's fallback action delay differed from the documented five seconds. | Added validation for action/retry settings and aligned the default action delay to five seconds. | Misconfiguration could remove pauses or disable the intended retry cooldown. |

### Still open / requires an explicit product decision

| Severity | Finding | Recommended next step | If not addressed |
|---|---|---|---|
| High | No category quota or balanced selection exists. The latest run's 20 total cap stopped 67 other eligible listings; 50 per category cannot be achieved with a 20-per-run cap. | If Shine authorizes automation, first define exact categories and a total volume that is allowed; then report and test fair per-category allocation. Do not simply remove all caps. | Higher-scoring or Early Applicant listings can consume the whole run while other categories receive few or no attempts. |
| High | Four search pages failed with HTTP 503: Agentic AI, Django, GenAI backend, and Python backend queries. The run correctly reported `partial_failure`, but it does not retry a failed page. | With permission, consider at most one delayed retry for transient 5xx responses, then preserve the failure and continue later; avoid rapid retry loops. | Jobs from those searches may be missed in that run; repeated outages can bias coverage toward the queries that succeeded. |
| Medium | There are synthetic selector tests, but no authorized end-to-end tests for the current live login, Apply form, and confirmation flow. | Use sanitized recorded HTML/fixtures for selector regressions. Do live verification only if the required permission is obtained and use a small, reviewed test scope. | A Shine UI change can cause missed jobs or manual-review outcomes. The strict confirmation path favors false negatives over claiming an unverified application. |
| Medium | Local `.env` differs from the repository sample: the local daily cap is 100 while the example is 20. | Review the ignored local cap before any permitted live use. The current code does not overwrite it. | A future authorized run could submit far more applications per day than the sample documentation suggests. |

## Latest run: what the evidence says

The supplied output and local report agree on the key distinction:

- **20 applied** and **67 `daily_or_run_limit`**: the global 20-per-run cap
  was reached. The code did not record those 67 as Apply failures.
- **87 accepted** = the 20 applied plus the 67 limit-stopped candidates.
- **54 rejected** and **129 pre-filtered**: matching rules excluded these
  before application. In the rejected rows, 21 lacked required Python and 17
  exceeded the four-year minimum-experience limit.
- **4 HTTP 503 pages** explain `partial_failure`; `application_failed_in_this_run`
  was zero.
- A title-based breakdown found applications in backend, framework-backend,
  GenAI/RAG, SDE, software-engineering, and other titles. Specifically, the
  report included two SDE2 and one SDE3 application. This was not an all-or-none
  role-family selection failure.

## Verification boundaries

The local suite passes **127 tests**. `git diff --check` is expected to report
only existing Windows CRLF-normalization notices. No live Shine run was made.
The permission gate was later reverted at the user's request. No live Shine run
was made as part of this review.
