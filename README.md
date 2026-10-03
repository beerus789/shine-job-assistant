# Shine Job Assistant

A conservative Playwright assistant that signs in to Shine, discovers jobs,
scores them against editable matching rules, and applies only within explicit
limits. Every result is recorded for auditing.

## Safety behavior

- A new job is recorded as successful only after Shine returns HTTP 200/201
  for that exact job ID and the current job still displays disabled **Applied**
  after a fresh reload.
- Search cards only prioritize candidates; the full Shine job page is loaded
  and scored before any application is attempted.
- Shine's **Be An Early Applicant** badge affects priority. It never makes an
  unsuitable job eligible or adds points to its match score.
- When a detail-page limit leaves jobs unchecked, later runs give unchecked
  jobs their turn before revisiting recently checked jobs.
- External websites are never used. External redirects or tabs go directly to
  the manual-review queue.
- Unknown questions, unsupported controls, and missing truthful answers are not
  guessed.
- Each attempt has a hard timeout, so one unusual application cannot block the
  rest of a run.
- Daily, per-run, and role-family limits reduce accidental bulk applications.
- CAPTCHA and OTP verification are never bypassed.

Review Shine's current rules and use automation responsibly. Start in dry-run
mode whenever you change matching settings.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item .env.example .env
```

Open `.env`, enter your own Shine credentials and truthful application values,
and keep `DRY_RUN=true` for the first run:

```powershell
.\.venv\Scripts\python.exe bot.py
```

Never commit `.env`. The included `.gitignore` excludes credentials, local
browser environments, application history, screenshots, and generated reports.

## Change job-matching rules without code

The [job_settings](job_settings/README.md) folder contains plain-text lists.
Add or remove one line to change a rule:

- `required-skills.txt`
- `preferred-skills.txt`
- `blocked-keywords.txt`
- `blocked-description-keywords.txt`
- `target-titles.txt`
- `search-queries.txt`
- `role-signals.txt`

Blank lines and lines beginning with `#` are ignored.

## Reports

- `artifacts/latest.csv`: only jobs without a confirmed application in the most
  recent run, with the reason and recommended next step. It resets each run.
- `artifacts/scored-and-applied.json`: scores and confirmed application history.
- `artifacts/manual-review.json`: unresolved redirects, questions, timeouts, or
  unsupported forms.
- `state/history.json`: local duplicate protection.
- `state/attempts.json`: retry cooldowns and manual-only jobs.
- `artifacts/run-status.json`: whether the run completed, has missing detail
  coverage, or encountered failures.
- `artifacts/search-diagnostics.json`: search-page results and failures.
- `state/detail-progress.json`: when candidate details were last checked, used
  to share the detail-page budget across runs.

Generated reports and history stay local and are excluded from Git.

## Documentation

1. [What the program does](docs/01-WHAT-THE-PROGRAM-DOES.md)
2. [Setup and everyday use](docs/02-SETUP-AND-EVERYDAY-USE.md)
3. [Resume-based job matching](docs/03-RESUME-BASED-JOB-MATCHING.md)
4. [Settings explained](docs/04-SETTINGS-EXPLAINED.md)
5. [Safety and troubleshooting](docs/05-SAFETY-AND-TROUBLESHOOTING.md)
6. [Understanding the JSON reports](docs/06-UNDERSTANDING-JSON-REPORTS.md)
7. [Discovery audit - 4 August 2026](docs/07-DISCOVERY-AUDIT-2026-08-04.md)
8. [September diagnostic fixes](docs/08-SEPTEMBER-DIAGNOSTIC-FIXES.md)
9. [Tech-lead code review](docs/09-TECH-LEAD-CODE-REVIEW.md)

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

GitHub Actions runs the same test suite for every push and pull request.

For a read-only check against Shine's current public page structure:

```powershell
.\.venv\Scripts\python.exe smoke_test.py
```

The smoke test does not enter credentials or click Apply.

For a thorough read-only run across every configured search page and selected
full job description:

```powershell
.\.venv\Scripts\python.exe audit_discovery.py
```

This creates `artifacts/discovery-audit.json` without signing in or applying.
