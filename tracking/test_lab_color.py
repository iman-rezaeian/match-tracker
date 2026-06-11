"""Red-kit wrap test for the Lab-based team classifier.

OpenCV hue is circular (0-179): red sits at BOTH ends (h≈0 and h≈178). The
old Euclidean-on-HSV classifier put the two halves of a red kit ~175 hue
units apart — splitting one red team across clusters and mis-ranking anchor
distances. In Lab they are the same color. This test feeds two red tracks
sampled from opposite sides of the wrap plus a blue track, and requires the
supervised 3-anchor path to put both reds on team 0 and blue on team 1.

Run:  .venv-post-game/bin/python -m tracking.test_lab_color
"""

import numpy as np
import pandas as pd

from post_game.team_classifier import classify_tracks


def _hsv_samples(h: int, s: int = 230, v: int = 200, n: int = 50) -> np.ndarray:
    arr = np.zeros((n, 3), dtype=np.float32)
    arr[:, 0] = h + np.random.RandomState(h).randint(-2, 3, size=n)
    arr[:, 1] = s
    arr[:, 2] = v
    arr[:, 0] = np.clip(arr[:, 0], 0, 179)
    return arr


def main() -> None:
    tracks_df = pd.DataFrame({"track_id": [1, 2, 3]})
    samples = {
        1: [_hsv_samples(2)],     # red, low side of the hue wrap
        2: [_hsv_samples(177)],   # same red, high side of the wrap
        3: [_hsv_samples(115)],   # blue
    }
    out = classify_tracks(
        tracks_df, samples,
        our_home_color_hex="#FF0000",   # red kit (us)
        opp_color_hex="#0000FF",        # blue kit (opponent)
        ref_color_hex="#FFFF00",        # yellow ref → supervised 3-anchor path
    )
    assert out[1] == 0, f"red (h=2) should be OUR team, got {out[1]}"
    assert out[2] == 0, f"red (h=177, across the wrap) should be OUR team, got {out[2]}"
    assert out[3] == 1, f"blue should be OPPONENT, got {out[3]}"
    print("ok: red kit classified consistently across the hue wrap; blue is opponent")


if __name__ == "__main__":
    main()
