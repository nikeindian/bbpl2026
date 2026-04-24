#!/usr/bin/env python3
"""
Fetch BB 2026 match results from CricHeroes and write results.json.
Runs via GitHub Actions every 10 minutes on tournament day.
"""

import asyncio
import json
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright

# ── Match registry ─────────────────────────────────────────────────────────
# CricHeroes match ID → {fixture_id, home team, away team}

WOMEN = [
    {'ch': 24201967, 'fid': 401, 'home': 'BB Super Queens', 'away': 'BB Sunrisers'},
    {'ch': 24201949, 'fid': 402, 'home': 'BB Smashers',     'away': 'BB Mavericks'},
    {'ch': 24201994, 'fid': 403, 'home': 'BB Smashers',     'away': 'BB Warriors'},
    {'ch': 24201980, 'fid': 404, 'home': 'BB Mavericks',    'away': 'BB Sunrisers'},
    {'ch': 24202038, 'fid': 405, 'home': 'BB Super Queens', 'away': 'BB Mavericks'},
    {'ch': 24202026, 'fid': 406, 'home': 'BB Smashers',     'away': 'BB Sunrisers'},
    {'ch': 24202076, 'fid': 407, 'home': 'BB Warriors',     'away': 'BB Sunrisers'},
    {'ch': 24202060, 'fid': 408, 'home': 'BB Super Queens', 'away': 'BB Smashers'},
    {'ch': 24202095, 'fid': 409, 'home': 'BB Warriors',     'away': 'BB Mavericks'},
    {'ch': 24202006, 'fid': 412, 'home': 'BB Super Queens', 'away': 'BB Warriors'},
]

# Men's matches — add entries here once tournament IDs are known
MEN = []

ALL_MATCHES = WOMEN + MEN

# ── Team name normalisation ─────────────────────────────────────────────────
ALIASES = {
    'bb super queens': 'BB Super Queens',
    'super queens':    'BB Super Queens',
    'bb smashers':     'BB Smashers',
    'smashers':        'BB Smashers',
    'bb warriors':     'BB Warriors',
    'warriors':        'BB Warriors',
    'bb mavericks':    'BB Mavericks',
    'mavericks':       'BB Mavericks',
    'bb sunrisers':    'BB Sunrisers',
    'sunrisers':       'BB Sunrisers',
}

def canon(name: str, home: str, away: str) -> str | None:
    """Return canonical team name, or None if it doesn't match either team."""
    key = name.lower().strip()
    mapped = ALIASES.get(key)
    if mapped in (home, away):
        return mapped
    # Fuzzy: check if any word from the team's short name appears
    for team in (home, away):
        short = team.replace('BB ', '').lower()
        if short in key or key in short:
            return team
    return None

# ── Scrape one scorecard page ───────────────────────────────────────────────
async def scrape(page, match: dict) -> dict | None:
    url = f"https://cricheroes.com/scorecard/{match['ch']}/"
    print(f"  GET {url}")
    try:
        await page.goto(url, wait_until='domcontentloaded', timeout=30_000)
        # Give React time to render
        await page.wait_for_timeout(4_000)
        body = await page.evaluate("document.body.innerText")
    except Exception as exc:
        print(f"    ✗ load error: {exc}")
        return None

    home, away = match['home'], match['away']

    # ── Result patterns ─────────────────────────────────────────────────────
    # CricHeroes shows e.g. "BB Super Queens won by 15 runs"
    #                    or "BB Smashers won by 3 wickets"
    #                    or "Match Tied"
    won_pat = re.compile(
        r'(BB\s+[\w\s]+?|[\w\s]+?)\s+won\s+by\s+(\d+\s+(?:runs?|wickets?))',
        re.IGNORECASE,
    )
    tie_pat = re.compile(r'\b(?:match\s+)?tied?\b', re.IGNORECASE)

    m = won_pat.search(body)
    if m:
        winner_raw = m.group(1).strip()
        margin     = m.group(2).strip()
        winner     = canon(winner_raw, home, away)
        if not winner:
            print(f"    ✗ unrecognised winner name: {winner_raw!r}")
            return None

        # Try to extract innings scores like "87/3 (7.0)"
        scores = re.findall(r'\d+/\d+\s*\(\d+\.?\d*\)', body)
        score_str = ' vs '.join(scores[:2]) if len(scores) >= 2 else ''

        print(f"    ✓ {winner} won by {margin}  {score_str}")
        return {'winner': winner, 'margin': margin, 'score': score_str}

    if tie_pat.search(body):
        scores = re.findall(r'\d+/\d+\s*\(\d+\.?\d*\)', body)
        score_str = ' vs '.join(scores[:2]) if len(scores) >= 2 else ''
        print(f"    ✓ Match tied  {score_str}")
        return {'winner': 'Tied', 'margin': '', 'score': score_str}

    # Detect if still upcoming / live
    if re.search(r'\b(upcoming|yet to start|toss|live|in progress)\b', body, re.I):
        print(f"    – match not yet completed")
    else:
        print(f"    – no result pattern found (page may have changed structure)")
        # Uncomment to debug:
        # print(body[:800])
    return None

# ── Main ────────────────────────────────────────────────────────────────────
async def main():
    results_path = Path(__file__).resolve().parent.parent / 'results.json'
    try:
        results: dict = json.loads(results_path.read_text()) if results_path.exists() else {}
    except Exception:
        results = {}

    changed = False

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=(
                'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
                'AppleWebKit/605.1.15 (KHTML, like Gecko) '
                'Version/17.0 Mobile/15E148 Safari/604.1'
            ),
            viewport={'width': 390, 'height': 844},
        )
        pg = await ctx.new_page()

        for match in ALL_MATCHES:
            fid = str(match['fid'])
            if fid in results:
                print(f"  skip fid={fid} (already recorded)")
                continue
            print(f"Checking fid={fid}  ch={match['ch']}  {match['home']} vs {match['away']}")
            result = await scrape(pg, match)
            if result:
                results[fid] = result
                changed = True

        await browser.close()

    if changed:
        results_path.write_text(json.dumps(results, indent=2))
        print(f"\nWrote {results_path} with {len(results)} total results.")
    else:
        print('\nNo new results — results.json unchanged.')

    return 0

if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
