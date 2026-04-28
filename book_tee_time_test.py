"""
TeeBot TEST SCRIPT - Beaverstown Golf Club BRS
DIAGNOSTIC VERSION - dumps full HTML of booking form
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

TEST_DAY_OF_WEEK = 2
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


async def diagnose_booking_form(page):
    """Dump everything about the booking form to diagnose the player fields."""

    print("\n=== DIAGNOSTIC: PAGE URL ===")
    print(f"  {page.url}")

    print("\n=== DIAGNOSTIC: IFRAMES ===")
    frames = page.frames
    print(f"  Total frames: {len(frames)}")
    for i, frame in enumerate(frames):
        print(f"  Frame {i}: url={frame.url} name={frame.name}")

    print("\n=== DIAGNOSTIC: ALL INPUTS (including in iframes) ===")
    for i, frame in enumerate(frames):
        inputs = await frame.locator("input").all()
        print(f"  Frame {i} ({frame.url[:60]}): {len(inputs)} inputs")
        for j, inp in enumerate(inputs):
            try:
                tp  = await inp.get_attribute("type") or "text"
                nm  = await inp.get_attribute("name") or ""
                ph  = await inp.get_attribute("placeholder") or ""
                val = await inp.input_value()
                vis = await inp.is_visible()
                print(f"    [{j}] type={tp} name={nm!r} ph={ph!r} val={val!r} vis={vis}")
            except Exception as ex:
                print(f"    [{j}] error: {ex}")

    print("\n=== DIAGNOSTIC: ELEMENTS WITH 'player' TEXT ===")
    # Find any element that contains "player" or "typing" text
    for frame in frames:
        els = await frame.locator('[placeholder*="player"], [placeholder*="typing"], [placeholder*="find"]').all()
        if els:
            print(f"  Frame {frame.url[:60]}: found {len(els)} elements with player/typing/find placeholder")
            for el in els:
                tag = await el.evaluate("el => el.tagName")
                ph  = await el.get_attribute("placeholder") or ""
                vis = await el.is_visible()
                print(f"    tag={tag} ph={ph!r} vis={vis}")

    print("\n=== DIAGNOSTIC: BOOKING FORM HTML (first 3000 chars) ===")
    # Get the main booking card HTML
    try:
        card = page.locator('.card, .booking-form, form').first
        html = await card.inner_html()
        print(html[:3000])
    except Exception as e:
        print(f"  Could not get card HTML: {e}")
        # Fall back to full page
        html = await page.content()
        # Find the section with "Player 2"
        idx = html.find("Player 2")
        if idx > 0:
            print(html[max(0, idx-200):idx+2000])
        else:
            print("  'Player 2' not found in page HTML")

    # Save full HTML to file for artifact inspection
    full_html = await page.content()
    with open("debug_booking_form.html", "w") as f:
        f.write(full_html)
    print("\n  Full HTML saved to debug_booking_form.html (check artifacts)")


async def main():
    players   = load_players()
    target_dt = get_next_date_for_dow(TEST_DAY_OF_WEEK)

    print("=" * 54)
    print("  TeeBot DIAGNOSTIC — Beaverstown Golf Club")
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
            await page.screenshot(path="debug_07_booking_form.png", full_page=True)
            await page.wait_for_timeout(1000)
            await diagnose_booking_form(page)
        else:
            print("\nCould not find time slot")

        await browser.close()

    print("\n" + "=" * 54)
    print("  Diagnostic complete — check artifacts for debug_booking_form.html")
    print("=" * 54)


if __name__ == "__main__":
    asyncio.run(main())
