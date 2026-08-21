"""
كل منطق اللعبة (نتيجة السبن، مستوى VIP، البونص اليومي) يحسب هنا في السيرفر فقط.
الفرونت اند لا يقرر أي نتيجة أبداً — فقط يعرض اللي يرجعه الـ API.
هذا أهم إجراء لمنع التلاعب (anti-cheat).
"""
import random
from .config import WHEEL_SEGMENTS, VIP_LEVELS, DAILY_BONUS_SCHEDULE


def spin_wheel() -> dict:
    """يرجع نتيجة عشوائية مبنية على الأوزان (weights) المحددة في config.py"""
    segments = WHEEL_SEGMENTS
    weights = [s["weight"] for s in segments]
    chosen = random.choices(segments, weights=weights, k=1)[0]
    return {"label": chosen["label"], "value": chosen["value"]}


def get_vip_level(lifetime_xp: float) -> str:
    level = VIP_LEVELS[0]["name"]
    for lvl in VIP_LEVELS:
        if lifetime_xp >= lvl["min_xp"]:
            level = lvl["name"]
    return level


def get_daily_bonus_amount(streak_day: int) -> float:
    """streak_day يبدأ من 1"""
    idx = min(streak_day - 1, len(DAILY_BONUS_SCHEDULE) - 1)
    return DAILY_BONUS_SCHEDULE[idx]


def build_dynamic_prize_table(play_amount: float, deposit_tier_multiplier: float = 1.0, display_values: list = None) -> list:
    """Builds the (prize, probability) list for a given play amount — used
    both to actually resolve a spin AND to show the wheel's segments to the
    player before they spin (GET /api/spin/wheel), with no draw involved.

    display_values: the fixed wheel-face value set to draw prizes from.
    Defaults to config.WHEEL_DISPLAY_VALUES_NGN when omitted, so every
    existing NGN call site (which never passed this argument) behaves
    EXACTLY as before. Pass config.WHEEL_DISPLAY_VALUES_USD here for the
    Crypto Balance ($) wheel instead — same function, same rule, different
    currency's value set.

    HARD RULE (anti-cheat, enforced here — not in JS): the wheel graphic
    always shows the full display_values set, but only the values from that
    set that are <= play_amount are ever put in this table — so a prize can
    never be picked that's bigger than what the player played, no matter
    what the wheel displays, and no matter which currency is being played.
    """
    if display_values is None:
        from .config import WHEEL_DISPLAY_VALUES_NGN
        display_values = WHEEL_DISPLAY_VALUES_NGN

    eligible = sorted({v for v in display_values if v <= play_amount})
    if not eligible:
        eligible = [0]
    if 0 not in eligible:
        eligible = [0] + eligible

    nonzero = [v for v in eligible if v > 0]
    if not nonzero:
        return [(0.0, 1.0)]

    # Smaller nonzero prizes are more likely than bigger ones — geometric-ish
    # decay over 50% of total probability mass; the other 50% always goes to
    # ₦0. Purely a house-edge weighting, has no effect on the <= play_amount
    # cap above, which is what actually prevents overpaying.
    raw_weights = [1.0 / (i + 1) for i in range(len(nonzero))]
    raw_sum = sum(raw_weights)
    table = [(0.0, 0.50)] + [
        (float(v), (w / raw_sum) * 0.50) for v, w in zip(nonzero, raw_weights)
    ]

    if deposit_tier_multiplier and deposit_tier_multiplier != 1.0 and len(table) > 1:
        top_index = max(range(len(table)), key=lambda i: table[i][0])
        boosted = min(table[top_index][1] * deposit_tier_multiplier, 0.95)
        delta = boosted - table[top_index][1]
        zero_index = min(range(len(table)), key=lambda i: table[i][0])
        if zero_index != top_index and table[zero_index][1] - delta > 0:
            new_zero_prob = table[zero_index][1] - delta
            table[zero_index] = (table[zero_index][0], new_zero_prob)
            table[top_index] = (table[top_index][0], boosted)

    return table


def resolve_dynamic_spin(play_amount: float, deposit_tier_multiplier: float = 1.0, display_values: list = None) -> dict:
    """NEW — server-side outcome for the variable play-amount "Smart Dynamic
    Wheel" (POST /api/spin/play). Never called with anything the frontend
    invented: play_amount is validated by the route before this runs, and
    the deposit-tier multiplier comes only from the user's real deposit
    history via ledger_service. display_values picks which currency's wheel-
    face set to draw from (defaults to NGN — see build_dynamic_prize_table)."""
    table = build_dynamic_prize_table(play_amount, deposit_tier_multiplier, display_values)
    prizes = [p for p, _ in table]
    weights = [w for _, w in table]
    chosen_prize = random.choices(prizes, weights=weights, k=1)[0]
    return {"prize": chosen_prize, "table": table}
