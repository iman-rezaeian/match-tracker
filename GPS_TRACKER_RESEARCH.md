# GPS trackers for per-player stats — deep research & gap analysis

> **SCOPE CORRECTION 2026-08-18: ~70% of the season is played INDOORS, where
> GPS does not work.** This document therefore covers only the outdoor
> minority of games. The primary hardware question is indoor tracking — see
> `INDOOR_TRACKING_RESEARCH.md` (UWB; recommended stack may also cover the
> outdoor pitch, which would make GPS unnecessary entirely).

Goal: pick a GPS tracker to buy **for each kid** (~14 units) that is compatible
with our pipeline — i.e. yields a raw per-player positional stream we can turn
into `tracks_df (track_id, time_s, x_m, y_m)` + a trivial `identity_by_track`,
so the entire existing stats/formation/heatmap/PWA stack works unchanged.
Researched 2026-08-18 (five parallel web-research passes). Builds on the
2026-08-08 "Player tracking hardware" session and `PLAYER_ID_RESEARCH.md`
(2026-06).

## TL;DR

- **No soccer-branded product passes both hard requirements** (raw positional
  export AND ≲$150/kid one-time). Confirmed harder in 2026 than in the prior
  research: the market moved *away* from open data (SOCCERBEE paywalled, Trace
  killed its pods, Titan absorbed by Hudl, PlayerTek EOL'd).
- **The used-pod market is a trap** — cheap hardware everywhere, but every pod
  family that emits raw data does it only through a licensed cloud
  (OpenField/Sonra/Catapult One). Ignore "no subscription" seller claims.
- **New find the prior research missed entirely: off-the-shelf GNSS data
  loggers.** The **Columbus P-1 Mark II** (10 Hz, native CSV/NMEA to microSD,
  mounts as a USB drive, IP66, **CAD $189, in stock at CanadaGPS.ca**) is a
  ready-made, zero-subscription version of the DIY pod — fleet of 14 ≈
  **CAD ~$2,650 one-time**, or a 1–2 unit pilot for ~CAD $200–400.
- **Wrist GPS watches are banned on field players** (Law 4 electronic-equipment
  clause; refs still treat watches as dangerous post-2026/27) — the prior
  research's used-Garmin option is dead for games.
- Vest pods are permitted **if the competition organiser (league/district —
  ECSA for LaSalle) approves and the ref accepts them as not dangerous**; get
  written approval + signed parent consent (kids' location data is sensitive
  under OPC guidance; under-13 consent must come from parents).

## Why we're here (one paragraph)

Per-player **distance/sprints are integrals** and need continuous identity;
every video path measured on 2026-08-09 fails on coverage or coach effort
(automatic ~20%, seeding 35%, fragment verification 52–62% for an hour,
click-and-fix ~1,900 clicks/game — see `PER_PLAYER_METRICS_DECISION.md`).
The facing-direction probe (`tracking/facing_distribution_probe.py`, commit
`1078c8f`) killed the cheap visual fixes: the camera sees the kids fine but
median best digit is ~18 px against a 14 px readability floor — no marker
printed on cloth resolves at 40 m. A worn GPS pod deletes identity as a
problem (it's a serial number) and gives ~100% coverage.

## What the prior research (2026-08-08) established

**The binding constraint is raw positional data access, not accuracy.**
Consumer trackers are closed ecosystems selling summary metrics; raw export is
deliberately a pro-tier upsell (a pod that exports CSV is a pod you only buy
once). Summary-only data would kill heatmaps, thirds, formation, field_tilt —
the things the PWA actually shows.

| Option | Verdict then | 2026 re-check (this doc) |
|---|---|---|
| Catapult One | Only off-the-shelf raw path; ~$2,700/yr rejected | **Confirmed unchanged** — see §A |
| STATSports Apex Athlete | Avoid (closed) | Replaced by "Academy", now one-time $250 — **still zero export** |
| SOCCERBEE | Avoid (retro-paywall) | Confirmed; $6.90/mo mandatory |
| SPT2 | Avoid | Partly wrong then: SPT2 IS FIFA-approved — but its CSV is summary-only, so still dead |
| Playermaker (boot IMU) | Wrong shape (no position) | Unchanged |
| AirTag / BLE / phones | Physically wrong tool | Unchanged |
| RTK | Overkill, ~$3k+ | Unchanged |
| Used Garmin watches | Cheap 1 Hz compromise | **DEAD for games — watches banned on field players (§E)** |
| DIY XIAO ESP32-S3 Sense + L76K | Recommendation then (~$33/pod, 5 Hz) | Still the cheapest path; now has a ready-made rival (§D) |

Key accuracy context (unchanged): 10 Hz consumer GPS ≈ ±1.3% distance, 1–2 m
position; 5 Hz is fine for distance/zones/heatmaps at U10. Validation
literature is on adults; expect somewhat worse on 9-year-olds.

**Threads the prior session left unfinished — now closed:**

1. Used/refurb commercial pods → researched, dead end (§C).
2. STATSports one-time tier export → verified: none (§A).
3. Community reverse-engineering → verified: none exists for current pods (§C).
4. PlayerData → researched: no raw export, ~US$2.1k+/yr (§B).
5. Non-soccer logger category → researched: **the new viable path** (§D).
6. Rules/privacy/logistics → researched: GO for vest pods with conditions (§E).

## Pipeline-compatibility criteria (verified against current code 2026-08-18)

The downstream seam is unchanged: `tracks_df` built in `post_game/pipeline.py`
(`x_m`/`y_m` at ~line 669) → `compute_player_stats` (`post_game/stats.py:214`)
via `identity_by_track`. A new `post_game/gps_ingest.py` must emit that frame.
A tracker is **compatible** iff:

1. **Raw per-device positional stream** (lat/lon + timestamps) at ≥1 Hz,
   ideally 5–10 Hz — summary metrics alone are incompatible.
2. **Programmatic export** — CSV/FIT/GPX/NMEA/JSON/API; per-unit manual steps
   tolerable only at ~1 min × 14; no vendor cloud required to read our own
   data (or at worst a cheap non-expiring one).
3. **Standalone onboard logging**, battery ≥ 90 min.
4. **Fleet-manageable by one volunteer** (charge 14, hand out, collect, offload).
5. **Kid-safe mount**: shoulder-blade vest pocket, junior sizes, ≤~50 g ideal,
   soft enclosure (back-mounted rigid pods measured **142 ± 42 J** fall-impact
   energy vs 5.8 ± 4 J without — Dunn/Hart/James 2018, ISEA).
6. **Cost**: one-time strongly preferred; ~$2,700/yr already rejected;
   target ≲ $150/kid.

Adapters we own either way: (a) per-venue geo-anchor — 4 surveyed points →
2D rigid transform WGS84→field frame (~50 lines, once per venue); (b) clock
sync — GPS time is atomic UTC (14 units inherently synced), align to video via
the coach-confirmed kickoff offsets that already gate analytics.

## 2026 landscape — findings

### A. Consumer per-kid soccer trackers: exactly one raw path, and it's the one we rejected

- **Catapult One** — the raw export is **verified current** (support article
  updated 2026-07-28): 10 Hz CSV of GPS/velocity/acceleration per
  player-session, still **support-enabled per account**, still **no bulk
  export, no API** → 14 manual downloads/game. Hardware is membership-only:
  individual $179 USD/yr; **Team $180/player/yr, 10-player min, 2-year
  commitment → ~$2,520 USD/yr** for 14 ($5,040 locked). No Canadian store
  (shipping unverified). Passes every requirement except cost — it is the
  same recurring model already rejected. **ToS problem for U10:** their own
  FAQ says under-13s can't use the app "regardless of parental consent" —
  per-child accounts are off the table; a coach-managed team account is the
  only conceivable shape and would need Catapult's blessing.
- **STATSports** — Apex Athlete Series is now legacy, replaced by
  **STATSports Academy** (Oct 2025): **$250–340 USD one-time, genuinely no
  subscription**, FIFA-validated 10 Hz pod, free Apex Coach app that
  aggregates a squad and exports **summary-metric CSV only**. **No positional
  export anywhere in the consumer tier** (Apple Health totals only). Raw data
  exists only behind team-tier Sonra (quote-only). Best economics on the
  market, hard-fails requirement 1.
- **SOCCERBEE** — $79–119 hardware + **mandatory $6.90/mo**; the Aug-2025
  retroactive paywall is confirmed via Trustpilot; no export. Disqualified on
  data access and trust.
- **Trace/Traceup** — pivoted to camera-AI ("PlayerFocus", sensorless); GPS
  Tracers officially legacy; used tracer fleets on eBay are cloud-dependent
  paperweights. Do not buy.
- **Hudl Titan** (ex-Titan GPS, acquired June 2025) — quote-only team product
  requiring a Hudl subscription; consumer path gone; no export evidence.
- **Oliver** — €150 + €9.99/mo after year 1; **PDF export only**. Dead.
- **PLAYR/Playertek** — brands dead; pods only work with a Catapult One
  subscription (see §C).
- **SPT GameTraka NXT** — $249.99 one-time, 10 Hz, free base platform,
  FIFA-approved, worldwide shipping — the most promising challenger, and
  **disproved with their own sample file**: the premium-tier CSV export is
  per-segment summary metrics (distance/zones/top speed, one row per player
  per segment), **no lat/lon, no timestamps**. Fails requirement 1.
  (Note: one research pass ranked SPT #1 "pending a sample CSV"; another
  pass actually downloaded the vendor's sample and it contains no
  coordinates — the sample file wins.)

**No 2025–2026 consumer entrant opens raw positional data.** The market moved
the opposite direction.

### B. Team/academy systems: raw data starts at ~$2.5k/yr and goes up

- **PlayerData EDGE** (the never-researched name from `PLAYER_ID_RESEARCH.md`):
  grassroots-focused, ~£10/unit/mo (14 ≈ £1,680/yr ≈ CAD $3,100), youth core
  market, COPPA-compliant under-13 path — but **CSV Builder exports session
  metrics only; no raw positional export or public API found**, and no FIFA
  certification found. Fails requirement 1; would never feed the pipeline.
- **JOHAN Sports** (NL) — advertises "APIs for exporting data to their own
  cloud" and "export raw data" for amateur teams; quote-only, EU-centric,
  no public API docs. The most raw-data-friendly amateur system on paper —
  worth one email if ever revisiting vendor systems, expect >€2k/yr.
- **STATSports Sonra Lite** (Feb 2025, grassroots tier) — the elite Sonra
  ThirdPartyAPI has a proven `getSessionRawData` endpoint (working R client
  on GitHub, pushed 2026-07); whether **Lite** gets API/raw is unverified.
  One phone call could price this; assume summary-only until proven.
- **McLloyd** ($6–9k/yr for 14), **VX Sport** ($4,200/yr, 3-yr term),
  **Fitogether/GPEXE** (pro-tier, quote-only), **Polar Team Pro**
  (discontinued), **Fieldwiz/ASI** (pivoted away; distributor site dead),
  **Beyond Pulse** (no GPS at all — HR belt + step count), **PitcheroGPS**
  ($60/yr/player, summary export) — all fail on price, availability, or data.

**Answer to the key question: no team system delivers documented raw
positional export under ~US$1,500/yr for 14 players.** The split is clean:
youth-priced dashboards withhold raw data; raw-capable systems cost
$2.5k–9k/yr.

### C. Used-pod market & extraction hacks: cheap hardware, no data path

- **Catapult Vector S7/T7** — real lots exist (~$415/pod for a 24-pod kit),
  but pods must be activated against **OpenField** licenses and their raw
  files are proprietary (only OpenField decodes them). No self-activation
  path documented. Paperweights for us.
- **STATSports Apex team pods** — 20-pod lots on eBay, but provisioning goes
  through **Sonra** (quote-priced); no documented revival of an orphaned lot.
- **Apex Athlete (consumer)** — used $108–290, genuinely subscription-free,
  strict 1-pod-1-account pairing (must be unpaired by seller/support) — and
  exports nothing, so irrelevant.
- **PlayerTek** — ~£30 used, some sellers claim "no subscription required" —
  **false since the 2024 EOL**: continued use requires an active Catapult One
  subscription; the legacy app is unsupported and pods reportedly stop
  syncing. The historical PlayerTek raw-CSV path is gone for new users.
- **Reverse engineering** — nothing public for any current pod (searched
  GitHub/code). Closest prior art scripted the PLAYR cloud REST API in 2022;
  that platform is dead. Greenfield BLE/app interception = weeks of
  unsupported, ToS-risky work.
- **Format parsing is a non-problem; access is the problem.** STATSports even
  publishes its raw CSV format + sample + GPS→XY converter on GitHub — usable
  only with a Sonra-tier API key. kloppy has zero GPS-pod formats; floodlight
  has only Kinexon, and its Catapult PR has sat unmerged since 2024.

**Verdict: no credible used-market path to 14 raw-data pods.**

### D. Ready-made GNSS loggers (the category the prior research skipped) — the new path

The motorsport/surveying logger market sells exactly what the soccer industry
withholds: raw high-rate positions, no subscription.

| | Columbus P-1 Mark II | RaceBox Micro | RaceBox Mini S | Qstarz BL-1000GT |
|---|---|---|---|---|
| Rate | 1/5/**10 Hz** (CONFIG.TXT) | **25 Hz** + IMU | 25 Hz | 10 Hz |
| Data out | **CSV/GPX/NMEA on microSD; mounts as USB drive** — no app ever | BLE → free app/cloud → CSV/VBO/GPX | BLE/USB-C, standalone memory | BLE + app/PC |
| Battery | internal, **48 h @1 Hz**, IP66 | **none — external 3.5–16 V** (LITPro sells a worn bundle w/ battery, $200) | internal 20 h, IP54 | internal |
| Size/weight | 55×85×18 mm, **80 g** | 25×40×12 mm, **15 g** bare | dash-puck | small |
| Price | **CAD $189 (CanadaGPS.ca, in stock)** | $129 USD (+power/enclosure) | $289 USD | $239 USD |
| Fleet of 14 | **~CAD $2,650 + SD cards, one-time** | ~$1,810 + $300–1,000 power | ~$4,050 | ~$3,350 |
| Offload/game | plug into USB hub, copy files: **~10–25 min, scriptable** | sequential BLE: **25–45 min + cloud round-trip** | BLE/USB | BLE |

- **Columbus P-1 Mark II is the standout**: ready-made DIY-pod-equivalent —
  10 Hz, files land on the card, timestamps are GPS UTC (fleet inherently
  synced), zero accounts/cloud/subscription, weatherproof, purchasable in
  Canada today. Traps: log **NMEA, not CSV** (the CSV time column has no
  milliseconds at 10 Hz; GGA/RMC carry `hhmmss.sss`); label each card's FAT
  volume with the kid's ID so ingest auto-attributes on mount. Weak point:
  **80 g and credit-card footprint** — double a pro pod; needs a padded
  junior vest pocket and won't be invisible on a 9-year-old.
- **RaceBox Micro** — the best data (25 Hz, 15 g) and a multi-device free
  app, standalone 130-min memory @25 Hz with hardware start button — but no
  internal battery: each unit needs a small LiPo/9V + soft enclosure (that's
  the one semi-DIY step), and offload is per-unit BLE.
- Dead options: VBOX Sport (130 g, ~$580), Dragy (performance-run meter, not
  a session logger), Locosys/Motion speedsurf loggers (discontinued/waitlist
  — the GP3S goldmine has run dry), CatLog/i-gotU (1 Hz / 5–10 m error /
  vendor dead), SM Modellbau (external power, clunky), COROS watches (price,
  per-device accounts — and watches are banned anyway, §E).
- **Body-worn caveat for all**: patch antennas want sky view; shoulder-blade
  mounting works (same placement as commercial pods; LITPro proves the Micro
  on motocross bodies) but expect some degradation vs open-sky specs —
  exactly what the pilot measures.

### E. Rules, safety, privacy, logistics (Ontario, U10)

- **Law 4 (2026/27)**: wearable EPTS is permitted subject to the
  **competition organiser** ensuring it's not dangerous (FIFA Quality
  Programme framework); separately, players may wear **no electronic
  equipment except approved EPTS**. The 2026-07 accessories liberalization
  does not rescue watches — youth-ref guidance (e.g., AYSO 26/27) still
  treats **watches as dangerous → remove**. **Vest pods: permissible with
  organiser sign-off + ref acceptance. Watches on field players: no.**
- **Ontario reality**: no Ontario Soccer EPTS policy exists; the circulating
  equipment directive is from 2006 and silent on trackers. The practical
  authority chain for us: club (LaSalle Stompers) → district (**Essex County
  Soccer Association** — note: could not verify "Sun County" as a district;
  evidence points to ECSA) → written approval to show refs, plus a pre-game
  word with the ref every match (they retain "dangerous equipment"
  discretion).
- **Safety**: the 142 J impact study is Dunn, Hart & James 2018 (ISEA,
  Sheffield Hallam) — mock pod vs bare back ≈ 25× impact energy on backward
  falls. Mitigate: snug junior vest (loose vests migrate), rounded/padded
  enclosure, never a hard-edged box at the spine. FIFA Basic (ex-IMS) is the
  safety mark — Catapult/PlayerTek/PLAYR, STATSports Apex, SPT2 carry it;
  Columbus/RaceBox obviously don't, so the enclosure + league conversation
  carries the safety burden. Junior vests exist small enough (STATSports
  Youth S/XS from age 6/7, Youth M 69–74 cm ≈ age 8/9; SOCCERBEE vests to
  XXS on Amazon.ca; generic junior GPS vests ~$30–60).
- **Privacy**: the vendor cloud is PIPEDA-covered; OPC guidance — under-13s
  can't meaningfully consent (parents must), and **location data is
  sensitive → express signed opt-in**, not a group email. Vendor ToS floors:
  Catapult One effectively bans under-13 app use outright; STATSports is
  parent-account-only; PlayerData is the only explicit supervised under-13
  path. **Cleanest architecture = local-only data (loggers/DIY) — no vendor
  cloud, no per-child accounts; names never leave our Firestore**, which
  already has parent-scoped access controls. Consent form must state:
  what's collected (on-field position/speed only, games/practices only),
  where stored, who sees it (each family: own child only; never for team
  selection), retention (season + N months), opt-out (no minutes impact),
  and risks.
- **Fleet logistics** (from team-system practice): **fixed pod↔kid pairing**
  (inter-unit variability makes per-kid consistency a measurement
  requirement, not tidiness) — number pods AND vests, pair permanently;
  charge the night before on a multi-port USB hub in a foam case; vests
  pre-stuffed and powered on in a numbered shoe-organizer bag; collect at
  the handshake **before snacks**, count 14/14 aloud; **coach washes vests,
  pods out** (pods die in washing machines); watch for kids swapping vests
  (corrupts identity AND unit-consistency) and pods left in hot cars.

## Gap analysis: prior research vs now

| # | Prior state (2026-08-08) | Now (2026-08-18) |
|---|---|---|
| 1 | Catapult One raw export assumed durable, price rejected | Confirmed intact (article updated Jul 2026), $2,520/yr + 2-yr lock, still support-gated per-session; **new blocker: under-13 ToS ban** |
| 2 | STATSports "closed, avoid" | Now one-time $250 (Academy) — economics fixed, **data still sealed**; verdict unchanged |
| 3 | Used-pod thread unchased | Closed: **no path** — every raw-capable family is license-locked; PlayerTek killed 2024 |
| 4 | PlayerData never researched | Closed: metrics-only platform, no raw export/API — dead for us |
| 5 | Only cheap raw path = DIY build (XIAO+L76K, ~$33, 5 Hz, soldering + enclosure work) | **New: ready-made Columbus P-1 MkII, 10 Hz, CAD $189, in Canadian stock — no build, no subscription**; RaceBox Micro 25 Hz/15 g as the high-fidelity variant |
| 6 | Used Garmin watches listed as viable cheap option | **Dead for games — Law 4 bans electronic equipment on players except approved EPTS; refs remove watches**. Practice-only |
| 7 | "League permission + parent consent" as a checklist bullet | Concrete: ECSA district route, written organiser approval + per-match ref word; OPC sensitive-location consent standard; vendor ToS age floors mapped |
| 8 | SPT dismissed on FIFA-list rumor | Corrected mechanism: SPT2 IS FIFA-approved, but its premium CSV is **per-segment summaries with no coordinates** (vendor's own sample file) — still dead, right reason now |

Net: the prior conclusion — *raw data access is the moat; own the stream* —
is **confirmed and strengthened**. What changed is the execution menu: the
choice is no longer "Catapult's price vs a soldering project"; it's
**ready-made loggers (CAD ~$190/kid, one-time) vs DIY (~$45/kid, more labor)
vs Catapult One Team (US$2,520/yr, ToS-encumbered)**.

## Recommendation

1. **Free and blocking (do first):** written approval from the club/league
   (ECSA route) for soft GPS vests with a shoulder-blade pod; draft the
   parent consent form (§E terms). Nothing should be bought before these.
2. **Pilot ≈ CAD $250–450 (decision instrument, not a fleet):** buy
   **1× Columbus P-1 Mark II** (CAD $189, CanadaGPS.ca) — optionally add
   **1× RaceBox Micro + LITPro worn bundle** if we want the 25 Hz comparison
   — plus 2 junior GPS vests from Amazon.ca. Configure P-1 to 10 Hz NMEA.
   One volunteer kid (ours) wears it for one game alongside the camera.
3. **Write `post_game/gps_ingest.py`** against the pilot files: NMEA →
   lat/lon @10 Hz → per-venue 4-point rigid transform → `tracks_df` +
   `identity_by_track`; reconcile against `read_analytics(game_id)` per the
   verify-against-production-first rule. (~150 lines; the downstream stack
   is untouched.)
4. **Fleet decision on measured evidence:**
   - Pilot validates + budget OK → **14× P-1 MkII ≈ CAD $2,650 one-time**,
     forever-free data, local-only privacy story.
   - Budget tight → port the proven ingest to the **DIY XIAO+L76K** build
     (~CAD $700 for 14, 5 Hz) — the ingest doesn't care which board made
     the fix.
   - Weight/bulk is the blocker on a 9-year-old → RaceBox Micro fleet
     (15 g pod + small battery) accepting the BLE offload tax.
5. **Do NOT:** buy used PlayerTek/Vector/Apex-team lots; put watches on
   field players; open per-child vendor accounts; re-litigate soccer-branded
   consumer products — as of Aug 2026 they are all summary-metric silos.

The camera stays — reel, events, team shape. The pods take over the one job
the camera measurably cannot do: continuous, named, per-kid position.

## Key unverified items (flagged honestly)

- Catapult One Canada shipping; exact CAD prices on Amazon.ca listings
  (fetches blocked); current consolidated FIFA certified-device list.
- Catapult One raw-CSV exact column list (article doesn't enumerate it).
- Sonra Lite raw-API inclusion (one sales call answers it).
- JOHAN Sports pricing (quote-only).
- "Sun County" vs ECSA as our actual district — confirm with the club.
- Columbus P-1 MkII on-torso accuracy — that's precisely what the pilot
  measures.
