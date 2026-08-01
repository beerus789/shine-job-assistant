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
job must still contain Python plus backend or applied-AI work and must not ask
for more than four years as its minimum experience.

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
3. Its minimum experience requirement is no more than four years.

Search cards do not make the final decision. They prioritize a limited set of
likely candidates, after which the bot opens each selected Shine page and reads
the complete job description, detail-page skills, and experience requirement.

Passing detailed jobs receive points for title similarity, required skills,
preferred resume skills, and experience fit. A score of 60 or more is eligible.
The bot also limits each role family to two successful applications per run so
one type of job cannot consume the whole run.

## Change matching lists

Open [job_settings](../job_settings/README.md) to add or remove titles, search
phrases, skills, blocked keywords, and role signals. Each item is a plain-text
line; no Python changes are required.

[Back to Start Here](../README.md)
