import os
import re
import hashlib
from playwright.sync_api import sync_playwright

NOTION_URL = os.environ["NOTION_URL"]
STATE_FILE = os.environ.get("STATE_FILE", "state/notion.sha256")
SNAPSHOT_FILE = os.environ.get("SNAPSHOT_FILE", "state/notion.txt")
RESET_BASELINE = os.environ.get("RESET_BASELINE", "false").lower() == "true"


def set_output(key: str, value: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def write_state(new_hash: str, snapshot: str) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(new_hash + "\n")
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        f.write(snapshot + "\n")


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def auto_scroll(page) -> None:
    # Helps Notion load lazy content if the page is long
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

        page.goto(url, wait_until="networkidle", timeout=90_000)
        auto_scroll(page)

        # Try to target main content if possible; fallback to body text
        # Notion DOM changes, so keep this defensive.
        text = page.evaluate("() => document.body.innerText || ''")

        browser.close()

    text = normalize_text(text)

    # If the page isn't public, you'll usually see login-like text.
    login_signals = ["Log in", "Continue with", "Sign up", "Create account"]
    if any(sig.lower() in text.lower() for sig in login_signals):
        raise SystemExit("Looks like a login wall. The Notion page is likely not public (Share to web).")

    return text


def main() -> None:
    snapshot = extract_text_with_playwright(NOTION_URL)

    new_hash = sha256_hex(snapshot)
    old_hash = read_file(STATE_FILE)

    first_run = (old_hash == "") or RESET_BASELINE
    changed = (not first_run) and (old_hash != new_hash)

    write_state(new_hash, snapshot)

    set_output("old_hash", old_hash)
    set_output("new_hash", new_hash)
    set_output("first_run", "true" if first_run else "false")
    set_output("changed", "true" if changed else "false")

    print(f"first_run={first_run} changed={changed}")
    print(f"snapshot_chars={len(snapshot)}")


if __name__ == "__main__":
    main()
