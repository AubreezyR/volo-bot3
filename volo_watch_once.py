import os
import re
import json
import hashlib
import smtplib
from email.mime.text import MIMEText
from playwright.sync_api import sync_playwright

DISCOVER_URL = os.environ.get("VOLO_URL")
if not DISCOVER_URL:
    raise RuntimeError("VOLO_URL env var is required")

STATE_FILE = "seen.json"

SMS_EMAIL = os.environ.get("SMS_EMAIL", "2402777979@tmomail.net")
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")

DEBUG = os.environ.get("DEBUG", "0") == "1"

# “Open play” style keywords (includes Pickup which is what your screenshot shows)
PROGRAM_KEYWORDS = ["pickup", "drop-in", "drop in", "open play", "open gym"]

# Optional extra filter: require venue name text on the card
VENUE_NAME_MUST_CONTAIN = os.environ.get("VENUE_NAME_MUST_CONTAIN", "").strip().lower()


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


def main():
    seen = load_seen()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(DISCOVER_URL, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(2000)

        # Debug screenshot (super helpful on Actions)
        page.screenshot(path="screenshot.png", full_page=True)

        body_text = normalize(page.inner_text("body"))
        if DEBUG:
            print(f"[DEBUG] volleyball in DOM? {'volleyball' in body_text}", flush=True)
            print(f"[DEBUG] pickup in DOM? {'pickup' in body_text}", flush=True)
            print(f"[DEBUG] venue filter: {VENUE_NAME_MUST_CONTAIN or '(none)'}", flush=True)

        # Heuristic: look for “cards” by scanning elements that contain volleyball text
        # and then filtering by keywords + availability.
        elements = page.locator("a, article, section, div").all()

        candidates = []
        for el in elements:
            txt = (el.inner_text() or "").strip()
            if not txt:
                continue
            low = normalize(txt)

            if "volleyball" not in low:
                continue
            if not any(k in low for k in PROGRAM_KEYWORDS):
                continue
            if "sold out" in low or "waitlist" in low:
                continue
            if VENUE_NAME_MUST_CONTAIN and VENUE_NAME_MUST_CONTAIN not in low:
                continue

            href = ""
            try:
                href = el.get_attribute("href") or ""
            except Exception:
                pass

            if href.startswith("/"):
                href = "https://www.volosports.com" + href

            summary = re.sub(r"\n+", "\n", txt).strip()
            sid = stable_id(summary, href)
            candidates.append((sid, summary, href))

        if DEBUG:
            print(f"[DEBUG] candidate count: {len(candidates)}", flush=True)
            for _, s, h in candidates[:5]:
                print("[DEBUG] CANDIDATE:", s.replace("\n", " | "), flush=True)
                if h:
                    print("[DEBUG] LINK:", h, flush=True)

        new_found = 0
        for sid, summary, href in candidates:
            if sid in seen:
                continue
            seen.add(sid)
            new_found += 1
            link = href if href else DISCOVER_URL
            notify(f"🏐 Volo Volleyball found:\n{summary}\n{link}")

        if new_found == 0:
            print("No new matching sessions (DOM scrape).", flush=True)

        save_seen(seen)


if __name__ == "__main__":
    main()