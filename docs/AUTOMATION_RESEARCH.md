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

## How python-substack is wired into `publish.py` (done 2026-09-20)

`publish.publish_api(post_path, title, subtitle, publication_url, cookies_string=None, cookies_path=None,
section_name=None, schedule_at=None) -> str` is the third sibling of `publish_manual` / `publish_browser`.
Verified against python-substack 0.7.0's source, not its README:

- `substack.Api(cookies_string=..., cookies_path=..., publication_url="https://hoodlem4real.substack.com")`.
  Cookie auth only; email/password is never passed. `publication_url` is matched by hostname against the
  account's publications, and the lib derives `<pub>/api/v1` for draft calls.
- Section: `api.get_sections()` returns the section dicts of the current publication; we match `name`
  case-insensitively to `publication.section` from `rubric.yaml` ("Top 3 Repos") and pass the id as
  `draft_section_id`. Unknown section = warning + draft without a section.
- Body: `strip_header()` removes the `# title` / `_subtitle_` lines that `build.render_issue` writes, since
  `create_draft_from_markdown(title, markdown, subtitle=..., draft_section_id=...)` takes them separately.
- Schedule: `api.schedule_draft(id, when_utc)` posts `{"trigger_at": <iso>}` to `drafts/<id>/scheduled_release`
  (the field name the reference confirms; the lib omits `post_audience`, which defaults to everyone).
  `next_publish_time(now, hour, tz)` picks the slot: today at `hour`:00 if still ahead, else now + 10 min.
- `publish_draft` is never called. The draft URL is `<publication_url>/publish/post/<id>`.

CLI: `substack publish --api` (draft only), `--schedule`, `--at YYYY-MM-DDTHH:MM`, `--section NAME`;
`scripts/morning.sh` runs `publish --api --schedule` when `SUBSTACK_COOKIES`/`_PATH` is set in `.env`.

Notes: `publish.post_note(text, cookies_string=...)` posts to `https://substack.com/api/v1/comment/feed`
with `{"bodyJson": <ProseMirror doc>, "replyMinimumRole": "everyone"}`, the ✅-verified shape in
substack-api-reference's `ENDPOINTS.md` and `openapi.yaml` (`NoteCreate`). It is a plain `requests.Session`
with the same cookies (the lib's `api.call()` targets the publication host with query params, not a JSON
body on substack.com). Not yet on a CLI flag.
