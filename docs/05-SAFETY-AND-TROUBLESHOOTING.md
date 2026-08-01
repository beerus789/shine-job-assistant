# Safety and Troubleshooting

## "Daily application limit reached"

Nothing is wrong. The number of confirmed applications recorded today reached
`MAX_APPLICATIONS_PER_DAY`. The program stops before opening the browser and can
run again on the next calendar day.

## The browser changes many URLs but applies to nothing

Open `artifacts/latest.csv` and inspect the `status` column:

- `rejected`: the job failed the matching rules.
- `shortlisted`: dry-run mode found a suitable job but did not apply.
- `already_seen`: the URL is already in local application history.
- `role_family_limit`: enough jobs of that type were already applied in the run.
- `needs_review`: the page changed, redirected, timed out, or needs attention.
- `applied`: Shine displayed the confirmed Applied state.

The same information appears in `artifacts/scored-and-applied.json`. Automation
failures remain in `artifacts/manual-review.json` until the job URL is recorded
in successful application history.

## Search pages load gradually

The bot intentionally waits two to five seconds between search pages. This
reduces burst traffic and gives each page time to settle. The delay does not
apply before the first search page.

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

## Salary or experience was not selected

Check the corresponding `.env` value and the failure reason in
`manual-review.json`. The bot selects by visible wording instead of a fixed
option number. A new unit or unsupported control is deliberately left for a
person rather than guessed.

## Login fails

Confirm `.env` is in the project folder and contains non-empty `SHINE_EMAIL` and
`SHINE_PASSWORD` values. Do not add spaces around `=`.

## The website layout changes

Look for new `error-*.png` files under `artifacts`. They show the page at the
time of failure and help identify which selector needs updating.

## Resetting history

Do not delete `state/history.json` merely to rerun the program. Deleting it
removes duplicate protection. Remove history only when you intentionally want
the bot to forget earlier applications.

Similarly, remove a URL from `state/attempts.json` only when you intentionally
want to release it from cooldown or manual-only status.

[Back to Start Here](../README.md)
