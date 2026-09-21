Every day my LinkedIn feed has twenty posts saying a GitHub repo "changes everything." Almost nobody who posts them has run the code. I'm going to.

## The format

Every morning at 6 AM PT: three repos, same shape every day.

- **What it claims:** one sentence, no adjectives.
- **Why it scored where it did:** the rubric numbers, sourced from the repo's own GitHub data.
- **The test:** a script under 15 lines you can run in 2 minutes. Every script is saved as a dated receipt.
- **Verdict:** my words, not the AI's.
- **Label:** ADOPTED, TRIED / NOT ADOPTED, or WATCHING. ADOPTED means the code is in a real branch of my stack at AlgoChains, not a scratch file.

## The rubric (10 points)

- **Real traction (2.0):** stars in the last 7 days vs. repo age, forks, unique committers.
- **Maintenance health (1.5):** commits in the last 30 days, CI, tagged releases, issue load.
- **Runs out of the box (2.0):** install and quickstart succeed in a clean container within 10 minutes.
- **Novelty vs. incumbents (1.5):** does it do something the obvious alternative doesn't?
- **License and safety (1.0):** OSI license, no telemetry surprises, sane dependencies.
- **Fit for a real stack (2.0):** would it survive in a production codebase? That one is mine.

Guardrails: under 50 stars needs my override to appear. A failed install caps the score at 6.0 no matter what. Hype in the LinkedIn post never moves the score.

## How I make this

AI scores every repo on the first five signals and drafts the claim and score lines. I run every test, write every verdict, and set every label. If I miss my morning window, the issue still ships, but every label is WATCHING and nothing is claimed as ADOPTED.

## What you get

Daily: Top 3 Repos. Fridays: a Deep Dive on one adopted repo a week later, and what it actually changed. Plus the Adopted log: every repo that made it into my stack.

**Reply and tell me what stack you're on.** The repos you name go to the front of the queue.
