import base64
from playwright.sync_api import sync_playwright

LOGIN_URL = "https://www.volosports.com/login"  # if this redirects, that's fine

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print("\nLog in manually in the opened browser window.")
        print("After you are fully logged in, return here and press Enter.\n")
        input("Press Enter once logged in... ")

        # Save storage state
        context.storage_state(path="state.json")
        browser.close()

    # Print base64 for GitHub Secret
    with open("state.json", "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")

    print("\n✅ Saved state.json")
    print("Add this as a GitHub secret named VOLO_STORAGE_STATE_B64:\n")
    print(b64)

if __name__ == "__main__":
    main()