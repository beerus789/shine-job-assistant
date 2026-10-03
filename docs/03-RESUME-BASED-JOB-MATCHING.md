# Resume-Based Job Matching

## Profile used by the program

The included example profile assumes nearly four years of production work in
Python, FastAPI, Django, asynchronous processing, Redis, Celery, RabbitMQ,
cloud services, and GenAI/RAG systems.

## Best-fit roles

### Primary targets

- Python Backend Engineer or Developer
- Backend Software Engineer
- Software Engineer II / SDE 2 - Backend
- FastAPI or Django Engineer
- GenAI Backend Engineer
- RAG or LLM Application Engineer
- Agentic AI or Applied AI Engineer

### Secondary targets

- Senior Python Backend roles whose minimum requirement is 4 years or less
- SDE 3 / Software Development Engineer III roles focused on Python backend
- Python full-stack roles where backend work is the main responsibility

SDE 3 is treated as a stretch target. A matching title is not sufficient: the
job must still contain Python plus backend or applied-AI work. The candidate
profile states four years of experience, so a clearly stated `3-6 years`
requirement includes the candidate; a `6+ years` minimum does not. If Shine's
short card conflicts with a clear range in the full description, the full
description is used for the experience check.

## Roles automatically rejected

- Internship, trainee, and fresher roles
- SDET, QA automation, and API-testing roles
- Data engineering and MLOps roles
- Android, Kotlin, firmware, embedded, and network roles
- DevOps-only, support, sales, PHP-only, Java-only, and .NET-only roles
- Pure research-oriented machine-learning roles

## How scoring works

Every job must first pass three gates:

1. It mentions Python.
2. It contains a backend or applied-AI signal.
3. The candidate's four years meet the minimum experience requirement. For a
   stated range, the minimum is checked against the candidate's experience;
   the range's upper endpoint is not mistaken for its minimum.

Search cards do not make the final decision. They prioritize a limited set of
likely candidates, after which the bot opens each selected Shine page and reads
the complete job description, detail-page skills, and experience requirement.

The detail-page budget rotates across runs: unchecked candidates come first,
then those checked longest ago. Among candidates with the same check history,
**Be An Early Applicant** jobs go ahead of other jobs, followed by the usual
title and skill signals. The badge adds no score and cannot override a failed
matching rule. After full scoring, eligible Early Applicant jobs are attempted
first, then other eligible jobs; each group is ordered by score.

Passing detailed jobs receive points for title similarity, required skills,
preferred resume skills, and experience fit. A score of 60 or more is eligible.
The bot also has a configurable per-run role-family safety cap. The supplied
configuration uses twenty so relevant backend jobs from different companies are
not stopped after only two successes.

If the full description states a higher requirement such as `7+ years`, that
requirement overrides a misleading lower range on the search card. Broken
values above 15 years are ignored because Shine sometimes collapses typography
such as `3-5` into `35` during text extraction.

## Change matching lists

Open [job_settings](../job_settings/README.md) to add or remove titles, search
phrases, skills, blocked keywords, and role signals. Each item is a plain-text
line; no Python changes are required.

[Back to Start Here](../README.md)
