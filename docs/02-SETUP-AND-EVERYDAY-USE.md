# Setup and Everyday Use

## First-time setup

Open PowerShell in the project folder and run:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item .env.example .env
```

Add the Shine email and password to `.env`. Do not paste them into documentation
or commit that file to source control.

Also enter your truthful experience, current and expected salary, and notice
period. Salary is written in LPA (lakhs per annum). These personal values are
blank in `.env.example` and must remain only in the ignored `.env` file.

## Normal daily use

Open PowerShell in the project folder, then run:

```powershell
.\.venv\Scripts\python.exe bot.py
```

The browser stays visible when `HEADLESS=false`. Let it finish unless Shine asks
for OTP or CAPTCHA.

After the run, check `artifacts/manual-review.json`. A job placed there was not
counted as applied and can be opened using its saved URL.

`artifacts/latest.csv` is cleared at the start of every run and updated as jobs
are processed. It contains only jobs without a confirmed application; confirmed
applications are left out. Read `reason_not_applied` for what happened and
`recommended_action` for what to check or do next. If every attempted job was
confirmed as applied, the CSV contains only its header row.

## Review without applying

Open `.env` and change:

```dotenv
DRY_RUN=true
```

Run the program, then inspect `artifacts/latest.csv`. Rows marked `shortlisted`
passed the full-description checks in this preview. A future live run reads the
job details again before deciding to apply. A dry run does not sign in or submit
applications, but it does update local reports and its own detail-check progress.
It does not change application history or failed-attempt cooldowns.

Check `artifacts/run-status.json` too. `incomplete` means the detail-page cap
left candidates unchecked; another run gives those candidates priority if they
are still found. `partial_failure` means some search, detail, or application
steps failed, so inspect the reports before treating the run as finished.

## Enable live applications

Change the same setting back to:

```dotenv
DRY_RUN=false
```

The next run can submit applications. The default per-run limit is twenty. The
daily limit is read from `MAX_APPLICATIONS_PER_DAY` in your `.env`.
## Stop the program

Click the PowerShell window and press `Ctrl+C`. A new application is recorded
only after a matching successful server response and an **Applied** button
that remains after reloading the job. If you stop during an application, check
that job on Shine before retrying it.

## Check whether Shine changed its pages

Run the read-only smoke check:

```powershell
.\.venv\Scripts\python.exe smoke_test.py
```

It checks the public login fields, search cards, job details, skills, and main
Apply selector. It never fills credentials or clicks Apply.

For a complete read-only discovery and scoring report using all configured
pages, run:

```powershell
.\.venv\Scripts\python.exe audit_discovery.py
```

Open `artifacts/discovery-audit.json` to see every accepted, rejected, and
`not_evaluated` job, including whether its full description was checked. This
command does not sign in or apply and keeps its detail progress separate from
live runs and previews.

[Back to Start Here](../README.md)
