"""
NEW (additive only, Prize Tiers): admin-configurable prize tables for the
NGN Smart Dynamic Wheel, keyed by play-amount bracket.

Deliberately kept SEPARATE from game_logic.py's build_dynamic_prize_table/
resolve_dynamic_spin, which remain completely untouched and still power the
Crypto ($) spin exactly as before — game_logic.py stays a pure, DB-free
module; everything here that needs a database session lives in this file
instead.

Why this exists: the old NGN mechanism drew every play amount's prize table
from ONE universal fixed list (config.WHEEL_DISPLAY_VALUES_NGN), just
filtered down to values <= play_amount. That meant the same small prizes
were always eligible and always dominated the probability mass regardless
of play_amount, so a ₦5,000 spin and a ₦50,000 spin looked almost
identical. This module instead looks up a genuinely DIFFERENT, admin-
editable set of (prize, weight) rows for whichever bracket the play_amount
falls into.
"""
import random
from datetime import datetime

from sqlalchemy.orm import Session

from . import models

# Used only to seed sensible defaults the FIRST time this feature runs
# (i.e. when no NGN tiers exist in the database yet at all) — matches the
# exact example tiers from the product spec, so Prize Tiers work
# out-of-the-box without requiring an admin to configure anything first.
# Never runs again once at least one NGN tier exists; never overwrites an
# admin's own edits.
_DEFAULT_NGN_TIERS = [
    (5000,  "₦5,000 Tier",  [0, 100, 300, 500, 1000]),
    (10000, "₦10,000 Tier", [0, 200, 500, 1000, 2000]),
    (20000, "₦20,000 Tier", [0, 500, 1000, 2500, 5000]),
    (50000, "₦50,000 Tier", [0, 1000, 2500, 5000, 10000, 20000]),
]


def seed_default_ngn_tiers_if_empty(db: Session) -> None:
    existing = db.query(models.SpinPrizeTier.id).filter_by(currency="NGN").first()
    if existing:
        return

    for min_amount, label, prizes in _DEFAULT_NGN_TIERS:
        tier = models.SpinPrizeTier(currency="NGN", min_play_amount=min_amount, label=label, is_active=True)
        db.add(tier)
        db.flush()
        for i, prize in enumerate(prizes):
            # Geometric decay (1, 1/2, 1/4, ...) — after normalization this
            # gives roughly a 50%-ish chance of ₦0, tapering off toward the
            # top prize, echoing the "small wins common, big wins rare"
            # feel the old system had, while being fully admin-editable
            # (raw weights, not a fixed formula) from this point forward.
            weight = 1.0 / (2 ** i)
            db.add(models.SpinPrizeValue(tier_id=tier.id, prize_amount=float(prize), weight=weight))

    db.commit()
    print(f"[spin_tier_service] seeded {len(_DEFAULT_NGN_TIERS)} default NGN prize tiers")


def get_tiers(db: Session, currency: str = "NGN", active_only: bool = True):
    q = db.query(models.SpinPrizeTier).filter(models.SpinPrizeTier.currency == currency)
    if active_only:
        q = q.filter(models.SpinPrizeTier.is_active == True)  # noqa: E712
    return q.order_by(models.SpinPrizeTier.min_play_amount.asc()).all()


def select_tier_for_play_amount(db: Session, play_amount: float, currency: str = "NGN"):
    """The tier with the LARGEST min_play_amount that is <= play_amount —
    i.e. the highest bracket this specific play amount actually qualifies
    for (this is what makes "Deposit Level يحدد Tier المناسب" true: it's
    driven by the real amount being wagered THIS spin, not a guess or
    anything the frontend asserts). If play_amount is below every
    configured tier's minimum, falls back to the LOWEST configured tier —
    never an error; its prize list just ends up filtered down to ~[₦0] by
    the play_amount cap below, which is always safe."""
    tiers = get_tiers(db, currency)
    if not tiers:
        return None
    applicable = [t for t in tiers if t.min_play_amount <= play_amount]
    if applicable:
        return max(applicable, key=lambda t: t.min_play_amount)
    return tiers[0]


def build_tier_prize_table(db: Session, play_amount: float, currency: str = "NGN", deposit_tier_multiplier: float = 1.0):
    """Returns (tier_or_None, [(prize, probability), ...]) with
    probabilities always summing to 1.0.

    HARD CAP (anti-cheat — matches the exact rule the old system already
    enforced, just against the NEW per-tier prize lists instead of the old
    universal one): any configured prize row whose amount is > play_amount
    is excluded here, unconditionally — this is what actually guarantees a
    prize can never exceed what was played, independent of how an admin
    configures a tier's rows.
    """
    tier = select_tier_for_play_amount(db, play_amount, currency)
    if tier is None:
        return None, [(0.0, 1.0)]   # no tiers configured at all yet — always safe, always ₦0

    eligible = [(pv.prize_amount, max(pv.weight, 0.0)) for pv in tier.prizes if pv.prize_amount <= play_amount]
    if not eligible:
        # Only true last resort: NOTHING the admin configured for this
        # tier is even reachable at this play amount (every configured
        # prize costs more than what was actually wagered this spin).
        # NGN 0 is the only safe outcome left. This is now the ONLY path
        # that can ever produce NGN 0 for a tier the admin built with no
        # NGN 0 row — a tier with at least one reachable configured prize
        # never falls back to 0 anymore.
        eligible.append((0.0, 1.0))

    total_weight = sum(w for _, w in eligible)
    if total_weight <= 0:
        return tier, [(0.0, 1.0)]

    table = [(p, w / total_weight) for p, w in eligible]

    # Same secondary "loyal depositor" nudge the old NGN system already
    # applied (see game_logic.build_dynamic_prize_table) — boosts THIS
    # tier's own top eligible prize's probability, taken out of ₦0's share.
    # Purely additive on top of the tier's own admin-configured odds; has
    # no effect on the play_amount cap above.
    if deposit_tier_multiplier and deposit_tier_multiplier != 1.0 and len(table) > 1:
        top_index = max(range(len(table)), key=lambda i: table[i][0])
        zero_index = min(range(len(table)), key=lambda i: table[i][0])
        if top_index != zero_index:
            boosted = min(table[top_index][1] * deposit_tier_multiplier, 0.95)
            delta = boosted - table[top_index][1]
            if table[zero_index][1] - delta > 0:
                table[zero_index] = (table[zero_index][0], table[zero_index][1] - delta)
                table[top_index] = (table[top_index][0], boosted)

    return tier, table


def resolve_tiered_spin(db: Session, play_amount: float, currency: str = "NGN", deposit_tier_multiplier: float = 1.0) -> dict:
    """Server-side outcome for a tiered NGN spin. Never called with
    anything the frontend invented: play_amount is validated by the route
    before this runs, and deposit_tier_multiplier comes only from the
    user's real deposit history via ledger_service — same anti-cheat
    posture as game_logic.resolve_dynamic_spin."""
    tier, table = build_tier_prize_table(db, play_amount, currency, deposit_tier_multiplier)
    prizes = [p for p, _ in table]
    weights = [w for _, w in table]
    chosen_prize = random.choices(prizes, weights=weights, k=1)[0]
    return {"prize": chosen_prize, "table": table, "tier": tier}


# =========================================================================
# NEW: Admin Win Boost — while the admin toggle (ledger_service.
# ADMIN_WIN_BOOST_SETTING_KEY) is ON, spins made by an ADMIN account use the
# two helpers below instead of resolve_tiered_spin. The operator plays a
# small amount and still lands the largest prize configured anywhere across
# this currency's active tiers. Regular players never go through here — the
# route checks user.is_admin AND the live flag server-side on every spin.
# =========================================================================

def get_max_configured_prize(db: Session, currency: str = "NGN") -> float:
    """Largest prize amount configured across ALL ACTIVE tiers for this
    currency (0.0 when no tiers exist or every configured row is ₦0).
    Read-only — used both to resolve an admin-boosted spin and to add the
    boosted value to GET /api/spin/wheel's display_segments so the wheel
    graphic can always land exactly on what POST /play returns."""
    max_prize = 0.0
    for tier in get_tiers(db, currency):
        for pv in tier.prizes:
            if pv.prize_amount > max_prize:
                max_prize = float(pv.prize_amount)
    return max_prize


def resolve_admin_boosted_spin(db: Session, play_amount: float, currency: str = "NGN") -> dict:
    """Deterministic top-prize outcome for an admin-boosted NGN spin.

    Deliberately ignores build_tier_prize_table's 'prize <= play_amount'
    cap. That cap exists to protect the HOUSE from overpaying players; here
    the house itself (the authenticated admin) asked for the payout via its
    own toggle, so paying above the wagered amount to that one account is
    intended behavior — no other user's outcome, odds, or balance changes.
    Returns the same dict shape as resolve_tiered_spin so callers can treat
    both identically. Falls back to a safe single-₦0 table when no tiers
    are configured at all yet."""
    tier = select_tier_for_play_amount(db, play_amount, currency)
    top_prize = get_max_configured_prize(db, currency)
    if tier is None or top_prize <= 0:
        return {"prize": 0.0, "table": [(0.0, 1.0)], "tier": tier}
    return {"prize": top_prize, "table": [(top_prize, 1.0)], "tier": tier}
