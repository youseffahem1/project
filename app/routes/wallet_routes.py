from decimal import Decimal, InvalidOperation
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..auth import get_current_user
from .. import email_service, wallet_gen_service, blockchain_monitor, withdrawal_monitor
from ..services import tron_service, price_service
from ..config import (
    POINTS_PER_USDT, AUTO_WITHDRAW_MAX_USDT, MIN_WITHDRAW_AMOUNT,
    TRON_ADDRESS_CURRENCY_LABEL, SUN_PER_TRX,
    USDT_TRC20_CONTRACT, USDT_TRC20_DECIMALS,
)

router = APIRouter(prefix="/api/wallet", tags=["wallet"])


@router.get("/summary")
def wallet_summary(user: models.User = Depends(get_current_user)):
    # points_balance is the ONE shared balance behind TRX and USDT-TRC20
    # (each is just a different deposit/withdraw rail into the same
    # balance). These *_equivalent fields are read-only conveniences
    # computed from real, live prices (price_service — the same one
    # deposits already use). Never fails the whole call if a price is
    # temporarily unavailable — that field is just None.
    # NOTE: BTC deposit/withdraw is intentionally not offered on this
    # deployment (removed by request) — see the currency whitelists in
    # get_deposit_addresses() and withdraw() below. The BitGo integration
    # code (bitgo_service.py, wallet_gen_service.get_or_create_user_btc_
    # address, blockchain_monitor.poll_bitgo_wallet_once) is left in place
    # untouched rather than deleted, so it's a quick, low-risk flip back on
    # if it's ever wanted again — it's simply never called from here.
    usd_equivalent = user.points_balance / POINTS_PER_USDT if POINTS_PER_USDT else 0.0

    trx_equivalent = None
    try:
        trx_equivalent = usd_equivalent / price_service.get_usd_price("TRX")
    except price_service.PriceUnavailableError:
        pass

    return {
        "points_balance": user.points_balance,
        "points_per_usdt": POINTS_PER_USDT,
        "ngn_balance": user.ngn_balance,   # NEW: real Naira balance, separate from points_balance
        # NEW: same shared points_balance, expressed in each crypto rail's terms
        "balances": {
            "TRX_NILE": {"points": user.points_balance, "usd_equivalent": usd_equivalent, "native_equivalent": trx_equivalent},
            "USDT_TRC20": {"points": user.points_balance, "usd_equivalent": usd_equivalent, "native_equivalent": usd_equivalent},
        },
    }


# =========================================================================
# DEPOSIT ADDRESS — via local TRON HD wallet (Mainnet), one per user,
# always returns the exact same address on every call (no regeneration).
# =========================================================================

@router.get("/addresses")
def get_deposit_addresses(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Returns this user's permanent deposit addresses for every currency this
    deployment actually supports: TRX and USDT-TRC20. TRON (Mainnet) is
    derived once from TRON_MASTER_SEED on first call, stored, and reused
    every time after that. USDT-TRC20 reuses that SAME TRON address (it's a
    token transfer on the same chain, not a separate address). No private
    key or seed is ever returned by this endpoint, for any currency.

    BTC is intentionally NOT offered here (removed by request) — the BitGo
    integration code itself (wallet_gen_service.get_or_create_user_btc_
    address, bitgo_service.py) is left untouched, just never called from
    this route, so re-enabling it later is a one-line change if ever wanted.

    "address"/"addresses[0]" (existing top-level fields) are UNCHANGED —
    still the TRON address, first in the list — so nothing that already
    reads this endpoint breaks.
    """
    try:
        trx_address = wallet_gen_service.get_or_create_user_tron_address(db, user.id)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=f"Could not create deposit address: {e}")

    addresses = [
        {"currency": TRON_ADDRESS_CURRENCY_LABEL, "address": trx_address, "network": "TRON Mainnet"},
        {"currency": "USDT_TRC20", "address": trx_address, "network": "TRON Mainnet (TRC20)"},
    ]

    return {
        "address": trx_address,
        "addresses": addresses,
    }


# =========================================================================
# NOTIFICATIONS — deposit confirmations show up here (polled by the frontend)
# =========================================================================

@router.get("/notifications", response_model=list[schemas.NotificationOut])
def get_notifications(
    unread_only: bool = True,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    q = db.query(models.Notification).filter_by(user_id=user.id)
    if unread_only:
        q = q.filter_by(is_read=False)
    return q.order_by(models.Notification.created_at.desc()).limit(20).all()


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    n = db.query(models.Notification).filter_by(id=notification_id, user_id=user.id).first()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    n.is_read = True
    db.commit()
    return {"ok": True}


# =========================================================================
# WITHDRAW — TRX and USDT-TRC20 via the dedicated TRON withdrawal wallet
# (Mainnet). Never trusts anything from the frontend: identity,
# balance, and address are all re-verified here against the database and
# the network. payload.currency picks the rail — TRX_NILE is the default
# and its exact math/behavior below is UNCHANGED from the original TRX-only
# version of this endpoint. BTC is intentionally not offered (removed by
# request) — see the whitelist check just below.
# =========================================================================

@router.post("/withdraw")
def withdraw(
    payload: schemas.WithdrawRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),   # <- 1) JWT verified, user resolved
):
    # --- 2) Idempotency: block an accidental duplicate submit of the same request ---
    if payload.idempotency_key:
        dup = db.query(models.Withdrawal).filter_by(idempotency_key=payload.idempotency_key).first()
        if dup:
            return {"message": "Withdrawal already submitted", "withdrawal_id": dup.id, "status": dup.status}

    currency = (payload.currency or "TRX_NILE").strip()
    if currency not in ("TRX_NILE", "USDT_TRC20"):
        # NOTE: BTC removed by request — was "TRX_NILE", "USDT_TRC20", "BTC".
        # This whitelist is the actual enforcement point: even a raw API call
        # with currency="BTC" now cleanly gets this same message rather than
        # attempting a real BitGo send.
        return {"success": False, "message": "Currency not available yet"}

    # --- 3) Validate amount using Decimal (never trust float precision for money) ---
    try:
        requested_points = Decimal(str(payload.amount_points))
    except InvalidOperation:
        raise HTTPException(status_code=400, detail="Invalid amount")

    if requested_points <= 0:
        raise HTTPException(status_code=400, detail="Invalid amount")
    if payload.amount_points < MIN_WITHDRAW_AMOUNT:
        raise HTTPException(status_code=400, detail=f"Minimum withdrawal is {MIN_WITHDRAW_AMOUNT} points")

    # --- 4) Validate destination TRON address (both rails left are TRON-based) ---
    address = payload.address.strip()
    if not tron_service.is_valid_tron_address(address):
        raise HTTPException(status_code=400, detail="Invalid TRON address")

    # --- Real amount to send on THIS rail, plus a USD-equivalent used only
    #      for the manual-review threshold below. TRX_NILE math here is
    #      BYTE-FOR-BYTE identical to what this endpoint always did. ---
    if currency == "TRX_NILE":
        amount_native = (requested_points / Decimal(POINTS_PER_USDT)).quantize(Decimal("0.000001"))
        amount_usd_equivalent = amount_native  # same 1:1 convention already used everywhere else in this app
        native_label = "TRX"
    else:  # USDT_TRC20
        amount_native = (requested_points / Decimal(POINTS_PER_USDT)).quantize(Decimal("0.000001"))
        amount_usd_equivalent = amount_native  # USDT is ~1:1 with USD
        native_label = "USDT"

    if amount_native <= 0:
        raise HTTPException(status_code=400, detail="Amount too small")

    # --- 5) Balance check under row-level locking where the DB backend supports it,
    #         to avoid a race condition between two concurrent withdrawal requests ---
    try:
        locked_user = db.query(models.User).filter_by(id=user.id).with_for_update().first()
    except Exception:
        db.rollback()
        locked_user = db.query(models.User).filter_by(id=user.id).first()  # e.g. SQLite: no-op fallback
    current_balance = Decimal(str(locked_user.points_balance))

    if requested_points > current_balance:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    # --- 6) Reserve the balance immediately (before sending) to prevent double-spend ---
    locked_user.points_balance = float(current_balance - requested_points)

    withdrawal = models.Withdrawal(
        user_id=user.id, amount_usdt=float(amount_usd_equivalent), address=address,
        idempotency_key=payload.idempotency_key, status=models.WithdrawalStatus.PENDING,
        currency=currency, amount_native=float(amount_native),
    )
    db.add(withdrawal)
    db.commit()
    db.refresh(withdrawal)

    currency_display = {"TRX_NILE": "TRX (TRON Mainnet)", "USDT_TRC20": "USDT (TRC20, TRON Mainnet)"}[currency]
    email_service.send_withdrawal_request_email(
        user_email=user.email, user_name=user.full_name,
        amount_points=payload.amount_points, amount_usdt=float(amount_usd_equivalent),
        currency=currency_display, address=address, withdrawal_id=withdrawal.id,
    )
    db.add(models.Notification(
        user_id=user.id, type="WITHDRAW_SUBMITTED", title="Withdrawal requested",
        message=f"Your withdrawal request of {amount_native} {native_label} has been submitted and is being reviewed.",
    ))
    db.commit()

    # --- 7) Large withdrawals wait for manual admin approval instead of auto-sending ---
    if float(amount_usd_equivalent) > AUTO_WITHDRAW_MAX_USDT:
        withdrawal.status = models.WithdrawalStatus.PENDING_REVIEW
        db.commit()

        # NOTE: no completion email here — PENDING_REVIEW is not a final state.
        # The email fires later, only once this withdrawal actually reaches
        # COMPLETED with a valid tx_hash (see withdrawal_monitor.py).
        return {
            "message": "Withdrawal request submitted and pending manual review",
            "withdrawal_id": withdrawal.id, "status": withdrawal.status, "currency": currency,
        }

    # --- 8) Send on the correct rail. withdrawal.id doubles as a memo-embedded
    #         / sequenceId idempotency marker on every rail, so a retry never
    #         causes a double-send from our side. ---
    withdrawal.status = models.WithdrawalStatus.PROCESSING
    db.commit()

    try:
        if currency == "TRX_NILE":
            amount_sun = int(amount_native * SUN_PER_TRX)
            result = tron_service.send_trx_withdrawal(
                to_address=address, amount_sun=amount_sun, sequence_id=withdrawal.id,
            )
            tx_hash = result.get("tx_hash")
        else:  # USDT_TRC20 (the only other currency reaching this point — see the whitelist check above)
            result = tron_service.send_trc20_withdrawal(
                to_address=address, amount_token=float(amount_native),
                contract_address=USDT_TRC20_CONTRACT, decimals=USDT_TRC20_DECIMALS,
                sequence_id=withdrawal.id,
            )
            tx_hash = result.get("tx_hash")

        if not tx_hash:
            raise RuntimeError(f"{currency} send did not return a transaction id: {result}")

        withdrawal.tx_hash = tx_hash
        withdrawal.status = models.WithdrawalStatus.PROCESSING  # يتأكد لاحقاً عبر /withdraw/status
        db.commit()
    except (tron_service.TronServiceError, RuntimeError) as e:
        # Refund points since the actual send failed
        locked_user.points_balance = float(Decimal(str(locked_user.points_balance)) + requested_points)
        withdrawal.status = models.WithdrawalStatus.FAILED
        db.commit()
        raise HTTPException(status_code=502, detail=f"Could not send withdrawal: {e}")

    db.add(models.Transaction(
        user_id=user.id, type=models.TransactionType.WITHDRAW, amount=-payload.amount_points,
        description=f"Withdrew {amount_native} {native_label} to {address[:6]}...{address[-4:]} (tx {withdrawal.tx_hash})",
    ))
    db.commit()

    # NOTE: no completion email here either — at this point status is still
    # PROCESSING (the send succeeded, but on-chain confirmation hasn't been
    # verified yet). The email fires once withdrawal_monitor / /withdraw/status
    # actually observes it reach COMPLETED with a valid tx_hash (BTC isn't
    # auto-finalized yet — see withdrawal_monitor.py's currency guard).

    return {
        "message": "Withdrawal submitted successfully",
        "withdrawal_id": withdrawal.id,
        "tx_hash": withdrawal.tx_hash,
        "amount": str(amount_native),
        "currency": currency,
        "status": withdrawal.status,
        "new_balance": locked_user.points_balance,
    }


@router.get("/withdraw/status/{withdrawal_id}")
def withdrawal_status(
    withdrawal_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    w = db.query(models.Withdrawal).filter_by(id=withdrawal_id, user_id=user.id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Withdrawal not found")

    # If it's still processing, ask TRON directly for the latest confirmation
    # state. Shared with the background poller in withdrawal_monitor.py so the
    # COMPLETED transition + completion email logic lives in exactly one place.
    withdrawal_monitor.check_and_finalize(db, w, user)

    return {"status": w.status, "amount": w.amount_usdt, "tx_hash": w.tx_hash, "currency": w.currency, "amount_native": w.amount_native}


@router.get("/transactions/{tx_hash}")
def lookup_transaction(
    tx_hash: str,
    user: models.User = Depends(get_current_user),
):
    """يجلب معلومات معاملة TRON مباشرة من الشبكة بالـ hash"""
    try:
        return tron_service.get_transaction_info(tx_hash)
    except tron_service.TronServiceError as e:
        raise HTTPException(status_code=404, detail=str(e))


# =========================================================================
# SHOP (unchanged)
# =========================================================================

@router.get("/shop", response_model=list[dict])
def get_shop(db: Session = Depends(get_db)):
    items = db.query(models.ShopItem).filter(models.ShopItem.is_active == True).all()
    return [{"id": i.id, "name": i.name, "cost_points": i.cost_points, "category": i.category} for i in items]


@router.post("/redeem")
def redeem_item(
    payload: schemas.RedeemRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    item = db.query(models.ShopItem).filter(models.ShopItem.id == payload.item_id).first()
    if not item or not item.is_active:
        raise HTTPException(status_code=404, detail="Item not found")
    if user.points_balance < item.cost_points:
        raise HTTPException(status_code=400, detail="Insufficient balance")

    user.points_balance -= item.cost_points
    db.add(models.Transaction(
        user_id=user.id, type=models.TransactionType.REDEEM, amount=-item.cost_points,
        description=f"Redeemed: {item.name}",
    ))
    db.commit()
    return {"message": f"Successfully redeemed {item.name}", "new_balance": user.points_balance}
