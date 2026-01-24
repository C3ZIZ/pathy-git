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
CHANGES_FILE = os.environ.get("CHANGES_FILE", "state/notion.changes.txt")

MAX_CHANGE_ITEMS = int(os.environ.get("MAX_CHANGE_ITEMS", "25"))
MAX_BLOCK_LINES = int(os.environ.get("MAX_BLOCK_LINES", "6"))


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

        page.goto(url, wait_until="domcontentloaded", timeout=120_000)
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
    return list(
        difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile="before",
            tofile="after",
            lineterm=""
        )
    )


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


def prev_nonempty_line(lines: list[str], idx: int, lookback: int = 25) -> str:
    k = min(idx - 1, len(lines) - 1)
    for i in range(k, max(-1, k - lookback), -1):
        s = lines[i].strip()
        if s and s.lower() != "notion":
            return s
    return ""


def clip_block(block: list[str], max_lines: int) -> str:
    b = [x.strip() for x in block if x.strip()]
    if not b:
        return "(empty)"
    if len(b) <= max_lines:
        return "\n".join(b)
    return "\n".join(b[:max_lines]) + "\n...[truncated]"


def build_change_report(old_text: str, new_text: str) -> tuple[str, str]:
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()

    sm = difflib.SequenceMatcher(None, old_lines, new_lines)
    opcodes = sm.get_opcodes()

    items = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            continue

        context = prev_nonempty_line(new_lines, j1) or prev_nonempty_line(old_lines, i1)

        old_block = old_lines[i1:i2]
        new_block = new_lines[j1:j2]

        # clean one-line replacements like: 1 -> 2
        if tag == "replace" and len(old_block) == 1 and len(new_block) == 1:
            o = old_block[0].strip()
            n = new_block[0].strip()
            if o != n:
                if context:
                    items.append(f"• {context}: {o} → {n}")
                else:
                    items.append(f"• {o} → {n}")
            continue

        # inserts/deletes/multiline replaces
        header = f"• Near: {context}" if context else "• Change"
        if tag == "insert":
            items.append(f"{header}\n  Added:\n  {clip_block(new_block, MAX_BLOCK_LINES).replace(chr(10), chr(10)+'  ')}")
        elif tag == "delete":
            items.append(f"{header}\n  Removed:\n  {clip_block(old_block, MAX_BLOCK_LINES).replace(chr(10), chr(10)+'  ')}")
        else:  # replace
            items.append(
                f"{header}\n"
                f"  Before:\n  {clip_block(old_block, MAX_BLOCK_LINES).replace(chr(10), chr(10)+'  ')}\n"
                f"  After:\n  {clip_block(new_block, MAX_BLOCK_LINES).replace(chr(10), chr(10)+'  ')}"
            )

        if len(items) >= MAX_CHANGE_ITEMS:
            items.append("• ...(more changes truncated)")
            break

    if not items:
        return ("No visible text changes detected.", "No changes")

    report = "What changed:\n" + "\n\n".join(items)
    brief = items[0].replace("\n", " ")[:180]
    return (report, brief)


def main() -> None:
    old_hash = read_file(STATE_HASH_FILE).strip()
    old_snapshot = read_file(STATE_SNAPSHOT_FILE)

    snapshot = extract_text_with_playwright(NOTION_URL)
    new_hash = sha256_hex(snapshot)

    first_run = (old_hash == "") or (old_snapshot.strip() == "")
    changed = (not first_run) and (old_hash != new_hash)

    diff_lines = make_unified_diff(old_snapshot, snapshot)
    added, removed = diff_summary(diff_lines)

    change_report, change_brief = build_change_report(old_snapshot, snapshot)

    # Write state (tracked)
    write_file(STATE_HASH_FILE, new_hash + "\n")
    write_file(STATE_SNAPSHOT_FILE, snapshot + "\n")

    # Write files for email attachments
    write_file(DIFF_FILE, "\n".join(diff_lines) + ("\n" if diff_lines else ""))
    write_file(CHANGES_FILE, change_report + "\n")

    set_output("old_hash", old_hash)
    set_output("new_hash", new_hash)
    set_output("first_run", "true" if first_run else "false")
    set_output("changed", "true" if changed else "false")
    set_output("diff_summary", f"+{added} / -{removed}")
    set_output("change_brief", change_brief)
    set_output_multiline("change_report", change_report)


if __name__ == "__main__":
    main()
