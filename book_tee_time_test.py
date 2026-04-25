"""
TeeBot TEST SCRIPT - Beaverstown Golf Club BRS
Flow:
1. Login
2. Go to tee sheet URL
3. Click the date button (SAT 25TH APR) to open calendar popup
4. Click target day number in calendar
5. Click BOOK NOW at target time
6. Add players and confirm
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
    await page.goto(BRS_LOGIN_URL, wait_until="networkidle")
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
    print("  Continuing...")


async def go_to_tee_sheet(page):
    print("\nStep 2: Navigating to tee sheet...")
    await page.goto(TEE_SHEET_URL, wait_until="networkidle")
    await page.wait_for_timeout(3000)
    await page.screenshot(path="debug_04_tee_sheet.png", full_page=True)
    print(f"  URL: {page.url}")


async def select_date(page, target_dt: datetime):
    """Click the date button to open calendar popup, then click the target day."""
    print(f"\nStep 3: Selecting {target_dt.strftime('%A %d %B %Y')}...")
    target_day   = target_dt.day        # e.g. 29
    target_month = MONTH_NAMES[target_dt.month - 1]  # e.g. "April"
    target_year  = str(target_dt.year)  # e.g. "2026"

    # Step 3a: Click the date button at the top to open the calendar
    print("  Clicking date button to open calendar...")
    try:
        # The date button shows e.g. "SAT 25TH APR" with a calendar icon
        await page.click(
            'button:has-text("APR"), button:has-text("MAY"), button:has-text("JUN"), '
            'button:has-text("JUL"), button:has-text("AUG"), button:has-text("SEP"), '
            'a:has-text("APR"), a:has-text("MAY"), '
            '.date-picker-toggle, [class*="date-btn"], [class*="datepicker-toggle"]',
            timeout=5000
        )
        print("  Opened calendar via button")
    except:
        # Try clicking the calendar icon area at top of page
        try:
            await page.click('.fa-calendar, .glyphicon-calendar, [class*="calendar-icon"]', timeout=3000)
            print("  Opened calendar via icon")
        except:
            # JS: find and click whatever contains the current date text
            clicked = await page.evaluate("""
                () => {
                    const btns = document.querySelectorAll('button, a, div, span');
                    for (const btn of btns) {
                        const text = btn.textContent.trim().toUpperCase();
                        const months = ['JAN','FEB','MAR','APR','MAY','JUN',
                                       'JUL','AUG','SEP','OCT','NOV','DEC'];
                        if (months.some(m => text.includes(m)) && text.length < 30) {
                            btn.click();
                            return text;
                        }
                    }
                    return null;
                }
            """)
            print(f"  Calendar opened via JS: {clicked}")

    await page.wait_for_timeout(1500)
    await page.screenshot(path="debug_05_calendar_open.png", full_page=True)

    # Step 3b: Navigate to correct month if needed
    for attempt in range(4):
        content = await page.content()
        if target_month in content and target_year in content:
            print(f"  Correct month showing: {target_month} {target_year}")
            break
        # Click next month arrow (>) in the calendar popup
        try:
            await page.click('th.next, .datepicker th.next, [aria-label="next month"]', timeout=2000)
            await page.wait_for_timeout(800)
            print(f"  Navigated to next month (attempt {attempt})")
        except:
            print(f"  Could not navigate month on attempt {attempt}")
            break

    await page.screenshot(path="debug_05b_correct_month.png", full_page=True)

    # Step 3c: Click the target day number in the calendar
    print(f"  Clicking day {target_day} in calendar...")
    try:
        # Calendar days are in <td> elements — click the one matching our day
        # Use text matching but exclude header cells (SUN, MON etc)
        await page.locator(
            f'td:has-text("{target_day}")'
        ).filter(has_not_text="SUN").filter(has_not_text="MON").first.click(timeout=3000)
        print(f"  Clicked day {target_day}!")
    except:
        # JS fallback: find td with exactly our day number
        clicked = await page.evaluate(f"""
            () => {{
                const cells = document.querySelectorAll('td');
                for (const cell of cells) {{
                    if (cell.textContent.trim() === '{target_day}' &&
                        !cell.classList.contains('disabled') &&
                        !cell.classList.contains('old') &&
                        cell.tagName === 'TD') {{
                        cell.click();
                        return true;
                    }}
                }}
                return false;
            }}
        """)
        if clicked:
            print(f"  Clicked day {target_day} via JS!")
        else:
            print(f"  WARNING: Could not click day {target_day}")

    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(3000)
    await page.screenshot(path="debug_06_date_selected.png", full_page=True)
    print(f"  Date selected — tee times should now show for {target_dt.strftime('%A %d %B')}")


async def select_time_and_book(page, target_time: str) -> bool:
    print(f"\nStep 4: Finding {target_time} BOOK NOW...")
    await page.wait_for_timeout(2000)
    await page.screenshot(path="debug_07_tee_times.png", full_page=True)

    content = await page.content()
    if target_time in content:
        print(f"  '{target_time}' found on page!")
    else:
        print(f"  '{target_time}' NOT found — visible times:")
        times = await page.locator('text=/\\d{2}:\\d{2}/').all_text_contents()
        print(f"  {times[:20]}")

    # Method 1: row with time + BOOK NOW link
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
            print("  Method 2: JS clicked!")
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(2000)
            await page.screenshot(path="debug_08_js_click.png", full_page=True)
            return True
    except Exception as e:
        print(f"  Method 2 failed: {e}")

    # Method 3: first BOOK NOW on page
    try:
        buttons = page.locator('a:has-text("BOOK NOW"), button:has-text("BOOK NOW")')
        count = await buttons.count()
        print(f"  Found {count} BOOK NOW buttons total")
        if count > 0:
            await buttons.first.click()
            print("  Clicked first BOOK NOW as fallback")
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(2000)
            await page.screenshot(path="debug_08_fallback.png", full_page=True)
            return True
    except Exception as e:
        print(f"  Method 3 failed: {e}")

    await page.screenshot(path="debug_08_failed.png", full_page=True)
    return False


async def fill_players_and_confirm(page, players: list) -> bool:
    print(f"\nStep 5: Adding players...")
    await page.screenshot(path="debug_09_booking_form.png", full_page=True)
    await page.wait_for_timeout(1000)

    for i, player in enumerate(players):
        slot_num   = i + 1
        first_name = player.split(",")[1].strip() if "," in player else player
        print(f"  Player {slot_num}: {player}")
        for sel in [f'select:nth-of-type({slot_num})', f'#player{slot_num}']:
            try:
                await page.select_option(sel, label=player, timeout=2000)
                break
            except: continue
        for sel in [
            f'input[placeholder*="buddy"]:nth-of-type({slot_num})',
            f'input[placeholder*="player"]:nth-of-type({slot_num})',
            f'input[placeholder*="member"]:nth-of-type({slot_num})',
        ]:
            try:
                await page.fill(sel, first_name, timeout=2000)
                await page.wait_for_timeout(800)
                await page.click('li[role="option"]:first-child, .autocomplete-suggestion:first-child', timeout=2000)
                break
            except: continue

    await page.wait_for_timeout(1000)
    await page.screenshot(path="debug_10_players_added.png", full_page=True)

    for sel in ['button:has-text("Create Booking")', 'a:has-text("Create Booking")',
                'button:has-text("Confirm")', 'button[type="submit"]']:
        try:
            await page.click(sel, timeout=3000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(3000)
            await page.screenshot(path="confirmation_test.png", full_page=True)
            print("  BOOKING COMPLETE!")
            return True
        except: continue

    await page.screenshot(path="debug_11_no_confirm.png", full_page=True)
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
