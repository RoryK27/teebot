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

# Use "Guest" as a player name for an anonymous guest slot, e.g.:
#   ["Kirwan, Rory", "Guest", "Guest", "Guest"]

TEST_DAY_OF_WEEK = 2       # 0=Mon 1=Tue 2=Wed 3=Thu 4=Fri 5=Sat 6=Sun
TEST_TIME        = "19:10"


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


async def get_player_inputs(page):
    """
    Find the player search inputs on the booking form.
    Logs every input so we can diagnose headless rendering.
    Returns list of (locator) for slots 2/3/4.
    """
    all_inputs = await page.locator("input").all()
    print(f"  Total inputs on page: {len(all_inputs)}")

    player_slots = []
    for i, inp in enumerate(all_inputs):
        try:
            tp  = await inp.get_attribute("type") or "text"
            nm  = await inp.get_attribute("name") or ""
            idd = await inp.get_attribute("id") or ""
            ph  = await inp.get_attribute("placeholder") or ""
            val = await inp.input_value()
            vis = await inp.is_visible()
            dis = await inp.get_attribute("disabled")
            ro  = await inp.get_attribute("readonly")
            print(f"    [{i}] type={tp} name={nm!r} id={idd!r} ph={ph!r} val={val!r} vis={vis} dis={dis} ro={ro}")

            if tp in ("hidden", "password", "submit", "button", "checkbox", "radio"):
                continue
            if not vis:
                continue
            if dis is not None or ro is not None:
                continue
            skip_names = ("first_name", "last_name", "email", "phone", "username")
            if any(x in nm or x in idd for x in skip_names):
                continue
            player_slots.append(inp)
        except Exception as ex:
            print(f"    [{i}] error: {ex}")

    print(f"  Player slot inputs found: {len(player_slots)}")
    return player_slots


async def add_player(page, slot_num: int, player_name: str, player_inputs: list) -> bool:
    print(f"  Adding player {slot_num}: {player_name}")
    is_guest = player_name.strip().lower() == "guest"
    surname  = player_name.split(",")[0].strip() if not is_guest else "Guest"

    input_index = slot_num - 2
    if input_index >= len(player_inputs):
        print(f"  ⚠️ Only {len(player_inputs)} inputs, need index {input_index}")
        return False

    target_input = player_inputs[input_index]

    # Click to open the dropdown
    await target_input.click()
    await page.wait_for_timeout(800)
    await page.screenshot(path=f"debug_player_{slot_num}_clicked.png", full_page=True)

    # Type surname to filter (skip for Guest — already visible)
    if not is_guest:
        await page.keyboard.type(surname, delay=80)
        await page.wait_for_timeout(2000)
        await page.screenshot(path=f"debug_player_{slot_num}_typed.png", full_page=True)

    # Log all visible li text
    all_li = await page.locator("li").all()
    li_texts = []
    for li in all_li:
        try:
            if await li.is_visible():
                li_texts.append((await li.text_content() or "").strip())
        except Exception:
            pass
    print(f"  Visible li items: {li_texts[:20]}")

    # Headers to skip
    SKIP = {"You and your buddies", "Other club members", "General",
            "Start typing to find player...", ""}

    def is_match(text):
        if text in SKIP:
            return False
        if is_guest:
            return text == "Guest"
        return text == player_name or text.startswith(surname)

    # Click the matching li
    for li in all_li:
        try:
            if not await li.is_visible():
                continue
            text = (await li.text_content() or "").strip()
            if is_match(text):
                await li.click(timeout=3000)
                await page.wait_for_timeout(800)
                print(f"  ✅ Selected '{text}' for player {slot_num}")
                return True
        except Exception:
            continue

    print(f"  ❌ Could not select '{player_name}' — check debug_player_{slot_num}_typed.png")
    return False


async def fill_players_and_confirm(page, players: list) -> bool:
    print(f"\nStep 4: Adding players to booking form...")
    await page.screenshot(path="debug_07_booking_form.png", full_page=True)
    await page.wait_for_timeout(500)

    print(f"  ✅ Player 1 ({players[0]}) pre-filled by BRS")

    player_inputs = await get_player_inputs(page)

    players_to_add = players[1:4]
    start_slot = 2

    for i, player in enumerate(players_to_add):
        slot = start_slot + i
        success = await add_player(page, slot, player, player_inputs)
        await page.wait_for_timeout(400)
        await page.screenshot(path=f"debug_player_{slot}_added.png", full_page=True)
        if not success:
            print(f"  ⚠️ Continuing without player {slot}")

    await page.wait_for_timeout(800)
    await page.screenshot(path="debug_08_all_players.png", full_page=True)

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
