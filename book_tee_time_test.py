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
    BRS booking form flow (confirmed from mobile screenshots):
      - Each player slot has a greyed display field (placeholder 'Start typing to find player...')
      - Clicking it opens a dropdown showing all buddies immediately
      - A search input below it gains keyboard focus
      - For named players: type surname into the focused input → list filters → click match
      - For "Guest": Guest is already visible under General — no typing needed, just click it
    """
    print(f"  Adding player {slot_num}: {player_name}")
    is_guest = player_name.strip().lower() == "guest"
    surname = player_name.split(",")[0].strip() if not is_guest else "Guest"

    # Step 1: Click the display field to open the dropdown
    display_inputs = page.locator('input[placeholder="Start typing to find player..."]')
    display_count = await display_inputs.count()
    print(f"  Found {display_count} display fields")

    display_index = slot_num - 2   # slot 2 → index 0, slot 3 → index 1, slot 4 → index 2
    if display_index >= display_count:
        print(f"  ⚠️ Not enough display fields ({display_count}) for slot {slot_num}")
        return False

    await display_inputs.nth(display_index).click()
    await page.wait_for_timeout(800)
    await page.screenshot(path=f"debug_player_{slot_num}_clicked.png", full_page=True)

    # Step 2: Type surname into the focused search input to filter the list
    # Skip typing for Guest — already visible in the open dropdown
    if not is_guest:
        await page.keyboard.type(surname, delay=80)
        await page.wait_for_timeout(2000)
        await page.screenshot(path=f"debug_player_{slot_num}_typed.png", full_page=True)

    # Log all visible li text for diagnosis in Actions logs
    visible_items = await page.evaluate("""
        () => [...document.querySelectorAll('li')]
                .filter(el => el.offsetParent !== null)
                .map(el => el.textContent.trim())
                .filter(t => t.length > 0)
                .join(' | ')
    """)
    print(f"  Visible li items: {visible_items[:500]}")

    # Step 3: Click the matching name — skip category headers
    SKIP = {"You and your buddies", "Other club members", "General",
            "Start typing to find player...", ""}

    def is_match(text: str) -> bool:
        if text in SKIP:
            return False
        if is_guest:
            return text == "Guest"
        return text == player_name or text.startswith(surname)

    target_sel = 'li:has-text("Guest")' if is_guest else f'li:has-text("{surname}")'
    try:
        items = page.locator(target_sel)
        count = await items.count()
        for i in range(count):
            item = items.nth(i)
            if not await item.is_visible():
                continue
            text = (await item.text_content() or "").strip()
            if is_match(text):
                await item.click(timeout=3000)
                await page.wait_for_timeout(800)
                print(f"  ✅ Selected '{text}' for player {slot_num}")
                return True
    except Exception as e:
        print(f"  Selector attempt failed: {e}")

    # JS fallback — walk visible li elements, skip headers
    matched = await page.evaluate(f"""
        () => {{
            const skip = ["You and your buddies", "Other club members", "General",
                          "Start typing to find player...", ""];
            const isGuest = {"true" if is_guest else "false"};
            const surname = "{surname}";
            const fullName = "{player_name}";
            for (const el of document.querySelectorAll("li")) {{
                const text = el.textContent.trim();
                if (!el.offsetParent || skip.includes(text)) continue;
                const match = isGuest
                    ? text === "Guest"
                    : (text === fullName || text.startsWith(surname));
                if (match) {{
                    el.click();
                    return text;
                }}
            }}
            return null;
        }}
    """)
    if matched:
        print(f"  ✅ JS fallback selected: '{matched}'")
        await page.wait_for_timeout(800)
        return True

    print(f"  ❌ Could not select '{player_name}' — check debug_player_{slot_num}_clicked.png")
    return False


async def fill_players_and_confirm(page, players: list) -> bool:
    print(f"\nStep 4: Adding players to booking form...")
    await page.screenshot(path="debug_07_booking_form.png", full_page=True)
    await page.wait_for_timeout(500)  # timer is running — don't dawdle

    # BRS always pre-fills Player 1 with the logged-in member
    print(f"  ✅ Player 1 ({players[0]}) pre-filled by BRS")
    players_to_add = players[1:4]
    start_slot = 2

    for i, player in enumerate(players_to_add):
        slot = start_slot + i
        success = await add_player(page, slot, player)
        await page.wait_for_timeout(400)
        await page.screenshot(path=f"debug_player_{slot}_added.png", full_page=True)
        if not success:
            print(f"  ⚠️ Continuing without player {slot}")

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
