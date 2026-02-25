import os
import re
import json
import hashlib
import smtplib
from email.mime.text import MIMEText

from playwright.sync_api import sync_playwright

VOLO_URL = "https://www.volosports.com/discover?cityName=New%20York%20Metro%20Area&subView=DAILY&view=SPORTS&sportNames%5B0%5D=Volleyball&programTypes%5B0%5D=PICKUP&programTypes%5B1%5D=DROPIN&venueIds%5B0%5D=d87a520a-8b88-4945-8ca9-e63259de3607&venueIds%5B1%5D=c1c5bae2-654e-4f58-81f6-825d6cbdf5d3&venueIds%5B2%5D=b6443f56-7157-41e1-8804-faded173e515&venueIds%5B3%5D=82dbb9a7-9ef0-4ec5-9e50-5b9c2836c633&timeLow=0&timeHigh=1410"

SMS_EMAIL = "2402777979@tmomail.net"
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")

STATE_FILE = "seen.json"
DEBUG = os.environ.get("DEBUG", "0") == "1"

PROGRAM_KEYWORDS = ["pickup", "drop-in", "drop in", "open play", "open gym"]
SPORT_KEYWORD = "volleyball"


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def stable_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


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


def send_sms(message: str) -> None:
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        print("⚠ Missing GMAIL_USER / GMAIL_APP_PASSWORD secrets.", flush=True)
        return

    message = message[:450]  # keep short for SMS gateway
    msg = MIMEText(message)
    msg["From"] = GMAIL_USER
    msg["To"] = SMS_EMAIL
    msg["Subject"] = ""

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, SMS_EMAIL, msg.as_string())


def click_if_visible(page, *, text=None, selector=None, timeout=3000):
    try:
        loc = page.locator(selector) if selector else page.get_by_text(text, exact=False)
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

        page.goto(VOLO_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1500)

        # Cookie banner + promo modal (from your screenshot)
        click_if_visible(page, text="Accept All", timeout=6000)
        click_if_visible(page, selector="button[aria-label='Close']", timeout=2000)
        click_if_visible(page, selector="button:has-text('×')", timeout=2000)

        page.wait_for_timeout(3500)

        # Find candidate “card-ish” blocks
        blocks = page.locator("div").all()
        candidates = []

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

                # avoid tiny/huge containers
                if len(t) < 30 or len(t) > 700:
                    continue

                summary = re.sub(r"\n+", "\n", txt).strip()
                candidates.append(summary)
            except Exception:
                continue

        # Deduplicate candidates
        uniq = list(dict.fromkeys(candidates))

        if DEBUG:
            print(f"[DEBUG] candidates: {len(uniq)}", flush=True)
            for s in uniq[:3]:
                print("[DEBUG] ", s.replace("\n", " | ")[:250], flush=True)

        # Alert on any *new* candidate
        new_found = 0
        for summary in uniq:
            sid = stable_id(summary)
            if sid in seen:
                continue
            seen.add(sid)
            new_found += 1
            send_sms(f"🏐 Volo Volleyball found:\n{summary}\n{VOLO_URL}")
            print("📲 Sent SMS alert.", flush=True)

        if new_found == 0:
            print("No new matching sessions.", flush=True)

        save_seen(seen)


if __name__ == "__main__":
    main()