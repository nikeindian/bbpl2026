#!/usr/bin/env python3
"""
Fetch BB 2026 match results from CricHeroes and write results.json.
Runs via GitHub Actions every 10 minutes on tournament day.

First run also auto-discovers all men's match IDs from the tournament
page (clicking "Load more" until all 42 are visible) and saves them
to scripts/men_matches.json for subsequent runs.
"""

import asyncio
import json
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent

# ── Women's matches (all IDs known) ────────────────────────────────────────
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

# ── Men's fixture schedule (fixture_id → canonical teams) ──────────────────
# Used to map auto-discovered CricHeroes matches → fixture IDs
MEN_FIXTURES = {
    101: ('BB Vikings',      'BB Devils'),
    102: ('BB Bashers',      'BB Big Blasters'),
    103: ('BB Bro Squad',    'BB United'),
    104: ('BB Vikings',      'BB Maratha'),
    105: ('BB Bashers',      'BB Devils'),
    106: ('BB United',       'BB Big Blasters'),
    107: ('BB Bro Squad',    'BB Maratha'),
    108: ('BB Vikings',      'BB Bashers'),
    109: ('BB United',       'BB Devils'),
    110: ('BB Maratha',      'BB Big Blasters'),
    111: ('BB Bashers',      'BB Bro Squad'),
    112: ('BB Vikings',      'BB United'),
    113: ('BB Maratha',      'BB Devils'),
    114: ('BB Bashers',      'BB United'),
    115: ('BB Bro Squad',    'BB Big Blasters'),
    116: ('BB Bro Squad',    'BB Devils'),
    117: ('BB Vikings',      'BB Big Blasters'),
    118: ('BB Vikings',      'BB Bro Squad'),
    119: ('BB Bashers',      'BB Maratha'),
    120: ('BB Big Blasters', 'BB Devils'),
    121: ('BB Maratha',      'BB United'),
    201: ('BB Giants',       'BB Super Kings'),
    202: ('BB SG Legends',   'BB Dhurandhars'),
    203: ('BB Gladiators',   'BB Yoddhas'),
    204: ('BB Giants',       'BB Game Changers'),
    205: ('BB SG Legends',   'BB Super Kings'),
    206: ('BB Yoddhas',      'BB Dhurandhars'),
    207: ('BB Gladiators',   'BB Game Changers'),
    208: ('BB Giants',       'BB SG Legends'),
    209: ('BB Yoddhas',      'BB Super Kings'),
    210: ('BB Game Changers','BB Dhurandhars'),
    211: ('BB SG Legends',   'BB Gladiators'),
    212: ('BB Giants',       'BB Yoddhas'),
    213: ('BB Game Changers','BB Super Kings'),
    214: ('BB Gladiators',   'BB Dhurandhars'),
    215: ('BB SG Legends',   'BB Yoddhas'),
    216: ('BB Giants',       'BB Dhurandhars'),
    217: ('BB Gladiators',   'BB Super Kings'),
    218: ('BB SG Legends',   'BB Game Changers'),
    219: ('BB Giants',       'BB Gladiators'),
    220: ('BB Dhurandhars',  'BB Super Kings'),
    221: ('BB Game Changers','BB Yoddhas'),
}

# ── Team name normalisation for CricHeroes → canonical ─────────────────────
MEN_ALIASES = {
    'bb vikings':           'BB Vikings',
    'vikings':              'BB Vikings',
    'bb bashers':           'BB Bashers',
    'bashers':              'BB Bashers',
    'bb the bro squad':     'BB Bro Squad',
    'bb bro squad':         'BB Bro Squad',
    'bro squad':            'BB Bro Squad',
    'bb maratha':           'BB Maratha',
    'maratha':              'BB Maratha',
    'bb united':            'BB United',
    'united':               'BB United',
    'bb big blasters':      'BB Big Blasters',
    'big blasters':         'BB Big Blasters',
    'bb devils':            'BB Devils',
    'devils':               'BB Devils',
    'bb giants':            'BB Giants',
    'giants':               'BB Giants',
    'bb sg legends':        'BB SG Legends',
    'sg legends':           'BB SG Legends',
    'bb gladiators 2026':   'BB Gladiators',
    'bb gladiators':        'BB Gladiators',
    'gladiators':           'BB Gladiators',
    'bb team game changers':'BB Game Changers',
    'bb game changers':     'BB Game Changers',
    'game changers':        'BB Game Changers',
    'bb yoddhas':           'BB Yoddhas',
    'yoddhas':              'BB Yoddhas',
    'bb dhurandars':        'BB Dhurandhars',
    'bb dhurandhars':       'BB Dhurandhars',
    'dhurandhars':          'BB Dhurandhars',
    'bb super kings':       'BB Super Kings',
    'super kings':          'BB Super Kings',
}

WOMEN_ALIASES = {
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

# Reverse lookup: frozenset of canonical team names → fixture_id
FIXTURE_BY_TEAMS = {
    frozenset(teams): fid for fid, teams in MEN_FIXTURES.items()
}


def canon_men(raw: str) -> str | None:
    return MEN_ALIASES.get(raw.lower().strip())


def canon_women(raw: str, home: str, away: str) -> str | None:
    mapped = WOMEN_ALIASES.get(raw.lower().strip())
    if mapped in (home, away):
        return mapped
    for team in (home, away):
        if team.replace('BB ', '').lower() in raw.lower():
            return team
    return None


# ── Auto-discover men's matches from tournament page ───────────────────────
async def discover_men_matches(page) -> list[dict]:
    """
    Load the tournament matches page, click 'Load more' until all 42
    matches are visible, then extract match IDs + team names and map
    them to fixture IDs.
    """
    url = ('https://cricheroes.com/tournament/1982024/'
           'beyond-boundaries-2026-championship-vp-male/matches/upcoming-matches')
    print(f'Discovering men\'s matches from tournament page...')
    await page.goto(url, wait_until='domcontentloaded', timeout=40_000)
    await page.wait_for_timeout(4_000)

    # Click 'Load more' until all matches are visible or button disappears
    for _ in range(10):
        try:
            btn = page.locator('text=/load more/i').first
            if await btn.is_visible(timeout=3_000):
                await btn.click()
                await page.wait_for_timeout(2_000)
            else:
                break
        except Exception:
            break

    # Extract all scorecard links
    links = await page.eval_on_selector_all(
        'a[href*="/scorecard/"]',
        'els => els.map(e => e.href)'
    )

    discovered = []
    seen_ch = set()
    pat = re.compile(r'/scorecard/(\d+)/beyond-boundaries-2026-championship-vp-male/([^/]+)/.*')

    for href in links:
        m = pat.search(href)
        if not m:
            continue
        ch_id = int(m.group(1))
        if ch_id in seen_ch:
            continue
        seen_ch.add(ch_id)

        slug = m.group(2)  # e.g. "bb-devils-vs-bb-vikings"
        parts = slug.split('-vs-')
        if len(parts) != 2:
            continue
        t1_raw = parts[0].replace('-', ' ').strip()
        t2_raw = parts[1].replace('-', ' ').strip()
        t1 = canon_men(t1_raw)
        t2 = canon_men(t2_raw)
        if not t1 or not t2:
            print(f'  ? unrecognised teams in slug: {slug}')
            continue

        fid = FIXTURE_BY_TEAMS.get(frozenset([t1, t2]))
        if not fid:
            print(f'  ? no fixture found for {t1} vs {t2}')
            continue

        discovered.append({'ch': ch_id, 'fid': fid, 'home': t1, 'away': t2})

    print(f'  discovered {len(discovered)} men\'s matches (expected 42)')
    return discovered


# ── Scrape one scorecard page for result ───────────────────────────────────
async def scrape_result(page, match: dict, aliases: dict) -> dict | None:
    url = f"https://cricheroes.com/scorecard/{match['ch']}/"
    print(f"  GET {match['ch']}  fid={match['fid']}  {match['home']} vs {match['away']}")
    try:
        await page.goto(url, wait_until='domcontentloaded', timeout=30_000)
        await page.wait_for_timeout(4_000)
        body = await page.evaluate('document.body.innerText')
    except Exception as exc:
        print(f'    ✗ load error: {exc}')
        return None

    home, away = match['home'], match['away']

    # Tie
    if re.search(r'\b(?:match\s+)?tied?\b', body, re.I):
        scores = re.findall(r'\d+/\d+\s*\(\d+\.?\d*\)', body)
        return {'winner': 'Tied', 'margin': '', 'score': ' vs '.join(scores[:2])}

    # Win
    won = re.search(
        r'([\w\s]+?)\s+won\s+by\s+(\d+\s+(?:runs?|wickets?))',
        body, re.IGNORECASE
    )
    if won:
        winner_raw = won.group(1).strip()
        margin     = won.group(2).strip()
        winner     = aliases.get(winner_raw.lower().strip())
        if not winner:
            # Fuzzy
            for team in (home, away):
                if team.replace('BB ', '').lower() in winner_raw.lower():
                    winner = team
                    break
        if winner not in (home, away):
            print(f'    ✗ unrecognised winner: {winner_raw!r}')
            return None
        scores = re.findall(r'\d+/\d+\s*\(\d+\.?\d*\)', body)
        score_str = ' vs '.join(scores[:2]) if len(scores) >= 2 else ''
        print(f'    ✓ {winner} won by {margin}  {score_str}')
        return {'winner': winner, 'margin': margin, 'score': score_str}

    if re.search(r'\b(upcoming|yet to start|toss|live|in progress)\b', body, re.I):
        print(f'    – not yet completed')
    else:
        print(f'    – no result pattern found')
    return None


# ── Main ────────────────────────────────────────────────────────────────────
async def main():
    results_path  = ROOT / 'results.json'
    men_cache     = ROOT / 'scripts' / 'men_matches.json'

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

        # ── Discover / load men's matches ──────────────────────────────────
        if men_cache.exists():
            men_matches = json.loads(men_cache.read_text())
            print(f'Loaded {len(men_matches)} men\'s matches from cache.')
        else:
            men_matches = await discover_men_matches(pg)
            if men_matches:
                men_cache.write_text(json.dumps(men_matches, indent=2))
                print(f'Saved men_matches.json ({len(men_matches)} matches).')
            changed = True  # cache file is new — always commit

        all_matches = [
            ('women', WOMEN, WOMEN_ALIASES),
            ('men',   men_matches, MEN_ALIASES),
        ]

        for label, matches, aliases in all_matches:
            print(f'\n── {label.upper()} ─────────────────────────────')
            for match in matches:
                fid = str(match['fid'])
                if fid in results:
                    print(f'  skip fid={fid} (already recorded)')
                    continue
                result = await scrape_result(pg, match, aliases)
                if result:
                    results[fid] = result
                    changed = True

        await browser.close()

    if changed:
        results_path.write_text(json.dumps(results, indent=2))
        print(f'\nWrote results.json  ({len(results)} total results)')
    else:
        print('\nNo changes — results.json unchanged.')

    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
