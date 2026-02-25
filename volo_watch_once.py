import os
import re
import json
from typing import Any, Dict, List, Iterable, Tuple
from playwright.sync_api import sync_playwright

DISCOVER_URL = os.environ["VOLO_URL"]
VENUE_ID = os.environ["VOLO_VENUE_ID"]
DEBUG = os.environ.get("DEBUG", "0") == "1"


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def deep_iter(obj: Any) -> Iterable[Any]:
    stack = [obj]
    while stack:
        cur = stack.pop()
        yield cur
        if isinstance(cur, dict):
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def dicts_containing_string(payload: Any, needle: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for node in deep_iter(payload):
        if isinstance(node, dict):
            try:
                if needle in json.dumps(node, ensure_ascii=False):
                    out.append(node)
            except Exception:
                pass
    return out


def main():
    json_payloads: List[Tuple[str, Any]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        def on_response(resp):
            try:
                body = resp.text()
                if not body:
                    return
                s = body.lstrip()
                if not (s.startswith("{") or s.startswith("[")):
                    return
                data = json.loads(body)
                json_payloads.append((resp.url, data))
            except Exception:
                return

        page.on("response", on_response)

        # Use domcontentloaded to avoid SPA "networkidle" hangs
        page.goto(DISCOVER_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(3000)

        # Screenshot without full_page to avoid timeouts on long/infinite pages
        page.set_viewport_size({"width": 1280, "height": 720})
        page.screenshot(path="screenshot.png", timeout=120_000)

    print(f"[DEBUG] JSON payloads parsed: {len(json_payloads)}", flush=True)

    # Save the first few payloads to files for inspection
    os.makedirs("payloads", exist_ok=True)
    for i, (url, data) in enumerate(json_payloads[:6]):
        path = f"payloads/payload_{i}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"url": url, "data": data}, f, ensure_ascii=False, indent=2)
        if DEBUG:
            print(f"[DEBUG] wrote {path}", flush=True)

    # Now look for venueId occurrences inside payloads
    total_hits = 0
    for i, (url, data) in enumerate(json_payloads):
        hits = dicts_containing_string(data, VENUE_ID)
        if hits:
            total_hits += len(hits)
            print(f"[DEBUG] VenueId found in payload #{i} ({len(hits)} dict hits)", flush=True)
            # Print a few key previews
            for h in hits[:3]:
                keys = list(h.keys())[:30]
                print(f"[DEBUG] hit keys: {keys}", flush=True)
                # Print a small snippet (redacted-ish)
                snippet = json.dumps(h, ensure_ascii=False)[:600]
                print(f"[DEBUG] hit snippet: {snippet}", flush=True)

    print(f"[DEBUG] Total venueId-containing dict hits: {total_hits}", flush=True)

    # If venueId never appears, it means the page filter is applied client-side
    # and the backend payloads don't include venueId in the objects we can see.
    if total_hits == 0:
        print("⚠️ VenueId not found in captured JSON payloads. Backend may not include it, or it’s encoded differently.", flush=True)


if __name__ == "__main__":
    main()