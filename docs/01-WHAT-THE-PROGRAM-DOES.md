# What the Program Does

Think of the program as a careful job-search assistant. It follows this order:

1. Signs in to Shine using the information stored in `.env`.
2. Searches using Python-backend and GenAI-specific phrases.
3. Reads each job's title, skills, and experience requirement.
4. Rejects jobs that do not match the resume.
5. Ranks suitable jobs and applies within the configured limits.
6. Selects known salary, experience, and notice-period dropdown cards when asked.
7. Sends slow, redirected, or unfamiliar forms to the manual-review queue.

## How an application is verified

The program does not assume that a click worked. It waits until Shine changes
the main job button to a disabled **Applied** button. Only then does it record
the job in `state/history.json`.

If an application opens a supported card, the program clicks the card and then
selects the option whose text matches the value in `.env`. It does not rely on
the option's position, because Shine can reorder the list.

## Files created while it runs

- `artifacts/latest.csv`: the most recent job evaluation report.
- `artifacts/scored-and-applied.json`: scored jobs and complete application history.
- `artifacts/manual-review.json`: unresolved failures that can be handled manually.
- `state/history.json`: jobs already applied to, used to prevent duplicates.
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

[Back to Start Here](../README.md)
