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

The browser stays visible. Let it finish unless Shine asks for OTP or CAPTCHA.
After the run, check `artifacts/manual-review.json`. A job placed there was not
counted as applied and can be opened using its saved URL.

## Review without applying

Open `.env` and change:

```dotenv
DRY_RUN=true
```

Run the program, then inspect `artifacts/latest.csv`. Rows marked `shortlisted`
would be considered for a future live run.

## Enable live applications

Change the same setting back to:

```dotenv
DRY_RUN=false
```

The next run can submit applications. The default per-run limit is three. The
daily limit is read from `MAX_APPLICATIONS_PER_DAY` in your `.env`.

## Stop the program

Click the PowerShell window and press `Ctrl+C`. A job is recorded only after
Shine visibly confirms **Applied**.

[Back to Start Here](../README.md)
