# Understanding the JSON Reports

The program creates two JSON files in the `artifacts` folder. JSON is structured
text; it can be opened in Notepad, Visual Studio Code, or a web browser.

## 1. `scored-and-applied.json`

Use this as the main record. It contains:

- `summary`: totals and status counts from the latest run.
- `applied_jobs`: every successful application stored in history.
- `scored_jobs`: every job evaluated during the latest run.
- `search_metrics`: pages, cards, and newly discovered URLs contributed by each
  search phrase.

Each scored job shows:

- `score`: match quality; 60 or more can qualify.
- `accepted`: whether the resume rules passed.
- `status`: what the program did with the job.
- `reasons`: how the score or rejection was decided.
- `url`: the Shine job page.

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
