"""
TeeBot - Beaverstown Golf Club BRS - PRODUCTION
Each GitHub Actions job runs this script with a different BOOKING_INDEX,
so every booking runs as a completely independent parallel job.
Speed-optimised: logs in early, sits on tee sheet, hammers refresh,
clicks BOOK NOW the instant it appears.
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

PRE_RELEASE_WAIT_SECS = 90
REFRESH_INTERVAL_SECS = 0.5
TIMEOUT_AFTER_RELEASE = 300  # 5 minutes after release before giving up


def load_players_json():
    """Load the full players.json file and return it."""
    if not GITHUB_TOKEN or not REPO:
        return None
    url = f"https://api.github.com/repos/{REPO}/contents/players.json"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3.raw",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  ⚠️  Could not load players.json: {e}")
        return None


def get_release_time(data: dict) -> datetime:
    """
    Get the release time in UTC (GitHub runners use UTC).
    Ireland is UTC+0 in winter, UTC+1 in summer (last Sun Mar - last Sun Oct).
    We detect Irish summer time and adjust accordingly.
    - Custom release_time from players.json is in Irish local time — convert to UTC
    - Default is 20:30 Irish time = 19:30 UTC summer / 20:30 UTC winter
    """
    import time as _time

    now = datetime.utcnow()

    # Detect if Ireland is currently on summer time (UTC+1)
    # Summer time runs last Sunday in March to last Sunday in October
    month = now.month
    irish_offset_hours = 1 if 4 <= month <= 9 else 0
    # More precise check for March and October boundary months
    if month == 3:
        # After last Sunday in March
        last_sun = max(d for d in range(25, 32)
                      if datetime(now.year, 3, d).weekday() == 6)
        irish_offset_hours = 1 if now.day > last_sun else 0
    elif month == 10:
        # Before last Sunday in October
        last_sun = max(d for d in range(25, 32)
                      if datetime(now.year, 10, d).weekday() == 6)
        irish_offset_hours = 0 if now.day >= last_sun else 1

    print(f"  Irish time offset: UTC+{irish_offset_hours} ({'summer' if irish_offset_hours else 'winter'})")

    custom = data.get("release_time")
    if custom:
        try:
            # Parse as Irish local time, convert to UTC for comparison
            release_irish = datetime.fromisoformat(custom)
            release_utc = release_irish - timedelta(hours=irish_offset_hours)
            print(f"  Custom release: {release_irish.strftime('%H:%M:%S')} Irish = {release_utc.strftime('%H:%M:%S')} UTC")
            return release_utc
        except Exception as e:
            print(f"  ⚠️  Could not parse release_time: {e} — using 20:30 Irish")

    # Default: 20:30 Irish time
    release_irish_hour = 20
    release_utc_hour = release_irish_hour - irish_offset_hours
    release_utc = now.replace(hour=release_utc_hour, minute=30, second=0, microsecond=0)
    print(f"  Default release: 20:30 Irish = {release_utc_hour}:30 UTC")
    return release_utc


def get_target_date(booking: dict) -> datetime:
    if "date" in booking:
        dt = datetime.strptime(booking["date"], "%Y-%m-%d")
        print(f"  Target date: {dt.strftime('%A %d %B %Y')}")
        return dt
    days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    dow = days.index(booking["day"])
    today = datetime.now()
    days_ahead = (dow - today.weekday()) % 7 or 7
    dt = today + timedelta(days=days_ahead)
    print(f"  Target date (calculated): {dt.strftime('%A %d %B %Y')}")
    return dt


def build_fallback_times(preferred: str, window: int, interval: int) -> list:
    h, m = map(int, preferred.split(":"))
    base = h * 60 + m
    times = [preferred]
    for delta in range(interval, window + 1, interval):
        for mins in [base + delta, base - delta]:
            if 0 <= mins < 1440:
                t = f"{mins//60:02d}:{mins%60:02d}"
                if t not in times:
                    times.append(t)
    times[1:] = sorted(times[1:], key=lambda t: abs(
        int(t.split(':')[0])*60 + int(t.split(':')[1]) - base
    ))
    return times


async def login(page):
    print("  Logging in...")
    await page.goto(BRS_LOGIN_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(1500)
    await page.fill('input[name="username"], input[type="text"]', BRS_EMAIL)
    await page.fill('input[type="password"]', BRS_PASSWORD)
    await page.click('button:has-text("LOGIN")')
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(2000)
    if "beaverstown" not in page.url:
        raise Exception("Login failed — check BRS_EMAIL / BRS_PASSWORD secrets")
    print("  ✅ Logged in")


async def navigate_to_date(page, target_dt: datetime):
    url = f"{TEE_SHEET_BASE}/{target_dt.strftime('%Y/%m/%d')}"
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(1500)
    print(f"  ✅ On tee sheet: {target_dt.strftime('%A %d %B')}")


async def try_click_book_now(page, fallback_times: list):
    content = await page.content()
    for try_time in fallback_times:
        if try_time not in content:
            continue
        try:
            # Try multiple selectors — BRS uses "Book Now" (not "BOOK NOW")
            # and the button has class "add-booking"
            btn = None
            for sel in [
                f'tr:has-text("{try_time}") a.add-booking',
                f'tr:has-text("{try_time}") a:has-text("Book Now")',
                f'tr:has-text("{try_time}") a:has-text("BOOK NOW")',
            ]:
                candidate = page.locator(sel).first
                if await candidate.count() > 0 and await candidate.is_visible():
                    btn = candidate
                    print(f"  Found button via: {sel}")
                    break

            if btn is None:
                continue

            print(f"  🚀 Clicking BOOK NOW for {try_time}!")
            await btn.click(timeout=3000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(1500)

            if await page.locator('text="already booked"').count() > 0:
                print(f"  ✗ {try_time} already fully booked — trying next")
                for sel in ['button:has-text("BACK")', 'button:has-text("Back")', 'button:has-text("OK")']:
                    try:
                        await page.locator(sel).first.click(timeout=1500)
                        await page.wait_for_timeout(500)
                        break
                    except: pass
                continue

            if await page.locator('text="Booking Details"').count() > 0:
                print(f"  ✅ Booking form open for {try_time}")
                return try_time
        except Exception as e:
            print(f"  ⚠️  Error trying {try_time}: {e}")
            continue
    return None


async def wait_and_grab_slot(page, preferred_time: str, fallback_times: list,
                              release_dt: datetime) -> bool:
    deadline = release_dt + timedelta(seconds=TIMEOUT_AFTER_RELEASE)
    attempt = 0

    print(f"  Checking if slots already available...")
    booked_time = await try_click_book_now(page, fallback_times)
    if booked_time:
        return True

    secs_to_release = (release_dt - datetime.utcnow()).total_seconds()
    if secs_to_release > 0:
        print(f"  ⏳ {secs_to_release:.0f}s to release at {release_dt.strftime('%H:%M:%S')} — hammering refresh...")
    else:
        print(f"  Slots not yet visible — refreshing...")

    while datetime.now() < deadline:
        attempt += 1
        try:
            await page.reload(wait_until="domcontentloaded", timeout=8000)
        except Exception:
            await asyncio.sleep(0.2)
            continue

        booked_time = await try_click_book_now(page, fallback_times)
        if booked_time:
            elapsed = (datetime.utcnow() - release_dt).total_seconds()
            print(f"  ✅ Grabbed {booked_time} at {elapsed:+.1f}s from release (attempt {attempt})")
            return True

        secs_to_release = (release_dt - datetime.utcnow()).total_seconds()
        if secs_to_release > 5:
            if attempt % 10 == 0:
                print(f"  ⏳ {secs_to_release:.0f}s to release (attempt {attempt})")
            await asyncio.sleep(REFRESH_INTERVAL_SECS)
        elif secs_to_release > 0:
            await asyncio.sleep(0.1)
        else:
            secs_past = abs(secs_to_release)
            if attempt % 5 == 0:
                print(f"  ⏳ +{secs_past:.0f}s past release, attempt {attempt}")
            await asyncio.sleep(0.3)

    print(f"  ❌ Timed out — no slot found after {TIMEOUT_AFTER_RELEASE}s past release")
    return False


async def set_player_via_select2(page, slot_num: int, player_name: str) -> bool:
    member_id = PLAYER_IDS.get(player_name)
    if not member_id:
        print(f"    ⚠️  No member ID for '{player_name}'")
        return False

    select_id = f"member_booking_form_player_{slot_num}"
    result = await page.evaluate(f"""
        () => {{
            const sel = document.getElementById('{select_id}');
            if (!sel) return 'ERROR: not found';
            const opt = sel.querySelector('option[value="{member_id}"]');
            if (!opt) return 'ERROR: option {member_id} missing';
            try {{
                const $ = window.jQuery || window.$;
                if ($ && $(sel).data('select2')) {{
                    $(sel).val('{member_id}').trigger('change');
                    return 'OK';
                }}
            }} catch(e) {{}}
            sel.value = '{member_id}';
            sel.dispatchEvent(new Event('change', {{bubbles:true}}));
            return 'OK';
        }}
    """)

    if 'ERROR' in result:
        print(f"    ⚠️  Slot {slot_num}: {result}")
        return False

    await page.wait_for_timeout(300)
    print(f"    ✅ Player {slot_num}: {player_name}")
    return True


async def fill_and_confirm(page, players: list, label: str) -> bool:
    print(f"  Filling {len([p for p in players[1:4] if p])} additional players...")

    # Wait for booking form AND Select2 dropdowns to fully load
    print("  Waiting for booking form to fully load...")
    try:
        await page.wait_for_selector('#member_booking_form_player_2', timeout=10000)
        print("  ✅ Booking form loaded")
    except Exception:
        print("  ⚠️  Timed out waiting for form — trying anyway")
    await page.wait_for_timeout(1500)

    # Verify Select2 is initialised
    select2_ready = await page.evaluate("""
        () => {
            const sel = document.getElementById('member_booking_form_player_2');
            if (!sel) return false;
            const $ = window.jQuery || window.$;
            if ($ && $(sel).data('select2')) return true;
            return sel.options.length > 1;
        }
    """)
    print(f"  Select2 ready: {select2_ready}")
    if not select2_ready:
        await page.wait_for_timeout(2000)

    # Explicitly set Player 1 as well in case BRS didn't pre-fill
    await set_player_via_select2(page, 1, players[0])
    await page.wait_for_timeout(500)

    for i, player in enumerate(players[1:4], start=2):
        if player:
            await set_player_via_select2(page, i, player)
            await page.wait_for_timeout(800)

    await page.screenshot(path=f"debug_players_{label}.png", full_page=True)

    # Dismiss any error modals before confirming
    print("  Checking for error modals...")
    for modal_sel in [
        'button:has-text("OK")',
        'button:has-text("Close")',
        'button:has-text("Dismiss")',
        '[class*="modal"] button',
        '[class*="alert"] button',
        '[class*="error"] button',
    ]:
        try:
            modal_btn = page.locator(modal_sel).first
            if await modal_btn.count() > 0 and await modal_btn.is_visible():
                try:
                    modal_text = await page.locator('[class*="modal"], [class*="alert"], [class*="error"]').first.text_content()
                    print(f"  ⚠️  Modal dismissed: {(modal_text or '').strip()[:100]}")
                except: pass
                await modal_btn.click(timeout=2000)
                await page.wait_for_timeout(500)
        except: pass

    await page.screenshot(path=f"debug_before_confirm_{label}.png", full_page=True)

    for sel in [
        'button:has-text("Create Booking")',
        'button:has-text("CREATE BOOKING")',
        'button:has-text("Update Booking")',
        '#member_booking_form_confirm_booking',
        'button[type="submit"]',
    ]:
        try:
            await page.click(sel, timeout=3000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(2000)

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

            await page.screenshot(path=f"confirmation_{label}.png", full_page=True)
            content = await page.content()
            if "confirmed" in content.lower() or "booking" in content.lower():
                print(f"  ✅ BOOKING CONFIRMED!")
                return True
        except: continue

    await page.screenshot(path=f"error_noconfirm_{label}.png", full_page=True)
    print(f"  ❌ Could not confirm booking")
    return False


async def main():
    # Load full players.json to get both booking and release time
    data = load_players_json()
    if not data:
        print("❌ Could not load players.json")
        return

    bookings = data.get("bookings", [])
    print(f"  players.json has {len(bookings)} booking(s) — this job is index {BOOKING_INDEX}")

    if BOOKING_INDEX >= len(bookings):
        print(f"  ℹ️  No booking at index {BOOKING_INDEX} — nothing to do")
        return

    booking  = bookings[BOOKING_INDEX]
    day      = booking.get("day", "?")
    time     = booking["time"]
    players  = booking["players"][:4]
    window   = int(booking.get("fallback_window", 30))
    interval = int(booking.get("fallback_interval", 10))

    target_dt      = get_target_date(booking)
    fallback_times = build_fallback_times(time, window, interval)
    release_dt     = get_release_time(data)  # reads from players.json or defaults to 20:30
    label          = f"{day.lower()[:3]}_{time.replace(':','')}"

    print("=" * 54)
    print(f"  TeeBot Job #{BOOKING_INDEX + 1} — Beaverstown Golf Club")
    print(f"  Date    : {target_dt.strftime('%A %d %B %Y')}")
    print(f"  Want    : {time}  |  Fallbacks: {fallback_times[1:4]}")
    print(f"  Players : {', '.join(p for p in players if p)}")
    print(f"  Release : {release_dt.strftime('%H:%M:%S')}")
    print("=" * 54)

    # Safety check — exit cleanly if triggered more than 15 mins before release
    # Prevents GitHub wasting runner time when triggered too early
    now_check = datetime.utcnow()
    secs_until = (release_dt - now_check).total_seconds()
    if secs_until > 900:
        mins = secs_until / 60
        print(f"  ⚠️  Release is {mins:.0f} mins away — triggered too early!")
        print(f"  ℹ️  Please trigger the workflow at 20:27 Irish time (3 mins before release)")
        print(f"  Exiting cleanly.")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page(viewport={"width": 1280, "height": 900})

        try:
            await login(page)

            # Navigate to tee sheet early and sit there
            # Sleep in 30-second chunks with status output so GitHub
            # doesn't kill the job for appearing idle
            wait_until = release_dt - timedelta(seconds=PRE_RELEASE_WAIT_SECS)
            now = datetime.now()
            if now < wait_until:
                secs = (wait_until - now).total_seconds()
                print(f"  Waiting {secs:.0f}s until {wait_until.strftime('%H:%M:%S')} then navigating to tee sheet...")
                while datetime.utcnow() < wait_until:
                    remaining = (wait_until - datetime.utcnow()).total_seconds()
                    if remaining <= 0:
                        break
                    sleep_chunk = min(30, remaining)
                    await asyncio.sleep(sleep_chunk)
                    still_remaining = (wait_until - datetime.now()).total_seconds()
                    if still_remaining > 0:
                        print(f"  ⏱  {still_remaining:.0f}s until tee sheet navigation...")
                print(f"  Navigating to tee sheet now...")

            await navigate_to_date(page, target_dt)
            await page.screenshot(path=f"debug_teesheet_{label}.png", full_page=True)

            booked = await wait_and_grab_slot(page, time, fallback_times, release_dt)

            if booked:
                await fill_and_confirm(page, players, label)
            else:
                await page.screenshot(path=f"error_noslot_{label}.png", full_page=True)

        except Exception as e:
            print(f"  ❌ Fatal error: {e}")
            try: await page.screenshot(path=f"error_crash_{label}.png", full_page=True)
            except: pass

        await browser.close()

    print("\n" + "=" * 54)
    print(f"  Job #{BOOKING_INDEX + 1} complete")
    print("=" * 54)


if __name__ == "__main__":
    asyncio.run(main())
