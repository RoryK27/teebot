"""
TeeBot - Beaverstown Golf Club BRS - PRODUCTION
Each GitHub Actions job runs this script with a different BOOKING_INDEX,
so every booking runs as a completely independent parallel job.
Speed-optimised: logs in early, sits on tee sheet, hammers refresh,
clicks BOOK NOW the instant it appears.

Nov 2026 fix: BRS draws the tee sheet with JavaScript AFTER the page loads,
so the bot now waits for the Book Now buttons to appear, finds the right
button by its link (which ends /YYYYMMDD/HHMM), and clicks it without
visibility checks.
"""

import asyncio, os, json, re, urllib.request
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

# Set when the bot opens the tee sheet
DATE_CODE        = ""     # e.g. "20261106"
RELEASE_DT       = None
TEE_SHEET_URL    = ""
_reported_buttons = False


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
    await page.wait_for_selector('input[type="password"]', timeout=15000)
    await page.wait_for_timeout(1000)

    user_box = page.locator('input[name="username"]')
    if await user_box.count() == 0:
        user_box = page.locator('input[type="text"], input[type="email"]')
    await user_box.first.fill(BRS_EMAIL)
    await page.locator('input[type="password"]').first.fill(BRS_PASSWORD)
    print(f"  Username entered: {len(BRS_EMAIL)} characters | Password entered: {len(BRS_PASSWORD)} characters")

    await page.locator('input[type="password"]').first.press("Enter")

    # Wait until we have actually left the login page
    try:
        await page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
    except Exception:
        pass
    await page.wait_for_timeout(1500)

    if "/login" in page.url:
        body = (await page.inner_text("body"))[:400].replace("\n", " | ")
        print(f"  🔎 Still on login page: {page.url}")
        print(f"  🔎 Page says: {body}")
        raise Exception("Login failed — see 'Page says' above")
    print(f"  ✅ Logged in (now on {page.url})")


async def navigate_to_date(page, target_dt: datetime):
    global DATE_CODE, TEE_SHEET_URL
    DATE_CODE = target_dt.strftime('%Y%m%d')
    TEE_SHEET_URL = f"{TEE_SHEET_BASE}/{target_dt.strftime('%Y/%m/%d')}"
    t0 = datetime.utcnow()
    await page.goto(TEE_SHEET_URL, wait_until="domcontentloaded")
    print(f"  ✅ On tee sheet: {target_dt.strftime('%A %d %B')}")

    # DIAGNOSTIC: what does the bot actually see, and how long do buttons take?
    try:
        await page.wait_for_selector('a.add-booking', state="attached", timeout=15000)
        secs = (datetime.utcnow() - t0).total_seconds()
        print(f"  🔎 Book Now buttons appeared {secs:.1f}s after opening the page")
    except Exception:
        print(f"  🔎 No Book Now buttons found within 15s")
    try:
        n = await page.locator('a.add-booking').count()
        title = await page.title()
        body = (await page.inner_text("body"))[:400].replace("\n", " | ")
        print(f"  🔎 URL: {page.url}")
        print(f"  🔎 Title: {title} | a.add-booking count: {n}")
        print(f"  🔎 Page text: {body}")
    except Exception as e:
        print(f"  🔎 Diagnostic error: {e}")


async def try_click_book_now(page, fallback_times: list):
    """
    BRS draws the tee sheet with JavaScript after the page loads, so wait
    briefly for Book Now buttons to exist. Each button's link ends with
    /YYYYMMDD/HHMM, which identifies the exact tee time.
    """
    global _reported_buttons

    try:
        await page.wait_for_selector('a.add-booking', state="attached", timeout=4000)
    except Exception:
        return None  # no Book Now buttons on the page (yet)

    if not _reported_buttons:
        n = await page.locator('a.add-booking').count()
        print(f"  👀 Tee sheet shows {n} Book Now buttons")
        _reported_buttons = True

    for try_time in fallback_times:
        hhmm = try_time.replace(":", "")
        link = page.locator(f'a.add-booking[href*="/{DATE_CODE}/{hhmm}"]').first
        if await link.count() == 0:
            continue

        print(f"  🚀 Book Now found for {try_time} — clicking")
        try:
            try:
                await link.click(force=True, timeout=3000)
            except Exception as e:
                href = await link.get_attribute("href")
                print(f"  Click failed ({e}) — opening link directly")
                if href.startswith("/"):
                    href = "https://members.brsgolf.com" + href
                await page.goto(href, wait_until="domcontentloaded")

            try:
                await page.wait_for_selector('form[name="member_booking_form"]',
                                             state="attached", timeout=8000)
                print(f"  ✅ Booking form open for {try_time}")
                return try_time
            except Exception:
                body = (await page.inner_text("body"))[:200].replace("\n", " ")
                print(f"  ✗ {try_time} did not open booking form. URL: {page.url} | Page says: {body}")
                await page.goto(TEE_SHEET_URL, wait_until="domcontentloaded")
                continue

        except Exception as e:
            print(f"  ⚠️  Error trying {try_time}: {e}")
            try:
                await page.goto(TEE_SHEET_URL, wait_until="domcontentloaded")
            except Exception:
                pass
            continue

    return None


RENDER_WAIT_JS = """
() => {
    if (document.querySelector('a.add-booking')) return true;
    const times = (document.body.innerText || '').match(/[0-9][0-9]:[0-9][0-9]/g);
    return times && times.length > 10;
}
"""
NUM_TABS = 3  # parallel tabs refreshing the tee sheet, staggered


async def tab_worker(page, tab_no, fallback_times, release_dt, deadline,
                     found, result, lock):
    """One tab: reload, wait for the sheet to finish drawing, look for Book Now."""
    await asyncio.sleep(tab_no * 0.5)  # stagger tabs so one is always fresh
    attempt = 0
    seen_buttons = False
    while datetime.utcnow() < deadline and not found.is_set():
        attempt += 1
        try:
            await page.reload(wait_until="domcontentloaded", timeout=8000)
        except Exception:
            await asyncio.sleep(0.2)
            continue
        try:
            await page.wait_for_function(RENDER_WAIT_JS, timeout=2500, polling=100)
        except Exception:
            pass
        if found.is_set():
            return attempt

        rel = (datetime.utcnow() - release_dt).total_seconds()
        if await page.locator('a.add-booking').count() > 0:
            if not seen_buttons:
                print(f"  👀 Tab {tab_no+1}: Book Now buttons visible at {rel:+.1f}s from release")
                seen_buttons = True
            async with lock:
                if found.is_set():
                    return attempt
                t = await try_click_book_now(page, fallback_times)
                if t:
                    result["page"] = page
                    result["time"] = t
                    found.set()
                    rel = (datetime.utcnow() - release_dt).total_seconds()
                    print(f"  ✅ Grabbed {t} at {rel:+.1f}s from release (tab {tab_no+1}, attempt {attempt})")
                    return attempt
        if tab_no == 0 and attempt % 10 == 0:
            print(f"  ⏳ {rel:+.0f}s from release, tab 1 attempt {attempt} — your times not bookable yet")
    return attempt


async def wait_and_grab_slot_tabs(page, preferred_time: str, fallback_times: list,
                              release_dt: datetime):
    """Returns the page with the booking form open, or None."""
    deadline = release_dt + timedelta(seconds=TIMEOUT_AFTER_RELEASE)

    print(f"  Checking if slots already available...")
    booked_time = await try_click_book_now(page, fallback_times)
    if booked_time:
        return page

    # Open extra tabs (same login) so the sheet is refreshed every ~0.5s
    pages = [page]
    for _ in range(NUM_TABS - 1):
        extra = await page.context.new_page()
        await extra.goto(TEE_SHEET_URL, wait_until="domcontentloaded")
        pages.append(extra)

    secs = (release_dt - datetime.utcnow()).total_seconds()
    print(f"  ⏳ {secs:.0f}s to release — {len(pages)} tabs refreshing...")

    found = asyncio.Event()
    lock = asyncio.Lock()
    result = {}
    t_start = datetime.utcnow()
    counts = await asyncio.gather(*[
        tab_worker(pg, i, fallback_times, release_dt, deadline, found, result, lock)
        for i, pg in enumerate(pages)
    ])
    secs_run = (datetime.utcnow() - t_start).total_seconds()
    total = sum(c or 0 for c in counts)
    if total:
        print(f"  📊 {total} refreshes in {secs_run:.0f}s across {len(pages)} tabs "
              f"= one fresh check every {secs_run/total:.2f}s")

    if result.get("page"):
        return result["page"]
    print(f"  ❌ Timed out — no slot found after {TIMEOUT_AFTER_RELEASE}s past release")
    return None


API_FETCH_JS = """
async (u) => {
    const r = await fetch(u, {credentials: 'same-origin',
        headers: {'Accept': 'application/json, text/plain, */*',
                  'X-Requested-With': 'XMLHttpRequest'}});
    return [r.status, await r.text()];
}
"""


def collect_slots(obj, out):
    """Find every "HH:MM": {"tee_time": {...}} entry anywhere in the JSON."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if (isinstance(k, str) and re.fullmatch(r"\d{2}:\d{2}", k)
                    and isinstance(v, dict) and isinstance(v.get("tee_time"), dict)):
                out[k] = v["tee_time"]
            else:
                collect_slots(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_slots(v, out)


def poll_interval(rel):
    if rel < -10:
        return 1.0    # calm until 10s before release
    if rel < 60:
        return 0.15   # flat out around the release
    return 0.5


async def wait_and_grab_slot(page, preferred_time: str, fallback_times: list,
                              release_dt: datetime):
    """
    FAST MODE: ask BRS for the tee sheet data directly (~0.2s per check)
    instead of waiting ~1.1s for the page to draw. Falls back to the
    3-tab method if the data feed doesn't work.
    Returns the page with the booking form open, or None.
    """
    data_base = TEE_SHEET_URL.replace("/tee-sheet/1/", "/tee-sheet/data/1/")
    deadline = release_dt + timedelta(seconds=TIMEOUT_AFTER_RELEASE)

    def rel():
        return (datetime.utcnow() - release_dt).total_seconds()

    async def fetch_slots():
        url = f"{data_base}?_={int(datetime.utcnow().timestamp()*1000)}"
        status, text = await page.evaluate(API_FETCH_JS, url)
        if status != 200:
            raise Exception(f"HTTP {status}")
        slots = {}
        collect_slots(json.loads(text), slots)
        return slots

    # Probe the data feed once
    try:
        slots = await fetch_slots()
        if not slots:
            raise Exception("no tee times found in data")
        print(f"  ⚡ Fast mode: data feed OK ({len(slots)} tee times)")
    except Exception as e:
        print(f"  ⚠️  Data feed not usable ({e}) — using 3-tab mode")
        return await wait_and_grab_slot_tabs(page, preferred_time, fallback_times, release_dt)

    failed = set()
    found = asyncio.Event()
    hit = {}
    stats = {"ok": 0, "bad": 0, "streak": 0}
    inflight = set()

    def pick(slots):
        for t in fallback_times:
            if t in failed:
                continue
            tt = slots.get(t)
            if tt and tt.get("bookable") and tt.get("url"):
                return t, tt["url"]
        return None

    async def one_check():
        try:
            sl = await fetch_slots()
            stats["ok"] += 1
            stats["streak"] = 0
        except Exception:
            stats["bad"] += 1
            stats["streak"] += 1
            return
        got = pick(sl)
        if got and not found.is_set():
            hit["time"], hit["url"], hit["seen"] = got[0], got[1], rel()
            found.set()

    got = pick(slots)
    if got:
        hit["time"], hit["url"], hit["seen"] = got[0], got[1], rel()
        found.set()

    print(f"  ⏳ {-rel():.0f}s to release — checking data feed...")
    last_log = 0.0
    while datetime.utcnow() < deadline:
        if not found.is_set():
            if len(inflight) < 6:
                task = asyncio.create_task(one_check())
                inflight.add(task)
                task.add_done_callback(inflight.discard)
            try:
                await asyncio.wait_for(found.wait(), timeout=poll_interval(rel()))
            except asyncio.TimeoutError:
                pass
            if stats["streak"] >= 10:
                print(f"  ⚠️  Data feed failing at {rel():+.1f}s — switching to 3-tab mode")
                await page.goto(TEE_SHEET_URL, wait_until="domcontentloaded")
                return await wait_and_grab_slot_tabs(page, preferred_time, fallback_times, release_dt)
            if rel() - last_log >= 10:
                last_log = rel()
                print(f"  ⏳ {rel():+.0f}s from release — {stats['ok']} checks, your times not bookable yet")
            continue

        t, url = hit["time"], hit["url"]
        if url.startswith("/"):
            url = "https://members.brsgolf.com" + url
        print(f"  👀 {t} became bookable — seen at {hit['seen']:+.2f}s from release")
        print(f"  🚀 Opening Book Now link for {t} at {rel():+.2f}s")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=10000)
            await page.wait_for_selector('form[name="member_booking_form"]',
                                         state="attached", timeout=8000)
            print(f"  ✅ Booking form open for {t} at {rel():+.2f}s "
                  f"({stats['ok']} checks, {stats['bad']} failed)")
            return page
        except Exception:
            try:
                body = (await page.inner_text("body"))[:200].replace("\n", " ")
            except Exception:
                body = "?"
            print(f"  ✗ {t} did not open booking form at {rel():+.2f}s. Page says: {body}")
            failed.add(t)
            hit.clear()
            found.clear()
            try:
                await page.goto(TEE_SHEET_URL, wait_until="domcontentloaded")
            except Exception:
                pass

    print(f"  ❌ Timed out — no slot found after {TIMEOUT_AFTER_RELEASE}s past release "
          f"({stats['ok']} checks, {stats['bad']} failed)")
    return None


async def set_player_via_select2(page, slot_num: int, player_name: str) -> bool:
    """
    BRS uses Select2 with class select2-hidden-accessible.
    Must set value via JavaScript directly — never use visibility checks.
    """
    member_id = PLAYER_IDS.get(player_name)
    if not member_id:
        print(f"    ⚠️  No member ID for '{player_name}'")
        return False

    select_id = f"member_booking_form_player_{slot_num}"
    print(f"    Setting player {slot_num}: {player_name} (id:{member_id})")

    result = await page.evaluate(f"""
        () => {{
            const sel = document.getElementById('{select_id}');
            if (!sel) return 'ERROR: select not found';

            // Verify option exists
            const opt = Array.from(sel.options).find(o => o.value === '{member_id}');
            if (!opt) return 'ERROR: option {member_id} not found (total options: ' + sel.options.length + ')';

            // Set value directly — do NOT dispatch events as they reset the value
            sel.value = '{member_id}';
            return 'OK';
        }}
    """)

    if 'ERROR' in result:
        print(f"    ❌ Player {slot_num}: {result}")
        return False

    await page.wait_for_timeout(600)

    # Verify it was set by checking the Select2 display label
    display = await page.evaluate(f"""
        () => {{
            const container = document.querySelector(
                '#select2-{select_id}-container, ' +
                'span[aria-labelledby="select2-{select_id}-container"]'
            );
            return container ? container.textContent.trim() : '';
        }}
    """)

    print(f"    ✅ Player {slot_num}: {player_name} ({result}) display='{display}'")
    return True


async def fill_and_confirm(page, players: list, label: str) -> bool:
    print(f"  Filling players and confirming booking...")

    def rel():
        return (datetime.utcnow() - RELEASE_DT).total_seconds() if RELEASE_DT else 0.0

    # Build player IDs
    player_ids = []
    for p in players[:4]:
        pid = PLAYER_IDS.get(p, "")
        player_ids.append(pid)
    while len(player_ids) < 4:
        player_ids.append("")

    # Wait only until the form and every player we need is selectable
    # (replaces the old fixed 2-second pause)
    ready_js = """
        (ids) => {
            const btn = document.getElementById('member_booking_form_confirm_booking');
            if (!btn) return false;
            for (let i = 0; i < 4; i++) {
                if (!ids[i]) continue;
                const sel = document.getElementById('member_booking_form_player_' + (i + 1));
                if (!sel) return false;
                if (!Array.from(sel.options).some(o => o.value === ids[i])) return false;
            }
            return true;
        }
    """
    try:
        await page.wait_for_function(ready_js, arg=player_ids, timeout=5000, polling=50)
        print(f"  ✅ Form ready at {rel():+.1f}s from release")
    except Exception:
        print(f"  ⚠️  Form not fully ready after 5s ({rel():+.1f}s) — submitting anyway")
    for p, pid in zip(players[:4], player_ids):
        print(f"    {p} -> {pid}")

    p1 = player_ids[0]
    p2 = player_ids[1]
    p3 = player_ids[2]
    p4 = player_ids[3]

    # Set all players and click submit in one atomic JS call
    result = await page.evaluate(f"""
        () => {{
            // Set player values
            const fields = [
                ['member_booking_form_player_1', '{p1}'],
                ['member_booking_form_player_2', '{p2}'],
                ['member_booking_form_player_3', '{p3}'],
                ['member_booking_form_player_4', '{p4}'],
            ];
            const results = [];
            for (const [id, val] of fields) {{
                const el = document.getElementById(id);
                if (el && val) {{
                    el.value = val;
                    results.push(id + '=' + el.value);
                }}
            }}

            // Verify via FormData
            const fd = {{}};
            const form = document.querySelector('form[name="member_booking_form"]');
            new FormData(form).forEach((v,k) => fd[k] = v);

            // Click submit
            const btn = document.getElementById('member_booking_form_confirm_booking')
                     || document.querySelector('button.bottom-btn')
                     || document.querySelector('button[type="submit"]');
            if (btn) {{
                btn.click();
                return 'CLICKED set=' + results.join(',') + ' fd_p2=' + (fd['member_booking_form[player_2]'] || 'EMPTY');
            }}
            return 'ERROR: no submit button';
        }}
    """)

    print(f"  Submit at {rel():+.1f}s from release: {result}")

    # Wait for page to change
    await page.wait_for_timeout(4000)
    await page.screenshot(path=f"confirmation_{label}.png", full_page=True)
    url = page.url
    content_after = await page.content()
    print(f"  URL after: {url}")

    # Check if still on booking form — if so the submission failed
    if "bookings/book" in url:
        # Check for error modal
        error_text = await page.evaluate("""
            () => {
                const modal = document.querySelector('.modal, [class*="modal"], [role="dialog"]');
                return modal ? modal.textContent.trim().substring(0, 200) : '';
            }
        """)
        if error_text:
            print(f"  ⚠️  Modal: {error_text[:150]}")
            # Dismiss and try submitting the form natively
            await page.evaluate("""
                () => {
                    const ok = document.querySelector('button:not(.bottom-btn)');
                    if (ok) ok.click();
                }
            """)
            await page.wait_for_timeout(500)

        # Try native form submit as fallback
        print("  Trying native form submit...")
        await page.evaluate(f"""
            () => {{
                document.getElementById('member_booking_form_player_2').value = '{p2}';
                document.getElementById('member_booking_form_player_3').value = '{p3}';
                document.getElementById('member_booking_form_player_4').value = '{p4}';
                document.querySelector('form[name="member_booking_form"]').submit();
            }}
        """)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(3000)
        await page.screenshot(path=f"confirmation2_{label}.png", full_page=True)
        print(f"  URL after native submit: {page.url}")

    print(f"  ✅ BOOKING COMPLETE — check BRS for confirmation")
    return True


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
    global RELEASE_DT
    RELEASE_DT     = release_dt
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
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page    = await context.new_page()

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

            booked_page = await wait_and_grab_slot(page, time, fallback_times, release_dt)

            if booked_page:
                await fill_and_confirm(booked_page, players, label)
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
