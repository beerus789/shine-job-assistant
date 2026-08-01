# Settings Explained

Most day-to-day settings are in `.env`. Login values are private; all other
settings can be adjusted using plain numbers or `true` / `false`.

| Setting | Safe example | Meaning |
|---|---:|---|
| `DRY_RUN` | `true` | Scores jobs without submitting applications. |
| `HEADLESS` | `false` | The browser remains visible. |
| `MAX_APPLICATIONS_PER_RUN` | `3` | At most three successful applications per launch. |
| `MAX_APPLICATIONS_PER_DAY` | `10` | Hard daily limit across all launches. |
| `MAX_PAGES_PER_SEARCH` | `1` | Checks the first page of each precise search. |
| `MAX_DETAIL_JOBS_PER_RUN` | `40` | Maximum candidate pages loaded for final scoring. |
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

The live ten-job test measured about 0.5 seconds for page loading, 0.3 seconds
for authentication hydration, and 2 seconds for Apply confirmation. The current
15-second waits are intentionally much larger than the measured times.

Increase a timeout only if reports repeatedly show a timeout and the internet
connection is slow. Do not reduce them below 10 seconds.

`PER_JOB_TIMEOUT_SECONDS` covers the whole attempt, including navigation,
authentication, card selection, and confirmation. When it expires, that job is
added to `manual-review.json` and the next job is attempted.

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
- `blocked-keywords.txt`: unsuitable roles rejected immediately.
- `role-signals.txt`: proof that a job is backend or applied AI.

Add or remove one line and save the file. Blank lines and lines beginning with
`#` are ignored. `config.py` only loads these files and contains the less-common
numeric thresholds such as minimum score and maximum experience.

[Back to Start Here](../README.md)
