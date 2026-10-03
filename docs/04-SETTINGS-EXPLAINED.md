# Settings Explained

Most day-to-day settings are in `.env`. Login values are private; all other
settings can be adjusted using plain numbers or `true` / `false`.

| Setting | Safe example | Meaning |
|---|---:|---|
| `DRY_RUN` | `true` | Scores jobs without submitting applications. |
| `HEADLESS` | `false` | The browser remains visible. |
| `ENABLE_TRACING` | `false` | Saves a private Playwright trace only when a run fails. |
| `MAX_APPLICATIONS_PER_RUN` | `20` | Maximum successful applications in one launch. When reached, remaining suitable jobs are marked `run_limit`. |
| `MAX_APPLICATIONS_PER_DAY` | `20` | Hard daily limit across launches. When reached, remaining suitable jobs are marked `daily_limit`. |
| `MAX_APPLICATIONS_PER_ROLE_FAMILY` | `20` | Additional ceiling for a role family in one launch. This never raises either global cap. |
| `MAX_PAGES_PER_SEARCH` | `3` | Checks the first three pages of each precise search. |
| `MAX_DETAIL_JOBS_PER_RUN` | `250` | Maximum candidate pages attempted for final scoring in one run; remaining candidates get priority in later runs. |
| `SEARCH_DELAY_MIN_SECONDS` | `2` | Minimum pause between search pages. |
| `SEARCH_DELAY_MAX_SECONDS` | `5` | Maximum pause between search pages. |
| `ACTION_DELAY_SECONDS` | `5` | Pause after a verified application. |
| `NAVIGATION_TIMEOUT_SECONDS` | `30` | Maximum wait for page navigation. |
| `AUTH_TIMEOUT_SECONDS` | `15` | Maximum wait for the signed-in job page. |
| `APPLY_TIMEOUT_SECONDS` | `15` | Maximum wait for the Applied confirmation. |
| `DETAIL_TIMEOUT_SECONDS` | `20` | Maximum time for one detail-page extraction. |
| `PER_JOB_TIMEOUT_SECONDS` | `45` | Hard limit for one complete job attempt. |
| `MANUAL_RETRY_DELAY_HOURS` | `72` | Cooldown before retrying a transient failure. |
| `MAX_TRANSIENT_ATTEMPTS` | `2` | Failed transient attempts before manual-only status. |
| `CANDIDATE_EXPERIENCE_YEARS` | blank | Your years of experience. |
| `CANDIDATE_EXPERIENCE_MONTHS` | blank | Your extra months of experience. |
| `CURRENT_SALARY_LPA` | blank | Your current annual salary, in lakhs. |
| `EXPECTED_SALARY_LPA` | blank | Your expected annual salary, in lakhs. |
| `NOTICE_PERIOD_DAYS` | blank | Your notice period in days. |

## When to change timing

A previous ten-job test measured roughly 0.5 seconds for page loading,
0.3 seconds for authentication hydration, and 2 seconds for Apply confirmation.
Those are historical measurements, not guarantees for today's site or network.
The bot waits for the expected page state within the configured deadline.

Increase a timeout only if reports repeatedly show a timeout and the internet
connection is slow. Do not reduce them below 10 seconds.

`PER_JOB_TIMEOUT_SECONDS` covers the whole attempt, including navigation,
authentication, card selection, and confirmation. When it expires, that job is
added to `manual-review.json` and the next job is attempted.

Detail scoring can take several minutes even when it is working. For example,
250 pages each reaching the 20-second detail timeout would take about
83 minutes, before searching or applying. Reduce `MAX_DETAIL_JOBS_PER_RUN` for
shorter runs; unchecked candidates take priority on later runs. Increasing the
cap does not repair broken page selectors or a failed login.

## Application-card values

These values are used only if Shine asks for the corresponding field. The bot
opens the dropdown and matches the visible option text. For example, `4` matches
`4 yrs`, `19` can match `19 LPA` or a `15-20 LPA` range, and `30` matches
`1 Month`.

Keep them truthful. If a field is required and its value is blank, the bot sends
the job to manual review instead of guessing.

## Job-selection settings

The [job_settings](../job_settings/README.md) directory contains separate,
plain-text files:

- `target-titles.txt`: acceptable titles and aliases.
- `search-queries.txt`: precise phrases sent to Shine.
- `required-skills.txt`: skills every job must contain.
- `preferred-skills.txt`: resume strengths that increase the score.
- `blocked-keywords.txt`: unsuitable role/title phrases, matched against the
  job title to avoid false rejection from incidental description mentions.
- `blocked-description-keywords.txt`: explicit disqualifying phrases matched
  in the full job text, such as "Java only" or "unpaid internship".
- `role-signals.txt`: proof that a job is backend or applied AI.

Add or remove one line and save the file. Blank lines and lines beginning with
`#` are ignored. `config.py` only loads these files and contains the less-common
numeric thresholds such as minimum score and maximum experience.

The included search list contains ten focused queries. With three pages per
query the bot can inspect up to 600 cards, then deduplicates repeated job URLs.
Review `search_metrics` in the main JSON report after several runs before
removing another query.

## How the detail-page budget rotates

`state/detail-progress.json` remembers recent check times, not scores. Unchecked
jobs come first, then jobs checked longest ago. The Early Applicant badge and
match signals order candidates with equal check history. Verified application
history, cooldowns, and manual-only holds do not consume the detail budget.

The record lasts up to seven days and is reset for changed matching rules.
Live runs, dry runs, and discovery audits use separate records, so a preview
does not move jobs to the back of the next live run. Progress is saved after
report outcomes are saved; stopping in the middle of detail scoring can cause
those pages to be checked again. There is no extra `.env` setting to enable it.

[Back to Start Here](../README.md)
