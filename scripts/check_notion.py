import os
import re
import hashlib
import difflib
import uuid
from playwright.sync_api import sync_playwright

NOTION_URL = os.environ["NOTION_URL"]

STATE_HASH_FILE = os.environ.get("STATE_FILE", "state/notion.sha256")
STATE_SNAPSHOT_FILE = os.environ.get("SNAPSHOT_FILE", "state/notion.txt")
DIFF_FILE = os.environ.get("DIFF_FILE", "state/notion.diff")

MAX_DIFF_LINES = int(os.environ.get("MAX_DIFF_LINES", "200"))   # for email snippet
MAX_DIFF_CHARS = int(os.environ.get("MAX_DIFF_CHARS", "12000")) # for email snippet


def set_output(key: str, value: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"{key}={value}\n")


def set_output_multiline(key: str, value: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    delim = f"EOF_{uuid.uuid4().hex}"
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"{key}<<{delim}\n")
        f.write(value.rstrip("\n") + "\n")
        f.write(f"{delim}\n")


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def write_file(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def auto_scroll(page) -> None:
    page.evaluate(
        """
        async () => {
          await new Promise((resolve) => {
            let total = 0;
            const distance = 800;
            const timer = setInterval(() => {
              window.scrollBy(0, distance);
              total += distance;
              if (total >= document.body.scrollHeight) {
                clearInterval(timer);
                resolve();
              }
            }, 200);
          });
        }
        """
    )


def extract_text_with_playwright(url: str) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_navigation_timeout(120_000)
        page.set_default_timeout(60_000)

        # Notion often never hits "networkidle"
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)

        # Let JS render
        page.wait_for_timeout(5000)

        try:
            page.wait_for_selector("main", timeout=30_000)
        except Exception:
            pass

        auto_scroll(page)
        page.wait_for_timeout(2000)

        text = page.evaluate("() => document.body ? document.body.innerText : ''")
        browser.close()

    text = normalize_text(text)

    login_signals = ["Log in", "Continue with", "Sign up", "Create account"]
    if any(sig.lower() in text.lower() for sig in login_signals):
        raise SystemExit("Login wall detected. The Notion page is likely not public (Share to web).")

    return text


def make_unified_diff(old_text: str, new_text: str) -> list[str]:
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    return list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile="before",
        tofile="after",
        lineterm=""
    ))


def diff_summary(diff_lines: list[str]) -> tuple[int, int]:
    added = 0
    removed = 0
    for line in diff_lines:
        if line.startswith("+++ ") or line.startswith("--- ") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def truncate_diff_for_email(diff_lines: list[str]) -> str:
    # Keep headers + first MAX_DIFF_LINES lines
    snippet_lines = diff_lines[:MAX_DIFF_LINES]
    snippet = "\n".join(snippet_lines)
    if len(snippet) > MAX_DIFF_CHARS:
        snippet = snippet[:MAX_DIFF_CHARS] + "\n...[truncated]"
    return snippet if snippet.strip() else "(diff empty)"


def main() -> None:
    old_hash = read_file(STATE_HASH_FILE).strip()
    old_snapshot = read_file(STATE_SNAPSHOT_FILE)

    snapshot = extract_text_with_playwright(NOTION_URL)
    new_hash = sha256_hex(snapshot)

    first_run = (old_hash == "") or (old_snapshot.strip() == "")
    changed = (not first_run) and (old_hash != new_hash)

    # Build diff even on first run (for file), but email only if changed
    diff_lines = make_unified_diff(old_snapshot, snapshot)
    added, removed = diff_summary(diff_lines)

    # Write state (tracked)
    write_file(STATE_HASH_FILE, new_hash + "\n")
    write_file(STATE_SNAPSHOT_FILE, snapshot + "\n")

    # Write diff (not tracked, used for email attachment)
    write_file(DIFF_FILE, "\n".join(diff_lines) + ("\n" if diff_lines else ""))

    set_output("old_hash", old_hash)
    set_output("new_hash", new_hash)
    set_output("first_run", "true" if first_run else "false")
    set_output("changed", "true" if changed else "false")
    set_output("diff_summary", f"+{added} / -{removed}")
    set_output_multiline("diff_snippet", truncate_diff_for_email(diff_lines))


if __name__ == "__main__":
    main()
