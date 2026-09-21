# Substack Audit & Daily Repo Review Blueprint (Sep 20, 2026)

## Audit: 1/10, blank slate
@hoodlem4real: no publication, no posts, no bio, 1 Note, 1 subscription.

## Positioning
"I test the repos everyone is posting about, so you don't have to."

Bio: "Daily. I scan every GitHub repo that hits my LinkedIn feed, score them, and hand-test the top 3. You get a verdict, a test case, and whether I actually adopted it. By Tyler Reynolds, founder of AlgoChains."

Name options: Top 3 Repos (recommended), Tested Daily, Repo Report, Tyler Reynolds. Keep the byline personal and change the handle to @tylerreynolds.

## Rubric (10 pts)
| Signal | Weight | Source |
|---|---|---|
| Real traction | 2.0 | GitHub API |
| Maintenance health | 1.5 | GitHub API |
| Runs out of the box | 2.0 | Test run |
| Novelty vs. incumbents | 1.5 | AI + Tyler |
| License and safety | 1.0 | Scan |
| Fit for a real stack | 2.0 | Tyler |

Guardrails:
- Repos under 50 stars need a Tyler override.
- A repo that fails the out-of-box run is capped at 6.0.
- ADOPTED requires the code to be in a real branch, not a scratch file.

## Pipeline
1. Scrape (nightly 11 PM)
2. Extract GitHub links and dedupe against the 30-day log
3. Enrich via the GitHub API
4. AI scores the top 10
5. Tyler tests the top 5, scores Fit, and picks 3 (6:00–6:45 AM PT)
6. Assemble the post and schedule it for 6 AM PT with 3 Notes

Fallback: at 8 AM, publish with WATCHING labels only.

## Growth
- Week 0: 3 posts live, no promotion.
- Week 1: 50 subscribers via a LinkedIn launch and 2 Notes/day.
- Week 2: 150 subscribers.
- Week 3: 300 subscribers, mutual recommendations, first Deep Dive.
- Week 4: 500 subscribers, guest swap, leaderboard.

Paid tier and referral program only after 1,000 subscribers.

## Risks
- **Pangram AI detection:** fill in "How I make this" on day 1.
- **LinkedIn scraping ToS:** scrape only your own feed at a human cadence, or use GitHub trending.
- **Claiming a test that wasn't run:** keep dated scripts in tested-daily/.
- **Burnout:** use the fallback; if needed, drop to 3x/week.
- **AlgoChains NDA:** name the service, never its internals.

Open question: which scraper is running, and does it use the personal LinkedIn login?
