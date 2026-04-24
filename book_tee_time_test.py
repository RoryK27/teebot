"""
TeeBot TEST SCRIPT - Beaverstown Golf Club BRS
Goes directly to tee sheet URL, navigates to date, clicks BOOK NOW.
"""

import asyncio, os, json, urllib.request
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

BRS_LOGIN_URL  = "https://members.brsgolf.com/beaverstown"
TEE_SHEET_URL  = "https://members.brsgolf.com/beaverstown/tee-sheet/1"

BRS_EMAIL    = os.environ["BRS_EMAIL"]
BRS_PASSWORD = os.environ["BRS_PASSWORD"]
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO         = os.environ.get("GITHUB_REPOSITORY", "")

DEFAULT_PLAYERS = ["Kirwan, Rory", "Kirwan, Lisa", "Carrick, Paul", "Hennelly, Ronan"]
DEFAULT_BOOKING = {"label": "Monday Evening", "day_of_week": 0, "target_time": "18:00"}

DAY_NAMES = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]


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
            players  = data.get("players", DEFAULT_PLAYERS)[:4]
            bookings = data.get("bookings", [DEFAULT_BOOKING])
            return players, bookings
    except Exception as e:
        print(f"Could not read players.json: {e} — using defaults")
        return DEFAULT_PLAYERS, [DEFAULT_BOOKING]


def get_next_date_for_dow(dow: int) -> datetime:
    today = datetime.now()
    days_ahead = (dow - today.weekday()) % 7 or 7
    return today + timedelta(days=days_ahead)


async def login(page) -> bool:
    print("Logging in...")
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

        # Check we're logged in by looking for membership number on page
        content = await page.content()
        if "31220248" in content or "Logout" in content or "Tee Sheet" in content:
            print("Logged in successfully!")
            return True
        else:
            print("Login may have failed — unexpected page content")
            await page.screenshot(path="error_login.png")
            return False
    except Exception as e:
        print(f"Login error: {e}")
        await page.screenshot(path="error_login.png")
        return False


async def go_to_date(page, target_dt: datetime) -> bool:
    """Navigate directly to the tee sheet and click to the right date."""

    # BRS uses date in URL format — try direct URL first
    date_str_url = target_dt.strftime("%Y-%m-%d")
    direct_url = f"{TEE_SHEET_URL}?date={date_str_url}"

    print(f"\nNavigating to tee sheet for {target_dt.strftime('%A %d %B')}...")
    print(f"Trying direct URL: {direct_url}")

    await page.goto(direct_url, wait_until="networkidle")
    await page.wait_for_timeout(2000)
    await page.screenshot(path="debug_04_tee_sheet_direct.png")

    # Check if we landed on the right date
    content = await page.content()
    day_name = DAY_NAMES[target_dt.weekday()].upper()
    day_num  = str(target_dt.day)
    month    = target_dt.strftime("%b").upper()

    print(f"Looking for: {day_name} {day_num} {month} in page")

    if day_num in content and month in content:
        print(f"Found target date on page!")
        return True

    # If direct URL didn't work, go to base tee sheet and use arrows
    print("Direct URL didn't land on correct date — navigating with arrows...")
    await page.goto(TEE_SHEET_URL, wait_until="networkidle")
    await page.wait_for_timeout(2000)

    for attempt in range(10):
        await page.screenshot(path=f"debug_nav_{attempt:02d}.png")
        content = await page.content()

        if day_num in content and month in content:
            print(f"Found date after {attempt} arrow clicks")
            return True

        # Try clicking the right/next arrow
        clicked = False
        for sel in [
            '.fc-next-button',
            'a[title="next day"]',
            'button[title="next day"]',
            '[aria-label="next"]',
            'a:has-text(">")',
            '.next',
            'a[href*="tee-sheet"]:has-text(">")',
        ]:
            try:
                await page.click(sel, timeout=2000)
                await page.wait_for_timeout(1000)
                clicked = True
                break
            except:
                continue

        if not clicked:
            # Try clicking the date header itself
            try:
                header = await page.text_content('.date-header, .tee-sheet-date, h2, h3')
                print(f"Current date header: {header}")
            except:
                pass
            print(f"Could not find next arrow on attempt {attempt}")
            break

    return True  # Continue anyway and try to book


async def click_book_now(page, target_time: str) -> bool:
    """Find the BOOK NOW button next to our target time and click it."""
    print(f"\nLooking for BOOK NOW at {target_time}...")
    await page.screenshot(path="debug_05_looking_for_time.png")

    # Print page structure for debugging
    try:
        content = await page.content()
        if target_time in content:
            print(f"'{target_time}' IS found in page content")
        else:
            print(f"'{target_time}' NOT found in page — printing visible times...")
            # Find all times visible on page
            times = await page.locator('text=/\\d{2}:\\d{2}/').all_text_contents()
            print(f"Times visible: {times[:10]}")
    except:
        pass

    # Method 1: Find row with the time and click its Book Now button
    try:
        # Use XPath-style to find BOOK NOW link in same row as time
        await page.locator(f'tr:has-text("{target_time}") a:has-text("BOOK NOW")').first.click(timeout=5000)
        print(f"Method 1: Clicked BOOK NOW in row with {target_time}")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(2000)
        await page.screenshot(path="debug_06_after_book_now.png")
        return True
    except Exception as e:
        print(f"Method 1 failed: {e}")

    # Method 2: JavaScript — find the time cell and click adjacent Book Now
    try:
        clicked = await page.evaluate(f"""
            () => {{
                // Find all cells/elements containing the target time
                const allElements = document.querySelectorAll('td, div, span');
                for (const el of allElements) {{
                    if (el.textContent.trim() === '{target_time}') {{
                        // Look in parent row for a Book Now link
                        const row = el.closest('tr') || el.parentElement;
                        if (row) {{
                            const links = row.querySelectorAll('a, button');
                            for (const link of links) {{
                                if (link.textContent.includes('BOOK') || link.textContent.includes('Book')) {{
                                    link.click();
                                    return true;
                                }}
                            }}
                        }}
                    }}
                }}
                return false;
            }}
        """)
        if clicked:
            print("Method 2: Clicked via JavaScript")
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)
            await page.screenshot(path="debug_06_after_js_click.png")
            return True
        else:
            print("Method 2: Time found but no Book Now button in same row")
    except Exception as e:
        print(f"Method 2 failed: {e}")

    # Method 3: Screenshot the full page so we can see what's there
    await page.screenshot(path="debug_full_page.png", full_page=True)
    print("Saved full page screenshot — check debug_full_page.png")
    return False


async def complete_booking(page, players: list) -> bool:
    """Fill in player details and confirm the booking."""
    print(f"\nCompleting booking for {', '.join(players)}...")
    await page.screenshot(path="debug_07_booking_form.png")

    # Set player count to 4
    try:
        await page.select_option('select', str(len(players)))
        print(f"Set player count to {len(players)}")
    except:
        pass

    await page.wait_for_timeout(1000)

    # Confirm / Book button
    for sel in [
        'button:has-text("Confirm")',
        'button:has-text("CONFIRM")',
        'a:has-text("Confirm")',
        'button:has-text("Book")',
        'input[value="Confirm"]',
        'button[type="submit"]',
    ]:
        try:
            await page.click(sel, timeout=3000)
            print(f"Clicked confirm!")
            await page.wait_for_timeout(3000)
            await page.screenshot(path="confirmation_test.png", full_page=True)
            return True
        except:
            continue

    await page.screenshot(path="debug_08_no_confirm.png")
    return False


async def main():
    players, bookings = load_plan()
    booking    = bookings[0]
    dow        = booking.get("day_of_week", 0)
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
            print("Login failed — check your BRS_EMAIL secret (should be GUI number 31220248)")
            await browser.close()
            return

        await go_to_date(page, target_dt)

        success = await click_book_now(page, target_time)

        if success:
            await complete_booking(page, players)
        else:
            print("\nCould not find the time slot — check debug_full_page.png to see what the bot saw")

        await browser.close()

    print("\n" + "=" * 54)
    print(f"  Done — check screenshots in Artifacts")
    print("=" * 54)


if __name__ == "__main__":
    asyncio.run(main())
