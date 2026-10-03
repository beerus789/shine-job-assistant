# Safety and Troubleshooting

## "Application run not started: ... limit reached"

The message names the exact cap: the per-run limit, the daily limit, or both.
If a cap is already exhausted at startup, the program writes the reason and
current counts to `artifacts/run-status.json` and does not open a browser.
Otherwise, suitable jobs after a cap is reached are listed in `latest.csv`
with a precise limit status.

## The browser changes many URLs but applies to nothing

Open `artifacts/latest.csv`. It is reset at the beginning of each run and only
lists jobs without a confirmed application. Successful `applied`,
`already_applied`, and `already_seen` outcomes are omitted. For each remaining
job, `reason_not_applied` describes the issue and `recommended_action` gives a
next step. Common statuses mean:

- `rejected`: the job failed the matching rules.
- `pre_filtered`: the search card failed the preliminary title or experience
  or role-signal checks; the full description was not read. `reasons` explains
  the exact title phrase, experience cap, or weak title match.
- `not_evaluated`: the detail-page limit left this job unchecked in this run.
- `shortlisted`: dry-run mode found a suitable job but did not apply.
- `run_limit`: the per-launch cap stopped further applications.
- `daily_limit`: the daily cap stopped further applications.
- `both_application_limits`: both global caps were reached at the same point.
- `role_family_limit`: the per-role-family cap stopped further applications.
- `retry_cooldown`: an earlier temporary failure is waiting for its retry time.
- `manual_review_pending`: a person must verify or complete the job first.
- `needs_review`: the page changed, redirected, timed out, or needs attention.

For `rejected`, review the `reasons` column and the job description; change your
matching rules only if the role truly fits. For `pre_filtered`, the full
description was not opened, so adjust preliminary title/experience rules only
if they are excluding suitable jobs. For `not_evaluated`, let a later run check
the job or increase `MAX_DETAIL_JOBS_PER_RUN`. For `shortlisted`, the run was a
preview; no application was sent. For a limit status, review the named cap in
`.env`; the daily cap resets on the next local calendar day, while the per-run
cap resets on a new launch. For `needs_review`, open
`artifacts/manual-review.json`, inspect any screenshot, then verify or finish
the application manually. Do not retry an outcome that might already have been
submitted until you verify its state on Shine.

The same information appears in `artifacts/scored-and-applied.json`. Automation
failures remain in `artifacts/manual-review.json` until the job URL is recorded
in successful application history.

Check `artifacts/run-status.json` before concluding that every job was checked:

- `complete`: the run finished with no detected search, detail, or application
  failures and no candidates left outside the detail budget. Application caps
  can still prevent otherwise suitable jobs from being applied to.
- `running`: the process is in the saved phase and has not written its final
  outcome yet.
- `incomplete`: the detail-page cap left candidates unchecked. The next run
  prioritizes those jobs if they still appear in search results.
- `partial_failure`: at least one search page, detail check, or application
  failed. The summary can also show unchecked jobs when both issues occur.
- `failed`: a fatal error stopped the run; the saved phase and error identify
  where it stopped.

Use `artifacts/search-diagnostics.json` for page failures and
`artifacts/manual-review.json` for jobs needing attention. A full-description
failure is not a confirmed application failure: no Apply step occurred for
that job.

Shine's **My Jobs → Applied** tab can briefly keep showing Recommended Jobs
after the tab is selected. During live verification it took about five seconds
to replace that content. The settled page exposed only the latest 20 applied
jobs and did not show pagination controls, so older confirmed applications may
not be visible there. Open an individual job URL and check its disabled primary
`Applied` button when reconciling an older entry.

## Search pages load gradually

The bot intentionally waits two to five seconds between search pages. This
reduces burst traffic and gives each page time to settle. The delay does not
apply before the first search page.

## Only some suitable-looking jobs were checked

Look at `not_evaluated_in_this_run` in the main JSON summary. The detail limit
is intentional, and a later run rotates toward previously unchecked candidates.
The Early Applicant badge raises priority among candidates with equal check
history, but it cannot bypass experience or skill rules. Missing a skill on a
short search card alone does not reject a job; the full description is checked
when that candidate gets its turn.

Rotation covers jobs rediscovered in the current searches. It does not revisit
listings that have disappeared from search results or guarantee a fixed number
of applications. A low score, cooldown, manual-only hold, verified history, or
application cap can still prevent an application.

## A job redirects outside Shine

The bot checks the destination before looking for an Applied button or another
form. If the current page or a newly opened tab is not an HTTPS `shine.com`
page, the new tab is closed when possible and the job goes straight to manual
review. The bot does not read, fill, or submit the external website.

## Shine asks for OTP or CAPTCHA

Complete it manually in the visible browser. The program intentionally does not
bypass account verification.

## A job asks screening questions

The job is sent to manual review. Answering automatically could provide
incorrect information, so the bot never invents responses.

## A job shows an unfamiliar form or takes too long

One job receives 45 seconds by default. An unknown control, missing submit
button, redirect, or expired deadline produces `needs_review`; the browser then
moves to the next job.

Questions, external redirects, and unsupported forms become `manual_only` and
are not retried automatically. Network and timeout failures receive one retry
after the configured cooldown; a second transient failure also becomes
`manual_only`. This state is stored locally in `state/attempts.json`.

Open `artifacts/manual-review.json` and use the saved job URL to finish it
manually. A reason and screenshot are included when possible.

Every active `error-*.png` directly inside `artifacts` belongs to an entry in
`manual-review.json`. Screenshots from older runs or jobs later confirmed as
applied are moved to `artifacts/stale-screenshots`; they are retained only as
diagnostics and do not represent current failures. Screenshot filenames include
a short job identifier so listings with the same title cannot overwrite one
another.

## Salary or experience was not selected

Check the corresponding `.env` value and the failure reason in
`manual-review.json`. The bot selects by visible wording instead of a fixed
option number. A new unit or unsupported control is deliberately left for a
person rather than guessed.

## Login fails

Confirm `.env` is in the project folder and contains non-empty `SHINE_EMAIL` and
`SHINE_PASSWORD` values. Do not add spaces around `=`.

## The terminal says "Connection closed while reading from the driver"

This message means Chromium or Playwright disconnected while the program was
closing the browser. Current versions of the project safely handle that
shutdown race. If it appears with an older copy, pull the latest code.

Check the modification time of `artifacts/scored-and-applied.json`. If it was
updated by the run, the evaluation report was saved before shutdown. Also check
the report's `mode`: `dry_run` evaluates and shortlists jobs but deliberately
does not submit any applications.

## The website layout changes

Look for new `error-*.png` files under `artifacts`. They show the page at the
time of failure and help identify which selector needs updating.

For deeper local diagnostics, set `ENABLE_TRACING=true`. A failed run saves
`artifacts/trace.zip`; a clean run discards the recording. The trace starts only
after login succeeds, but it can still contain personal profile details, job
URLs, screenshots, and form values. Never upload or commit it. The entire
`artifacts` directory is ignored by Git.

## Resetting history

Do not delete `state/history.json` merely to rerun the program. Deleting it
removes duplicate protection. Remove history only when you intentionally want
the bot to forget earlier applications.

Similarly, remove a URL from `state/attempts.json` only when you intentionally
want to release it from cooldown or manual-only status.

[Back to Start Here](../README.md)
