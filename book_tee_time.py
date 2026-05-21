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

# How long before release to be sitting on the tee sheet (seconds)
PRE_RELEASE_WAIT_SECS = 90
# Refresh interval while waiting for BOOK NOW (seconds)
REFRESH_INTERVAL_SECS = 0.5
# Give up this many seconds after release time
# Set high enough to cover BRS releasing slightly late
TIMEOUT_AFTER_RELEASE = 300  # 5 minutes after release before giving up


def load_booking():
    """Load the specific booking for this job's BOOKING_INDEX."""
    if not GITHUB_TOKEN or not REPO:
        print(f"  ⚠️  No GITHUB_TOKEN/REPO set")
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
            print(f"  players.json has {len(bookings)} booking(s) — this job is index {BOOKING_INDEX}")

            if BOOKING_INDEX >= len(bookings):
                print(f"  ℹ️  No booking at index {BOOKING_INDEX} — nothing to do")
                return None

            return bookings[BOOKING_INDEX]
    except Exception as e:
        print(f"  ⚠️  Could not load players.json: {e}")
        return None


def get_target_date(booking: dict) -> datetime:
    if "date" in booking:
        return datetime.strptime(booking["date"], "%Y-%m-%d")
    days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    dow = days.index(booking["day"])
    today = datetime.now()
    days_ahead = (dow - today.weekday()) % 7 or 7
    return today + timedelta(days=days_ahead)


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


async def try_click_book_now(page, fallback_times: list) -> str | None:
    """
    Check current page content and click BOOK NOW if visible for any fallback time.
    Returns the booked time string if successful, None otherwise.
    """
    content = await page.content()
    for try_time in fallback_times:
        if try_time not in content:
            continue
        try:
            btn = page.locator(
                f'tr:has-text("{try_time}") a:has-text("BOOK NOW")'
            ).first
            if await btn.count() > 0 and await btn.is_visible():
                print(f"  🚀 BOOK NOW visible for {try_time} — clicking!")
                await btn.click(timeout=3000)
                await page.wait_for_load_state("domcontentloaded")
                await page.wait_for_timeout(1500)

                # Check for already booked modal
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
    """
    First check if BOOK NOW is already available (slots already released).
    If not, hammer refresh until release time, then grab immediately.
    """
    deadline = release_dt + timedelta(seconds=TIMEOUT_AFTER_RELEASE)
    attempt = 0

    # Check immediately on first load — slots may already be available
    print(f"  Checking if slots already available...")
    booked_time = await try_click_book_now(page, fallback_times)
    if booked_time:
        return True

    secs_to_release = (release_dt - datetime.now()).total_seconds()
    if secs_to_release > 0:
        print(f"  ⏳ {secs_to_release:.0f}s to release — hammering refresh...")
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
            elapsed = (datetime.now() - release_dt).total_seconds()
            print(f"  ✅ Grabbed {booked_time} at {elapsed:+.1f}s from release (attempt {attempt})")
            return True

        secs_to_release = (release_dt - datetime.now()).total_seconds()
        if secs_to_release > 5:
            print(f"  ⏳ {secs_to_release:.0f}s to release (attempt {attempt})")
            await asyncio.sleep(REFRESH_INTERVAL_SECS)
        elif secs_to_release > 0:
            await asyncio.sleep(0.1)  # Very close — go as fast as possible
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

    # Verify Select2 is initialised for player slots
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
        await page.wait_for_timeout(2000)  # extra wait if not ready yet

    for i, player in enumerate(players[1:4], start=2):
        if player:
            await set_player_via_select2(page, i, player)
            await page.wait_for_timeout(800)  # wait for BRS to register each player

    await page.screenshot(path=f"debug_players_{label}.png", full_page=True)

    # Dismiss any error/warning modals before confirming
    # (e.g. player has insufficient competition purse funds)
    print("  Checking for any error modals before confirming...")
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
                modal_text = await page.locator('[class*="modal"], [class*="alert"], [class*="error"]').first.text_content()
                print(f"  ⚠️  Modal dismissed: {(modal_text or '').strip()[:100]}")
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

            # Check for post-submit error modals and dismiss them too
            for modal_sel in ['button:has-text("OK")', 'button:has-text("Close")', '[class*="modal"] button']:
                try:
                    modal_btn = page.locator(modal_sel).first
                    if await modal_btn.count() > 0 and await modal_btn.is_visible():
                        modal_text = await page.locator('[class*="modal"], [class*="alert"]').first.text_content()
                        print(f"  ⚠️  Post-submit modal: {(modal_text or '').strip()[:100]}")
                        await modal_btn.click(timeout=2000)
                        await page.wait_for_timeout(500)
                except: pass

            await page.screenshot(path=f"confirmation_{label}.png", full_page=True)

            # Verify we got a confirmation
            content = await page.content()
            if "confirmed" in content.lower() or "booking" in content.lower():
                print(f"  ✅ BOOKING CONFIRMED!")
                return True
        except: continue

    await page.screenshot(path=f"error_noconfirm_{label}.png", full_page=True)
    print(f"  ❌ Could not confirm booking")
    return False


async def main():
    booking = load_booking()
    if not booking:
        print(f"  Nothing to do for index {BOOKING_INDEX} — exiting cleanly")
        return

    day      = booking.get("day", "?")
    time     = booking["time"]
    players  = booking["players"][:4]
    window   = int(booking.get("fallback_window", 30))
    interval = int(booking.get("fallback_interval", 10))

    target_dt      = get_target_date(booking)
    fallback_times = build_fallback_times(time, window, interval)
    # Determine release time:
    # 1. If players.json has a custom release_time (set via TeeBot site override) — use that
    # 2. Otherwise default to 20:30 today (standard Monday BRS release)
    now = datetime.now()
    custom_release = booking.get("release_time") or data.get("release_time") if "data" in dir() else None

    # Re-read from players.json for release_time at top level
    try:
        rel_req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/contents/players.json",
            headers={"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3.raw"}
        )
        with urllib.request.urlopen(rel_req) as r:
            pj = json.loads(r.read())
            custom_release = pj.get("release_time")
    except Exception:
        custom_release = None

    if custom_release:
        try:
            release_dt = datetime.fromisoformat(custom_release)
            print(f"  Custom release time: {release_dt.strftime('%A %d %B %H:%M:%S')}")
        except Exception:
            release_dt = now.replace(hour=20, minute=30, second=0, microsecond=0)
            print(f"  Could not parse custom release time — using 20:30")
    else:
        release_dt = now.replace(hour=20, minute=30, second=0, microsecond=0)
        print(f"  Default release time: 20:30:00")

    secs_until = (release_dt - now).total_seconds()
    if secs_until > 0:
        print(f"  Waiting {secs_until:.0f}s until release...")
    else:
        print(f"  Release time already passed by {abs(secs_until):.0f}s — booking immediately")
    label          = f"{day.lower()[:3]}_{time.replace(':','')}"

    print("=" * 54)
    print(f"  TeeBot Job #{BOOKING_INDEX + 1} — Beaverstown Golf Club")
    print(f"  Date    : {target_dt.strftime('%A %d %B %Y')}")
    print(f"  Want    : {time}  |  Fallbacks: {fallback_times[1:4]}")
    print(f"  Players : {', '.join(p for p in players if p)}")
    print(f"  Release : {release_dt.strftime('%H:%M:%S')} (now)")
    print("=" * 54)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page(viewport={"width": 1280, "height": 900})

        try:
            await login(page)

            # Navigate to tee sheet early and sit there
            wait_until = release_dt - timedelta(seconds=PRE_RELEASE_WAIT_SECS)
            now = datetime.now()
            if now < wait_until:
                secs = (wait_until - now).total_seconds()
                print(f"  Waiting {secs:.0f}s then navigating to tee sheet early...")
                await asyncio.sleep(secs)

            await navigate_to_date(page, target_dt)
            await page.screenshot(path=f"debug_teesheet_{label}.png", full_page=True)

            # Hammer refresh until BOOK NOW appears
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
