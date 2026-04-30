"""
TeeBot - Beaverstown Golf Club BRS - PRODUCTION
Speed-optimised: logs in early, navigates to tee sheet before release,
then hammers refresh until BOOK NOW appears and clicks immediately.
Handles multiple bookings in parallel.
"""

import asyncio, os, json, urllib.request
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

BRS_LOGIN_URL  = "https://members.brsgolf.com/beaverstown"
TEE_SHEET_BASE = "https://members.brsgolf.com/beaverstown/tee-sheet/1"

BRS_EMAIL    = os.environ["BRS_EMAIL"]
BRS_PASSWORD = os.environ["BRS_PASSWORD"]
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO         = os.environ.get("GITHUB_REPOSITORY", "")

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

# How long before release to start waiting on the tee sheet (seconds)
PRE_RELEASE_WAIT_SECS = 90
# How often to refresh while waiting for BOOK NOW to appear (ms)
REFRESH_INTERVAL_MS   = 500
# Give up waiting after this many seconds past release time
TIMEOUT_AFTER_RELEASE = 60


def load_bookings():
    if not GITHUB_TOKEN or not REPO:
        print("  ⚠️  No GITHUB_TOKEN/REPO")
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


def get_target_date(booking: dict) -> datetime:
    """Get the target date from ISO date string or day name."""
    if "date" in booking:
        return datetime.strptime(booking["date"], "%Y-%m-%d")
    # Fall back to next occurrence of day name
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
    # Sort by closeness to preferred
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
        raise Exception("Login failed")
    print("  ✅ Logged in")


async def navigate_to_date(page, target_dt: datetime):
    url = f"{TEE_SHEET_BASE}/{target_dt.strftime('%Y/%m/%d')}"
    print(f"  Navigating to {target_dt.strftime('%A %d %B')} tee sheet...")
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)
    print(f"  ✅ On tee sheet for {target_dt.strftime('%A %d %B')}")


async def wait_and_grab_slot(page, preferred_time: str, fallback_times: list,
                              release_dt: datetime, booking_label: str) -> bool:
    """
    Rapidly refresh the tee sheet until BOOK NOW appears for the target time,
    then click it immediately. Falls back to adjacent times if preferred is taken.
    """
    print(f"\n  [{booking_label}] Waiting for release at {release_dt.strftime('%H:%M:%S')}...")

    deadline = release_dt + timedelta(seconds=TIMEOUT_AFTER_RELEASE)
    attempt = 0

    while datetime.now() < deadline:
        now = datetime.now()
        attempt += 1

        # Reload page to get fresh content
        try:
            await page.reload(wait_until="domcontentloaded", timeout=8000)
        except Exception:
            await asyncio.sleep(0.3)
            continue

        content = await page.content()

        # Check each time in fallback order
        for try_time in fallback_times:
            if try_time not in content:
                continue

            # Look for BOOK NOW in that row
            try:
                btn = page.locator(
                    f'tr:has-text("{try_time}") a:has-text("BOOK NOW")'
                ).first
                if await btn.count() > 0 and await btn.is_visible():
                    print(f"  [{booking_label}] 🚀 BOOK NOW visible for {try_time} — clicking NOW! (attempt {attempt})")
                    await btn.click(timeout=3000)
                    await page.wait_for_load_state("domcontentloaded")
                    await page.wait_for_timeout(1500)

                    # Verify booking form opened
                    if await page.locator('text="Booking Details"').count() > 0:
                        print(f"  [{booking_label}] ✅ Booking form open for {try_time}")
                        return True

                    # Check if it was already booked
                    if await page.locator('text="already booked"').count() > 0:
                        print(f"  [{booking_label}] ✗ {try_time} already taken — trying next")
                        for sel in ['button:has-text("BACK")', 'button:has-text("Back")', 'button:has-text("OK")']:
                            try:
                                await page.locator(sel).first.click(timeout=1500)
                                break
                            except: pass
                        continue
            except Exception as e:
                continue

        secs_to_release = (release_dt - now).total_seconds()
        if secs_to_release > 0:
            print(f"  [{booking_label}] Waiting... {secs_to_release:.0f}s to release (attempt {attempt})")
            await asyncio.sleep(REFRESH_INTERVAL_MS / 1000)
        else:
            secs_past = -secs_to_release
            print(f"  [{booking_label}] +{secs_past:.0f}s past release, attempt {attempt} — still refreshing")
            await asyncio.sleep(0.3)

    print(f"  [{booking_label}] ❌ Timed out — no slot secured")
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
    print(f"  [{label}] Filling players...")
    await page.wait_for_timeout(400)
    for i, player in enumerate(players[1:4], start=2):
        if player:
            await set_player_via_select2(page, i, player)
            await page.wait_for_timeout(200)

    await page.screenshot(path=f"debug_{label}_players.png", full_page=True)

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
            await page.screenshot(path=f"confirmation_{label}.png", full_page=True)
            print(f"  [{label}] ✅ BOOKING CONFIRMED!")
            return True
        except: continue

    await page.screenshot(path=f"error_{label}_noconfirm.png", full_page=True)
    print(f"  [{label}] ❌ Could not confirm booking")
    return False


async def run_booking(browser, booking: dict, release_dt: datetime):
    """Run a single booking in its own browser page."""
    day     = booking.get("day", "?")
    time    = booking["time"]
    players = booking["players"][:4]
    window  = booking.get("fallback_window", 30)
    interval= booking.get("fallback_interval", 10)
    label   = f"{day}-{time.replace(':','')}"

    fallback_times = build_fallback_times(time, window, interval)
    target_dt = get_target_date(booking)

    print(f"\n{'='*54}")
    print(f"  [{label}] {day} {target_dt.strftime('%d %B')} — want {time}")
    print(f"  [{label}] Fallbacks: {fallback_times}")
    print(f"  [{label}] Players: {', '.join(p for p in players if p)}")
    print(f"{'='*54}")

    page = await browser.new_page(viewport={"width": 1280, "height": 900})

    try:
        await login(page)
        await navigate_to_date(page, target_dt)

        # Wait until PRE_RELEASE_WAIT_SECS before release, then start hammering
        wait_until = release_dt - timedelta(seconds=PRE_RELEASE_WAIT_SECS)
        now = datetime.now()
        if now < wait_until:
            secs = (wait_until - now).total_seconds()
            print(f"  [{label}] Waiting {secs:.0f}s then moving to tee sheet...")
            await asyncio.sleep(secs)
            await navigate_to_date(page, target_dt)

        booked = await wait_and_grab_slot(page, time, fallback_times, release_dt, label)

        if booked:
            await fill_and_confirm(page, players, label)
        else:
            await page.screenshot(path=f"error_{label}_no_slot.png", full_page=True)

    except Exception as e:
        print(f"  [{label}] ❌ Error: {e}")
        try: await page.screenshot(path=f"error_{label}_crash.png", full_page=True)
        except: pass
    finally:
        await page.close()


async def main():
    bookings = load_bookings()
    if not bookings:
        print("No bookings in players.json — nothing to do")
        return

    # Determine release time
    # GitHub Actions runs this at the scheduled time, so release = NOW
    release_dt = datetime.now()

    print("=" * 54)
    print("  TeeBot PRODUCTION — Beaverstown Golf Club")
    print(f"  Run time   : {release_dt.strftime('%A %d %B %Y %H:%M:%S')}")
    print(f"  Bookings   : {len(bookings)}")
    print(f"  Strategy   : Log in early, wait on tee sheet, fire immediately")
    print("=" * 54)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # Run all bookings in parallel
        tasks = [run_booking(browser, b, release_dt) for b in bookings]
        await asyncio.gather(*tasks)

        await browser.close()

    print("\n" + "=" * 54)
    print("  TeeBot complete — check artifacts for screenshots")
    print("=" * 54)


if __name__ == "__main__":
    asyncio.run(main())
