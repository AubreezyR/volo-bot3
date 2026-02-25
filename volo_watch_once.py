import os
import re
import json
import base64
import hashlib
import smtplib
from email.mime.text import MIMEText
from typing import Any, Dict, List, Iterable, Optional

from playwright.sync_api import sync_playwright

DISCOVER_URL = os.environ.get("VOLO_URL")
if not DISCOVER_URL:
    raise RuntimeError("VOLO_URL env var is required")

VENUE_ID = os.environ.get("VOLO_VENUE_ID")
if not VENUE_ID:
    raise RuntimeError("VOLO_VENUE_ID env var is required")

STATE_FILE = "seen.json"

SMS_EMAIL = os.environ.get("SMS_EMAIL", "2402777979@tmomail.net")
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")

AUTO_SIGNUP = os.environ.get("AUTO_SIGNUP", "0") == "1"
VOLO_STORAGE_STATE_B64 = os.environ.get("VOLO_STORAGE_STATE_B64", "")
DEBUG = os.environ.get("DEBUG", "0") == "1"

OPEN_PLAY_KEYWORDS = ["pickup", "drop-in", "drop in", "open play", "open gym"]


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def stable_id(*parts: str) -> str:
    return hashlib.sha256("||".join(parts).encode("utf-8")).hexdigest()[:16]


def load_seen() -> set[str]:
    if not os.path.exists(STATE_FILE):
        return set()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_seen(seen: set[str]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, indent=2)


def send_sms_via_email(message: str) -> None:
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        print("⚠️ Missing GMAIL_USER / GMAIL_APP_PASSWORD (GitHub secrets).")
        return

    message = message[:450]
    msg = MIMEText(message)
    msg["From"] = GMAIL_USER
    msg["To"] = SMS_EMAIL
    msg["Subject"] = ""

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, SMS_EMAIL, msg.as_string())


def notify(message: str) -> None:
    print(message, flush=True)
    try:
        send_sms_via_email(message)
        print("📲 SMS sent.", flush=True)
    except Exception as e:
        print(f"❌ SMS failed: {e}", flush=True)


def deep_iter(obj: Any) -> Iterable[Any]:
    stack = [obj]
    while stack:
        cur = stack.pop()
        yield cur
        if isinstance(cur, dict):
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def looks_like_event(d: Dict[str, Any]) -> bool:
    keys = {k.lower() for k in d.keys()}
    # heuristic: event-ish objects have 2+ of these
    hits = 0
    for k in ["title", "name", "start", "starttime", "startsat", "venue", "venueid", "sport"]:
        if k in keys:
            hits += 1
    return hits >= 2


def extract_events(payload: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for node in deep_iter(payload):
        if isinstance(node, dict) and looks_like_event(node):
            out.append(node)
    return out


def venue_matches(event: Dict[str, Any]) -> bool:
    return VENUE_ID in json.dumps(event, ensure_ascii=False)


def is_volleyball(event: Dict[str, Any]) -> bool:
    return "volleyball" in normalize(json.dumps(event, ensure_ascii=False))


def is_open_play(event: Dict[str, Any]) -> bool:
    blob = normalize(json.dumps(event, ensure_ascii=False))
    return any(k in blob for k in OPEN_PLAY_KEYWORDS)


def is_available(event: Dict[str, Any]) -> bool:
    blob = normalize(json.dumps(event, ensure_ascii=False))
    if "sold out" in blob or "waitlist" in blob:
        return False
    return True


def event_summary(event: Dict[str, Any]) -> str:
    title = str(event.get("title") or event.get("name") or "Volo Volleyball")
    start = str(event.get("start") or event.get("startTime") or event.get("startsAt") or event.get("dateTime") or "")
    venue_name = ""
    if isinstance(event.get("venue"), dict):
        venue_name = str(event["venue"].get("name") or "")
    return " | ".join([p for p in [title, start, venue_name] if p])


def event_url(event: Dict[str, Any]) -> str:
    for k in ["url", "eventUrl", "href", "link", "shareUrl"]:
        v = event.get(k)
        if isinstance(v, str) and v.startswith("http"):
            return v
    return DISCOVER_URL


def write_storage_state_from_secret() -> Optional[str]:
    if not VOLO_STORAGE_STATE_B64:
        return None
    try:
        raw = base64.b64decode(VOLO_STORAGE_STATE_B64.encode("utf-8"))
        path = "storage_state.json"
        with open(path, "wb") as f:
            f.write(raw)
        return path
    except Exception as e:
        print(f"❌ Failed to decode VOLO_STORAGE_STATE_B64: {e}", flush=True)
        return None


def attempt_signup(playwright, target_url: str) -> bool:
    storage_path = write_storage_state_from_secret()
    if not storage_path:
        print("⚠️ No storage state available; skipping auto-signup.", flush=True)
        return False

    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(storage_state=storage_path)
    page = context.new_page()

    try:
        page.goto(target_url, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(1500)

        if "login" in page.url.lower():
            print("⚠️ Session expired (redirected to login).", flush=True)
            return False

        # Best-effort click
        for label in ["Register", "Join", "Sign up", "Sign Up", "Enroll"]:
            btn = page.get_by_role("button", name=re.compile(label, re.I))
            if btn.count() > 0:
                btn.first.click(timeout=5_000)
                page.wait_for_timeout(1500)
                break

        # Safety stop (no bypass)
        page_text = normalize(page.inner_text("body"))
        if any(x in page_text for x in ["captcha", "payment", "card number", "checkout", "verify", "3d secure"]):
            print("⚠️ Hit payment/verification; stopping.", flush=True)
            return False

        return True
    except Exception as e:
        print(f"❌ Auto-signup error: {e}", flush=True)
        return False
    finally:
        context.close()
        browser.close()


def main():
    seen = load_seen()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        json_events: List[Dict[str, Any]] = []
        json_urls: List[str] = []

        def on_response(resp):
            # Try to parse JSON regardless of content-type.
            try:
                body = resp.text()
                if not body:
                    return
                s = body.lstrip()
                if not (s.startswith("{") or s.startswith("[")):
                    return
                data = json.loads(body)
                json_urls.append(resp.url)
                json_events.extend(extract_events(data))
            except Exception:
                return

        page.on("response", on_response)

        page.goto(DISCOVER_URL, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(2500)

        if DEBUG:
            print(f"[DEBUG] json responses parsed: {len(json_urls)}", flush=True)
            print(f"[DEBUG] event-like dicts extracted: {len(json_events)}", flush=True)

        # Filter events
        candidates = [ev for ev in json_events if venue_matches(ev) and is_volleyball(ev)]
        matches = [ev for ev in candidates if is_open_play(ev) and is_available(ev)]

        if DEBUG:
            print(f"[DEBUG] venue+volleyball candidates: {len(candidates)}", flush=True)
            for ev in candidates[:10]:
                print("[DEBUG] CANDIDATE:", event_summary(ev), flush=True)
            print(f"[DEBUG] matches after open-play filter: {len(matches)}", flush=True)

        new_found = 0
        for ev in matches:
            summary = event_summary(ev)
            url = event_url(ev)
            sid = stable_id(summary, url)
            if sid in seen:
                continue
            seen.add(sid)
            new_found += 1

            notify(f"🏐 New Volo session found:\n{summary}\n{url}")

            if AUTO_SIGNUP:
                ok = attempt_signup(p, url)
                if ok:
                    notify(f"✅ Auto-signup attempted:\n{summary}\n{url}")
                else:
                    notify(f"⚠️ Auto-signup could not complete.\nOpen link:\n{url}")

        if new_found == 0:
            print("No new matching sessions (response sniff).", flush=True)

        save_seen(seen)


if __name__ == "__main__":
    main()