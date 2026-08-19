#!/usr/bin/env python3
"""Classify a TeamSnap iCal feed into the event types the PWA calendar needs.

The feed carries NO event-type field — `CATEGORIES` is absent and the only
signal is free text in SUMMARY — so type has to be inferred from the title.
This script is the offline instrument for that inference: it fetches (or reads)
the .ics, applies the rules, and prints every event with its assigned type so
misfiles can be spotted by eye before anything ships.

Order matters. `Off- no practice` is the ABSENCE of a practice and must be
tested before the practice rule, or a naive "practice" match claims it. Tryouts
are their own type (they happen once a year and are not training), so they are
tested before practice too. Likewise `Full Turf Practice fun Scrimmage vs 2014
boys` is a practice that mentions a scrimmage, so the practice rule has to win
there while `Scrimmage vs Caboto` still classifies as a game.

Usage:
    python3 scripts/teamsnap_ics_probe.py <feed-url-or-path>

The feed URL is a credential (it serves the team's whole schedule to anyone
holding it), so it is never hardcoded here — pass it in.
"""
from __future__ import annotations

import re
import sys
import urllib.request
from collections import Counter

# Every title is prefixed with the team name; strip it before matching so the
# rules read against the part the coach actually types.
TEAM_PREFIX = re.compile(r'^\s*Stompers\s*\d*\s*boys\s*-\s*', re.I)
CANCEL_PREFIX = re.compile(r'^\s*\[CANCELED\]\s*', re.I)


def classify(title: str) -> str:
    """Return one of: off, tryout, practice, game, team_event.

    Rules are ordered most-specific first; the first match wins.
    """
    t = title.lower()

    # "Off- no practice" / "Off- No Practice" — an explicit non-event. Must beat
    # the practice rule below, which would otherwise claim it.
    if re.search(r'\boff\b\s*[-–]?\s*no\s+practice', t) or re.search(r'\bno\s+practice\b', t):
        return 'off'

    # Tryouts are a once-a-year event, not training — the coach wants them
    # visually distinct. Tested before practice so a "Tryouts" title never
    # falls through to it.
    if 'tryout' in t:
        return 'tryout'

    # Practice wins over the game keywords, so an internal "fun Scrimmage"
    # during a turf practice stays a practice.
    if 'practice' in t:
        return 'practice'

    # Competitive fixtures. Festival/tournament/invitational are the
    # multi-day competition blocks; scrimmage/game are one-offs.
    if re.search(r'\b(tournament|festival|invitational|classic|cup|scrimmage|game)\b', t):
        # "Parents vs Players Game and Pizza Party" is social, not competitive.
        if 'parents vs players' in t or 'pizza' in t:
            return 'team_event'
        return 'game'

    return 'team_event'


def unfold(raw: str) -> str:
    """iCal folds long lines with a leading space/tab on the continuation."""
    return re.sub(r'\r?\n[ \t]', '', raw)


def prop(block: str, key: str) -> str | None:
    m = re.search(r'^' + key + r'(?:;[^:]*)?:(.*)$', block, re.M)
    return m.group(1).strip() if m else None


def parse(raw: str) -> list[dict]:
    events = []
    for block in re.findall(r'BEGIN:VEVENT(.*?)END:VEVENT', unfold(raw), re.S):
        summary = prop(block, 'SUMMARY') or ''
        canceled = bool(CANCEL_PREFIX.match(summary))
        clean = TEAM_PREFIX.sub('', CANCEL_PREFIX.sub('', summary)).strip()
        # Some titles ALSO end in "Cancelled" on top of the [CANCELED] prefix,
        # which would otherwise render cancelled three ways over (struck-through
        # title, badge, and the word itself).
        if canceled:
            clean = re.sub(r'[\s-]*cancell?ed\s*$', '', clean, flags=re.I).strip()

        dt = re.search(r'^DTSTART(?:;TZID=([^:]+))?:(\S+)', block, re.M)
        tz, start = (dt.group(1), dt.group(2)) if dt else (None, '')
        end = re.search(r'^DTEND(?:;TZID=[^:]+)?:(\S+)', block, re.M)

        # An all-day block is midnight-to-midnight with no TZID — that is how
        # TeamSnap represents a tournament DAY (not a single game).
        all_day = start.endswith('T000000') and tz is None

        desc = prop(block, 'DESCRIPTION') or ''
        arrival = re.search(r'Arrival Time:\s*([0-9]{1,2}:[0-9]{2}\s*[AP]M)', desc)
        # The venue name lives in the description; LOCATION holds a street address.
        venue = re.search(r'Location:\s*(.*?)(?:\\n|$)', desc)

        events.append({
            'uid': prop(block, 'UID'),
            'title': clean,
            'type': classify(clean),
            'canceled': canceled,
            'start': start,
            'end': end.group(1) if end else '',
            'tz': tz,
            'all_day': all_day,
            'venue': (venue.group(1).strip() if venue else ''),
            'address': (prop(block, 'LOCATION') or '').replace('\\n', ', ').replace('\\,', ','),
            'arrival': (arrival.group(1) if arrival else ''),
            'modified': prop(block, 'LAST-MODIFIED'),
        })
    return events


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = sys.argv[1]
    if src.startswith('http'):
        with urllib.request.urlopen(src, timeout=45) as r:
            raw = r.read().decode('utf-8', 'replace')
    else:
        raw = open(src, encoding='utf-8').read()

    events = parse(raw)
    events.sort(key=lambda e: e['start'])

    print(f'{len(events)} events\n')
    counts = Counter(e['type'] for e in events)
    for k, n in counts.most_common():
        print(f'  {k:11s} {n}')
    print(f'  {"canceled":11s} {sum(1 for e in events if e["canceled"])}')
    print(f'  {"all-day":11s} {sum(1 for e in events if e["all_day"])}')

    print('\n' + '-' * 78)
    for e in events:
        flags = ''.join(['X' if e['canceled'] else ' ', 'A' if e['all_day'] else ' '])
        when = f"{e['start'][:8]} {e['start'][9:13] or '....'}"
        print(f"{flags} {e['type']:10s} {when}  {e['title'][:44]:44s} {e['venue'][:22]}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
