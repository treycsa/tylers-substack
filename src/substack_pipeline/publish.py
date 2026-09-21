"""Stage 7: get the issue into Substack.

Substack has no official write API. Three paths, all of which stop short of "published" so there is
always a last look in the editor:

1. `substack publish --api` (recommended): python-substack drives the same cookie-authenticated
   endpoints the web app uses. Auth is `SUBSTACK_COOKIES` (the Cookie header copied from browser
   devtools on substack.com) or `SUBSTACK_COOKIES_PATH` (a JSON cookie file) in `.env`; the pipeline
   never sees a password and never logs the cookie. It creates a draft in the configured section and,
   with `--schedule` / `--at`, schedules it. It never calls publish.

2. `substack publish --browser`: Playwright drives a persistent Chromium profile. The FIRST time,
   run `substack login`, which opens a headed browser to substack.com/sign-in; you log in yourself
   (only the browser profile keeps the session). Stops at a draft.

3. `substack publish` (manual, the fallback): prints the path of the issue markdown and copies it to
   the clipboard when possible. Paste into a new post; Substack's editor converts markdown headings,
   bold and code fences on paste.

Notes are a separate surface: `post_note` hits `POST https://substack.com/api/v1/comment/feed`, the
request shape documented (verified) in github.com/AnthonyDavidAdams/substack-api-reference.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger(__name__)

PROFILE_DIR = Path.home() / ".tylers-substack-browser"
NEW_POST_URL = "https://{handle}.substack.com/publish/post?type=newsletter"
NOTES_URL = "https://substack.com/api/v1/comment/feed"
NOTES_DOC_URL = "https://github.com/AnthonyDavidAdams/substack-api-reference/blob/main/ENDPOINTS.md"
COOKIES_HELP = ("Set SUBSTACK_COOKIES in .env (copy the Cookie header from browser devtools on substack.com, "
                "quoted: \"connect.sid=...; ...\") or SUBSTACK_COOKIES_PATH (a JSON cookie file).")
TIMEOUT = 30


def copy_to_clipboard(text: str) -> bool:
    for cmd in (["pbcopy"], ["xclip", "-selection", "clipboard"], ["wl-copy"]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, input=text.encode(), check=False)
            return True
    return False


def publish_manual(post_path: Path, handle: str) -> str:
    text = post_path.read_text()
    copied = copy_to_clipboard(text)
    return (f"Issue ready: {post_path}\n"
            f"{'Copied to clipboard. ' if copied else ''}Open {NEW_POST_URL.format(handle=handle)} and paste.")


def login(handle: str) -> None:
    from playwright.sync_api import sync_playwright  # lazy import; optional dependency

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False)
        page = ctx.new_page()
        page.goto("https://substack.com/sign-in")
        print("Log in in the browser window. Close the window when you see your dashboard.")
        page.wait_for_event("close", timeout=0)
        ctx.close()


def publish_browser(post_path: Path, handle: str, title: str, subtitle: str) -> str:
    from playwright.sync_api import sync_playwright

    body = post_path.read_text()
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=True)
        page = ctx.new_page()
        page.goto(NEW_POST_URL.format(handle=handle), wait_until="networkidle")
        if "sign-in" in page.url:
            ctx.close()
            return "Not logged in. Run `substack login` first."
        page.get_by_placeholder("Title").fill(title)
        page.get_by_placeholder("Add a subtitle").fill(subtitle)
        editor = page.locator("[contenteditable='true']").last
        editor.click()
        # Paste as markdown: Substack converts fenced code and headings on paste.
        page.evaluate("(t) => navigator.clipboard.writeText(t)", body)
        page.keyboard.press("Control+V")
        page.wait_for_timeout(1500)
        url = page.url
        ctx.close()
    return f"Draft created: {url}\nReview it, then schedule from the editor."


# --- API path (python-substack, cookie auth) ------------------------------
def next_publish_time(now: datetime, hour: int, tz_name: str, grace_minutes: int = 10) -> datetime:
    """Next `hour`:00 in `tz_name` at least `grace_minutes` after `now`; if today's slot is closer than
    that (or has passed), `now` + grace, rounded up to the next whole minute. The margin covers the
    draft-creation round-trips that happen before `schedule_draft`, so Substack never gets a past time.
    Aware datetimes only; the result is in `tz_name`."""
    if now.tzinfo is None:
        raise ValueError("next_publish_time needs an aware datetime")
    local = now.astimezone(ZoneInfo(tz_name))
    slot = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if slot >= local + timedelta(minutes=grace_minutes):
        return slot
    later = local + timedelta(minutes=grace_minutes)
    if later.second or later.microsecond:
        later = later.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return later


def strip_header(markdown: str) -> str:
    """Drop the `# title` line and the `_subtitle_` line that `build.render_issue` puts first; the API
    takes title and subtitle as separate fields."""
    lines = markdown.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
        if lines and lines[0].startswith("_") and lines[0].rstrip().endswith("_"):
            lines = lines[1:]
    while lines and not lines[0].strip():
        lines = lines[1:]
    return "\n".join(lines)


def _section_id(api, name: str) -> int | None:
    """Case-insensitive lookup of a section id by name; None (with a warning) when it cannot be resolved."""
    try:
        sections = api.get_sections() or []
    except Exception as e:  # the lib raises on a shape it does not expect; a section is optional
        log.warning("could not list sections (%s); creating the draft without a section", e)
        return None
    want = name.strip().lower()
    for sec in sections:
        if str(sec.get("name", "")).strip().lower() == want:
            return sec.get("id")
    log.warning("section %r not found (have: %s); creating the draft without a section",
                name, [s.get("name") for s in sections])
    return None


def draft_url(publication_url: str, draft_id) -> str:
    base = publication_url.rstrip("/")
    if base.endswith("/api/v1"):
        base = base[: -len("/api/v1")]
    return f"{base}/publish/post/{draft_id}"


def publish_api(post_path: Path, title: str, subtitle: str, publication_url: str,
                cookies_string: str | None = None, cookies_path: str | None = None,
                section_name: str | None = None, schedule_at: datetime | None = None) -> str:
    """Create a draft from the issue markdown (and schedule it when `schedule_at` is given). Cookie auth
    only; never publishes. Returns a human line with the draft URL."""
    if not cookies_string and not cookies_path:
        raise ValueError("No Substack cookies. " + COOKIES_HELP)
    if schedule_at is not None and schedule_at.tzinfo is None:
        raise ValueError("schedule_at must be timezone-aware")
    from substack import Api  # lazy import; optional dependency (pip install -e ".[publish]")

    api = Api(cookies_string=cookies_string or None, cookies_path=cookies_path or None,
              publication_url=publication_url)
    section_id = _section_id(api, section_name) if section_name else None
    body = strip_header(post_path.read_text())
    result = api.create_draft_from_markdown(title=title, markdown=body, subtitle=subtitle,
                                            draft_section_id=section_id)
    draft_id = result["draft"]["id"]
    url = draft_url(publication_url, draft_id)
    where = f" in section {section_name!r}" if section_id is not None else ""
    if schedule_at is None:
        return f"Draft created{where}: {url}\nNot scheduled. Review it, then schedule from the editor."
    api.schedule_draft(draft_id, schedule_at.astimezone(timezone.utc))
    return (f"Draft created{where} and scheduled for {schedule_at.strftime('%Y-%m-%d %H:%M %Z')}: {url}\n"
            "Not published. Unschedule from the editor if it needs another look.")


# --- Notes ----------------------------------------------------------------
def _cookies(cookies_string: str | None, cookies_path: str | None) -> dict[str, str]:
    """Cookie dict from a devtools-style "a=b; c=d" string or a JSON cookie file (python-substack's format)."""
    if cookies_path:
        return dict(json.loads(Path(cookies_path).read_text()))
    if cookies_string:
        pairs = (p.split("=", 1) for p in cookies_string.split(";") if "=" in p)
        return {k.strip(): unquote(v.strip()) for k, v in pairs}
    raise ValueError("No Substack cookies. " + COOKIES_HELP)


def note_payload(text: str) -> dict:
    """The verified body for POST /api/v1/comment/feed: a ProseMirror doc, one paragraph per text line."""
    paragraphs = [{"type": "paragraph", "content": [{"type": "text", "text": line}]} if line.strip()
                  else {"type": "paragraph"} for line in text.strip().splitlines()]
    return {"bodyJson": {"type": "doc", "attrs": {"schemaVersion": "v1", "title": None}, "content": paragraphs},
            "replyMinimumRole": "everyone"}


def post_note(text: str, cookies_string: str | None = None, cookies_path: str | None = None,
              session: requests.Session | None = None) -> str:
    """Post a Substack Note (account-level, so no publication URL is involved). Returns a human line with
    the new note id. Request shape per substack-api-reference ENDPOINTS.md, "Notes" section."""
    if not text.strip():
        raise ValueError("empty note")
    s = session or requests.Session()
    s.cookies.update(_cookies(cookies_string, cookies_path))
    s.headers["User-Agent"] = "Mozilla/5.0"  # overwrite, not setdefault: Substack 403s the python-requests default UA
    r = s.post(NOTES_URL, json=note_payload(text), timeout=TIMEOUT)
    if not 200 <= r.status_code < 300:
        raise RuntimeError(f"Substack returned {r.status_code} for the note: {r.text[:200]}")
    note_id = (r.json() or {}).get("id") if r.content else None
    return f"Note posted (id={note_id}): {text[:60]!r}"
