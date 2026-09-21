"""Stage 7: get the issue into Substack.

Substack has no official write API. Two paths:

1. `substack publish --manual` (default): prints the path of the issue markdown and copies it to the
   clipboard when possible. Paste into a new post; Substack's editor converts markdown headings,
   bold and code fences on paste.

2. `substack publish --browser`: Playwright drives a persistent Chromium profile. The FIRST time,
   run `substack login`, which opens a headed browser to substack.com/sign-in; you log in yourself
   (the pipeline never sees or stores your password — only the browser profile keeps the session).
   After that, publish creates a draft with the title, subtitle and body and schedules it for the
   configured hour. It stops at "scheduled", never "published", so you always get a last look.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

PROFILE_DIR = Path.home() / ".tylers-substack-browser"
NEW_POST_URL = "https://{handle}.substack.com/publish/post?type=newsletter"


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
