"""
TeeBot - Beaverstown Golf Club BRS
Production booking script. Reads bookings from players.json,
books each active slot the moment the tee sheet is released.
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

# Member IDs from BRS select options
PLAYER_IDS = {
    "Kirwan, Rory":      "434",
    "Kirwan, Lisa":      "3107",
    "Carrick, Paul":     "2022",
    "Hennelly, Ronan":   "3106",
    "Kelly, Edward":     "2833",
    "Kelly, Peter 'Seve'": "396",
    "Kirwan, Barry":     "433",
    "Kirwan, Mary":      "912",
    "Legge, Simon":      "3010",
    "Lynch, Niall":      "2197",
    "Moore, George":     "590",
    "Guest":             "-2",
}

# Default fallback: try times within 30 mins, closest first
FALLBACK_WINDOW_MINS = 30
FALLBACK_INTERVAL    = 10  # try every 10 mins


def load_bookings():
    """
    Load bookings from players.json in the repo.
    Format:
    {
      "bookings": [
        { "day": "Tuesday",   "time": "18:10", "players": ["Kirwan, Rory", "Kirwan, Lisa", "Carrick, Paul", "Hennelly, Ronan"] },
        { "day": "Wednesday", "time": "18:40", "players": ["Kirwan, Rory", "Guest", "Guest"] }
      ]
    }
    """
    if not GITHUB_TOKEN or not REPO:
        print("  ⚠️  No GITHUB_TOKEN/REPO — cannot load players.json")
        return []

    url = f"https://api.github.com/repos/{REPO}/contents/players.json"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3.raw",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            bookings = data.get("bookings", [])
            print(f"  Loaded {len(bookings)} booking(s) from players.json")
            return bookings
    except Exception as e:
        print(f"  ⚠️  Could not load players.json: {e}")
        return []


def get_next_date_for_dow(day_name: str) -> datetime:
    days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    dow = days.index(day_name)
    today = datetime.now()
    days_ahead = (dow - today.weekday()) % 7 or 7
    return today + timedelta(days=days_ahead)


def build_fallback_times(preferred_time: str) -> list:
    """Return list of times to try in order, closest to preferred first."""
    h, m = map(int, preferred_time.split(":"))
    base = h * 60 + m
    candidates = []
    for delta in range(FALLBACK_INTERVAL, FALLBACK_WINDOW_MINS + 1, FALLBACK_INTERVAL):
        candidates.append((abs(delta), base + delta))
        candidates.append((abs(delta), base - delta))
    candidates.sort(key=lambda x: x[0])
    times = []
    for _, mins in candidates:
        if 0 <= mins < 24 * 60:
            t = f"{mins//60:02d}:{mins%60:02d}"
            if t not in times:
                times.append(t)
    return [preferred_time] + times


async def login(page):
    print("Step 1: Logging in...")
    await page.goto(BRS_LOGIN_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)
    await page.fill('input[name="username"], input[type="text"]', BRS_EMAIL)
    await page.fill('input[type="password"]', BRS_PASSWORD)
    await page.click('button:has-text("LOGIN")')
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(3000)
    print(f"  URL after login: {page.url}")
    if "beaverstown" not in page.url:
        raise Exception("Login failed — check BRS_EMAIL and BRS_PASSWORD secrets")


async def go_to_date(page, target_dt: datetime):
    date_str = target_dt.strftime("%Y/%m/%d")
    url = f"{TEE_SHEET_BASE}/{date_str}"
    print(f"\nStep 2: Loading {target_dt.strftime('%A %d %B')} tee sheet...")
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)


async def try_book_time(page, target_time: str) -> bool:
    """Attempt to click BOOK NOW for target_time. Returns True if booking form opens."""
    content = await page.content()
    if target_time not in content:
        print(f"  ⏭  {target_time} not on page")
        return False

    # Method 1: row selector
    try:
        btn = page.locator(f'tr:has-text("{target_time}") a:has-text("BOOK NOW")').first
        if await btn.count() > 0:
            await btn.click(timeout=5000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(2000)

            # Check for "already booked" modal
            if await page.locator('text="already booked"').count() > 0:
                print(f"  ✗  {target_time} already fully booked")
                for sel in ['button:has-text("BACK")', 'button:has-text("Back")', 'button:has-text("OK")']:
                    try:
                        await page.locator(sel).first.click(timeout=2000)
                        await page.wait_for_timeout(800)
                        break
                    except: pass
                return False

            # Verify booking form opened
            if await page.locator('text="Booking Details"').count() > 0:
                print(f"  ✅  Booking form opened for {target_time}")
                return True
    except Exception as e:
        print(f"  Method 1 failed for {target_time}: {e}")

    # Method 2: JS row scan
    try:
        clicked = await page.evaluate(f"""
            () => {{
                for (const row of document.querySelectorAll('tr')) {{
                    if (row.textContent.includes('{target_time}')) {{
                        const btn = row.querySelector('a, button');
                        if (btn) {{ btn.click(); return true; }}
                    }}
                }}
                return false;
            }}
        """)
        if clicked:
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(2000)
            if await page.locator('text="Booking Details"').count() > 0:
                print(f"  ✅  Booking form opened for {target_time} (JS method)")
                return True
    except Exception as e:
        print(f"  Method 2 failed for {target_time}: {e}")

    return False


async def set_player_via_select2(page, slot_num: int, player_name: str) -> bool:
    """Set a player slot using Select2's programmatic API."""
    member_id = PLAYER_IDS.get(player_name)
    if not member_id:
        print(f"  ⚠️  No member ID for '{player_name}'")
        return False

    select_id = f"member_booking_form_player_{slot_num}"

    result = await page.evaluate(f"""
        () => {{
            const sel = document.getElementById('{select_id}');
            if (!sel) return 'ERROR: element not found';
            const option = sel.querySelector('option[value="{member_id}"]');
            if (!option) return 'ERROR: option {member_id} not in select';
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

    if result.startswith("ERROR"):
        print(f"  ⚠️  Slot {slot_num} ({player_name}): {result}")
        return False

    await page.wait_for_timeout(400)
    display = await page.evaluate(f"""
        () => {{
            const el = document.getElementById('select2-member_booking_form_player_{slot_num}-container');
            return el ? el.textContent.trim() : '';
        }}
    """)
    print(f"  ✅  Player {slot_num}: {display or player_name}")
    return True


async def confirm_booking(page) -> bool:
    """Click the confirm/submit button."""
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
            await page.wait_for_timeout(3000)
            # Check for confirmation
            content = await page.content()
            if "confirmed" in content.lower() or "booking" in content.lower():
                print("  ✅  BOOKING CONFIRMED!")
                await page.screenshot(path=f"confirmation_{datetime.now().strftime('%H%M%S')}.png", full_page=True)
                return True
        except:
            continue
    print("  ❌  Could not find confirm button")
    await page.screenshot(path="error_no_confirm.png", full_page=True)
    return False


async def book_slot(page, booking: dict) -> bool:
    """Book a single slot — tries preferred time then fallbacks."""
    day     = booking["day"]
    time    = booking["time"]
    players = booking["players"][:4]

    target_dt = get_next_date_for_dow(day)
    fallback_times = build_fallback_times(time)

    print(f"\n{'='*54}")
    print(f"  Booking: {day} {target_dt.strftime('%d %B')} — preferred {time}")
    print(f"  Players: {', '.join(p for p in players if p)}")
    print(f"  Will try: {fallback_times}")
    print(f"{'='*54}")

    await go_to_date(page, target_dt)

    booked_time = None
    for attempt_time in fallback_times:
        print(f"\n  Trying {attempt_time}...")
        opened = await try_book_time(page, attempt_time)
        if opened:
            booked_time = attempt_time
            break
        # Reload the tee sheet before next attempt
        await go_to_date(page, target_dt)

    if not booked_time:
        print(f"\n  ❌  No available slot found within fallback window")
        await page.screenshot(path=f"error_{day.lower()}_no_slot.png", full_page=True)
        return False

    # Fill players
    print(f"\n  Setting players for {booked_time}...")
    print(f"  ✅  Player 1 ({players[0]}) pre-filled by BRS")
    for i, player in enumerate(players[1:], start=2):
        if player:
            await set_player_via_select2(page, i, player)
            await page.wait_for_timeout(300)

    await page.screenshot(path=f"debug_{day.lower()}_players.png", full_page=True)

    # Confirm
    await page.wait_for_timeout(500)
    success = await confirm_booking(page)
    return success


async def main():
    bookings = load_bookings()
    if not bookings:
        print("No bookings configured in players.json — nothing to do")
        return

    print("=" * 54)
    print("  TeeBot — Beaverstown Golf Club")
    print(f"  Run time: {datetime.now().strftime('%A %d %B %Y %H:%M:%S')}")
    print(f"  Bookings to process: {len(bookings)}")
    print("=" * 54)

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page(viewport={"width": 1280, "height": 900})

        await login(page)

        for booking in bookings:
            try:
                success = await book_slot(page, booking)
                results.append((booking["day"], booking["time"], success))
            except Exception as e:
                print(f"\n  ❌  Error booking {booking.get('day','?')}: {e}")
                await page.screenshot(path=f"error_{booking.get('day','unknown').lower()}.png", full_page=True)
                results.append((booking["day"], booking["time"], False))

        await browser.close()

    print("\n" + "=" * 54)
    print("  RESULTS")
    for day, time, ok in results:
        print(f"  {'✅' if ok else '❌'}  {day} {time}")
    print("=" * 54)


if __name__ == "__main__":
    asyncio.run(main())
