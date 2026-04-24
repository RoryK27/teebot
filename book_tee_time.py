"""
TeeBot - Automated BRS Golf Tee Time Booker
Reads both players AND booking schedule from players.json saved by the web app.
"""

import asyncio, os, json, urllib.request
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

# ── Credentials (GitHub Secrets) ──────────────────────────────────────────────
BRS_URL      = os.environ.get("BRS_URL", "https://members.brsgolf.com/beaverstown/tee-sheet/1")
BRS_EMAIL    = os.environ["BRS_EMAIL"]
BRS_PASSWORD = os.environ["BRS_PASSWORD"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
REPO         = os.environ["GITHUB_REPOSITORY"]

# ── Fallback if players.json hasn't been saved yet ────────────────────────────
DEFAULT_BOOKINGS = [
    {"label": "Saturday Morning", "day_of_week": 5, "target_time": "08:20", "players": 4},
]
DEFAULT_PLAYERS = [
    "Carrick, Paul",
    "Hennelly, Ronan",
    "Kelly, Peter 'Seve'",
    "Kelly, Edward",
]
# ──────────────────────────────────────────────────────────────────────────────


def load_plan() -> tuple[list[str], list[dict]]:
    """Load players + bookings from players.json in the repo."""
    url = f"https://api.github.com/repos/{REPO}/contents/players.json"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3.raw",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            players  = data.get("players",  DEFAULT_PLAYERS)[:4]
            bookings = data.get("bookings", DEFAULT_BOOKINGS)
            print(f"✅ Players : {', '.join(players)}")
            print(f"✅ Bookings: {len(bookings)} slot(s)")
            for b in bookings:
                print(f"   → {b['label']} at {b['target_time']}")
            return players, bookings
    except Exception as e:
        print(f"⚠️  Could not read players.json ({e}) — using defaults")
        return DEFAULT_PLAYERS, DEFAULT_BOOKINGS


def get_next_date_for_dow(dow: int) -> str:
    """Next date (DD/MM/YYYY) for a given day-of-week (0=Mon … 6=Sun)."""
    today = datetime.now()
    days_ahead = (dow - today.weekday()) % 7 or 7
    return (today + timedelta(days=days_ahead)).strftime("%d/%m/%Y")


async def login(page) -> bool:
    print("\n🔐 Logging into BRS...")
    await page.goto(BRS_URL, wait_until="networkidle")
    try:
        await page.fill('input[type="email"], input[name="email"], input[name="username"]', BRS_EMAIL)
        await page.fill('input[type="password"]', BRS_PASSWORD)
        await page.click('button[type="submit"], input[type="submit"]')
        await page.wait_for_load_state("networkidle")
        print("✅ Logged in")
        return True
    except Exception as e:
        print(f"❌ Login failed: {e}")
        return False


async def add_players(page, players: list[str]):
    for i, player in enumerate(players):
        for sel in [
            f'input[placeholder*="member"]:nth-of-type({i+1})',
            f'input[placeholder*="player"]:nth-of-type({i+1})',
            f'.player-input:nth-child({i+1}) input',
        ]:
            try:
                await page.fill(sel, player, timeout=2000)
                await page.wait_for_timeout(600)
                await page.click('.autocomplete-item:first-child, .suggestion:first-child, li[role="option"]:first-child', timeout=1500)
                print(f"   ✅ Added {player}")
                break
            except:
                continue


async def book_single(page, booking: dict, players: list[str]) -> bool:
    dow         = booking.get("day_of_week", 5)
    target_date = get_next_date_for_dow(dow)
    label       = booking.get("label", "Booking")
    target_time = booking.get("target_time", "08:20")
    num_players = booking.get("players", len(players))

    print(f"\n{'─'*54}")
    print(f"  📅 {label}  |  {target_date}  |  {target_time}  |  {num_players}p")
    print(f"{'─'*54}")

    try:
        await page.goto(BRS_URL, wait_until="networkidle")
        await page.wait_for_timeout(1500)

        # Select date
        day_num = target_date.split("/")[0]
        for sel in [f'a:has-text("{target_date}")', f'td:has-text("{day_num}")', f'button:has-text("{day_num}")']:
            try:
                await page.click(sel, timeout=3000); break
            except: continue
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1500)

        # Click time slot
        clicked = False
        for sel in [f'a:has-text("{target_time}")', f'button:has-text("{target_time}")',
                    f'td:has-text("{target_time}")', f'[data-time="{target_time}"]']:
            try:
                await page.click(sel, timeout=3000)
                print(f"   ✅ Clicked {target_time}")
                clicked = True; break
            except: continue

        if not clicked:
            print(f"   ⚠️  {target_time} not found — trying earliest available")
            await page.click('a.bookable, button.available, td.available a', timeout=5000)

        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1000)

        # Set player count
        try:
            await page.select_option('select[name*="player"], select[name*="member"], select#players', str(num_players))
        except:
            for _ in range(num_players - 1):
                try: await page.click('button:has-text("+"), .increase-players', timeout=2000)
                except: break

        await add_players(page, players)
        await page.wait_for_timeout(1000)

        # Confirm
        for sel in ['button:has-text("Confirm")', 'button:has-text("Book")',
                    'input[value="Confirm"]', 'button[type="submit"]']:
            try:
                await page.click(sel, timeout=3000)
                print("   ✅ Booking confirmed!")
                break
            except: continue

        await page.wait_for_timeout(2000)
        safe = label.lower().replace(" ", "_")
        await page.screenshot(path=f"confirmation_{safe}.png", full_page=True)
        return True

    except Exception as e:
        print(f"   ❌ Failed: {e}")
        safe = label.lower().replace(" ", "_")
        try: await page.screenshot(path=f"error_{safe}.png", full_page=True)
        except: pass
        return False


async def main():
    print("=" * 54)
    print("  🏌️  TeeBot")
    print(f"  {datetime.now().strftime('%A %d %B %Y, %H:%M')}")
    print("=" * 54)

    players, bookings = load_plan()

    if not bookings:
        print("No bookings configured. Add days in the TeeBot app first.")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page    = await browser.new_page()

        if not await login(page):
            print("Cannot proceed — check credentials.")
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
    print(f"\n  👥 Booked for: {', '.join(players)}")
    print("=" * 54)


if __name__ == "__main__":
    asyncio.run(main())
