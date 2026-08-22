"""
NEW (additive only): Nigerian Bank Transfer withdrawal flow — the mirror of
nigerian_deposit_routes.py.

- User submits their bank account details (name + account number + optional
  bank name) + an amount -> the amount is deducted from
  user.ngn_winnings_balance IMMEDIATELY (a "hold"), and a PENDING request
  is created. Deducting up front (not on approval) is what makes it safe
  against a user firing two withdrawal requests before either is reviewed
  — the second one simply fails on insufficient balance, same
  anti-double-spend pattern already used by POST /api/spin/play.
- Admin reviews and Approves (the hold becomes final — admin ledger is
  debited, marking the payout as actually sent outside this system) or
  Rejects (refunds the held amount back to user.ngn_winnings_balance).

NOTE: as of the Main/Winnings balance split, withdrawals draw from
ngn_winnings_balance (spin winnings only) — never ngn_balance (deposit
funds, playing-only, not withdrawable). See models.py's User.ngn_balance /
User.ngn_winnings_balance comments and spin_routes.py's _spin_play_ngn.

This file does NOT touch auth_routes.py, wallet_routes.py, admin_routes.py,
spin_routes.py, user_routes.py, nigerian_deposit_routes.py, models.py's
existing tables/columns, or any existing endpoint, request body, response
contract, or accounting logic. It only reuses what already exists:
get_current_user / get_current_admin, ledger_service, and the existing
Transaction/Notification tables.
"""
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas, ledger_service
from ..database import get_db
from ..auth import get_current_user, get_current_admin
from ..config import MIN_WITHDRAWAL_NGN

user_router = APIRouter(prefix="/api/wallet", tags=["nigerian-withdrawal"])
admin_router = APIRouter(prefix="/api/admin/nigerian-withdrawals", tags=["admin-nigerian-withdrawal"])


def _to_out(w: models.NigerianWithdrawal, db: Session) -> schemas.NigerianWithdrawalOut:
    u = db.query(models.User).filter_by(id=w.user_id).first()
    return schemas.NigerianWithdrawalOut(
        id=w.id, user_id=w.user_id,
        user_name=u.full_name if u else None,
        user_email=u.email if u else None,
        amount_ngn=w.amount_ngn,
        account_name=w.account_name,
        account_number=w.account_number,
        bank_name=w.bank_name,
        status=w.status.value if hasattr(w.status, "value") else w.status,
        admin_note=w.admin_note,
        rejection_reason=w.rejection_reason,
        created_at=w.created_at,
    )


# =========================================================================
# USER-FACING: withdrawal info + create withdrawal request
# =========================================================================

@user_router.get("/nigerian-withdraw/info")
def get_nigerian_withdrawal_info(user: models.User = Depends(get_current_user)):
    """Static, public withdrawal instructions — safe to expose to any logged-in user."""
    return {
        "currency": "NGN",
        "min_withdrawal_ngn": MIN_WITHDRAWAL_NGN,
    }


@user_router.get("/nigerian-withdraw/mine", response_model=list[schemas.NigerianWithdrawalOut])
def list_my_nigerian_withdrawals(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    rows = (
        db.query(models.NigerianWithdrawal)
        .filter_by(user_id=user.id)
        .order_by(models.NigerianWithdrawal.created_at.desc())
        .limit(50)
        .all()
    )
    return [_to_out(w, db) for w in rows]


@user_router.post("/nigerian-withdraw", response_model=schemas.NigerianWithdrawalOut)
def create_nigerian_withdrawal(
    payload: schemas.NigerianWithdrawalCreateRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    if payload.amount_ngn < MIN_WITHDRAWAL_NGN:
        raise HTTPException(status_code=400, detail=f"Minimum withdrawal is ₦{MIN_WITHDRAWAL_NGN:,.0f}")

    account_name = payload.account_name.strip()
    account_number = payload.account_number.strip()
    if not account_name:
        raise HTTPException(status_code=400, detail="Enter the account holder's name")
    if not account_number:
        raise HTTPException(status_code=400, detail="Enter the account number")

    # Row-lock the user for the whole operation — same double-click / race
    # protection pattern as POST /api/spin/play and the crypto withdrawal
    # flow in wallet_routes.py.
    try:
        locked_user = db.query(models.User).filter_by(id=user.id).with_for_update().first()
    except Exception:
        db.rollback()
        locked_user = db.query(models.User).filter_by(id=user.id).first()  # SQLite fallback

    current_balance = Decimal(str(locked_user.ngn_winnings_balance))
    amount_dec = Decimal(str(payload.amount_ngn))
    if current_balance < amount_dec:
        raise HTTPException(status_code=402, detail="Insufficient balance")

    # Hold the funds immediately — deducted now, refunded only if an admin
    # rejects the request. This is what prevents a user from requesting
    # more than their real balance across several pending withdrawals.
    # NEW: withdrawals only ever come out of ngn_winnings_balance (Winnings
    # Balance) — never ngn_balance (Main Playing Balance), which is
    # deposit-funded and playing-only, never withdrawable.
    locked_user.ngn_winnings_balance = float(current_balance - amount_dec)

    w = models.NigerianWithdrawal(
        user_id=user.id,
        amount_ngn=payload.amount_ngn,
        account_name=account_name,
        account_number=account_number,
        bank_name=(payload.bank_name.strip() if payload.bank_name else None),
        status=models.NigerianWithdrawalStatus.PENDING,
    )
    db.add(w)
    db.flush()

    db.add(models.Transaction(
        user_id=locked_user.id,
        type=models.TransactionType.WITHDRAW,
        amount=-payload.amount_ngn,
        currency="NGN",
        description=f"Nigerian bank withdrawal requested — ₦{payload.amount_ngn} (request {w.id})",
    ))

    db.add(models.Notification(
        user_id=locked_user.id, type="WITHDRAWAL_REQUESTED",
        title="Withdrawal Requested",
        message=f"Your withdrawal request for ₦{payload.amount_ngn:,.0f} is being reviewed.\nNew Winnings Balance: ₦{locked_user.ngn_winnings_balance:,.0f}",
    ))

    db.commit()
    db.refresh(w)

    return _to_out(w, db)


# =========================================================================
# ADMIN-FACING: list, approve, reject
# =========================================================================

@admin_router.get("", response_model=list[schemas.NigerianWithdrawalOut])
def list_nigerian_withdrawals(
    status: str = None,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    q = db.query(models.NigerianWithdrawal)
    if status:
        q = q.filter(models.NigerianWithdrawal.status == status)
    rows = q.order_by(models.NigerianWithdrawal.created_at.desc()).limit(300).all()
    return [_to_out(w, db) for w in rows]


@admin_router.post("/{withdrawal_id}/approve")
def approve_nigerian_withdrawal(
    withdrawal_id: str,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    try:
        w = db.query(models.NigerianWithdrawal).filter_by(id=withdrawal_id).with_for_update().first()
    except Exception:
        w = db.query(models.NigerianWithdrawal).filter_by(id=withdrawal_id).first()

    if not w:
        raise HTTPException(status_code=404, detail="Withdrawal request not found")
    if w.status != models.NigerianWithdrawalStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Withdrawal is already {w.status.value}")

    # Funds were already deducted from the user at request time — approving
    # just finalizes it and records the payout in the admin ledger (the
    # actual bank transfer happens manually, outside this system).
    try:
        ledger_service.debit_ledger(
            db, currency="NGN", amount=w.amount_ngn,
            tx_type=models.LedgerTxType.NGN_WITHDRAWAL, user_id=w.user_id, reference_id=w.id,
            status=models.LedgerTxStatus.COMPLETED,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Insufficient ledger balance to approve this payout")

    db.add(models.Notification(
        user_id=w.user_id, type="WITHDRAWAL_SUCCESS",
        title="Withdrawal Approved",
        message=f"Your withdrawal of ₦{w.amount_ngn:,.0f} has been sent.",
    ))

    w.status = models.NigerianWithdrawalStatus.APPROVED
    w.approved_by = admin.id
    w.approved_at = datetime.utcnow()

    db.commit()
    db.refresh(w)

    return {"success": True, "message": "Withdrawal approved", "withdrawal_id": w.id}


@admin_router.post("/{withdrawal_id}/reject")
def reject_nigerian_withdrawal(
    withdrawal_id: str,
    payload: schemas.NigerianWithdrawalRejectRequest,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    try:
        w = db.query(models.NigerianWithdrawal).filter_by(id=withdrawal_id).with_for_update().first()
    except Exception:
        w = db.query(models.NigerianWithdrawal).filter_by(id=withdrawal_id).first()

    if not w:
        raise HTTPException(status_code=404, detail="Withdrawal request not found")
    if w.status != models.NigerianWithdrawalStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Withdrawal is already {w.status.value}")

    # Refund the held amount back to the user — it was deducted at request
    # time from ngn_winnings_balance, so it's refunded there too (never to
    # ngn_balance — a rejected withdrawal must not turn into extra playing
    # balance).
    try:
        target_user = db.query(models.User).filter_by(id=w.user_id).with_for_update().first()
    except Exception:
        target_user = db.query(models.User).filter_by(id=w.user_id).first()
    if target_user:
        target_user.ngn_winnings_balance = float(Decimal(str(target_user.ngn_winnings_balance)) + Decimal(str(w.amount_ngn)))
        db.add(models.Transaction(
            user_id=target_user.id,
            type=models.TransactionType.WITHDRAW,
            amount=w.amount_ngn,
            currency="NGN",
            description=f"Nigerian bank withdrawal rejected — refunded ₦{w.amount_ngn} (request {w.id})",
        ))

    w.status = models.NigerianWithdrawalStatus.REJECTED
    w.rejection_reason = payload.reason
    w.rejected_by = admin.id
    w.rejected_at = datetime.utcnow()

    db.add(models.Notification(
        user_id=w.user_id, type="WITHDRAWAL_REJECTED",
        title="Withdrawal Rejected",
        message=f"Your withdrawal request was rejected and ₦{w.amount_ngn:,.0f} was refunded to your balance.\nReason: {payload.reason}",
    ))

    db.commit()
    db.refresh(w)

    return {"success": True, "message": "Withdrawal rejected", "withdrawal_id": w.id}
