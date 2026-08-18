"""Compare two half_split_r runs HONESTLY — on the players both of them named.

The trap this exists to prevent
-------------------------------
The headline r from `tracking.half_split_r` is computed over whatever players a
run managed to name. Two runs therefore usually score DIFFERENT player sets, and
a change that simply drops the hard-to-name children raises r without improving
anything. Measured on gap-split (2026-08-05):

    pooled headline   baseline +0.396  ->  gap-split 30s +0.572   (looks like a win)
    same 11 players   baseline +0.733  ->  gap-split 30s +0.370   (it is a loss)

The composition effect was larger than the effect being measured, and it pointed
the wrong way. So: never compare headlines. Compare on the intersection.

Usage:
    .venv-post-game/bin/python -m tracking.half_split_compare \
        --before baseline --after gs30 \
        --games mri01pvelv46d mqcf9axlvtuyt
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

OUT_DIR = Path(__file__).resolve().parent / "outputs" / "identity_eval"
N_BOOT = 20000


def _load(game_id: str, label: str) -> dict | None:
    p = OUT_DIR / f"{game_id}.halfr.{label}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def _paired_ids(d: dict) -> set[str]:
    return set(d["work_rate_h1"]) & set(d["work_rate_h2"])


def _z(v: np.ndarray) -> np.ndarray:
    return (v - v.mean()) / v.std() if v.std() > 0 else v - v.mean()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", required=True, help="label of the baseline run")
    ap.add_argument("--after", required=True, help="label of the changed run")
    ap.add_argument("--games", nargs="+", required=True)
    args = ap.parse_args()

    rng = np.random.default_rng(0)
    A: list[float] = []   # before, H1
    B: list[float] = []   # before, H2
    C: list[float] = []   # after,  H1
    D: list[float] = []   # after,  H2
    rows = []
    for gid in args.games:
        b, a = _load(gid, args.before), _load(gid, args.after)
        if b is None or a is None:
            print(f"  ! {gid}: missing a run ({args.before if b is None else args.after})")
            continue
        common = sorted(_paired_ids(b) & _paired_ids(a))
        only_before = sorted(_paired_ids(b) - _paired_ids(a))
        only_after = sorted(_paired_ids(a) - _paired_ids(b))
        if len(common) < 3:
            print(f"  ! {gid}: only {len(common)} common players — skipped")
            continue
        ba = np.array([b["work_rate_h1"][p] for p in common])
        bb = np.array([b["work_rate_h2"][p] for p in common])
        aa = np.array([a["work_rate_h1"][p] for p in common])
        ab = np.array([a["work_rate_h2"][p] for p in common])
        rows.append((gid, len(common), len(only_before), len(only_after),
                     float(np.corrcoef(ba, bb)[0, 1]), float(np.corrcoef(aa, ab)[0, 1])))
        A += list(_z(ba)); B += list(_z(bb))
        C += list(_z(aa)); D += list(_z(ab))

    if not rows:
        raise SystemExit("no comparable games")

    print(f"=== {args.before}  ->  {args.after} ===")
    print(f"{'game':<16}{'common':>7}{'dropped':>9}{'added':>7}"
          f"{'r before':>10}{'r after':>9}{'delta':>8}")
    for gid, nc, nd, na, rb, ra in rows:
        print(f"{gid:<16}{nc:>7}{nd:>9}{na:>7}{rb:>+10.3f}{ra:>+9.3f}{ra - rb:>+8.3f}")

    A, B, C, D = map(np.asarray, (A, B, C, D))
    n = len(A)
    r_b = float(np.corrcoef(A, B)[0, 1])
    r_a = float(np.corrcoef(C, D)[0, 1])

    # Bootstrap the DIFFERENCE on paired players (resample players, not halves)
    # so the interval answers "did this change anything", not "what is r".
    diffs = []
    for _ in range(N_BOOT):
        k = rng.integers(0, n, n)
        if A[k].std() == 0 or B[k].std() == 0 or C[k].std() == 0 or D[k].std() == 0:
            continue
        diffs.append(np.corrcoef(C[k], D[k])[0, 1] - np.corrcoef(A[k], B[k])[0, 1])
    diffs = np.array(diffs)
    lo, hi = np.percentile(diffs, [2.5, 97.5])

    print()
    print(f"POOLED on the SAME {n} player-halves:")
    print(f"  {args.before:<12} r = {r_b:+.3f}")
    print(f"  {args.after:<12} r = {r_a:+.3f}")
    print(f"  delta        = {r_a - r_b:+.3f}   95% CI [{lo:+.3f}, {hi:+.3f}]")
    if lo > 0:
        print("  -> IMPROVEMENT (interval excludes zero)")
    elif hi < 0:
        print("  -> REGRESSION (interval excludes zero)")
    else:
        print("  -> not resolvable: the interval spans zero")

    n_drop = sum(r[2] for r in rows)
    if n_drop:
        print(f"\n  NOTE: '{args.after}' failed to name {n_drop} player(s) that "
              f"'{args.before}' named.\n  Those are excluded above by design — but a "
              f"change that drops players is\n  paying for r with coverage, which is "
              f"not a win. Check them before shipping.")


if __name__ == "__main__":
    main()
