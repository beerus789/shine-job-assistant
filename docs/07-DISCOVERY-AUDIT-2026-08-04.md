# Discovery Audit - 4 August 2026

This audit was read-only. It did not sign in, click **Apply**, or change any
Shine job.

## Why only a few jobs were applied

The live `.env` allowed only three successful applications, one search-result
page per query, and forty full job pages. It did not define a role-family cap,
so the code used its old fallback of two. The latest live report therefore
showed:

- 142 unique jobs discovered from ten first pages;
- 11 jobs accepted by the detailed matcher;
- 3 applications submitted;
- 7 accepted jobs stopped by the per-run limit;
- 1 accepted job stopped by the role-family limit; and
- 34 jobs never fully evaluated because the forty-page detail budget was full.

The pagination URL construction was not broken. Pages 1, 2, and 3 all loaded
twenty cards for every configured search. The bot did not request them because
`MAX_PAGES_PER_SEARCH` was set to `1`.

## Independent three-page comparison

The ten configured searches were inspected through pages 1-3:

- 600 cards were read;
- 417 unique job URLs remained after deduplication;
- an independent resume-based card pass found 107 plausible candidates; and
- 68 of those candidates appeared only on pages 2 or 3.

Card matching was treated only as a lead. Eleven strong-looking omitted jobs
were then opened and checked against their full descriptions. Suitable examples
included Python backend or applied-AI roles from NTT DATA, Fidelity
International, UnitedHealth/Optum, Phigital Care, Bombay Softwares, Terralogic,
and datavruti.

The full-description step also prevented bad applications. Two attractive
cards from UST and Icogz advertised lower experience ranges, while their full
descriptions required 7+ and 8+ years respectively.

## Result after the changes

The revised bot was run read-only through the same thirty search pages with a
250-page detail budget:

- 600 cards and 417 unique jobs were discovered;
- all 173 candidates that passed the safe preliminary filter were opened;
- 85 passed final detailed scoring;
- 244 clearly unsuitable cards were labeled `pre_filtered` for their title or
  minimum experience;
- no candidate was omitted because of the detail-page limit; and
- misleading UST and Icogz roles were rejected using the higher experience
  requirement from their full descriptions.

Manual review of those 85 found one malformed Wipro listing whose AI title and
skills conflicted with a VLSI/hardware role description. `vlsi` was therefore
added to the blocked list after the measured run; the accepted count is an
audit measurement, not a target the bot tries to maximize.

The supplied configuration now checks three pages per search, can inspect up to
250 full job pages, and can submit up to twenty confirmed applications per run.
Jobs outside the detail budget are reported as `not_evaluated`, not incorrectly
described as rejected.

Because Shine results change over time, these figures are an audit snapshot,
not a permanent promise about the number of available jobs.
