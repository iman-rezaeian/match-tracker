# Indoor (UWB) per-player tracking — deep research & recommendation

Context: **~70% of our season is played indoors** (coach, 2026-08-18), where GPS
does not work at all — so `GPS_TRACKER_RESEARCH.md` covers only the outdoor
minority of games, and the indoor question is the primary one. Trigger was a
coach-supplied DIY guide (DWM1001 + PANS + Raspberry Pi listener): right
concept, dead platform. Researched 2026-08-18 (two parallel passes: DIY UWB
landscape + commercial indoor RTLS market). Requirements identical to the GPS
doc: per-kid raw `(id, timestamp, x, y)` at ≥2 Hz (5–10 ideal) feeding
`tracks_df`, one-time cost, fleet run by one volunteer, tags kid-safe in a
shoulder-blade pouch, setup ≤15 min in a rented facility.

## TL;DR

- The coach's DWM1001 guide is unbuildable as written: **Qorvo EOL'd the whole
  DW1000 family** (PCN 25-0154, last-time-buy June/July 2026 — passed; DigiKey
  shows zero stock, restock quoted Feb 2027). PANS firmware is closed, frozen,
  and only runs on that dead silicon.
- **Both research passes independently converged on the same living answer:
  Makerfabs MaUWB (DW3000-based, STM32 TDMA "AT" firmware).** ~**CAD
  $1,150–1,480 one-time** for 6 anchors + 10–14 tags, ~6–7 Hz per tag at
  14 tags, positions solved in our own Python from anchor-range streams —
  no cloud, no subscription, no vendor account.
- Everything turnkey costs 2–4×: Marvelmind IA ~CAD $3,100 (ultrasonic —
  occlusion risk in kid scrums), LEAPS RTLS (the real PANS successor, by the
  original Decawave team) ~US$1,700–2,500, enterprise UWB kits US$4.4k+,
  sports LPS (Kinexon/ClearSky/Gengee) pro-priced. Pozyx is gone; BLE AoA is
  an integration project with worse accuracy on moving bodies.
- **The same MaUWB system plausibly stretches to the outdoor pitch**
  (PA/LNA variants, ~100 m demonstrated outdoors; 6–8 tripod anchors on a
  36×55 m U10 field) — if a range test passes, ONE system covers 100% of the
  season and GPS pods become unnecessary.
- **Next step costs ~US$110:** buy 2 MaUWB boards, run a ranging test on our
  actual indoor turf (and the outdoor pitch), before committing to a fleet.

## Why the DWM1001 guide fails (evaluated 2026-08-18)

1. **Dead platform.** EOL announced 2025-12-04 ("lack of demand"), last-time-buy
   2026-06/07, last-ship 2026-12-15
   ([PCN 25-0154](https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/8458/PCN_25-0154.pdf)).
   Only grey-market/AliExpress stock remains (~US$31–35/module); PANS is a
   closed binary with "no firmware updates, limited support" per
   [LEAPS' own comparison](https://docs.leapslabs.com/leaps-solutions/comparison/).
   Building a 21-node season-critical system on it = no path when boards die.
2. **Capacity math error in the guide.** PANS caps ~150 Hz aggregate TWR →
   16 tags × 10 Hz = 160 Hz exceeds it; it was really a 5 Hz (or ≤15-tag)
   system. Its single UART listener at 115200 baud is also marginal at rate.
3. **Cost.** Honest BOM ≈ US$1,250–1,350 (CAD ~$1,700–1,850) — more than the
   already-rejected Columbus GPS fleet.
4. What the guide got **right**: UWB is the correct indoor physics; anchors
   define the pitch frame so output is *already* `x_m, y_m` (no geo-transform,
   unlike GPS); tag ID = identity by hardware; CSV logging to one gateway
   matches our ingest model exactly.

## The living option space (Aug 2026)

| Option | 6 anchors + 10–14 tags | Per-tag rate @14 | Data access | Status |
|---|---|---|---|---|
| **Makerfabs MaUWB (DW3000)** | **~US$840–1,080 / CAD ~$1,150–1,480** | **~6–7 Hz** (TDMA, `AT+SETCAP`) | serial/WiFi range stream → our Python solves x,y | ✅ in stock, firmware v1.1.6, new variant shipped Jul 2026 |
| LEAPS RTLS on QM33120WDK2 (ex-Decawave team; true PANS successor) | ~US$1,700–2,500 (kits of 6) | 10+ Hz (UL-TDoA, 600 Hz aggregate) | turnkey RTLS, local | ✅ but 2–3× budget; licensing outside kits opaque |
| Dead-stock PANS (MDEK1001/AliExpress) | ~US$500–1,300 if findable | 10 Hz | `lec/lep` CSV listener | ⚠️ EOL lottery; only if a cheap 12-pack appears before Dec 2026 |
| Marvelmind IA (ultrasonic; Mini-RX/Badge tags) | ~€2,023+ / CAD ~$3,100+ | ~4–8 Hz, independent of tag count | local engine, CSV/Python/MQTT — genuinely good | ✅ but ultrasonic LOS: clustered kids = occlusion; no published team-sport precedent |
| UbiTrack Personnel Kit (cheapest turnkey UWB found) | ~US$2,900 / CAD ~$4,000 | unverified | local platform, API unverified | ✅, 3× budget |
| Pozyx / Sewio / Eliko / u-blox AoA | US$4.4k+ / quote-only / integration project | — | — | ❌ out |
| Kinexon / ClearSky / ShotTracker / Gengee | pro-priced (quote, ~$10k+) | pro-grade | pro APIs | ❌ out (Gengee INSAIT KS is futsal-specific — worth one price email, expect $5k+) |
| Bitcraze Loco (TDoA, arch. ideal) | $180/anchor, tag EOL | unlimited tags | open firmware | ❌ price + DW1000 silicon |
| ESP32-UWB roll-your-own firmware | ~$40/board | — | — | ❌ writing a 14-tag TDMA = the thing Makerfabs already sells |

## Recommended stack: Makerfabs MaUWB

Hardware ([MaUWB_ESP32S3](https://www.makerfabs.com/mauwb-esp32s3-uwb-module.html)
$54.80 anchors — USB-C, LiPo charging, WiFi; MaUWB_DW3000 bare chipset $38.90
(~$30 bulk) tags — 30×21 mm, ~5 g + 500 mAh LiPo ≈ ~15–20 g in a foam pouch):

| Fleet | Cost |
|---|---|
| 6 anchors + 10 tags + LiPos/pouches/power banks | **≈ US$840 ≈ CAD $1,150** |
| 8 anchors + 14 tags (full roster, both waves) | ≈ US$1,080 ≈ CAD $1,480 |

- **Firmware**: vendor STM32 TDMA "AT" firmware (closed but actively
  maintained, v1.1.6; 8 anchors + 64 tags; unlimited-anchor auto-select since
  v1.1.3). `AT+SETCAP=14,10` → ~7.1 Hz/tag, each slot ranges to all 8 anchors
  ([AT manual in repo](https://github.com/Makerfabs/MaUWB_ESP32S3-with-STM32-AT-Command)).
- **Data path**: one USB-connected node streams `AT+RANGE=tid:..,range:(r0..r7)`
  for every tag; vendor's own `position.py` demo already does
  laptop-side parse→trilaterate→CSV. We replace their 2-circle solver with
  robust least-squares multilateration + outlier gating + constant-velocity
  filter → `tracks_df` directly (anchor tripods surveyed by tape measure ARE
  the field frame; clock is the laptop's, synced to video at kickoff as usual).
- **Measured accuracy** ([independent CNX review](https://www.cnx-software.com/2024/04/16/mauwb_dw3000-with-stm32-at-command-review-arduino-uwb-range-precision-indoor-positioning/)):
  ~1–3 cm LOS, ~17 cm with a body blocking, ~20 m/link indoors at default
  power (raisable), ~100 m outdoors. Realistic expectation with 8 moving kids
  and 6–8 anchors: **~0.1–0.5 m sustained, with NLOS outliers in scrums** —
  more than enough for distance/heatmaps/thirds, and identity is free.
- **Effort**: no PCB design. ~1 weekend to first fix; 2–3 more for the
  14-tag config, logger, anchor-survey routine, vest pouches. Per-game setup:
  place tripods, power on, start logger — ≤15 min once practiced.

**Risks, stated plainly:** the TDMA firmware is a closed binary from one small
Shenzhen vendor, with **no published independent test at 10+ simultaneous
tags** — we would be first through that gap (mitigant: TDMA slots are
deterministic; 14 tags = 14 slots, not contention). Qorvo's UWB business
itself is wobbly (Skyworks merger) — buy 2–4 spare boards with the fleet.

## Outdoor stretch (could this replace GPS entirely?)

Plausible but unproven at DIY grade: PA/LNA variants demonstrated ~100 m
outdoors (vendor claims more); a 36×55 m pitch with 6–8 anchors on 2.5–3 m
tripods (corners + mid-lines) keeps most tag–anchor links well under that.
The physics threat is **body-blocking** (a torso can fully attenuate UWB;
NLOS errors reach meters) — pro outdoor UWB (Kinexon-class) solves it with
anchor density and height. U10 bodies are small and the shoulder-blade mount
is best-case. **Verdict: worth testing before buying any GPS hardware — but
do not skip GPS on faith.** If the outdoor test fails, GPS pods/rotation
(see `GPS_TRACKER_RESEARCH.md`) remain the answer for the outdoor 30%.

## Rules / safety / privacy (indoor delta from the GPS doc)

- Indoor rec leagues: same Law-4-style organiser-approval + ref-discretion
  logic; facility permission needed for 6 corner/sideline tripods (ask the
  facility AND the league). Tags ~15–20 g in a padded scapula pouch are an
  easier safety story than 80 g GPS loggers.
- Privacy is the cleanest possible: fully local (laptop logger), no vendor
  cloud, no accounts — names never leave our Firestore. Parent consent form
  still required (location of minors = sensitive; express opt-in).

## Decision plan

1. **Bench test — ~US$110, do first**: 2× MaUWB boards; verify ranging on our
   actual indoor turf (RF environment, netting, metal roof) and a 2-anchor
   range check on the outdoor pitch. Kill criteria: <15 m reliable indoor
   link range, or firmware instability.
2. **Pilot — ~US$350**: +4 boards → 4 anchors + 2 tags on 2 kids for one
   indoor game; write `post_game/uwb_ingest.py` (parse → multilaterate →
   `tracks_df`); reconcile against video analytics where the camera does
   track those kids.
3. **Fleet — to ~US$840–1,080 total**: scale to 6–8 anchors + 10–14 tags.
   Buy spares. Decide GPS separately only if the outdoor stretch test failed.
4. League/facility approval + parent consent before any kid wears one
   (same checklist as `GPS_TRACKER_RESEARCH.md` §E).

## Key unverified items

- MaUWB behaviour at 10–14 *simultaneous moving* tags (nobody has published
  it; our pilot would be the first real test).
- Exact CAD landed cost (Makerfabs ships from Shenzhen; duties apply).
- LEAPS licensing terms for self-bought DWM3001CDK boards (fallback path).
- Marvelmind IA real-world performance on clustered children (vendor LOS
  requirement suggests trouble; no team-sport precedent found).
- Indoor facility tripod permission — ask before the bench test game.
