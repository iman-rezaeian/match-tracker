"""Which position was a player in at a given moment.

The coach rotates children through positions constantly at this age — measured
across three games, 9 to 12 of 12 players changed role within a single match,
with a median board move of over half the pitch. So a whole-match per-player
heatmap blends two or three different jobs into one blob, and the split is
worth making.

The signal is already in the data: the tactical board writes a POSITION event
(normalized x, y) on every drag, 45-68 per game, and SUB events bound who was
on the field. This module turns those into ROLE STINTS on the game clock, which
is the unit both the position timeline and the role-split heatmaps consume.

⚠ A role here is the coach's INSTRUCTION, not measured position: it says where
the child was put on the board, not where he ran. That is deliberate (the gap
between the two is itself informative) but it means a stint label must never be
presented as an observation.

Granularity is FIVE roles. Measured on the two click-sampled games: nine roles
(3 bands x 3 lanes) left almost no role above the click threshold, three bands
were coarse enough to collapse most players' moves into a single bucket, and
five preserves the wide/central midfield distinction — which is most of how the
rotation actually works — at no cost in coverage versus four.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

# Depth bands on the coach board. `y` is 0 at the halfway line and 1 at our own
# goal, so depth = 1 - y is "how far up the pitch".
_DEF_MAX = 0.36
_MID_MAX = 0.68
# Central lane for the midfield split.
_CENTRE_LO, _CENTRE_HI = 0.30, 0.70

ROLES = ("GK", "DEF", "MID-W", "MID-C", "FWD")
# Stints shorter than this are board-drag noise (a correction a second later),
# not a spell in a position.
MIN_STINT_S = 20.0


@dataclass(frozen=True)
class RoleStint:
    player_id: str
    start_s: float          # game clock, both halves continuous
    end_s: float
    role: str

    @property
    def seconds(self) -> float:
        return max(0.0, self.end_s - self.start_s)


def role_from_board(x: float, y: float) -> str:
    """Board coords -> one of ROLES (never GK; the keeper is known separately)."""
    depth = 1.0 - float(y)
    if depth < _DEF_MAX:
        return "DEF"
    if depth < _MID_MAX:
        return "MID-C" if _CENTRE_LO <= float(x) <= _CENTRE_HI else "MID-W"
    return "FWD"


def _clock(period: int, elapsed: float, half_s: float) -> float:
    return (max(1, int(period)) - 1) * half_s + min(float(elapsed or 0.0), half_s)


def build_stints(
    events: Iterable[Any],
    half_s: float,
    starting_lineup: Optional[list[str]] = None,
    gk_player_id: Optional[str] = None,
) -> dict[str, list[RoleStint]]:
    """{player_id: [RoleStint, ...]} from POSITION + SUB events.

    On-field spans come from SUB events (a starter's span opens at kickoff);
    within each span the role changes at every board drag. The keeper is
    labelled GK for his whole on-field time rather than by board position — the
    board puts him deep, which would read as DEF and hide that he kept.
    """
    evs = list(events or [])
    board: dict[str, list[tuple[float, str]]] = {}
    on_off: dict[str, list[tuple[str, float]]] = {}

    for e in evs:
        etype = (getattr(e, "type", None) or "").upper()
        pid = getattr(e, "player_id", None)
        extras = getattr(e, "extras", None) or {}
        try:
            t = _clock(int(getattr(e, "period", 1) or 1),
                       float(getattr(e, "elapsed", 0) or 0), half_s)
        except (TypeError, ValueError):
            continue
        if etype == "POSITION" and pid:
            x, y = extras.get("x"), extras.get("y")
            if x is None or y is None:
                continue
            try:
                board.setdefault(pid, []).append((t, role_from_board(float(x), float(y))))
            except (TypeError, ValueError):
                continue
        elif etype == "SUB":
            if pid:
                on_off.setdefault(pid, []).append(("off", t))
            son = extras.get("subOnPlayerId")
            if son:
                on_off.setdefault(son, []).append(("on", t))

    lineup = set(starting_lineup or [])
    full = half_s * 2.0
    out: dict[str, list[RoleStint]] = {}

    for pid in set(board) | set(on_off) | lineup:
        spans: list[tuple[float, float]] = []
        cur: Optional[float] = 0.0 if pid in lineup else None
        for kind, t in sorted(on_off.get(pid, []), key=lambda r: r[1]):
            if kind == "on" and cur is None:
                cur = t
            elif kind == "off" and cur is not None:
                spans.append((cur, t))
                cur = None
        if cur is not None:
            spans.append((cur, full))

        marks = sorted(board.get(pid, []))
        stints: list[RoleStint] = []
        for a, b in spans:
            if pid and pid == gk_player_id:
                stints.append(RoleStint(pid, a, b, "GK"))
                continue
            # role in force at the span's start = last drag at or before it,
            # falling back to the first drag ever seen (a player subbed on
            # before his first drag still has a position).
            role = next((r for t, r in reversed(marks) if t <= a),
                        marks[0][1] if marks else None)
            if role is None:
                continue
            t0 = a
            for mt, mr in marks:
                if a < mt < b and mr != role:
                    stints.append(RoleStint(pid, t0, mt, role))
                    t0, role = mt, mr
            stints.append(RoleStint(pid, t0, b, role))
        kept = [s for s in stints if s.seconds > MIN_STINT_S]
        if kept:
            out[pid] = kept
    return out


def role_at(stints: list[RoleStint], game_clock_s: float) -> Optional[str]:
    for s in stints:
        if s.start_s <= game_clock_s <= s.end_s:
            return s.role
    return None


def minutes_by_role(stints: list[RoleStint]) -> dict[str, float]:
    out: dict[str, float] = {}
    for s in stints:
        out[s.role] = out.get(s.role, 0.0) + s.seconds / 60.0
    return {k: round(v, 1) for k, v in sorted(out.items(), key=lambda kv: -kv[1])}
