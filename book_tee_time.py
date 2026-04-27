"""
TeeBot - Beaverstown Golf Club
Main weekly bot — runs Monday 8:28 PM, logs in early, waits for 8:30,
then books immediately. Supports fallback times if first choice is taken.
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

DEFAULT_PLAYERS = ["Kirwan, Rory", "Kirwan, Lisa", "Carrick, Paul", "Hennelly, Ronan"]
DEFAULT_BOOKINGS = [
    {
        "label": "Saturday Morning",
        "day_of_week": 5,
        "preferred_times": ["08:20", "08:30", "08:40", "08:50", "09:00"],
        "players": 4
    }
]


def load_plan():
    if not GITHUB_TOKEN or not REPO:
        return DEFAULT_PLAYERS, DEFAULT_BOOKINGS
    url = f"https://api.github.com/repos/{REPO}/contents/players.json"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3.raw",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            players  = data.get("players", DEFAULT_PLAYERS)[:4]
            bookings = data.get("bookings", DEFAULT_BOOKINGS)
            print(f"✅ Players : {', '.join(players)}")
            print(f"✅ Bookings: {len(bookings)} slot(s)")
            return players, bookings
    except Exception as e:
        print(f"⚠️  Could not read players.json: {e} — using defaults")
        return DEFAULT_PLAYERS, DEFAULT_BOOKINGS


def get_next_date_for_dow(dow: int) -> datetime:
    today = datetime.now()
    days_ahead = (dow - today.weekday()) % 7 or 7
    return today + timedelta(days=days_ahead)


async def login(page) -> bool:
    print("\n🔐 Logging in...")
    await page.goto(BRS_LOGIN_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)
    try:
        await page.fill('input[name="username"], input[type="text"]', BRS_EMAIL)
        await page.fill('input[type="password"]', BRS_PASSWORD)
        await page.click('button:has-text("LOGIN")')
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(2000)
        print(f"✅ Logged in — URL: {page.url}")
        return True
    except Exception as e:
        print(f"❌ Login failed: {e}")
        return False


async def navigate_to_date(page, target_dt: datetime):
    """Go directly to the tee sheet URL for the target date."""
    date_path = target_dt.strftime("%Y/%m/%d")
    url = f"{TEE_SHEET_BASE}/{date_path}"
    print(f"\n📅 Navigating to {target_dt.strftime('%A %d %B')} — {url}")
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)


async def wait_for_8_30(page, target_dt: datetime):
    """
    If it's before 8:30 PM, stay on the page and wait.
    Refresh every 10 seconds until 8:30 hits, then go immediately.
    This keeps the session alive and means zero delay when slots open.
    """
    now = datetime.now()
    opening_time = now.replace(hour=20, minute=30, second=0, microsecond=0)

    if now >= opening_time:
        print("⏰ Already past 8:30 PM — booking immediately")
        return

    wait_secs = (opening_time - now).total_seconds()
    print(f"⏳ Waiting {wait_secs:.0f}s until 8:30 PM — staying on page, refreshing every 10s")

    while True:
        now = datetime.now()
        remaining = (opening_time - now).total_seconds()
        if remaining <= 0:
            print("🚀 8:30 PM — GO!")
            # Refresh one final time to get fresh tee sheet
            date_path = target_dt.strftime("%Y/%m/%d")
            await page.goto(f"{TEE_SHEET_BASE}/{date_path}", wait_until="domcontentloaded")
            await page.wait_for_timeout(500)
            return
        elif remaining <= 10:
            # In the last 10 seconds — check every 0.5s
            await asyncio.sleep(0.5)
        else:
            # Refresh page every 10s to keep session alive
            await asyncio.sleep(10)
            date_path = target_dt.strftime("%Y/%m/%d")
            await page.goto(f"{TEE_SHEET_BASE}/{date_path}", wait_until="domcontentloaded")
            print(f"  🔄 Refreshed — {remaining:.0f}s remaining")


async def try_book_time(page, target_time: str) -> bool:
    """Try to click BOOK NOW for a specific time. Returns True if booking form opens."""
    print(f"  ⏱  Trying {target_time}...")

    # Method 1: row with time text + BOOK NOW link
    try:
        await page.locator(f'tr:has-text("{target_time}") a:has-text("BOOK NOW")').first.click(timeout=3000)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(1500)
        # Check we're on booking form (not "already booked" popup)
        content = await page.content()
        if "Booking Details" in content or "Create Booking" in content:
            print(f"  ✅ {target_time} slot opened!")
            return True
        elif "Booked" in content or "already" in content.lower():
            print(f"  ❌ {target_time} already taken")
            await page.go_back()
            await page.wait_for_timeout(1000)
            return False
    except:
        pass

    # Method 2: JS row scan
    try:
        clicked = await page.evaluate(f"""
            () => {{
                const rows = document.querySelectorAll('tr');
                for (const row of rows) {{
                    if (row.textContent.includes('{target_time}')) {{
                        const btn = row.querySelector('a[href*="book"], a:last-child');
                        if (btn) {{ btn.click(); return true; }}
                    }}
                }}
                return false;
            }}
        """)
        if clicked:
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(1500)
            content = await page.content()
            if "Booking Details" in content or "Create Booking" in content:
                print(f"  ✅ {target_time} opened via JS!")
                return True
            await page.go_back()
            await page.wait_for_timeout(1000)
    except:
        pass

    return False


async def add_player(page, slot_num: int, player_name: str) -> bool:
    """Click the player slot input, wait for buddy dropdown, click the name."""
    print(f"  👤 Player {slot_num}: {player_name}")

    inputs = page.locator('input[placeholder*="typing"], input[placeholder*="find player"]')
    count  = await inputs.count()
    input_index = slot_num - 2  # slot 2 = index 0

    if input_index >= count:
        print(f"  ⚠️ Only {count} inputs, can't fill slot {slot_num}")
        return False

    # Click the input to open the buddy dropdown
    await inputs.nth(input_index).click()
    await page.wait_for_timeout(1500)

    # Click the player name in the dropdown
    for sel in [
        f'li:has-text("{player_name}")',
        f'div:has-text("{player_name}")',
        f'[role="option"]:has-text("{player_name}")',
    ]:
        try:
            await page.locator(sel).filter(has_text=player_name).first.click(timeout=3000)
            print(f"  ✅ Selected {player_name}")
            await page.wait_for_timeout(600)
            return True
        except:
            continue

    # JS fallback — exact text match
    clicked = await page.evaluate(f"""
        () => {{
            const all = document.querySelectorAll('li, div, span');
            for (const el of all) {{
                if (el.textContent.trim() === '{player_name}') {{
                    el.click();
                    return true;
                }}
            }}
            return false;
        }}
    """)
    if clicked:
        print(f"  ✅ JS selected {player_name}")
        await page.wait_for_timeout(600)
        return True

    print(f"  ⚠️ Could not find {player_name} in dropdown")
    return False


async def complete_booking(page, players: list) -> bool:
    """Fill players 2-4 and click Create Booking."""
    print("\n📋 Filling booking form...")
    await page.wait_for_timeout(500)

    # Player 1 is pre-filled — fill 2, 3, 4
    for i, player in enumerate(players[1:4]):
        slot = i + 2
        await add_player(page, slot, player)

    await page.wait_for_timeout(800)

    # Click Create Booking
    for sel in [
        'button:has-text("Create Booking")',
        'a:has-text("Create Booking")',
        'button:has-text("CREATE BOOKING")',
        'button[type="submit"]',
    ]:
        try:
            await page.click(sel, timeout=3000)
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(2000)
            content = await page.content()
            if "confirmed" in content.lower() or "booking" in content.lower():
                await page.screenshot(path="confirmation.png", full_page=True)
                print("✅ BOOKING CONFIRMED!")
                return True
        except:
            continue

    await page.screenshot(path="error_no_confirm.png", full_page=True)
    return False


async def book_single(page, booking: dict, players: list) -> bool:
    """Handle one booking — tries preferred times in order until one succeeds."""
    dow             = booking.get("day_of_week", 5)
    preferred_times = booking.get("preferred_times", ["08:20"])
    label           = booking.get("label", "Booking")
    target_dt       = get_next_date_for_dow(dow)

    print(f"\n{'─'*54}")
    print(f"📅 {label} — {target_dt.strftime('%A %d %B %Y')}")
    print(f"⏰ Preferred times: {', '.join(preferred_times)}")
    print(f"👥 Players: {', '.join(players)}")
    print(f"{'─'*54}")

    await navigate_to_date(page, target_dt)
    await wait_for_8_30(page, target_dt)

    # Try each preferred time in order
    for t in preferred_times:
        booked = await try_book_time(page, t)
        if booked:
            success = await complete_booking(page, players)
            if success:
                return True
            else:
                # Booking form failed — go back and try next time
                await navigate_to_date(page, target_dt)
        # Small pause between attempts
        await page.wait_for_timeout(300)

    print(f"❌ All preferred times exhausted for {label}")
    return False


async def main():
    players, bookings = load_plan()

    print("=" * 54)
    print("  🏌️  TeeBot — Beaverstown Golf Club")
    print(f"  {datetime.now().strftime('%A %d %B %Y, %H:%M')}")
    print("=" * 54)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page(viewport={"width": 1280, "height": 900})

        if not await login(page):
            print("❌ Login failed — check secrets")
            await browser.close()
            return

        results = []
        for booking in bookings:
            ok = await book_single(page, booking, players)
            results.append((booking["label"], ok))

        await browser.close()

    print("\n" + "=" * 54)
    print("  📊 Summary")
    print("=" * 54)
    for label, ok in results:
        print(f"  {'✅' if ok else '❌'}  {label}")
    print("=" * 54)


if __name__ == "__main__":
    asyncio.run(main())
