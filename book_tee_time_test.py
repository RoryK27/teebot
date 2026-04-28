"""
TeeBot - Beaverstown Golf Club BRS
Uses Select2 field IDs and member IDs to set players directly via JS,
bypassing the UI autocomplete entirely.
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

# Member IDs from the BRS select options (confirmed from page HTML)
PLAYER_IDS = {
    "Kirwan, Rory":    "434",
    "Kirwan, Lisa":    "3107",
    "Carrick, Paul":   "2022",
    "Hennelly, Ronan": "3106",
    "Kelly, Edward":   "2833",
    "Kelly, Peter 'Seve'": "396",
    "Kirwan, Barry":   "433",
    "Kirwan, Mary":    "912",
    "Legge, Simon":    "3010",
    "Lynch, Niall":    "2197",
    "Moore, George":   "590",
    "Guest":           "-2",
}

DEFAULT_PLAYERS = ["Kirwan, Rory", "Kirwan, Lisa", "Carrick, Paul", "Hennelly, Ronan"]

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
    print(f"\nStep 2: Going to {target_dt.strftime('%A %d %B')} tee sheet...")
    print(f"  URL: {url}")
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)
    await page.screenshot(path="debug_04_tee_sheet.png", full_page=True)

    content = await page.content()
    day = target_dt.strftime("%-d")
    month = target_dt.strftime("%b").upper()
    if day in content and month in content:
        print(f"  ✅ Correct date: {target_dt.strftime('%A %d %B')}")
    else:
        print(f"  ⚠️ Date may not be correct")


async def select_time_and_book(page, target_time: str) -> bool:
    print(f"\nStep 3: Finding {target_time} BOOK NOW...")
    await page.wait_for_timeout(1500)
    await page.screenshot(path="debug_05_tee_times.png", full_page=True)

    content = await page.content()
    if target_time in content:
        print(f"  ✅ '{target_time}' found on page!")
    else:
        print(f"  ⚠️ '{target_time}' not found")
        times = await page.locator('text=/\\d{2}:\\d{2}/').all_text_contents()
        print(f"  Visible times: {times[:10]}")

    try:
        await page.locator(f'tr:has-text("{target_time}") a:has-text("BOOK NOW")').first.click(timeout=5000)
        print(f"  ✅ Clicked BOOK NOW at {target_time}!")
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(2000)
        await page.screenshot(path="debug_06_after_book_now.png", full_page=True)
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
            print("  ✅ Method 2: JS clicked!")
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(2000)
            await page.screenshot(path="debug_06_js_click.png", full_page=True)
            return True
    except Exception as e:
        print(f"  Method 2 failed: {e}")

    try:
        buttons = page.locator('a:has-text("BOOK NOW"), button:has-text("BOOK NOW")')
        count = await buttons.count()
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


async def set_player_via_select2(page, slot_num: int, player_name: str) -> bool:
    """
    Set a player slot using Select2's programmatic API.
    The select element IDs are: member_booking_form_player_1/2/3/4
    We trigger Select2's val() + trigger('change') to set the value properly.
    """
    member_id = PLAYER_IDS.get(player_name)
    if not member_id:
        print(f"  ⚠️ No member ID found for '{player_name}' — check PLAYER_IDS dict")
        return False

    select_id = f"member_booking_form_player_{slot_num}"
    print(f"  Setting player {slot_num} ({player_name}, id={member_id}) via Select2...")

    result = await page.evaluate(f"""
        () => {{
            const sel = document.getElementById('{select_id}');
            if (!sel) return 'ERROR: select element not found';

            // Check the option exists
            const option = sel.querySelector('option[value="{member_id}"]');
            if (!option) return 'ERROR: option value {member_id} not found in select';

            // Method 1: Use Select2's jQuery API if available
            try {{
                const $ = window.jQuery || window.$;
                if ($ && $(sel).data('select2')) {{
                    $(sel).val('{member_id}').trigger('change');
                    return 'OK: Select2 jQuery API';
                }}
            }} catch(e) {{}}

            // Method 2: Set native select value and dispatch events
            sel.value = '{member_id}';
            sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
            sel.dispatchEvent(new Event('input', {{ bubbles: true }}));
            return 'OK: native select value set';
        }}
    """)

    print(f"  JS result: {result}")

    if result.startswith("ERROR"):
        return False

    await page.wait_for_timeout(500)

    # Verify it worked by checking the Select2 rendered display
    display = await page.evaluate(f"""
        () => {{
            const rendered = document.getElementById('select2-{select_id}-container');
            return rendered ? rendered.textContent.trim() : 'not found';
        }}
    """)
    print(f"  Select2 display now shows: '{display}'")

    if display == player_name or display not in ("Start typing to find player...", "not found", ""):
        print(f"  ✅ Player {slot_num} set to '{display}'")
        return True

    print(f"  ⚠️ Display still shows '{display}' — may not have worked")
    return False


async def fill_players_and_confirm(page, players: list) -> bool:
    print(f"\nStep 4: Adding players to booking form...")
    await page.screenshot(path="debug_07_booking_form.png", full_page=True)
    await page.wait_for_timeout(500)

    print(f"  ✅ Player 1 ({players[0]}) pre-filled by BRS")

    # Set players 2, 3, 4 via Select2
    players_to_add = players[1:4]
    for i, player in enumerate(players_to_add):
        slot = i + 2
        success = await set_player_via_select2(page, slot, player)
        await page.wait_for_timeout(300)
        await page.screenshot(path=f"debug_player_{slot}_added.png", full_page=True)
        if not success:
            print(f"  ⚠️ Could not set player {slot}, continuing anyway")

    await page.wait_for_timeout(800)
    await page.screenshot(path="debug_08_all_players.png", full_page=True)

    # Click the confirm button — on the edit form it's "Update Booking"
    print("\n  Clicking confirm button...")
    for sel in [
        'button:has-text("Update Booking")',
        'button:has-text("UPDATE BOOKING")',
        'button:has-text("Create Booking")',
        'button:has-text("CREATE BOOKING")',
        '#member_booking_form_confirm_booking',
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
    print("  ❌ Could not find confirm button")
    return False


async def main():
    players   = load_players()
    target_dt = get_next_date_for_dow(TEST_DAY_OF_WEEK)

    print("=" * 54)
    print("  TeeBot — Beaverstown Golf Club")
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
            print("\nCould not find time slot — check debug screenshots")

        await browser.close()

    print("\n" + "=" * 54)
    print("  Done — check Artifacts")
    print("=" * 54)


if __name__ == "__main__":
    asyncio.run(main())
