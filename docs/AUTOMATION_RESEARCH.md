# GitHub repos that can automate the pipeline (researched 2026-09-20)

Stars and last-push dates were pulled from the GitHub API on 2026-09-20.

## Recommended stack

| Stage | Pick | Why |
|---|---|---|
| Discover | [GitHubTrendingRSS](https://github.com/mshibanami/GitHubTrendingRSS) (367★, MIT) + GitHub Search API (`created:>DATE sort:stars`) | Zero ToS risk. Hosted feeds, nothing to scrape. Drop LinkedIn as a discovery source. |
| Enrich | [githubkit](https://github.com/yanyongyu/githubkit) (347★, MIT) | Typed, async, full REST + GraphQL. Fallback: PyGithub (7.8k★, LGPL). |
| Score | direct LLM call (already in `scorer.py`) | No repo needed. |
| Test sandbox | [repo2docker](https://github.com/jupyterhub/repo2docker) (1.7k★, BSD-3) + [testcontainers-python](https://github.com/testcontainers/testcontainers-python) (2.3k★, Apache-2.0) | repo2docker turns an arbitrary repo into a runnable image; testcontainers runs it with timeouts. Network off, hard timeout. Never run third-party code on the host. |
| Publish | [python-substack](https://github.com/ma2za/python-substack) (175★, MIT, pushed 2026-09-18) | Markdown → draft, `drafts.schedule()` / `publish()`, CLI + MCP server, cookie auth. **No Notes support.** |
| Notes | [substack-api-reference](https://github.com/AnthonyDavidAdams/substack-api-reference) (spec, MIT) | Documents `POST /comment/feed` with `connect.sid`. ~40 lines to add a `post_note()`. |
| LinkedIn cross-post | [linkedin-api-python-client](https://github.com/linkedin-developers/linkedin-api-python-client) (official, unmaintained 2 yrs) | The only ToS-clean write path. |

## Skip

- `huchenme/github-trending-api`: dead since 2023.
- `daytonaio/daytona`: no license detected.
- `tomquirk/linkedin-api`: repo now 404s; unofficial LinkedIn clients violate the User Agreement and get accounts banned.
- `rotemweiss57/gpt-newspaper`: stale 2 yrs, inspiration only.
- `NHagar/substack_api`: read-only.

## Risk notes

- All Substack write APIs are unofficial and cookie-based; they can break without notice. Keep `publish_browser` (Playwright) as the fallback.
- Stage 4 runs untrusted code. That is the biggest risk in the pipeline, bigger than either ToS issue.

## How to wire python-substack into `publish.py`

Add a third sibling with the same signature as `publish_manual` / `publish_browser`:

```python
def publish_api(post_path, handle, title, subtitle, schedule_at=None):
    from substack import Api  # cookie auth via env
    api = Api(cookies_path=...)
    draft = api.create_draft_from_markdown(post_path.read_text(), title=title, subtitle=subtitle)
    if schedule_at:
        api.drafts.schedule(draft["id"], schedule_at)
    return draft_url
```

Stop at *scheduled*, never *published*, so there is always a last look. Wire `--api` into the CLI flag switch next to `--browser`.
