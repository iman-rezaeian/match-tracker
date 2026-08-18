"""No React hook may be called after an early return.

React identifies hooks by CALL ORDER, so a hook below a conditional `return`
makes the hook COUNT depend on that condition. The first render bails early
having run N hooks; a later render goes further and runs N+1; React throws
error #310 ("rendered more hooks than during the previous render") and the whole
subtree unmounts to a blank screen.

That is exactly how the DUGOUT went black on 2026-08-15: `bandHist = useMemo(...)`
in `CoachApp` sat below `if (!unlocked)` and `if (!loaded)`. It had been latent for
as long as those returns existed — nothing detects it until a render actually
stops at the early return and then continues past it on a subsequent pass.

A grep-level test is the right tool here: the failure is a static property of the
source (hook lexically after a conditional return), there is no JS test harness in
this repo, and the runtime symptom only appears behind Google auth on a deployed
build, which cannot be reached locally.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
JSX = REPO / "soccer_team_app.jsx"

HOOK_CALL = re.compile(r"=\s*(useState|useMemo|useEffect|useRef|useCallback|"
                       r"useImperativeHandle|useReducer|useContext)\s*\(")
# Bare `useEffect(...)` / `useModalHistory(...)` with no assignment also counts.
HOOK_BARE = re.compile(r"^\s{2,}(useEffect|useLayoutEffect|useModalHistory)\s*\(")
# A conditional early return at the top level of a component body: either
# `  if (x) return ...` on one line, or a `  if (x) {` block containing a return.
IF_ONELINE_RETURN = re.compile(r"^  if \(.*\)\s*return\b")
IF_BLOCK = re.compile(r"^  if \(.*\)\s*\{$")


def _components() -> list[tuple[str, int, list[str]]]:
    """[(name, first_line_no, body_lines)] for each top-level function.

    Splitting on `^function` alone mis-attributes hooks: a plain helper declared
    just above a component swallows that component's body, so its hooks look like
    they follow the helper's own early return. Bound each body at the next
    top-level declaration of ANY kind, and keep only functions that actually call
    a hook at their own top level (i.e. real components/custom hooks).
    """
    src = JSX.read_text()
    starts = [(m.start(), m.group(1))
              for m in re.finditer(r"^function (\w+)\(", src, re.M)]
    # Any line at column 0 that ends a function body: `}` alone.
    out = []
    for i, (pos, name) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(src)
        body = src[pos:end]
        # Trim at the first column-0 `}` — the function's own closing brace — so a
        # trailing helper or comment block is not counted as part of this body.
        m = re.search(r"^\}", body, re.M)
        if m:
            body = body[:m.end()]
        lines = body.split("\n")
        if not any(HOOK_CALL.search(l) or HOOK_BARE.match(l) for l in lines):
            continue
        out.append((name, src[:pos].count("\n") + 1, lines))
    return out


def _first_early_return(lines: list[str]) -> int | None:
    """Index of the first top-level conditional return, or None."""
    for i, line in enumerate(lines):
        if IF_ONELINE_RETURN.match(line):
            return i
        if IF_BLOCK.match(line):
            # A return within the guarded block (4-space indent), before it closes.
            for probe in lines[i + 1:i + 10]:
                if re.match(r"^  \}", probe):
                    break
                if re.match(r"^    return\b", probe):
                    return i
    return None


def test_no_hook_is_called_after_an_early_return():
    offenders: list[str] = []
    for name, start_line, lines in _components():
        cut = _first_early_return(lines)
        if cut is None:
            continue
        for j, line in enumerate(lines[cut + 1:], start=cut + 1):
            # Only the component's own top level; a hook inside a nested
            # function/callback (deeper indent) is not part of its hook order.
            if not (HOOK_CALL.search(line) and re.match(r"^  \S", line)) \
                    and not HOOK_BARE.match(line):
                continue
            if not re.match(r"^  (const|let|var)?\s*\w", line):
                continue
            offenders.append(
                f"{name} (jsx:{start_line + j}): {line.strip()[:90]}")
    assert not offenders, (
        "React hook(s) called after an early return — the hook count becomes "
        "conditional and React throws #310 (blank screen):\n  "
        + "\n  ".join(offenders))


def test_coach_app_computes_bandhist_before_its_guards():
    """Pin the specific regression that black-screened the DUGOUT."""
    name_to = {n: (s, b) for n, s, b in _components()}
    assert "CoachApp" in name_to
    _, body = name_to["CoachApp"]
    hook = next(i for i, l in enumerate(body) if "bandHist = useMemo(" in l)
    guard = next(i for i, l in enumerate(body) if l.startswith("  if (!unlocked)"))
    assert hook < guard, (
        "bandHist useMemo must stay ABOVE the !unlocked/!loaded early returns; "
        "below them the hook count depends on auth state (React #310)")


if __name__ == "__main__":
    import traceback
    bad = 0
    for k, v in sorted(globals().items()):
        if k.startswith("test_"):
            try:
                v()
                print(f"ok   {k}")
            except Exception:
                bad += 1
                print(f"FAIL {k}")
                traceback.print_exc()
    raise SystemExit(1 if bad else 0)
