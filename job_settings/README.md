# Editing Job-Matching Settings

This folder contains the lists used to find and score jobs. You do not need to
edit Python code.

## How to edit a list

1. Open the relevant `.txt` file in Notepad or another text editor.
2. Put one title, skill, or phrase on each line.
3. Add a new line to include something, or delete its line to remove it.
4. Save the file and run the bot normally.

Blank lines and lines beginning with `#` are ignored. Matching is
case-insensitive, so `FastAPI` and `fastapi` behave the same.

## Which file should I change?

- `required-skills.txt`: every accepted job must mention all listed skills.
- `preferred-skills.txt`: these improve a job's score but are not mandatory.
- `blocked-keywords.txt`: role/title phrases that reject a job only when they
  appear in the title. This avoids rejecting a backend role just because its
  description mentions a neighboring team or technology.
- `blocked-description-keywords.txt`: explicit disqualifiers that reject a job
  when they appear anywhere in the job text, such as `unpaid internship` or
  `Java only`.
- `target-titles.txt`: job titles considered similar to the desired roles.
- `search-queries.txt`: phrases entered into Shine's job search.
- `role-signals.txt`: proof that a job is backend or applied-AI work.

Keep `python` in `required-skills.txt` unless you intentionally want to apply
for non-Python positions. Run with `DRY_RUN=true` after a major change and
review the generated report before enabling live applications.

Changing matching lists or scoring thresholds resets the corresponding recent
detail-check ordering. The next run uses the new rules. Dry runs keep their own
ordering and do not move jobs behind others in a live run.

Shine's Early Applicant badge is detected automatically; do not add that phrase
to required skills or role signals. It changes the order of suitable jobs, not
their eligibility or score.

The main JSON report records `cards_found` and `unique_jobs_added` for every
search query. A query that repeatedly adds no unique jobs is a good candidate
for removal.
