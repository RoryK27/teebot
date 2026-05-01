"""
TeeBot TEST SCRIPT - Beaverstown Golf Club BRS
Reads day/time/players from players.json exactly like the production script.
Only books the FIRST active booking in players.json.
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
BOOKING_INDEX  = int(os.environ.get("BOOKING_INDEX", "0"))

# Member IDs from BRS select options
PLAYER_IDS = {
    "Kirwan, Rory":        "434",
    "Kirwan, Lisa":        "3107",
    "Carrick, Paul":       "2022",
    "Hennelly, Ronan":     "3106",
    "Kelly, Edward":       "2833",
    "Kelly, Peter 'Seve'": "396",
    "Kirwan, Barry":       "433",
    "Kirwan, Mary":        "912",
    "Legge, Simon":        "3010",
    "Lynch, Niall":        "2197",
    "Moore, George":       "590",
    "Guest":               "-2",
}

# Fallback: if preferred time is taken, try these windows
FALLBACK_WINDOW_MINS = 30
FALLBACK_INTERVAL    = 10


def load_booking():
    """Load the first booking from players.json in the repo."""
    if not GITHUB_TOKEN or not REPO:
        print("  ⚠️  No GITHUB_TOKEN/REPO set")
        return None

    url = f"https://api.github.com/repos/{REPO}/contents/players.json"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3.raw",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            bookings = data.get("bookings", [])
            if not bookings:
                print("  ⚠️  No bookings found in players.json")
                return None
            print(f"  Loaded {len(bookings)} booking(s) — this job is index {BOOKING_INDEX}")
            if BOOKING_INDEX >= len(bookings):
                print(f"  ℹ️  No booking at index {BOOKING_INDEX} — nothing to do")
                return None
            return bookings[BOOKING_INDEX]
    except Exception as e:
        print(f"  ⚠️  Could not load players.json: {e}")
        return None


def get_next_date_for_dow(day_name: str) -> datetime:
    days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    dow = days.index(day_name)
    today = datetime.now()
    days_ahead = (dow - today.weekday()) % 7 or 7
    return today + timedelta(days=days_ahead)


def build_fallback_times(preferred_time: str) -> list:
    h, m = map(int, preferred_time.split(":"))
    base = h * 60 + m
    candidates = []
    for delta in range(FALLBACK_INTERVAL, FALLBACK_WINDOW_MINS + 1, FALLBACK_INTERVAL):
        candidates.append((delta, base + delta))
        candidates.append((delta, base - delta))
    candidates.sort(key=lambda x: x[0])
    times = [preferred_time]
    for _, mins in candidates:
        if 0 <= mins < 24 * 60:
            t = f"{mins//60:02d}:{mins%60:02d}"
            if t not in times:
                times.append(t)
    return times


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
        print(f"  ⚠️ Date may not be correct — check debug_04_tee_sheet.png")


async def try_book_time(page, target_time: str) -> bool:
    """Try to click BOOK NOW for target_time. Returns True if booking form opens."""
    content = await page.content()
    if target_time not in content:
        print(f"  ⏭  {target_time} not on page")
        return False

    # Method 1: visible BOOK NOW in the correct row
    try:
        btn = page.locator(f'tr:has-text("{target_time}") a:has-text("BOOK NOW")').first
        if await btn.count() > 0 and await btn.is_visible():
            await btn.click(timeout=5000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(2000)
            await page.screenshot(path="debug_06_after_book_now.png", full_page=True)

            if await page.locator('text="already booked"').count() > 0:
                print(f"  ✗  {target_time} already fully booked")
                for sel in ['button:has-text("BACK")', 'button:has-text("Back")', 'button:has-text("OK")']:
                    try:
                        await page.locator(sel).first.click(timeout=2000)
                        await page.wait_for_timeout(800)
                        break
                    except: pass
                return False

            if await page.locator('text="Booking Details"').count() > 0:
                print(f"  ✅ Booking form opened for {target_time}")
                return True
    except Exception as e:
        print(f"  Method 1 failed: {e}")

    # Method 2: JS fallback — only click BOOK NOW links, not any button
    try:
        clicked = await page.evaluate(f"""
            () => {{
                for (const row of document.querySelectorAll('tr')) {{
                    if (row.textContent.includes('{target_time}')) {{
                        const btn = row.querySelector('a');
                        if (btn && btn.textContent.includes('BOOK NOW')) {{
                            btn.click(); return true;
                        }}
                    }}
                }}
                return false;
            }}
        """)
        if clicked:
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(2000)
            if await page.locator('text="Booking Details"').count() > 0:
                print(f"  ✅ Booking form opened for {target_time} (JS)")
                return True
    except Exception as e:
        print(f"  Method 2 failed: {e}")

    return False

async def set_player_via_select2(page, slot_num: int, player_name: str) -> bool:
    member_id = PLAYER_IDS.get(player_name)
    if not member_id:
        print(f"  ⚠️ No member ID for '{player_name}'")
        return False

    select_id = f"member_booking_form_player_{slot_num}"

    result = await page.evaluate(f"""
        () => {{
            const sel = document.getElementById('{select_id}');
            if (!sel) return 'ERROR: element not found';
            const option = sel.querySelector('option[value="{member_id}"]');
            if (!option) return 'ERROR: option {member_id} not found';
            try {{
                const $ = window.jQuery || window.$;
                if ($ && $(sel).data('select2')) {{
                    $(sel).val('{member_id}').trigger('change');
                    return 'OK:select2';
                }}
            }} catch(e) {{}}
            sel.value = '{member_id}';
            sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return 'OK:native';
        }}
    """)

    if result.startswith('ERROR'):
        print(f"  ⚠️ Slot {slot_num} ({player_name}): {result}")
        return False

    await page.wait_for_timeout(400)
    display = await page.evaluate(f"""
        () => {{
            const el = document.getElementById('select2-member_booking_form_player_{slot_num}-container');
            return el ? el.textContent.trim() : '';
        }}
    """)
    print(f"  ✅ Player {slot_num}: {display or player_name}")
    return True


async def fill_players_and_confirm(page, players: list) -> bool:
    print(f"\nStep 4: Adding players to booking form...")
    await page.screenshot(path="debug_07_booking_form.png", full_page=True)
    await page.wait_for_timeout(500)

    print(f"  ✅ Player 1 ({players[0]}) pre-filled by BRS")

    for i, player in enumerate(players[1:4], start=2):
        if player:
            await set_player_via_select2(page, i, player)
            await page.wait_for_timeout(300)
            await page.screenshot(path=f"debug_player_{i}_added.png", full_page=True)

    await page.wait_for_timeout(800)
    await page.screenshot(path="debug_08_all_players.png", full_page=True)

    # Dismiss any error modals before confirming
    # (e.g. player has insufficient competition purse funds)
    print("  Checking for error modals before confirming...")
    for modal_sel in [
        'button:has-text("OK")',
        'button:has-text("Close")',
        'button:has-text("Dismiss")',
        '[class*="modal"] button',
        '[class*="alert"] button',
    ]:
        try:
            modal_btn = page.locator(modal_sel).first
            if await modal_btn.count() > 0 and await modal_btn.is_visible():
                try:
                    modal_text = await page.locator('[class*="modal"], [class*="alert"]').first.text_content()
                    print(f"  ⚠️  Modal dismissed: {(modal_text or '').strip()[:100]}")
                except: pass
                await modal_btn.click(timeout=2000)
                await page.wait_for_timeout(500)
        except: pass

    print("  Clicking CREATE BOOKING...")
    for sel in [
        'button:has-text("Create Booking")',
        'button:has-text("CREATE BOOKING")',
        'button:has-text("Update Booking")',
        'button:has-text("UPDATE BOOKING")',
        '#member_booking_form_confirm_booking',
        'button[type="submit"]',
    ]:
        try:
            await page.click(sel, timeout=3000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(2000)

            # Dismiss any post-submit error modals too
            for modal_sel in ['button:has-text("OK")', 'button:has-text("Close")', '[class*="modal"] button']:
                try:
                    modal_btn = page.locator(modal_sel).first
                    if await modal_btn.count() > 0 and await modal_btn.is_visible():
                        try:
                            modal_text = await page.locator('[class*="modal"], [class*="alert"]').first.text_content()
                            print(f"  ⚠️  Post-submit modal: {(modal_text or '').strip()[:100]}")
                        except: pass
                        await modal_btn.click(timeout=2000)
                        await page.wait_for_timeout(500)
                except: pass

            await page.screenshot(path="confirmation_test.png", full_page=True)
            content = await page.content()
            if "confirmed" in content.lower() or "booking" in content.lower():
                print("  ✅ BOOKING COMPLETE!")
                return True
        except:
            continue

    await page.screenshot(path="debug_09_no_confirm.png", full_page=True)
    print("  ❌ Could not find confirm button")
    return False


async def main():
    booking = load_booking()
    if not booking:
        print("❌ No booking config found — check players.json")
        return

    day     = booking["day"]
    time    = booking["time"]
    players = booking["players"][:4]

    target_dt      = get_next_date_for_dow(day)
    fallback_times = build_fallback_times(time)

    print("=" * 54)
    print("  TeeBot TEST — Beaverstown Golf Club")
    print(f"  Date   : {target_dt.strftime('%A %d %B %Y')}")
    print(f"  Time   : {time} (+ fallbacks: {fallback_times[1:4]})")
    print(f"  Players: {', '.join(p for p in players if p)}")
    print("=" * 54)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page(viewport={"width": 1280, "height": 900})

        await login(page)
        await go_to_date(page, target_dt)

        booked_time = None
        for attempt_time in fallback_times:
            print(f"\n  Trying {attempt_time}...")
            opened = await try_book_time(page, attempt_time)
            if opened:
                booked_time = attempt_time
                break
            await go_to_date(page, target_dt)

        if booked_time:
            await fill_players_and_confirm(page, players)
        else:
            print("\n❌ No available slot found")
            await page.screenshot(path="debug_no_slot.png", full_page=True)

        await browser.close()

    print("\n" + "=" * 54)
    print("  Done — check Artifacts")
    print("=" * 54)


if __name__ == "__main__":
    asyncio.run(main())
