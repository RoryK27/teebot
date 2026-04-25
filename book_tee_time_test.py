"""
TeeBot TEST SCRIPT - Beaverstown Golf Club BRS
Flow: Login -> go directly to tee sheet URL -> select date -> click BOOK NOW -> add players -> confirm
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
DEFAULT_BOOKING = {"label": "Monday Evening", "day_of_week": 0, "target_time": "18:00"}
MONTH_NAMES     = ["January","February","March","April","May","June",
                   "July","August","September","October","November","December"]


def load_plan():
    if not GITHUB_TOKEN or not REPO:
        return DEFAULT_PLAYERS, [DEFAULT_BOOKING]
    url = f"https://api.github.com/repos/{REPO}/contents/players.json"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3.raw",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            return data.get("players", DEFAULT_PLAYERS)[:4], data.get("bookings", [DEFAULT_BOOKING])
    except Exception as e:
        print(f"Could not read players.json: {e} — using defaults")
        return DEFAULT_PLAYERS, [DEFAULT_BOOKING]


def get_next_date_for_dow(dow: int) -> datetime:
    today = datetime.now()
    days_ahead = (dow - today.weekday()) % 7 or 7
    return today + timedelta(days=days_ahead)


async def login(page) -> bool:
    print("Step 1: Logging in...")
    await page.goto(BRS_LOGIN_URL, wait_until="networkidle")
    await page.wait_for_timeout(1500)
    await page.screenshot(path="debug_01_landing.png")
    try:
        await page.fill('input[name="username"], input[type="text"]', BRS_EMAIL)
        await page.fill('input[type="password"]', BRS_PASSWORD)
        await page.screenshot(path="debug_02_credentials_filled.png")
        await page.click('button:has-text("LOGIN")')
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(2000)
        await page.screenshot(path="debug_03_after_login.png")
        content = await page.content()
        if "Logout" in content or "Tee Sheet" in content or "31220248" in content:
            print("  Logged in!")
            return True
        print("  Login may have failed")
        await page.screenshot(path="error_login.png")
        return False
    except Exception as e:
        print(f"  Login error: {e}")
        await page.screenshot(path="error_login.png")
        return False


async def go_to_tee_sheet(page) -> bool:
    print("\nStep 2: Going directly to tee sheet...")
    await page.goto(TEE_SHEET_URL, wait_until="networkidle")
    await page.wait_for_timeout(2000)
    await page.screenshot(path="debug_04_tee_sheet.png", full_page=True)
    print("  On tee sheet!")
    return True


async def select_date(page, target_dt: datetime) -> bool:
    print(f"\nStep 3: Selecting date {target_dt.strftime('%A %d %B')}...")
    await page.screenshot(path="debug_05_calendar.png", full_page=True)

    target_day   = target_dt.day
    target_month = target_dt.month
    target_year  = target_dt.year
    month_name   = MONTH_NAMES[target_month - 1]

    for attempt in range(8):
        await page.screenshot(path=f"debug_cal_{attempt:02d}.png")
        content = await page.content()

        if month_name in content and str(target_year) in content:
            print(f"  Correct month ({month_name} {target_year}) showing")
            try:
                await page.click(
                    f'[data-date="{target_dt.strftime("%Y-%m-%d")}"], '
                    f'td:has-text("{target_day}"):not(.disabled):not(.old):not(.new), '
                    f'[data-day="{target_day}"]',
                    timeout=3000
                )
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(2000)
                await page.screenshot(path="debug_06_date_selected.png", full_page=True)
                print(f"  Clicked day {target_day}!")
                return True
            except Exception as e:
                print(f"  Direct click failed: {e}")
                clicked = await page.evaluate(f"""
                    () => {{
                        const cells = document.querySelectorAll('td, .day, a');
                        for (const cell of cells) {{
                            if (cell.textContent.trim() === '{target_day}' &&
                                !cell.classList.contains('disabled') &&
                                !cell.classList.contains('old')) {{
                                cell.click();
                                return true;
                            }}
                        }}
                        return false;
                    }}
                """)
                if clicked:
                    await page.wait_for_timeout(2000)
                    await page.screenshot(path="debug_06_date_selected.png", full_page=True)
                    print(f"  Clicked day {target_day} via JS!")
                    return True

        try:
            await page.click(
                'th.next, .next-month, [aria-label="next month"], '
                'th:has-text("›"), th:has-text(">")',
                timeout=2000
            )
            await page.wait_for_timeout(1000)
            print(f"  Clicked next month (attempt {attempt})")
        except:
            print(f"  No next arrow found on attempt {attempt}")
            break

    await page.screenshot(path="debug_06_date_final.png", full_page=True)
    return True


async def select_time_and_book(page, target_time: str) -> bool:
    print(f"\nStep 4: Finding {target_time} BOOK NOW button...")
    await page.wait_for_timeout(1500)
    await page.screenshot(path="debug_07_tee_sheet.png", full_page=True)

    content = await page.content()
    if target_time in content:
        print(f"  '{target_time}' found on page!")
    else:
        print(f"  '{target_time}' NOT in page — printing visible times...")
        times = await page.locator('text=/\\d{2}:\\d{2}/').all_text_contents()
        print(f"  Visible times: {times[:15]}")

    try:
        await page.locator(f'tr:has-text("{target_time}") a:has-text("BOOK NOW")').first.click(timeout=5000)
        print(f"  Clicked BOOK NOW at {target_time}!")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(2000)
        await page.screenshot(path="debug_08_after_book_now.png", full_page=True)
        return True
    except Exception as e:
        print(f"  Method 1 failed: {e}")

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
            print("  Method 2: JS click succeeded")
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)
            await page.screenshot(path="debug_08_js_click.png", full_page=True)
            return True
        print("  Method 2: No match found")
    except Exception as e:
        print(f"  Method 2 failed: {e}")

    try:
        buttons = page.locator('a:has-text("BOOK NOW"), button:has-text("BOOK NOW")')
        count = await buttons.count()
        print(f"  Found {count} BOOK NOW buttons total")
        if count > 0:
            await buttons.first.click()
            print("  Clicked first BOOK NOW as fallback")
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)
            await page.screenshot(path="debug_08_fallback.png", full_page=True)
            return True
    except Exception as e:
        print(f"  Method 3 failed: {e}")

    await page.screenshot(path="debug_08_no_slot_found.png", full_page=True)
    return False


async def fill_players_and_confirm(page, players: list) -> bool:
    print(f"\nStep 5: Adding {len(players)} players...")
    await page.screenshot(path="debug_09_booking_form.png", full_page=True)
    await page.wait_for_timeout(1000)

    for i, player in enumerate(players):
        slot_num = i + 1
        first_name = player.split(",")[1].strip() if "," in player else player
        print(f"  Player {slot_num}: {player}")

        for sel in [
            f'select:nth-of-type({slot_num})',
            f'select[name*="player{slot_num}"]',
            f'#player{slot_num}',
        ]:
            try:
                await page.select_option(sel, label=player, timeout=2000)
                print(f"    Dropdown selected: {player}")
                break
            except:
                continue

        for sel in [
            f'input[placeholder*="buddy"]:nth-of-type({slot_num})',
            f'input[placeholder*="player"]:nth-of-type({slot_num})',
            f'input[placeholder*="member"]:nth-of-type({slot_num})',
            f'input[placeholder*="search"]:nth-of-type({slot_num})',
        ]:
            try:
                await page.fill(sel, first_name, timeout=2000)
                await page.wait_for_timeout(800)
                await page.click(
                    'li[role="option"]:first-child, '
                    '.autocomplete-suggestion:first-child, '
                    '.dropdown-item:first-child',
                    timeout=2000
                )
                print(f"    Autocomplete selected: {player}")
                break
            except:
                continue

    await page.wait_for_timeout(1000)
    await page.screenshot(path="debug_10_players_added.png", full_page=True)

    print("  Clicking Create Booking...")
    for sel in [
        'button:has-text("Create Booking")',
        'a:has-text("Create Booking")',
        'button:has-text("CREATE BOOKING")',
        'input[value="Create Booking"]',
        'button:has-text("Confirm")',
        'button:has-text("Book")',
        'button[type="submit"]',
    ]:
        try:
            await page.click(sel, timeout=3000)
            print(f"  Clicked confirm!")
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(3000)
            await page.screenshot(path="confirmation_test.png", full_page=True)
            print("  BOOKING COMPLETE!")
            return True
        except:
            continue

    await page.screenshot(path="debug_11_no_confirm.png", full_page=True)
    print("  Could not find Create Booking — check debug_10_players_added.png")
    return False


async def main():
    players, bookings = load_plan()
    booking     = bookings[0]
    dow         = booking.get("day_of_week", 0)
    target_time = booking.get("target_time", "18:00")
    target_dt   = get_next_date_for_dow(dow)

    print("=" * 54)
    print("  TeeBot TEST — Beaverstown Golf Club")
    print(f"  Date   : {target_dt.strftime('%A %d %B %Y')}")
    print(f"  Time   : {target_time}")
    print(f"  Players: {', '.join(players)}")
    print("=" * 54)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page(viewport={"width": 1280, "height": 900})

        if not await login(page):
            await browser.close()
            return

        await go_to_tee_sheet(page)
        await select_date(page, target_dt)
        success = await select_time_and_book(page, target_time)

        if success:
            await fill_players_and_confirm(page, players)
        else:
            print("\nCould not find time slot — check debug_07_tee_sheet.png")

        await browser.close()

    print("\n" + "=" * 54)
    print("  Done — check Artifacts for screenshots")
    print("=" * 54)


if __name__ == "__main__":
    asyncio.run(main())
