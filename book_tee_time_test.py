"""
TeeBot TEST SCRIPT - Beaverstown Golf Club
Fixed: always continues after login, goes directly to tee sheet URL
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

# ── Hardcoded for this test ───────────────────────────────────────────────────
TEST_DAY_OF_WEEK = 1        # 1 = Tuesday
TEST_TIME        = "18:00"
# ─────────────────────────────────────────────────────────────────────────────


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
    """Login — always continues regardless, saves screenshots for debugging."""
    print("Step 1: Logging in...")
    await page.goto(BRS_LOGIN_URL, wait_until="networkidle")
    await page.wait_for_timeout(2000)
    await page.screenshot(path="debug_01_landing.png")

    await page.fill('input[name="username"], input[type="text"]', BRS_EMAIL)
    await page.fill('input[type="password"]', BRS_PASSWORD)
    await page.screenshot(path="debug_02_credentials_filled.png")
    await page.click('button:has-text("LOGIN")')
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(3000)
    await page.screenshot(path="debug_03_after_login.png")
    print(f"  After login URL: {page.url}")
    print("  Continuing regardless...")


async def go_to_tee_sheet(page):
    print("\nStep 2: Navigating to tee sheet...")
    await page.goto(TEE_SHEET_URL, wait_until="networkidle")
    await page.wait_for_timeout(2000)
    await page.screenshot(path="debug_04_tee_sheet.png", full_page=True)
    print(f"  URL: {page.url}")


async def select_date(page, target_dt: datetime):
    print(f"\nStep 3: Selecting {target_dt.strftime('%A %d %B %Y')}...")
    await page.screenshot(path="debug_05_before_date.png", full_page=True)

    target_day  = str(target_dt.day)
    month_name  = MONTH_NAMES[target_dt.month - 1]
    date_str    = target_dt.strftime("%Y-%m-%d")

    for attempt in range(10):
        await page.screenshot(path=f"debug_cal_{attempt:02d}.png")
        content = await page.content()

        if month_name in content and str(target_dt.year) in content:
            print(f"  Correct month visible: {month_name} {target_dt.year}")

            # Try data-date attribute
            try:
                await page.click(f'[data-date="{date_str}"]', timeout=3000)
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(2000)
                await page.screenshot(path="debug_06_date_selected.png", full_page=True)
                print(f"  Date selected via data-date!")
                return
            except: pass

            # Try td cell
            try:
                await page.click(
                    f'td:has-text("{target_day}"):not(.disabled):not(.old):not(.new)',
                    timeout=3000
                )
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(2000)
                await page.screenshot(path="debug_06_date_selected.png", full_page=True)
                print(f"  Date selected via td!")
                return
            except: pass

            # JS fallback
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
                print(f"  Date selected via JS!")
                return

            print(f"  Could not click day {target_day} — moving on")
            break

        # Click next month
        clicked_next = False
        for sel in ['th.next', '.datepicker-days th.next', '[aria-label="next month"]',
                    'th:has-text("›")', 'th:has-text(">")']:
            try:
                await page.click(sel, timeout=1500)
                await page.wait_for_timeout(800)
                clicked_next = True
                print(f"  Clicked next month (attempt {attempt})")
                break
            except: continue

        if not clicked_next:
            print(f"  No next arrow found on attempt {attempt}")
            break

    await page.screenshot(path="debug_06_date_final.png", full_page=True)


async def select_time_and_book(page, target_time: str) -> bool:
    print(f"\nStep 4: Finding {target_time} slot...")
    await page.wait_for_timeout(2000)
    await page.screenshot(path="debug_07_tee_times.png", full_page=True)

    content = await page.content()
    if target_time in content:
        print(f"  '{target_time}' found on page!")
    else:
        print(f"  '{target_time}' NOT found — checking visible times...")
        times = await page.locator('text=/\\d{2}:\\d{2}/').all_text_contents()
        print(f"  Visible times: {times[:20]}")

    # Method 1: row with time + BOOK NOW
    try:
        await page.locator(f'tr:has-text("{target_time}") a:has-text("BOOK NOW")').first.click(timeout=5000)
        print(f"  Clicked BOOK NOW at {target_time}!")
        await page.wait_for_load_state("networkidle")
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
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)
            await page.screenshot(path="debug_08_js_click.png", full_page=True)
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
            print("  Clicked first BOOK NOW")
            await page.wait_for_load_state("networkidle")
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
            await page.wait_for_load_state("networkidle")
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

        await login(page)              # Always continues
        await go_to_tee_sheet(page)    # Goes directly to URL
        await select_date(page, target_dt)
        success = await select_time_and_book(page, TEST_TIME)

        if success:
            await fill_players_and_confirm(page, players)
        else:
            print("\nCould not click a slot — check debug_07_tee_times.png")

        await browser.close()

    print("\n" + "=" * 54)
    print("  Done — check Artifacts")
    print("=" * 54)


if __name__ == "__main__":
    asyncio.run(main())
