"""
TeeBot TEST SCRIPT - Beaverstown Golf Club BRS
Logs in, navigates tee sheet, selects date and books the target time.
"""

import asyncio, os, json, urllib.request
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

BRS_URL      = "https://members.brsgolf.com/beaverstown"
TEE_SHEET_URL = "https://members.brsgolf.com/beaverstown/tee-sheet/1"
BRS_EMAIL    = os.environ["BRS_EMAIL"]
BRS_PASSWORD = os.environ["BRS_PASSWORD"]
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO         = os.environ.get("GITHUB_REPOSITORY", "")

DAY_MAP = {
    "monday":0,"tuesday":1,"wednesday":2,"thursday":3,
    "friday":4,"saturday":5,"sunday":6
}

DEFAULT_PLAYERS = ["Kirwan, Rory", "Kirwan, Lisa", "Carrick, Paul", "Hennelly, Ronan"]


def load_plan():
    """Load players + bookings from players.json."""
    if not GITHUB_TOKEN or not REPO:
        return DEFAULT_PLAYERS, [{"label":"Monday Evening","day_of_week":0,"target_time":"18:00"}]
    url = f"https://api.github.com/repos/{REPO}/contents/players.json"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3.raw",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            players  = data.get("players", DEFAULT_PLAYERS)[:4]
            bookings = data.get("bookings", [{"label":"Monday Evening","day_of_week":0,"target_time":"18:00"}])
            print(f"Players : {', '.join(players)}")
            print(f"Bookings: {[(b['label'], b['target_time']) for b in bookings]}")
            return players, bookings
    except Exception as e:
        print(f"Could not read players.json: {e} — using defaults")
        return DEFAULT_PLAYERS, [{"label":"Monday Evening","day_of_week":0,"target_time":"18:00"}]


def get_next_date_for_dow(dow: int):
    today = datetime.now()
    days_ahead = (dow - today.weekday()) % 7 or 7
    target = today + timedelta(days=days_ahead)
    return target, target.strftime("%d/%m/%Y")


async def login(page) -> bool:
    print("\n Logging in...")
    await page.goto(BRS_URL, wait_until="networkidle")
    await page.screenshot(path="debug_01_landing.png")
    try:
        # Fill username (GUI number) and password
        await page.fill('input[name="username"], input[placeholder*="GUI"], input[placeholder*="digit"], input[type="text"]', BRS_EMAIL)
        await page.fill('input[type="password"]', BRS_PASSWORD)
        await page.screenshot(path="debug_02_credentials_filled.png")
        await page.click('button:has-text("LOGIN"), input[value="LOGIN"], button[type="submit"]')
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(2000)
        await page.screenshot(path="debug_03_after_login.png")
        print(" Logged in successfully")
        return True
    except Exception as e:
        print(f" Login failed: {e}")
        await page.screenshot(path="error_login.png")
        return False


async def navigate_to_tee_sheet(page, target_date: datetime) -> bool:
    """Click Tee Sheet in menu and navigate to the correct date."""
    print(f"\n Navigating to tee sheet...")
    try:
        # Click "Tee Sheet" in left nav
        await page.click('a:has-text("Tee Sheet"), a[href*="tee-sheet"]', timeout=5000)
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(2000)
        await page.screenshot(path="debug_04_tee_sheet.png")
        print(" On tee sheet page")
    except Exception as e:
        print(f" Could not click Tee Sheet nav: {e} — going directly")
        await page.goto(TEE_SHEET_URL, wait_until="networkidle")
        await page.wait_for_timeout(2000)
        await page.screenshot(path="debug_04_tee_sheet.png")

    # Now navigate to the right date
    # BRS shows a date at the top — we need to click forward/back arrows to reach our date
    print(f" Looking for date: {target_date.strftime('%a %d %b').upper()}")
    await select_date(page, target_date)
    return True


async def select_date(page, target_date: datetime):
    """Click the forward arrow on the calendar until we reach the target date."""
    target_str_variants = [
        target_date.strftime("%a %d %b").upper(),      # MON 27 APR
        target_date.strftime("%-d %b").upper(),         # 27 APR
        target_date.strftime("%d/%m/%Y"),               # 27/04/2025
        target_date.strftime("%A %d %B"),               # Monday 27 April
        target_date.strftime("%d %b").upper(),          # 27 APR
    ]

    print(f" Target date variants: {target_str_variants}")

    # Check if already on the right date
    for variant in target_str_variants:
        try:
            content = await page.content()
            if variant in content.upper():
                print(f" Already on correct date: {variant}")
                return
        except:
            pass

    # Click the forward (>) arrow up to 14 times to find the date
    for attempt in range(14):
        await page.screenshot(path=f"debug_date_nav_{attempt:02d}.png")

        # Check current page for our target date
        content = await page.content()
        for variant in target_str_variants:
            if variant in content.upper():
                print(f" Found target date after {attempt} clicks: {variant}")
                return

        # Click the next day arrow
        next_selectors = [
            'a[aria-label="next"]',
            'button[aria-label="next"]',
            '.next-day',
            '.fc-next-button',
            'a:has-text(">")',
            'button:has-text(">")',
            '.arrow-right',
            '[class*="next"]',
        ]

        clicked = False
        for sel in next_selectors:
            try:
                await page.click(sel, timeout=2000)
                await page.wait_for_timeout(1000)
                clicked = True
                break
            except:
                continue

        if not clicked:
            print(f" Could not find next arrow on attempt {attempt}")
            break

    await page.screenshot(path="debug_date_final.png")


async def book_slot(page, target_time: str, players: list) -> bool:
    """Find and click the BOOK NOW button for the target time."""
    print(f"\n Looking for {target_time} slot...")
    await page.screenshot(path="debug_05_before_booking.png")

    # Try clicking BOOK NOW button next to the target time
    # BRS layout: time is in left column, BOOK NOW button on the right of same row
    time_variants = [target_time, target_time.replace(":","")]  # "18:00" or "1800"

    for t in time_variants:
        try:
            # Try finding a row containing the time and clicking Book Now within it
            row = page.locator(f'tr:has-text("{t}"), div:has-text("{t}")')
            await row.locator('a:has-text("BOOK NOW"), button:has-text("BOOK NOW")').first.click(timeout=3000)
            print(f" Clicked BOOK NOW for {t}")
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)
            await page.screenshot(path="debug_06_after_book_now.png")
            return await complete_booking(page, players)
        except Exception as e:
            print(f" Row approach failed for {t}: {e}")

    # Fallback: find all BOOK NOW buttons and pick the one nearest our time
    try:
        # Get page content and find the right button by position
        await page.evaluate(f"""
            const rows = document.querySelectorAll('tr, .slot-row, [class*="row"]');
            for (const row of rows) {{
                if (row.textContent.includes('{target_time}')) {{
                    const btn = row.querySelector('a, button');
                    if (btn && btn.textContent.includes('BOOK')) btn.click();
                }}
            }}
        """)
        await page.wait_for_timeout(2000)
        await page.screenshot(path="debug_06_after_js_click.png")
        return await complete_booking(page, players)
    except Exception as e:
        print(f" JS fallback failed: {e}")

    return False


async def complete_booking(page, players: list) -> bool:
    """Fill in the booking form and confirm."""
    print(f"\n Completing booking form...")
    await page.screenshot(path="debug_07_booking_form.png")

    # Set number of players (4)
    try:
        await page.select_option('select[name*="player"], select[name*="guest"], #players', str(len(players)))
        print(f" Set player count to {len(players)}")
    except:
        for _ in range(len(players) - 1):
            try:
                await page.click('button:has-text("+"), .btn-plus, [class*="increase"]', timeout=2000)
                await page.wait_for_timeout(300)
            except:
                break

    # Try to add buddy names from the buddy list search
    for i, player in enumerate(players):
        try:
            # BRS buddy search — type name and select from dropdown
            search_selectors = [
                f'input[placeholder*="buddy"]:nth-of-type({i+1})',
                f'input[placeholder*="search"]:nth-of-type({i+1})',
                f'input[placeholder*="member"]:nth-of-type({i+1})',
                f'.buddy-search input:nth-of-type({i+1})',
            ]
            for sel in search_selectors:
                try:
                    await page.fill(sel, player.split(",")[0].strip(), timeout=2000)
                    await page.wait_for_timeout(800)
                    await page.click('.autocomplete-suggestion:first-child, li[role="option"]:first-child, .dropdown-item:first-child', timeout=2000)
                    print(f" Added {player}")
                    break
                except:
                    continue
        except:
            pass

    await page.wait_for_timeout(1000)
    await page.screenshot(path="debug_08_form_filled.png")

    # Click Confirm / Book
    for sel in [
        'button:has-text("Confirm")',
        'button:has-text("Book")',
        'a:has-text("Confirm")',
        'input[value="Confirm"]',
        'button[type="submit"]',
        '.btn-confirm',
    ]:
        try:
            await page.click(sel, timeout=3000)
            print(" Clicked confirm!")
            await page.wait_for_timeout(3000)
            await page.screenshot(path="confirmation_test.png", full_page=True)
            return True
        except:
            continue

    await page.screenshot(path="debug_09_no_confirm_found.png")
    return False


async def main():
    players, bookings = load_plan()
    booking = bookings[0]  # For test, just run the first booking
    dow = booking.get("day_of_week", 0)
    target_time = booking.get("target_time", "18:00")
    target_dt, target_date_str = get_next_date_for_dow(dow)

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
            print("Login failed — check BRS_EMAIL and BRS_PASSWORD secrets")
            await browser.close()
            return

        await navigate_to_tee_sheet(page, target_dt)
        await page.wait_for_timeout(1500)
        await page.screenshot(path="debug_05_date_page.png")

        success = await book_slot(page, target_time, players)

        await browser.close()

    print("\n" + "=" * 54)
    print(f"  Result: {'BOOKED!' if success else 'Check screenshots for details'}")
    print("=" * 54)


if __name__ == "__main__":
    asyncio.run(main())
