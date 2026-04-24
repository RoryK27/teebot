"""
TeeBot TEST SCRIPT - One-off manual booking
Run this via the 'TeeBot - ONE-OFF TEST' GitHub Actions workflow.
Picks up TEST_DAY and TEST_TIME from environment variables.
"""

import asyncio, os, json, urllib.request
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

BRS_URL      = os.environ.get("BRS_URL", "https://members.brsgolf.com/beaverstown/tee-sheet/1")
BRS_EMAIL    = os.environ["BRS_EMAIL"]
BRS_PASSWORD = os.environ["BRS_PASSWORD"]
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO         = os.environ.get("GITHUB_REPOSITORY", "")

# Read from workflow inputs
TEST_DAY  = os.environ.get("TEST_DAY", "Monday")     # e.g. "Monday"
TEST_TIME = os.environ.get("TEST_TIME", "18:00")     # e.g. "18:00"

DAY_MAP = {
    "monday":0,"tuesday":1,"wednesday":2,"thursday":3,
    "friday":4,"saturday":5,"sunday":6
}

DEFAULT_PLAYERS = ["Kirwan, Rory", "Kirwan, Lisa", "Carrick, Paul", "Hennelly, Ronan"]


def load_players() -> list[str]:
    if not GITHUB_TOKEN or not REPO:
        return DEFAULT_PLAYERS
    url = f"https://api.github.com/repos/{REPO}/contents/players.json"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3.raw",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            players = data.get("players", DEFAULT_PLAYERS)[:4]
            print(f"✅ Players from app: {', '.join(players)}")
            return players
    except:
        print(f"ℹ️  Using default players: {DEFAULT_PLAYERS}")
        return DEFAULT_PLAYERS


def get_next_date_for_day(day_name: str) -> str:
    dow = DAY_MAP.get(day_name.lower(), 0)
    today = datetime.now()
    days_ahead = (dow - today.weekday()) % 7 or 7
    return (today + timedelta(days=days_ahead)).strftime("%d/%m/%Y")


async def main():
    players     = load_players()
    target_date = get_next_date_for_day(TEST_DAY)

    print("=" * 54)
    print(f"  🏌️  TeeBot TEST RUN")
    print(f"  Day    : {TEST_DAY} ({target_date})")
    print(f"  Time   : {TEST_TIME}")
    print(f"  Players: {', '.join(players)}")
    print("=" * 54)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page()

        # ── Login ──────────────────────────────────────────────────────────────
        print("\n🔐 Logging in...")
        await page.goto(BRS_URL, wait_until="networkidle")
        await page.screenshot(path="debug_01_landing.png")

        try:
            await page.fill('input[name="username"], input[placeholder*="GUI"], input[placeholder*="digit"]', BRS_EMAIL)
            await page.fill('input[type="password"]', BRS_PASSWORD)
            await page.screenshot(path="debug_02_credentials_filled.png")
            await page.click('button[type="submit"], input[type="submit"]')
            await page.wait_for_load_state("networkidle")
            await page.screenshot(path="debug_03_after_login.png")
            print("✅ Logged in")
        except Exception as e:
            print(f"❌ Login failed: {e}")
            await page.screenshot(path="error_login.png")
            await browser.close()
            return

        # ── Navigate to tee sheet ──────────────────────────────────────────────
        print(f"\n📅 Looking for {TEST_DAY} {target_date}...")
        day_num = target_date.split("/")[0]

        for sel in [f'a:has-text("{target_date}")', f'td:has-text("{day_num}")', f'button:has-text("{day_num}")']:
            try:
                await page.click(sel, timeout=3000); break
            except: continue

        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1500)
        await page.screenshot(path="debug_04_date_selected.png")

        # ── Find time slot ─────────────────────────────────────────────────────
        print(f"\n🕐 Looking for {TEST_TIME} slot...")
        clicked = False
        for sel in [f'a:has-text("{TEST_TIME}")', f'button:has-text("{TEST_TIME}")',
                    f'td:has-text("{TEST_TIME}")', f'[data-time="{TEST_TIME}"]']:
            try:
                await page.click(sel, timeout=3000)
                print(f"✅ Clicked {TEST_TIME}")
                clicked = True; break
            except: continue

        if not clicked:
            print(f"⚠️  {TEST_TIME} not found exactly — trying nearby slots")
            try:
                await page.click('a.bookable, button.available, td.available a', timeout=5000)
                print("✅ Clicked first available slot")
            except Exception as e:
                print(f"❌ No available slots found: {e}")

        await page.wait_for_load_state("networkidle")
        await page.screenshot(path="debug_05_time_selected.png")

        # ── Set players ────────────────────────────────────────────────────────
        print(f"\n👥 Setting {len(players)} players...")
        try:
            await page.select_option('select[name*="player"], select[name*="member"], select#players', str(len(players)))
        except:
            for _ in range(len(players) - 1):
                try: await page.click('button:has-text("+"), .increase-players', timeout=2000)
                except: break

        # Add named players
        for i, player in enumerate(players):
            for sel in [
                f'input[placeholder*="member"]:nth-of-type({i+1})',
                f'input[placeholder*="player"]:nth-of-type({i+1})',
                f'.player-input:nth-child({i+1}) input',
            ]:
                try:
                    await page.fill(sel, player, timeout=2000)
                    await page.wait_for_timeout(600)
                    await page.click('.autocomplete-item:first-child, li[role="option"]:first-child', timeout=1500)
                    print(f"  ✅ Added {player}")
                    break
                except: continue

        await page.wait_for_timeout(1000)
        await page.screenshot(path="debug_06_players_added.png")

        # ── Confirm ────────────────────────────────────────────────────────────
        print("\n📋 Confirming...")
        for sel in ['button:has-text("Confirm")', 'button:has-text("Book")',
                    'input[value="Confirm"]', 'button[type="submit"]']:
            try:
                await page.click(sel, timeout=3000)
                print("✅ Booking confirmed!")
                break
            except: continue

        await page.wait_for_timeout(2000)
        await page.screenshot(path="confirmation_test.png", full_page=True)
        print("\n📸 All screenshots saved — check the Actions artifacts tab.")
        await browser.close()

    print("\n" + "=" * 54)
    print("  TEST COMPLETE — check screenshots in GitHub Actions")
    print("=" * 54)

if __name__ == "__main__":
    asyncio.run(main())
