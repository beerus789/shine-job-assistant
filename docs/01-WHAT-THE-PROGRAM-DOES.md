# What the Program Does

Think of the program as a careful job-search assistant. It follows this order:

1. In live mode, signs in to Shine using the information stored in `.env`.
   Dry-run mode searches public pages without signing in or applying.
2. Searches using Python-backend and GenAI-specific phrases, checking three
   result pages for every phrase by default.
3. Uses search cards to choose candidates for a closer look. Unchecked jobs
   go first, followed by the least recently checked jobs. Among equally fresh
   candidates, the Early Applicant badge takes priority, then match signals.
4. Opens selected Shine job pages and reads their full description, skills, and
   experience requirement.
5. Calculates the final score only from the detailed job data.
6. Ranks suitable jobs with Early Applicant jobs first, then by match score,
   and applies within the configured limits.
7. Selects known salary, experience, and notice-period dropdown cards when asked.
8. Sends slow, redirected, or unfamiliar forms to the manual-review queue.

The Early Applicant badge changes priority, not eligibility. A job still needs
to pass the skill, experience, blocked-keyword, and score rules.

If the detail-page limit is reached, unchecked jobs stay visible as
`not_evaluated`. The next run can check these first if they still appear in the
search results. Every selected job's details are loaded again; a previous score
is never reused to submit an application.

## How an application is verified

The program does not assume that a click worked. For a new application it
requires Shine's application API to return HTTP 200/201 for the same job ID,
then reloads that exact job and requires its main button to remain disabled
**Applied**. Only then does it record the job in `state/history.json`.

If an application opens a supported card, the program clicks the card and then
selects the option whose text matches the value in `.env`. It does not rely on
the option's position, because Shine can reorder the list.

## Files created while it runs

- `artifacts/latest.csv`: only jobs without a confirmed application from the
  latest run, with a reason and recommended next step. It is reset at run start.
- `artifacts/scored-and-applied.json`: scored jobs and complete application history.
- `artifacts/manual-review.json`: unresolved failures that can be handled manually.
- `state/history.json`: jobs already applied to, used to prevent duplicates.
- `state/attempts.json`: failed-attempt counts, cooldowns, and manual-only jobs.
- `state/detail-progress.json`: recent detail-check times, kept separately for
  live runs, dry runs, and discovery audits.
- `artifacts/run-status.json`: the current phase, actual fully scored count,
  and whether the run finished completely, partially, or with an error.
- `artifacts/search-diagnostics.json`: pages visited and search failures.
- `artifacts/error-*.png`: screenshots created only when a job needs review.

## What it will not do

- It will not bypass CAPTCHA or OTP verification.
- It will not invent answers to employer screening questions.
- It will not guess a salary, experience value, or notice period that is absent
  from `.env`.
- It will not interact with an external employer website. Off-Shine redirects
  and new tabs go directly to manual review.
- It will not apply after the daily limit is reached.
- It will not reapply to a URL already stored in application history.
- It will not repeatedly retry an unresolved manual-only job.

[Back to Start Here](../README.md)
