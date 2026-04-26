"""
TeeBot TEST SCRIPT - Beaverstown Golf Club BRS
Navigates directly to date URL instead of using calendar popup.
"""

import asyncio, os, json, urllib.request
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

BRS_LOGIN_URL  = "https://members.brsgolf.com/beaverstown"
TEE_SHEET_BASE = "https://members.brsgolf.com/beaverstown/tee-sheet/1"

BRS_EMAIL      = os.environ["BRS_EMAIL"]
BRS_PASSWORD   = os.environ["BRS_PASSWORD"]
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN", "")
REPO           = os.environ.get("GITHUB_REPOSITORY", "")

DEFAULT_PLAYERS = ["Kirwan, Rory", "Kirwan, Lisa", "Carrick, Paul", "Hennelly, Ronan"]

TEST_DAY_OF_WEEK = 1       # 1 = Tuesday
TEST_TIME        = "18:00"


def load_players():
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
            return data.get("players", DEFAULT_PLAYERS)[:4]
    except:
        return DEFAULT_PLAYERS


def get_next_date_for_dow(dow: int) -> datetime:
    today = datetime.now()
    days_ahead = (dow - today.weekday()) % 7 or 7
    return today + timedelta(days=days_ahead)


async def login(page):
    print("Step 1: Logging in...")
    await page.goto(BRS_LOGIN_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)
    await page.screenshot(path="debug_01_landing.png")
    await page.fill('input[name="username"], input[type="text"]', BRS_EMAIL)
    await page.fill('input[type="password"]', BRS_PASSWORD)
    await page.screenshot(path="debug_02_credentials_filled.png")
    await page.click('button:has-text("LOGIN")')
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(3000)
    await page.screenshot(path="debug_03_after_login.png")
    print(f"  URL after login: {page.url}")


async def go_to_date(page, target_dt: datetime):
    """Navigate directly to the tee sheet URL for the target date."""
    date_str = target_dt.strftime("%Y/%m/%d")
    url = f"{TEE_SHEET_BASE}/{date_str}"
    print(f"\nStep 2: Going directly to {target_dt.strftime('%A %d %B')} tee sheet...")
    print(f"  URL: {url}")
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)
    await page.screenshot(path="debug_04_tee_sheet.png", full_page=True)

    # Verify correct date is showing
    content = await page.content()
    day = target_dt.strftime("%-d")
    month = target_dt.strftime("%b").upper()
    if day in content and month in content:
        print(f"  ✅ Correct date showing: {target_dt.strftime('%A %d %B')}")
    else:
        print(f"  ⚠️ Date may not be correct — check debug_04_tee_sheet.png")


async def select_time_and_book(page, target_time: str) -> bool:
    print(f"\nStep 3: Finding {target_time} BOOK NOW...")
    await page.wait_for_timeout(1500)
    await page.screenshot(path="debug_05_tee_times.png", full_page=True)

    content = await page.content()
    if target_time in content:
        print(f"  ✅ '{target_time}' found on page!")
    else:
        print(f"  ⚠️ '{target_time}' not found — visible times:")
        times = await page.locator('text=/\\d{2}:\\d{2}/').all_text_contents()
        print(f"  {times[:10]}")

    # Method 1: row with time + BOOK NOW
    try:
        await page.locator(f'tr:has-text("{target_time}") a:has-text("BOOK NOW")').first.click(timeout=5000)
        print(f"  ✅ Clicked BOOK NOW at {target_time}!")
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(2000)
        await page.screenshot(path="debug_06_after_book_now.png", full_page=True)
        return True
    except Exception as e:
        print(f"  Method 1 failed: {e}")

    # Method 2: JS row scan
    try:
        clicked = await page.evaluate(f"""
            () => {{
                const rows = document.querySelectorAll('tr');
                for (const row of rows) {{
                    if (row.textContent.includes('{target_time}')) {{
                        const btn = row.querySelector('a, button');
                        if (btn) {{ btn.click(); return true; }}
                    }}
                }}
                return false;
            }}
        """)
        if clicked:
            print("  ✅ Method 2: JS clicked!")
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(2000)
            await page.screenshot(path="debug_06_js_click.png", full_page=True)
            return True
    except Exception as e:
        print(f"  Method 2 failed: {e}")

    # Method 3: first BOOK NOW on page
    try:
        buttons = page.locator('a:has-text("BOOK NOW"), button:has-text("BOOK NOW")')
        count = await buttons.count()
        print(f"  Found {count} BOOK NOW buttons")
        if count > 0:
            await buttons.first.click()
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(2000)
            await page.screenshot(path="debug_06_fallback.png", full_page=True)
            return True
    except Exception as e:
        print(f"  Method 3 failed: {e}")

    await page.screenshot(path="debug_06_failed.png", full_page=True)
    return False


async def add_player(page, slot_num: int, player_name: str) -> bool:
    """Type player surname into slot input and select from autocomplete."""
    surname   = player_name.split(",")[0].strip()
    firstname = player_name.split(",")[1].strip() if "," in player_name else player_name
    print(f"  Adding player {slot_num}: {player_name}")

    # Get all player search inputs
    inputs = page.locator('input[placeholder*="typing"], input[placeholder*="player"], input[placeholder*="find"]')
    count  = await inputs.count()

    if count == 0:
        inputs = page.locator('input[type="text"]:visible')
        count  = await inputs.count()

    if slot_num > count:
        print(f"  ⚠️ Only {count} inputs found for slot {slot_num}")
        return False

    target_input = inputs.nth(slot_num - 1)
    await target_input.click()
    await target_input.fill("")
    await page.wait_for_timeout(300)
    await target_input.type(surname, delay=80)
    await page.wait_for_timeout(2000)
    await page.screenshot(path=f"debug_player_{slot_num}_typing.png", full_page=True)

    # Try clicking autocomplete result
    for sel in [
        f'li:has-text("{surname}")',
        f'li:has-text("{firstname}")',
        f'[role="option"]:has-text("{surname}")',
        f'.autocomplete-result:has-text("{surname}")',
        f'.tt-suggestion:has-text("{surname}")',
        f'ul li:has-text("{surname}")',
        f'.dropdown-item:has-text("{surname}")',
    ]:
        try:
            await page.click(sel, timeout=3000)
            print(f"  ✅ Selected {player_name}")
            await page.wait_for_timeout(500)
            return True
        except:
            continue

    # Press Enter as last resort
    await target_input.press("Enter")
    await page.wait_for_timeout(500)
    print(f"  ↩️ Pressed Enter for {player_name}")
    return True


async def fill_players_and_confirm(page, players: list) -> bool:
    print(f"\nStep 4: Adding players to booking form...")
    await page.screenshot(path="debug_07_booking_form.png", full_page=True)
    await page.wait_for_timeout(1000)

    # Player 1 (Kirwan, Rory) is pre-filled by BRS — fill slots 2, 3, 4
    content = await page.content()
    player1_filled = "Kirwan, Rory" in content or "Rory" in content
    if player1_filled:
        print("  ✅ Player 1 (Kirwan, Rory) already filled by BRS")
        players_to_add = players[1:4]
        start_slot = 2
    else:
        players_to_add = players
        start_slot = 1

    for i, player in enumerate(players_to_add):
        slot = start_slot + i
        await add_player(page, slot, player)
        await page.wait_for_timeout(500)
        await page.screenshot(path=f"debug_player_{slot}_added.png", full_page=True)

    await page.wait_for_timeout(1000)
    await page.screenshot(path="debug_08_all_players.png", full_page=True)

    # Click CREATE BOOKING
    print("\n  Clicking CREATE BOOKING...")
    for sel in [
        'button:has-text("Create Booking")',
        'a:has-text("Create Booking")',
        'button:has-text("CREATE BOOKING")',
        '.btn:has-text("Create")',
        'input[value="Create Booking"]',
        'button[type="submit"]',
    ]:
        try:
            await page.click(sel, timeout=3000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(3000)
            await page.screenshot(path="confirmation_test.png", full_page=True)
            print("  ✅ BOOKING COMPLETE!")
            return True
        except:
            continue

    await page.screenshot(path="debug_09_no_confirm.png", full_page=True)
    print("  ❌ Could not find Create Booking button")
    return False


async def main():
    players   = load_players()
    target_dt = get_next_date_for_dow(TEST_DAY_OF_WEEK)

    print("=" * 54)
    print("  TeeBot TEST — Beaverstown Golf Club")
    print(f"  Date   : {target_dt.strftime('%A %d %B %Y')}")
    print(f"  Time   : {TEST_TIME}")
    print(f"  Players: {', '.join(players)}")
    print("=" * 54)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page(viewport={"width": 1280, "height": 900})

        await login(page)
        await go_to_date(page, target_dt)
        success = await select_time_and_book(page, TEST_TIME)

        if success:
            await fill_players_and_confirm(page, players)
        else:
            print("\nCould not find time slot — check debug_05_tee_times.png")

        await browser.close()

    print("\n" + "=" * 54)
    print("  Done — check Artifacts")
    print("=" * 54)


if __name__ == "__main__":
    asyncio.run(main())
