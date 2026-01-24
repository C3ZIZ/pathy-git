import os, re, hashlib, requests
from bs4 import BeautifulSoup

NOTION_URL = os.environ["NOTION_URL"]
STATE_FILE = os.environ.get("STATE_FILE", "state/notion.sha256")
SNAPSHOT_FILE = os.environ.get("SNAPSHOT_FILE", "state/notion.txt")


def set_output(key: str, value: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def fetch(url: str) -> requests.Response:
    headers = {
        "User-Agent": "pathy-git-notion-watch/1.0",
        "Accept": "text/html,application/xhtml+xml",
    }
    return requests.get(url, headers=headers, timeout=30)


def extract_visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    # remove noise
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)

    return text.strip()


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


def main() -> None:
    r = fetch(NOTION_URL)

    set_output("http_status", str(r.status_code))
    set_output("etag", r.headers.get("ETag", ""))
    set_output("last_modified", r.headers.get("Last-Modified", ""))

    if r.status_code != 200:
        raise SystemExit(f"HTTP {r.status_code} fetching Notion URL.")

    snapshot = extract_visible_text(r.text)

    if len(snapshot) < 200:
        set_output("warning", "snapshot_too_short_possible_js_render")

    new_hash = sha256_hex(snapshot)
    old_hash = read_file(STATE_FILE)

    first_run = (old_hash == "")
    changed = (not first_run) and (old_hash != new_hash)
    write_state(new_hash, snapshot)

    set_output("old_hash", old_hash)
    set_output("new_hash", new_hash)
    set_output("first_run", "true" if first_run else "false")
    set_output("changed", "true" if changed else "false")

    print(f"first_run={first_run} changed={changed}")
    print(f"old_hash={old_hash}")
    print(f"new_hash={new_hash}")


if __name__ == "__main__":
    main()
