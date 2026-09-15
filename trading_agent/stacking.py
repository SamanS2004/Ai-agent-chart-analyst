"""Treats several FVGs that formed back to back during one strong push as a
single continuous imbalance, and picks the one zone from that stack worth
actually trading, instead of offering every gap in it as a separate
opportunity.

Priority within a stack, in order:
1. Discount/premium (hard filter): a bullish FVG only counts if its
   midpoint sits in the lower half of the recent range (discount); a
   bearish one only in the upper half (premium). Fails this -> dropped,
   even if otherwise valid.
2. Structurally nearest member first (highest top for a bullish stack,
   lowest bottom for a bearish one -- the last gap formed during the push,
   so the first one price reaches on a retracement). If THAT nearest gap
   has already been traded through, the whole stack is disqualified for
   this cycle -- no falling back to a deeper gap that happens to still be
   technically untouched. Detection is stateless (recomputed fresh each
   cycle), so "the nearest one already failed, try the next" can't be told
   apart from "still approaching" any other way; disqualifying the stack
   is what actually stops "chasing into the middle of a stack."
3. Order-block confluence beats plain imbalance, but only among gaps that
   are still live per (2): if any of them overlaps a valid order block,
   the nearest *confluence* gap is nominated instead of the plain nearest
   one -- confluence upgrades which live zone you react at, it doesn't
   grant permission to skip past an already-failed near gap.

Width ("the widest gap in the stack holds more weight than the small ones
around it") is reported as `PriorityZone.is_widest_in_stack` rather than
used to override the nearest-price pick -- rules 3-4 above already have
explicit "beats/wins" language pointing at a single winner, and folding a
third, differently-scaled criterion (price distance vs. gap width) into
that same comparison would make the winner depend on arbitrary unit
weighting. Widest-in-stack is still real signal, so it's surfaced for the
caller to use in confidence/rationale rather than dropped.
"""

from __future__ import annotations

from .models import ConfluenceZone, FairValueGap, PriorityZone

DEFAULT_MAX_INDEX_GAP = 2  # candles between FVGs to still count as "back to back"


def _passes_discount_premium(fvg: FairValueGap, equilibrium: float) -> bool:
    if fvg.kind == "bullish":
        return fvg.mid <= equilibrium
    return fvg.mid >= equilibrium


def _is_approachable(kind: str, top: float, bottom: float, price: float) -> bool:
    if kind == "bullish":
        return top <= price
    return bottom >= price


def _distance(kind: str, top: float, bottom: float, price: float) -> float:
    return price - top if kind == "bullish" else bottom - price


def group_into_stacks(
    fvgs: list[FairValueGap], max_index_gap: int = DEFAULT_MAX_INDEX_GAP
) -> list[list[FairValueGap]]:
    """Groups same-kind FVGs into stacks when consecutive members (by
    formation order) are within `max_index_gap` candles of each other.
    A gap with no neighbors is its own one-member stack."""
    stacks: list[list[FairValueGap]] = []
    for kind in ("bullish", "bearish"):
        members = sorted((f for f in fvgs if f.kind == kind), key=lambda f: f.index)
        current: list[FairValueGap] = []
        for fvg in members:
            if current and fvg.index - current[-1].index > max_index_gap:
                stacks.append(current)
                current = []
            current.append(fvg)
        if current:
            stacks.append(current)
    return stacks


def resolve_fvg_stacks(
    fvgs: list[FairValueGap],
    confluence_zones: list[ConfluenceZone],
    price: float,
    range_high: float,
    range_low: float,
    max_index_gap: int = DEFAULT_MAX_INDEX_GAP,
) -> list[PriorityZone]:
    """One PriorityZone per stack that still has a live, in-range, still-
    approachable candidate -- everything the strategy layer needs to pick
    an entry, with the stacking/prioritization already resolved."""
    equilibrium = (range_high + range_low) / 2
    eligible = [
        f for f in fvgs if not f.mitigated and _passes_discount_premium(f, equilibrium)
    ]
    if not eligible:
        return []

    confluence_ob_by_key = {
        (z.fvg.kind, z.fvg.top, z.fvg.bottom, z.fvg.index): z.order_block
        for z in confluence_zones
    }

    results: list[PriorityZone] = []
    for stack in group_into_stacks(eligible, max_index_gap):
        kind = stack[0].kind
        # Structurally nearest = last gap formed during the push (highest
        # top for bullish, lowest bottom for bearish) -- the first one price
        # reaches on a retracement, independent of where price sits right now.
        nearest = max(stack, key=lambda f: f.top) if kind == "bullish" else min(stack, key=lambda f: f.bottom)
        if not _is_approachable(nearest.kind, nearest.top, nearest.bottom, price):
            continue  # already traded through the near edge of this stack -- don't chase a deeper gap

        confluence_members = [
            f for f in stack if (f.kind, f.top, f.bottom, f.index) in confluence_ob_by_key
        ]
        if confluence_members:
            best = min(confluence_members, key=lambda f: _distance(f.kind, f.top, f.bottom, price))
        else:
            best = nearest

        widest = max(stack, key=lambda f: f.top - f.bottom)

        results.append(
            PriorityZone(
                kind=best.kind,
                top=best.top,
                bottom=best.bottom,
                stack_size=len(stack),
                has_confluence=best in confluence_members,
                is_widest_in_stack=best is widest,
                order_block=confluence_ob_by_key.get(
                    (best.kind, best.top, best.bottom, best.index)
                ),
            )
        )

    return results
