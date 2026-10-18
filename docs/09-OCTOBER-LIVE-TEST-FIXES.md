# October 18, 2026: Live Apply Confirmation Fix

## What happened

A one-attempt live run submitted an application for a suitable Drip Capital
backend role. Shine returned HTTP 201 with `message: SUCCESS`, echoed the same
job ID under the response's `data` object, and emitted its completed event.
The bot looked for `job_id` only at the response's top level, so it incorrectly
put the job in manual review.

The follow-up read-only check showed the job as Applied in Shine. The current
portal renders that status as an accessible “You already applied to this job”
chip in the details card and sticky action bar, rather than the disabled button
used by the older page layout.

## Changes and verification

- Accept Shine's current nested response shape while still checking the exact
  job ID and successful HTTP status.
- Recognize the current Applied chip in the primary details card and sticky
  action bar; keep the legacy button check strict to avoid confusing other jobs'
  Applied buttons with the current job.
- Cap each run by total Apply attempts, including unsuccessful ones, so repeated
  failures cannot trigger unbounded submissions.
- Record this verified application in history and remove its incorrect manual
  review entry, preventing a future duplicate attempt.
- Add regression tests for the nested response and both current and legacy
  Applied indicators.

The live run discovered 177 jobs, fully evaluated 30, and made one Apply
attempt. Shine confirmed the application, and a separate signed-in read-only
page check verified the Applied state. No second Apply was clicked. The smoke
test passed, and the full test suite passed (147 tests).
