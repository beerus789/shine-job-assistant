# Understanding the JSON Reports

The program writes JSON reports in `artifacts`. JSON is structured text; it can
be opened in Notepad, Visual Studio Code, or a web browser.

## 1. `scored-and-applied.json`

Use this as the main record. It contains:

- `status`: whether work completed, remains unchecked, or had a failure.
- `summary`: separate counts for discovered jobs, report rows, and full-detail
  scoring.
- `applied_jobs`: every successful application stored in history.
- `scored_jobs`: one row for each discovered job, including jobs not fully
  scored.
- `search_metrics`: pages, cards, and newly discovered URLs contributed by each
  search phrase.

Each scored job shows:

- `score`: match quality; 60 or more can qualify.
- `accepted`: whether the resume rules passed.
- `status`: what the program did with the job.
- `reasons`: how the score or rejection was decided.
- `url`: the Shine job page.
- `detail_evaluated`: `true` only when that run loaded and scored the full
  description.
- `is_early_applicant`: whether the search card carried Shine's badge. The
  badge changes priority, not eligibility or score.

The summary's `evaluated_in_this_run` counts full-description checks.
`discovered_in_this_run` counts distinct jobs found and `reported_in_this_run`
counts rows written. `pre_filtered_in_this_run` counts search cards set aside
before opening their descriptions; `not_evaluated_in_this_run` counts
candidates left outside the detail-page budget. A `needs_review` item can have
`detail_evaluated: true` when scoring succeeded and a later application step
failed.

`not_evaluated` means the job was discovered but its full page was outside the
configured detail-page budget. It is not the same as a scoring rejection. Raise
`MAX_DETAIL_JOBS_PER_RUN` if suitable-looking jobs repeatedly receive this
status.

`pre_filtered` means a preliminary title, experience, or role-signal rule
excluded the card before reading its full description. The `status` and
`reasons` fields identify the exact rule, including the closest configured title
for a weak title match. Treat this as a rule-based skip, not as proof that the
full job page was checked. `pre_filter_reason_counts` rolls up these reasons in
the summary so borderline rules are easier to audit.

Application caps have separate statuses and summary counters:
`run_limit_in_this_run`, `daily_limit_in_this_run`, and
`both_application_limits_in_this_run`. If a cap is already exhausted before
the browser starts, `run-status.json` records `phase: preflight_limits` and the
specific blocking cap instead of silently appearing to be a login failure.

## Run and search reports

`artifacts/run-status.json` records the current phase and the actual full-detail
count. `running` means work is still underway. A finished run can be `complete`,
`incomplete` (the detail budget left candidates unchecked), `partial_failure`
(a search, detail, or application step failed), or
`failed` (a fatal error stopped the run).

`artifacts/search-diagnostics.json` records the pages visited for each query.
Check it when the summary's `search_pages_failed` count is greater than zero.
Some results from a partial search may still be reported, so inspect failed
pages before treating that search as complete.

`state/detail-progress.json` stores only recent check times, separated for live
runs, dry runs, and audits. It stores no scores and does not confirm an
application. Unchecked candidates get priority in later runs while they
continue to appear in search results. Entries expire after seven days or when
matching settings change.

Each new application-history entry also has a `confirmation` object. A newly
submitted job is recorded only when Shine returns HTTP 200/201 for the same job
ID and the current job's disabled `Applied` button remains after a fresh reload.
The object records the method, job ID, HTTP status, response job ID, and
verification time. A job that was already applied uses the persisted primary
button as its confirmation method and does not pretend that a new request was
sent.

An older history record without this job-specific evidence is not counted as a
confirmed application. It remains visible in `manual-review.json` until the
exact job is verified.

## 2. `manual-review.json`

Use this only when `unresolved_count` is greater than zero. Every item contains:

- `failure_reason`: why automation could not confirm the application.
- `url`: the page to open manually.
- `screenshot`: the captured error page when available.
- `manual_action`: simple instructions for completing the job yourself.
- `automation_status`: either waiting for a retry or manual-only.
- `attempt_count`: number of failed automated attempts.
- `retry_after`: earliest automatic retry time, or `null` for manual-only jobs.

Typical failure reasons include a 45-second timeout, a redirect, an unfamiliar
employer question, a missing `.env` answer, or a dropdown option that could not
be matched truthfully.

Failures are preserved across runs. When a job URL later appears in successful
application history, it is removed from the unresolved manual-review queue.

## Important distinction

A high score does not mean an application succeeded. Only `status: applied` in
application history means Shine confirmed the application. Anything in
`manual-review.json` must be checked by a person.

[Back to Start Here](../README.md)
