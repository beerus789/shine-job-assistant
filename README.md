# Shine Job Assistant

A conservative Playwright assistant that signs in to Shine, discovers jobs,
scores them against editable matching rules, and applies only within explicit
limits. Every result is recorded for auditing.

## Safety behavior

- A job is recorded as successful only after Shine displays **Applied**.
- Search cards only prioritize candidates; the full Shine job page is loaded
  and scored before any application is attempted.
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
- `target-titles.txt`
- `search-queries.txt`
- `role-signals.txt`

Blank lines and lines beginning with `#` are ignored.

## Reports

- `artifacts/latest.csv`: latest evaluated jobs.
- `artifacts/scored-and-applied.json`: scores and confirmed application history.
- `artifacts/manual-review.json`: unresolved redirects, questions, timeouts, or
  unsupported forms.
- `state/history.json`: local duplicate protection.
- `state/attempts.json`: retry cooldowns and manual-only jobs.

Generated reports and history stay local and are excluded from Git.

## Documentation

1. [What the program does](docs/01-WHAT-THE-PROGRAM-DOES.md)
2. [Setup and everyday use](docs/02-SETUP-AND-EVERYDAY-USE.md)
3. [Resume-based job matching](docs/03-RESUME-BASED-JOB-MATCHING.md)
4. [Settings explained](docs/04-SETTINGS-EXPLAINED.md)
5. [Safety and troubleshooting](docs/05-SAFETY-AND-TROUBLESHOOTING.md)
6. [Understanding the JSON reports](docs/06-UNDERSTANDING-JSON-REPORTS.md)

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

GitHub Actions runs the same test suite for every push and pull request.
