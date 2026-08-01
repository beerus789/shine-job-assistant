# Understanding the JSON Reports

The program creates two JSON files in the `artifacts` folder. JSON is structured
text; it can be opened in Notepad, Visual Studio Code, or a web browser.

## 1. `scored-and-applied.json`

Use this as the main record. It contains:

- `summary`: totals and status counts from the latest run.
- `applied_jobs`: every successful application stored in history.
- `scored_jobs`: every job evaluated during the latest run.

Each scored job shows:

- `score`: match quality; 60 or more can qualify.
- `accepted`: whether the resume rules passed.
- `status`: what the program did with the job.
- `reasons`: how the score or rejection was decided.
- `url`: the Shine job page.

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
