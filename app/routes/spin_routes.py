from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .. import models, schemas, ledger_service, game_logic, spin_tier_service
from ..database import get_db
from ..auth import get_current_user
from ..game_logic import spin_wheel, get_daily_bonus_amount, build_dynamic_prize_table, resolve_dynamic_spin
from ..config import (
    SPIN_FEE_POINTS, SPIN_FEE_USD, TRON_ADDRESS_CURRENCY_LABEL,
    SPIN_PLAY_PRESETS_NGN, SPIN_MIN_PLAY_NGN, SPIN_MAX_PLAY_NGN,
    SPIN_CUSTOM_AMOUNT_ALLOWED, WHEEL_DISPLAY_VALUES_NGN,
    SPIN_PLAY_PRESETS_USD, SPIN_MIN_PLAY_USD, SPIN_MAX_PLAY_USD,
    WHEEL_DISPLAY_VALUES_USD, POINTS_PER_USDT,
)

router = APIRouter(prefix="/api/spin", tags=["spin"])


@router.get("/status")
def spin_status(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    NEW behavior: spinning is pay-per-play (no more 24h cooldown), so "can spin"
    now depends on having deposited + having enough balance for the fee — not
    on time elapsed. last_spin_at is still returned for reference/history.
    """
    has_deposited = ledger_service.user_has_deposited(db, user.id)
    can_spin = has_deposited and user.points_balance >= SPIN_FEE_POINTS
    return {
        "can_spin": can_spin,
        "has_deposited": has_deposited,
        "spin_fee_points": SPIN_FEE_POINTS,
        "points_balance": user.points_balance,
        "last_spin_at": user.last_spin_at.isoformat() if user.last_spin_at else None,
    }


@router.post("", response_model=None)
def do_spin(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    now = datetime.utcnow()

    # --- 1) Must have at least one confirmed deposit before ever spinning ---
    if not ledger_service.user_has_deposited(db, user.id):
        return JSONResponse(
            status_code=402,
            content={"success": False, "message": "Deposit required before playing"},
        )

    # --- 2) Re-check balance under row-level locking (same pattern as /wallet/withdraw)
    #         to avoid a race between two concurrent spin requests ---
    try:
        locked_user = db.query(models.User).filter_by(id=user.id).with_for_update().first()
    except Exception:
        db.rollback()
        locked_user = db.query(models.User).filter_by(id=user.id).first()  # e.g. SQLite: no-op fallback

    current_balance = Decimal(str(locked_user.points_balance))
    fee = Decimal(str(SPIN_FEE_POINTS))
    if current_balance < fee:
        return JSONResponse(
            status_code=402,
            content={"success": False, "message": "Insufficient balance. Please deposit to continue."},
        )

    # --- 3) Charge the $1 spin fee ---
    locked_user.points_balance = float(current_balance - fee)

    # --- 4) Run the wheel exactly as before (server-decided, anti-cheat unchanged) ---
    result = spin_wheel()

    locked_user.last_spin_at = now
    locked_user.locked_points += result["value"]
    locked_user.lifetime_xp += result["value"]

    spin_tx = models.Transaction(
        user_id=locked_user.id,
        type=models.TransactionType.SPIN_WIN,
        amount=result["value"],
        description=f"سبن العجلة - ربح {result['label']}",
    )
    db.add(spin_tx)
    db.flush()  # نحتاج spin_tx.id كـ reference_id بالأسفل بدون commit كامل بعد

    # --- 5) NEW: the $1 fee goes to the platform Admin Ledger as a SPIN_FEE ---
    fee_tx = models.Transaction(
        user_id=locked_user.id,
        type=models.TransactionType.SPIN_FEE,
        amount=-SPIN_FEE_USD,
        description="Spin fee ($1) charged for playing",
    )
    db.add(fee_tx)

    ledger_service.credit_ledger(
        db, currency=TRON_ADDRESS_CURRENCY_LABEL, amount=SPIN_FEE_USD,
        tx_type=models.LedgerTxType.SPIN_FEE, user_id=locked_user.id,
        reference_id=spin_tx.id, status=models.LedgerTxStatus.COMPLETED,
    )

    db.commit()
    db.refresh(locked_user)

    return schemas.SpinResult(
        success=True,
        label=result["label"],
        value=result["value"],
        fee_charged=SPIN_FEE_USD,
        new_balance=locked_user.points_balance,
        new_locked=locked_user.locked_points,
        next_spin_at=None,
    )


@router.get("/daily-bonus/status")
def daily_bonus_status(user: models.User = Depends(get_current_user)):
    now = datetime.utcnow()
    can_claim = True
    if user.last_daily_bonus_at is not None:
        can_claim = now.date() > user.last_daily_bonus_at.date()
    return {"can_claim": can_claim, "streak": user.daily_bonus_streak}


@router.post("/daily-bonus")
def claim_daily_bonus(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    now = datetime.utcnow()
    if user.last_daily_bonus_at is not None:
        if now.date() <= user.last_daily_bonus_at.date():
            raise HTTPException(status_code=429, detail="Today's bonus has already been claimed")
        # لو فاته يوم كامل ينكسر الستريك
        if (now.date() - user.last_daily_bonus_at.date()).days > 1:
            user.daily_bonus_streak = 0

    user.daily_bonus_streak = min(user.daily_bonus_streak + 1, 7)
    amount = get_daily_bonus_amount(user.daily_bonus_streak)

    user.locked_points += amount
    user.lifetime_xp += amount
    user.last_daily_bonus_at = now

    tx = models.Transaction(
        user_id=user.id,
        type=models.TransactionType.DAILY_BONUS,
        amount=amount,
        description=f"بونص يومي - يوم {user.daily_bonus_streak}",
    )
    db.add(tx)
    db.commit()
    db.refresh(user)

    return {"amount": amount, "streak": user.daily_bonus_streak, "new_locked": user.locked_points}


# =========================================================================
# NEW: "Smart Dynamic Wheel" — variable play-amount spin. Fully additive —
# everything above this line (fixed 100-point POST /api/spin, daily bonus)
# is completely untouched and keeps working exactly as before.
# =========================================================================

def _validate_play_amount(play_amount: float, currency: str = "NGN"):
    """Never trusts the number from the browser as final truth — re-checked
    here regardless of what the frontend sent or how it was tampered with.
    Limits/presets depend on which currency is being played — NGN's
    behavior/messages here are byte-identical to before when currency is
    omitted or "NGN" (the default)."""
    if play_amount is None or play_amount <= 0:
        raise HTTPException(status_code=400, detail="Play amount must be greater than zero")

    if currency == "USD":
        min_amt, max_amt, presets, symbol = SPIN_MIN_PLAY_USD, SPIN_MAX_PLAY_USD, SPIN_PLAY_PRESETS_USD, "$"
    else:
        min_amt, max_amt, presets, symbol = SPIN_MIN_PLAY_NGN, SPIN_MAX_PLAY_NGN, SPIN_PLAY_PRESETS_NGN, "₦"

    if play_amount < min_amt:
        raise HTTPException(status_code=400, detail=f"Minimum play amount is {symbol}{min_amt:,.0f}")
    if play_amount > max_amt:
        raise HTTPException(status_code=400, detail=f"Maximum play amount is {symbol}{max_amt:,.0f}")
    is_preset = play_amount in presets
    if not is_preset and not SPIN_CUSTOM_AMOUNT_ALLOWED:
        raise HTTPException(status_code=400, detail="Custom play amounts are not allowed")


@router.get("/play-config")
def spin_play_config(
    currency: str = "NGN",
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Everything the Play-Amount picker UI needs in one call: presets,
    limits, the player's REAL balance for the chosen currency, and their
    deposit tier — all straight from the database, nothing computed
    client-side. currency="NGN" (the default) returns EXACTLY the same
    response shape as before this change — nothing here changed for
    existing NGN callers. currency="USD" is the new Crypto Balance branch:
    reads points_balance (shared with TRX/USDT/BTC — see wallet_routes.py),
    converted to USD, and gated on a real CRYPTO deposit specifically (not
    just "any deposit" — an NGN-only depositor correctly sees
    has_deposited=false here)."""
    if currency == "USD":
        has_deposited = ledger_service.user_has_crypto_deposited(db, user.id)
        tier_label, tier_multiplier = ledger_service.get_user_deposit_tier(db, user.id)
        return {
            "currency": "USD",
            "presets": SPIN_PLAY_PRESETS_USD,
            "min_play": SPIN_MIN_PLAY_USD,
            "max_play": SPIN_MAX_PLAY_USD,
            "custom_allowed": SPIN_CUSTOM_AMOUNT_ALLOWED,
            "balance": user.points_balance / POINTS_PER_USDT,
            "winnings_balance": user.usd_winnings_balance,
            "has_deposited": has_deposited,
            "deposit_tier": tier_label,
        }

    has_deposited = ledger_service.user_has_deposited(db, user.id)
    tier_label, tier_multiplier = ledger_service.get_user_ngn_deposit_tier(db, user.id)
    # NEW (Prize Tiers): lightweight preview of the configured play-amount
    # brackets, purely additive — lets the frontend show "at ₦20,000+ you
    # unlock these prizes" style UI before the player even picks an amount.
    # Real DB data (spin_tier_service), same tiers /wheel and /play use.
    prize_tiers = [
        {
            "min_play_amount": t.min_play_amount,
            "label": t.label,
            "prizes": sorted({pv.prize_amount for pv in t.prizes}),
        }
        for t in spin_tier_service.get_tiers(db, "NGN")
    ]
    return {
        "currency": "NGN",
        "presets": SPIN_PLAY_PRESETS_NGN,
        "min_play_ngn": SPIN_MIN_PLAY_NGN,
        "max_play_ngn": SPIN_MAX_PLAY_NGN,
        "custom_allowed": SPIN_CUSTOM_AMOUNT_ALLOWED,
        "ngn_balance": user.ngn_balance,
        "ngn_winnings_balance": user.ngn_winnings_balance,
        "has_deposited": has_deposited,
        "deposit_tier": tier_label,
        "prize_tiers": prize_tiers,
    }


@router.get("/wheel")
def spin_wheel_preview(
    play_amount: float,
    currency: str = "NGN",
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Returns what the frontend needs to draw AND correctly land the wheel:
    - display_segments: for USD, the FULL fixed wheel face
      (WHEEL_DISPLAY_VALUES_USD) — always the same regardless of play
      amount. For NGN (CHANGED — Prize Tiers), this is now the current
      play-amount bracket's own admin-configured prize set, which
      genuinely differs between brackets — see spin_tier_service.py.
    - segments: only the prize/probability pairs that are actually
      REACHABLE for this play amount (every value here is <= play_amount,
      enforced server-side — never trust the frontend for this, and never
      mixed between currencies).
    Read-only — draws nothing, deducts nothing. The actual result always
    comes from POST /play, and it is always one of display_segments'
    values, so the frontend can always find a matching segment to land on.
    currency="NGN" (the default) is byte-identical to before this change."""
    _validate_play_amount(play_amount, currency)

    if currency == "USD":
        _, tier_multiplier = ledger_service.get_user_deposit_tier(db, user.id)
        table = build_dynamic_prize_table(play_amount, tier_multiplier, WHEEL_DISPLAY_VALUES_USD)
        return {
            "currency": "USD",
            "play_amount": play_amount,
            "display_segments": WHEEL_DISPLAY_VALUES_USD,
            "segments": [{"prize": prize, "probability": prob} for prize, prob in table],
        }

    _, tier_multiplier = ledger_service.get_user_ngn_deposit_tier(db, user.id)
    # CHANGED (Prize Tiers): NGN now uses genuinely different, admin-
    # configurable prize tables per play-amount bracket instead of one
    # universal filtered list — see spin_tier_service.py for why. The wheel
    # therefore shows a DIFFERENT set of segments depending on play_amount,
    # unlike before. USD/Crypto spin below is untouched and still uses the
    # old game_logic.build_dynamic_prize_table/WHEEL_DISPLAY_VALUES_USD.
    prize_tier, table = spin_tier_service.build_tier_prize_table(db, play_amount, "NGN", tier_multiplier)
    display_values = sorted({p for p, _ in table})
    return {
        "currency": "NGN",
        "play_amount": play_amount,
        "display_segments": display_values,
        "segments": [{"prize": prize, "probability": prob} for prize, prob in table],
        "tier_label": prize_tier.label if prize_tier else None,
    }


@router.post("/play", response_model=schemas.DynamicSpinResult)
def spin_play(
    payload: schemas.DynamicSpinRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Branches entirely on payload.currency, decided and validated here —
    never trusts the frontend beyond "which balance did they ask to play
    with". The two paths below NEVER touch each other's balance field:
    the USD/Crypto path only ever reads/writes points_balance, the NGN path
    only ever reads/writes ngn_balance. currency="NGN" (the default, for
    backward compatibility with any caller that omits it) runs EXACTLY the
    same code that existed here before this change — nothing about the NGN
    path's math, gating, or response values is different."""
    currency = (payload.currency or "NGN").strip().upper()
    if currency not in ("NGN", "USD"):
        return JSONResponse(status_code=400, content={"success": False, "message": "Unsupported spin currency"})

    _validate_play_amount(payload.play_amount, currency)

    if currency == "USD":
        return _spin_play_usd(payload, db, user)
    return _spin_play_ngn(payload, db, user)


def _spin_play_usd(payload, db: Session, user: models.User):
    """NEW — Crypto Balance ($) dynamic spin. Deducts and credits
    user.points_balance ONLY (the same shared balance TRX/USDT/BTC deposits
    already feed — see wallet_routes.py). Never touches ngn_balance."""
    if not ledger_service.user_has_crypto_deposited(db, user.id):
        return JSONResponse(
            status_code=402,
            content={"success": False, "message": "A TRX or USDT deposit is required before playing with Crypto Balance"},
        )

    try:
        locked_user = db.query(models.User).filter_by(id=user.id).with_for_update().first()
    except Exception:
        db.rollback()
        locked_user = db.query(models.User).filter_by(id=user.id).first()  # SQLite fallback

    play_amount_dec = Decimal(str(payload.play_amount))
    current_balance_usd = Decimal(str(locked_user.points_balance)) / Decimal(str(POINTS_PER_USDT))
    if current_balance_usd < play_amount_dec:
        return JSONResponse(status_code=402, content={"success": False, "message": "Insufficient balance"})

    # Deduct the play amount FIRST (converted to points, the balance's real
    # storage unit), then determine the outcome — same sequencing the NGN
    # path already uses: balance drops immediately, THEN the server decides
    # win/loss, so a crash in between can never leave the player credited
    # without having paid.
    play_amount_points = play_amount_dec * Decimal(str(POINTS_PER_USDT))
    locked_user.points_balance = float(Decimal(str(locked_user.points_balance)) - play_amount_points)

    tier_label, tier_multiplier = ledger_service.get_user_deposit_tier(db, user.id)
    outcome = resolve_dynamic_spin(payload.play_amount, tier_multiplier, WHEEL_DISPLAY_VALUES_USD)
    prize_usd = outcome["prize"]   # HARD CAP already enforced inside build_dynamic_prize_table: never > payload.play_amount

    fee_tx = models.Transaction(
        user_id=locked_user.id, type=models.TransactionType.SPIN_FEE,
        amount=-payload.play_amount, currency="USD",
        description=f"Spin play — ${payload.play_amount} ({tier_label})",
    )
    db.add(fee_tx)
    db.flush()

    ledger_service.credit_ledger(
        db, currency=TRON_ADDRESS_CURRENCY_LABEL, amount=float(play_amount_dec),
        tx_type=models.LedgerTxType.SPIN_FEE, user_id=locked_user.id,
        reference_id=fee_tx.id, status=models.LedgerTxStatus.COMPLETED,
    )

    win_tx = None
    if prize_usd > 0:
        locked_user.usd_winnings_balance = float(Decimal(str(locked_user.usd_winnings_balance)) + Decimal(str(prize_usd)))
        win_tx = models.Transaction(
            user_id=locked_user.id, type=models.TransactionType.SPIN_WIN,
            amount=prize_usd, currency="USD",
            description=f"Spin win — +${prize_usd} (played ${payload.play_amount})",
        )
        db.add(win_tx)
        db.flush()

    locked_user.last_spin_at = datetime.utcnow()
    db.commit()
    db.refresh(locked_user)

    return schemas.DynamicSpinResult(
        success=True, currency="USD", play_amount=payload.play_amount,
        winning_amount=prize_usd, new_balance=locked_user.points_balance / POINTS_PER_USDT,
        new_winnings_balance=locked_user.usd_winnings_balance,
        transaction_id=(win_tx.id if win_tx else fee_tx.id),
        status="WIN" if prize_usd > 0 else "LOSS",
    )


def _spin_play_ngn(payload, db: Session, user: models.User):
    """NGN-native — deducts and credits user.ngn_balance ONLY. Never touches
    points_balance/locked_points, which belong to the separate crypto/fixed
    spin systems. UNCHANGED from before this file was split into
    currency-specific helper functions — same checks, same order, same math."""
    if not ledger_service.user_has_deposited(db, user.id):
        return JSONResponse(
            status_code=402,
            content={"success": False, "message": "Deposit required before playing"},
        )

    try:
        locked_user = db.query(models.User).filter_by(id=user.id).with_for_update().first()
    except Exception:
        db.rollback()
        locked_user = db.query(models.User).filter_by(id=user.id).first()  # SQLite fallback

    current_balance = Decimal(str(locked_user.ngn_balance))
    play_amount_dec = Decimal(str(payload.play_amount))
    if current_balance < play_amount_dec:
        return JSONResponse(status_code=402, content={"success": False, "message": "Insufficient balance"})

    locked_user.ngn_balance = float(current_balance - play_amount_dec)

    tier_label, tier_multiplier = ledger_service.get_user_ngn_deposit_tier(db, user.id)
    # CHANGED (Prize Tiers): server-side outcome now comes from the
    # play-amount-specific tier table (spin_tier_service), not the old
    # universal filtered list — see spin_wheel_preview above for the same
    # change on the read-only preview side. The hard "prize <= play_amount"
    # cap is still enforced, just against the new per-tier prize rows.
    outcome = spin_tier_service.resolve_tiered_spin(db, payload.play_amount, "NGN", tier_multiplier)
    prize = outcome["prize"]

    fee_tx = models.Transaction(
        user_id=locked_user.id,
        type=models.TransactionType.SPIN_FEE,
        amount=-payload.play_amount,
        currency="NGN",
        description=f"Spin play — ₦{payload.play_amount} ({tier_label})",
    )
    db.add(fee_tx)
    db.flush()

    ledger_service.credit_ledger(
        db, currency="NGN", amount=payload.play_amount,
        tx_type=models.LedgerTxType.SPIN_FEE, user_id=locked_user.id,
        reference_id=fee_tx.id, status=models.LedgerTxStatus.COMPLETED,
    )

    win_tx = None
    if prize > 0:
        locked_user.ngn_winnings_balance = float(Decimal(str(locked_user.ngn_winnings_balance)) + Decimal(str(prize)))
        win_tx = models.Transaction(
            user_id=locked_user.id,
            type=models.TransactionType.SPIN_WIN,
            amount=prize,
            currency="NGN",
            description=f"Spin win — +₦{prize} (played ₦{payload.play_amount})",
        )
        db.add(win_tx)
        db.flush()

    locked_user.last_spin_at = datetime.utcnow()
    db.commit()
    db.refresh(locked_user)

    return schemas.DynamicSpinResult(
        success=True, currency="NGN", play_amount=payload.play_amount,
        winning_amount=prize, new_balance=locked_user.ngn_balance,
        new_winnings_balance=locked_user.ngn_winnings_balance,
        transaction_id=(win_tx.id if win_tx else fee_tx.id),
        status="WIN" if prize > 0 else "LOSS",
    )
