import os
import re
import json
import hashlib
import smtplib
from email.mime.text import MIMEText
from typing import List, Tuple

from playwright.sync_api import sync_playwright

VOLO_URL = os.environ["VOLO_URL"]
SMS_EMAIL = os.environ.get("SMS_EMAIL", "2402777979@tmomail.net")
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")

DEBUG = os.environ.get("DEBUG", "0") == "1"

# Optional: require this substring to appear in the card text (ex: "boys and girls club of lodi" or "jersey city")
VENUE_NAME_MUST_CONTAIN = os.environ.get("VENUE_NAME_MUST_CONTAIN", "").strip().lower()

STATE_FILE = "seen.json"

PROGRAM_KEYWORDS = ["pickup", "drop-in", "drop in", "open play", "open gym"]
SPORT_KEYWORD = "volleyball"


def norm(s: str) -> str:
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
        print("⚠️ Gmail secrets missing. Check GitHub repo secrets GMAIL_USER and GMAIL_APP_PASSWORD.", flush=True)
        return

    message = message[:450]  # keep short for SMS gateway
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


def click_if_visible(page, selector=None, text=None, timeout=2000) -> bool:
    try:
        if selector:
            loc = page.locator(selector)
        else:
            loc = page.get_by_text(text, exact=False)
        if loc.count() > 0 and loc.first.is_visible():
            loc.first.click(timeout=timeout)
            return True
    except Exception:
        pass
    return False


def main():
    seen = load_seen()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Load without "networkidle" (SPAs often never go idle)
        page.goto(VOLO_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1500)

        # 1) Accept cookies (your screenshot shows it blocks the page)
        click_if_visible(page, text="Accept All", timeout=5000)

        # 2) Close promo modal (blue popup with X)
        # Try a few close strategies
        click_if_visible(page, selector="button[aria-label='Close']", timeout=2000)
        click_if_visible(page, selector="button:has-text('×')", timeout=2000)
        click_if_visible(page, selector="button:has-text('Close')", timeout=2000)

        # Let the cards render
        page.wait_for_timeout(4000)

        # Debug screenshot to verify what the runner sees
        page.set_viewport_size({"width": 1280, "height": 720})
        page.screenshot(path="screenshot.png", timeout=120_000)

        body = norm(page.inner_text("body"))
        if DEBUG:
            print(f"[DEBUG] volleyball in DOM? {SPORT_KEYWORD in body}", flush=True)
            print(f"[DEBUG] pickup in DOM? {'pickup' in body}", flush=True)
            print(f"[DEBUG] venue filter: {VENUE_NAME_MUST_CONTAIN or '(none)'}", flush=True)

        # Heuristic: grab medium-sized blocks containing Volleyball + any program keyword.
        # This avoids relying on fragile classnames.
        blocks = page.locator("div").all()

        candidates: List[Tuple[str, str]] = []  # (sid, summary)

        for el in blocks:
            try:
                txt = (el.inner_text() or "").strip()
                if not txt:
                    continue

                t = norm(txt)

                if SPORT_KEYWORD not in t:
                    continue
                if not any(k in t for k in PROGRAM_KEYWORDS):
                    continue
                if "sold out" in t or "waitlist" in t:
                    continue
                if VENUE_NAME_MUST_CONTAIN and VENUE_NAME_MUST_CONTAIN not in t:
                    continue

                # Reduce noise: ignore tiny or huge containers
                if len(t) < 30 or len(t) > 600:
                    continue

                summary = re.sub(r"\n+", "\n", txt).strip()
                sid = stable_id(summary)

                candidates.append((sid, summary))
            except Exception:
                continue

        # Deduplicate within the run
        uniq = {}
        for sid, summary in candidates:
            uniq[sid] = summary
        candidates = list(uniq.items())

        if DEBUG:
            print(f"[DEBUG] candidate blocks found: {len(candidates)}", flush=True)
            for sid, summ in candidates[:3]:
                print("[DEBUG] CANDIDATE:", summ.replace("\n", " | ")[:300], flush=True)

        new_found = 0
        for sid, summary in candidates:
            if sid in seen:
                continue
            seen.add(sid)
            new_found += 1
            notify(f"🏐 Volo Volleyball session found:\n{summary}\n{VOLO_URL}")

        if new_found == 0:
            print("No new matching sessions (DOM).", flush=True)

        save_seen(seen)


if __name__ == "__main__":
    main()