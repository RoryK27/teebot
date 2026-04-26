"""
TeeBot TEST SCRIPT - Beaverstown Golf Club BRS
Working flow - now with full 4-player autocomplete search
"""

import asyncio, os, json, urllib.request
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

BRS_LOGIN_URL  = "https://members.brsgolf.com/beaverstown"
TEE_SHEET_URL  = "https://members.brsgolf.com/beaverstown/tee-sheet/1"
BRS_EMAIL      = os.environ["BRS_EMAIL"]
BRS_PASSWORD   = os.environ["BRS_PASSWORD"]
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN", "")
REPO           = os.environ.get("GITHUB_REPOSITORY", "")

DEFAULT_PLAYERS = ["Kirwan, Rory", "Kirwan, Lisa", "Carrick, Paul", "Hennelly, Ronan"]
MONTH_NAMES     = ["January","February","March","April","May","June",
                   "July","August","September","October","November","December"]

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


async def go_to_tee_sheet(page):
    print("\nStep 2: Navigating to tee sheet...")
    await page.goto(TEE_SHEET_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)
    await page.screenshot(path="debug_04_tee_sheet.png", full_page=True)


async def select_date(page, target_dt: datetime):
    print(f"\nStep 3: Selecting {target_dt.strftime('%A %d %B %Y')}...")
    target_day   = str(target_dt.day)   # string e.g. "28"
    target_month = MONTH_NAMES[target_dt.month - 1]
    target_year  = str(target_dt.year)

    # Step 1: Click the date button at top of tee sheet to open calendar
    print("  Opening calendar by clicking date button...")
    try:
        # The date button is a <button> containing the current date e.g. "SUN 27TH APR"
        await page.click('button.btn-date, button[class*="date"], .date-picker button', timeout=3000)
        print("  Opened via .btn-date")
    except:
        # JS fallback — find button/element containing a month abbreviation
        clicked = await page.evaluate("""
            () => {
                const months = ['JAN','FEB','MAR','APR','MAY','JUN',
                               'JUL','AUG','SEP','OCT','NOV','DEC'];
                // Look specifically in the header area
                const header = document.querySelector('header, nav, .header, .tee-sheet-header, .top-bar');
                const searchIn = header || document;
                const btns = searchIn.querySelectorAll('button, a');
                for (const btn of btns) {
                    const text = btn.textContent.trim().toUpperCase();
                    if (months.some(m => text.includes(m)) && text.length < 40) {
                        btn.click();
                        return text;
                    }
                }
                return null;
            }
        """)
        print(f"  Opened via JS: {clicked}")

    await page.wait_for_timeout(2000)
    await page.screenshot(path="debug_05_calendar_open.png", full_page=True)

    # Step 2: Navigate to correct month if needed
    for attempt in range(4):
        page_content = await page.content()
        if target_month in page_content and target_year in page_content:
            print(f"  Correct month visible: {target_month} {target_year}")
            break
        try:
            await page.click('th.next, .datepicker th.next, [aria-label="next month"]', timeout=2000)
            await page.wait_for_timeout(800)
            print(f"  Clicked next month")
        except:
            print(f"  Could not navigate month")
            break

    await page.screenshot(path="debug_05b_month.png", full_page=True)

    # Step 3: Click the target day — compare as string to avoid type mismatch
    print(f"  Clicking day {target_day}...")
    clicked = await page.evaluate(f"""
        () => {{
            const cells = document.querySelectorAll('td');
            for (const cell of cells) {{
                const txt = cell.textContent.trim();
                if (txt === '{target_day}' &&
                    !cell.classList.contains('disabled') &&
                    !cell.classList.contains('old') &&
                    !cell.classList.contains('new')) {{
                    cell.click();
                    return true;
                }}
            }}
            // Also try any element with just that number
            const all = document.querySelectorAll('td, span, div');
            for (const el of all) {{
                if (el.textContent.trim() === '{target_day}' && el.tagName !== 'SPAN') {{
                    el.click();
                    return 'fallback';
                }}
            }}
            return false;
        }}
    """)
    print(f"  Day {target_day} click result: {clicked}")
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(3000)
    await page.screenshot(path="debug_06_date_selected.png", full_page=True)
    print(f"  Tee sheet should now show {target_dt.strftime('%A %d %B')}")


async def select_time_and_book(page, target_time: str) -> bool:
    print(f"\nStep 4: Finding {target_time} BOOK NOW...")
    await page.wait_for_timeout(2000)
    await page.screenshot(path="debug_07_tee_times.png", full_page=True)

    content = await page.content()
    if target_time in content:
        print(f"  '{target_time}' found!")
    else:
        times = await page.locator('text=/\\d{2}:\\d{2}/').all_text_contents()
        print(f"  '{target_time}' not found. Visible: {times[:10]}")

    # Method 1: row with time + BOOK NOW
    try:
        await page.locator(f'tr:has-text("{target_time}") a:has-text("BOOK NOW")').first.click(timeout=5000)
        print(f"  Clicked BOOK NOW at {target_time}!")
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(2000)
        await page.screenshot(path="debug_08_after_book_now.png", full_page=True)
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
            print("  Method 2 worked!")
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(2000)
            await page.screenshot(path="debug_08_js_click.png", full_page=True)
            return True
    except Exception as e:
        print(f"  Method 2 failed: {e}")

    # Method 3: first BOOK NOW
    try:
        buttons = page.locator('a:has-text("BOOK NOW"), button:has-text("BOOK NOW")')
        count = await buttons.count()
        print(f"  Found {count} BOOK NOW buttons")
        if count > 0:
            await buttons.first.click()
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(2000)
            await page.screenshot(path="debug_08_fallback.png", full_page=True)
            return True
    except Exception as e:
        print(f"  Method 3 failed: {e}")

    await page.screenshot(path="debug_08_failed.png", full_page=True)
    return False


async def add_player(page, slot_num: int, player_name: str) -> bool:
    """
    Type player name into the slot input, wait for autocomplete dropdown,
    then click the matching result.
    BRS format: "Kirwan, Rory" — search by surname first.
    """
    surname   = player_name.split(",")[0].strip()   # e.g. "Kirwan"
    firstname = player_name.split(",")[1].strip() if "," in player_name else player_name

    print(f"  Adding player {slot_num}: {player_name} (searching '{surname}')")

    # Get all player input fields on the page
    inputs = page.locator('input[placeholder*="typing"], input[placeholder*="player"], input[placeholder*="find"]')
    count  = await inputs.count()
    print(f"  Found {count} player input fields")

    if count == 0:
        # Try any visible text input that isn't username/password
        inputs = page.locator('input[type="text"]:visible')
        count  = await inputs.count()
        print(f"  Found {count} visible text inputs as fallback")

    if slot_num > count:
        print(f"  Not enough inputs for slot {slot_num}")
        return False

    target_input = inputs.nth(slot_num - 1)

    # Clear and type surname
    await target_input.click()
    await target_input.fill("")
    await page.wait_for_timeout(300)
    await target_input.type(surname, delay=80)  # Type slowly to trigger autocomplete
    await page.wait_for_timeout(2000)  # Wait for dropdown to appear

    await page.screenshot(path=f"debug_player_{slot_num}_typing.png", full_page=True)

    # Look for autocomplete dropdown results
    dropdown_selectors = [
        f'li:has-text("{surname}")',
        f'li:has-text("{firstname}")',
        f'.autocomplete-result:has-text("{surname}")',
        f'[role="option"]:has-text("{surname}")',
        f'.dropdown-item:has-text("{surname}")',
        f'ul li:has-text("{surname}")',
        f'.tt-suggestion:has-text("{surname}")',
        f'.ui-autocomplete li:has-text("{surname}")',
    ]

    for sel in dropdown_selectors:
        try:
            await page.click(sel, timeout=3000)
            print(f"  Selected '{player_name}' from dropdown!")
            await page.wait_for_timeout(500)
            return True
        except:
            continue

    # If no dropdown found, try pressing Enter or Tab
    try:
        await target_input.press("Enter")
        await page.wait_for_timeout(500)
        print(f"  Pressed Enter for {player_name}")
        return True
    except:
        pass

    print(f"  Could not select {player_name} from dropdown")
    return False


async def fill_players_and_confirm(page, players: list) -> bool:
    print(f"\nStep 5: Adding 4 players to booking form...")
    await page.screenshot(path="debug_09_booking_form.png", full_page=True)
    await page.wait_for_timeout(1000)

    # Player 1 is already filled with the logged-in member (Kirwan, Rory)
    # We need to fill players 2, 3, 4
    # Check if player 1 is already filled
    content = await page.content()
    player1_filled = "Kirwan, Rory" in content or "Kirwan,Rory" in content

    if player1_filled:
        print("  Player 1 (Kirwan, Rory) already filled by BRS ✅")
        # Fill players 2, 3, 4 — use players[1], [2], [3] from our list
        players_to_add = players[1:4]  # Skip Rory, add the other 3
        start_slot = 2
    else:
        players_to_add = players      # Fill all 4
        start_slot = 1

    for i, player in enumerate(players_to_add):
        slot = start_slot + i
        success = await add_player(page, slot, player)
        await page.wait_for_timeout(800)
        await page.screenshot(path=f"debug_player_{slot}_added.png", full_page=True)

    await page.wait_for_timeout(1000)
    await page.screenshot(path="debug_10_all_players.png", full_page=True)

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

    await page.screenshot(path="debug_11_no_confirm.png", full_page=True)
    print("  Could not find Create Booking button")
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
        await go_to_tee_sheet(page)
        await select_date(page, target_dt)
        success = await select_time_and_book(page, TEST_TIME)

        if success:
            await fill_players_and_confirm(page, players)
        else:
            print("\nCould not find time slot — check debug_07_tee_times.png")

        await browser.close()

    print("\n" + "=" * 54)
    print("  Done — check Artifacts")
    print("=" * 54)


if __name__ == "__main__":
    asyncio.run(main())
