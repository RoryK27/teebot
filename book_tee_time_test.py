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
TEST_TIME        = "18:40"


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
    """
    Type the player's surname into the BRS autocomplete input,
    wait for the AJAX dropdown, then click the matching result.
    Includes diagnostic HTML dump so the dropdown structure is
    visible in GitHub Actions logs on failure.
    """
    print(f"  Adding player {slot_num}: {player_name}")

    inputs = page.locator('input[placeholder*="typing"], input[placeholder*="find player"]')
    count = await inputs.count()
    print(f"  Found {count} player input fields")

    input_index = slot_num - 2
    if input_index >= count:
        print(f"  ⚠️ Not enough inputs ({count}) for slot {slot_num}")
        return False

    target_input = inputs.nth(input_index)
    surname = player_name.split(",")[0].strip()  # e.g. "Kirwan" from "Kirwan, Rory"

    # Click, clear, then type surname to trigger the autocomplete AJAX call
    await target_input.click()
    await page.wait_for_timeout(400)
    await target_input.fill("")
    await target_input.type(surname, delay=100)  # human-like typing speed
    await page.wait_for_timeout(2000)            # wait for AJAX response
    await page.screenshot(path=f"debug_player_{slot_num}_typed.png", full_page=True)

    # Dump visible dropdown HTML to logs for diagnosis
    dropdown_html = await page.evaluate("""
        () => {
            const sel = 'ul, [class*="dropdown"], [class*="autocomplete"], [class*="suggest"], [class*="result"]';
            return [...document.querySelectorAll(sel)]
                .filter(el => el.offsetParent !== null)
                .map(el => el.outerHTML)
                .join('\\n---\\n');
        }
    """)
    print(f"  Visible dropdowns after typing:\n{dropdown_html[:2000]}")

    # Try every plausible selector for the dropdown items
    for sel in [
        f'ul li:has-text("{player_name}")',
        f'ul li:has-text("{surname}")',
        f'[class*="dropdown"] li:has-text("{surname}")',
        f'[class*="autocomplete"] li:has-text("{surname}")',
        f'[class*="result"] li:has-text("{surname}")',
        f'[class*="suggest"] li:has-text("{surname}")',
        f'[role="option"]:has-text("{surname}")',
        f'[role="listbox"] [role="option"]:has-text("{surname}")',
        f'li:has-text("{player_name}")',
        f'li:has-text("{surname}")',
    ]:
        try:
            item = page.locator(sel).first
            if await item.count() > 0 and await item.is_visible():
                await item.click(timeout=3000)
                await page.wait_for_timeout(600)
                # Verify the input now has a value — blank means the click didn't register
                val = await target_input.input_value()
                if val and len(val) > 2:
                    print(f"  ✅ Selected '{player_name}' via [{sel}] → input='{val}'")
                    return True
                print(f"  ⚠️ Clicked via [{sel}] but input still empty — trying next selector")
        except Exception:
            continue

    # Nuclear JS fallback — find any visible leaf node containing the surname
    clicked = await page.evaluate(f"""
        () => {{
            const targets = [...document.querySelectorAll('li, div, span, a, td')];
            for (const el of targets) {{
                if (
                    el.offsetParent !== null &&
                    el.children.length === 0 &&
                    el.textContent.trim().includes('{surname}')
                ) {{
                    el.click();
                    return el.textContent.trim();
                }}
            }}
            return null;
        }}
    """)
    if clicked:
        print(f"  ✅ JS fallback clicked: '{clicked}'")
        await page.wait_for_timeout(600)
        return True

    print(f"  ❌ Could not select '{player_name}' — check debug_player_{slot_num}_typed.png")
    return False


async def fill_players_and_confirm(page, players: list) -> bool:
    print(f"\nStep 4: Adding players to booking form...")
    await page.screenshot(path="debug_07_booking_form.png", full_page=True)
    await page.wait_for_timeout(500)  # don't dawdle — 3-minute timer is running

    # BRS always pre-fills Player 1 with the logged-in member
    print("  ✅ Player 1 (Kirwan, Rory) pre-filled by BRS")
    players_to_add = players[1:4]  # Lisa, Paul, Ronan
    start_slot = 2

    for i, player in enumerate(players_to_add):
        slot = start_slot + i
        success = await add_player(page, slot, player)
        await page.wait_for_timeout(400)
        await page.screenshot(path=f"debug_player_{slot}_added.png", full_page=True)
        if not success:
            print(f"  ⚠️ Continuing without player {slot} — will book with fewer players")

    await page.wait_for_timeout(800)
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
