"""
NEW (additive only): Nigerian Bank Transfer deposit flow.

- User submits a manual bank transfer + payment proof screenshot -> PENDING.
- Admin reviews the proof and Approves (credits user.ngn_balance directly,
  1:1, real NGN — NOT Points — + ledger + Transaction + Notification, all
  in one DB transaction) or Rejects (with a reason, no balance change).

This file does NOT touch auth_routes.py, wallet_routes.py, admin_routes.py,
spin_routes.py, user_routes.py, models.py's existing tables/columns, or any
existing endpoint, request body, response contract, or accounting logic.
It only reuses what already exists: get_current_user / get_current_admin,
ledger_service.credit_ledger(), the existing Transaction/Notification
tables, and the existing email_service safe-send pattern.
"""
import os
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Form, File, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import models, schemas, email_service, ledger_service
from ..database import get_db
from ..auth import get_current_user, get_current_admin
from ..config import (
    NGN_BANK_NAME, NGN_ACCOUNT_NAME, NGN_ACCOUNT_NUMBER,
    NIGERIAN_DEPOSIT_UPLOAD_DIR, MAX_PROOF_UPLOAD_MB, MIN_DEPOSIT_NGN,
    ALLOWED_PROOF_EXTENSIONS, ALLOWED_PROOF_CONTENT_TYPES,
    MIN_REFERRAL_QUALIFYING_DEPOSIT_NGN,
)
from ..ledger_service import get_referral_reward_amount

user_router = APIRouter(prefix="/api/wallet", tags=["nigerian-deposit"])
admin_router = APIRouter(prefix="/api/admin/nigerian-deposits", tags=["admin-nigerian-deposit"])


def _to_out(dep: models.NigerianDeposit, db: Session) -> schemas.NigerianDepositOut:
    u = db.query(models.User).filter_by(id=dep.user_id).first()
    return schemas.NigerianDepositOut(
        id=dep.id, user_id=dep.user_id,
        user_name=u.full_name if u else None,
        user_email=u.email if u else None,
        amount_ngn=dep.amount_ngn,
        points_credited=dep.points_credited,
        status=dep.status.value if hasattr(dep.status, "value") else dep.status,
        admin_note=dep.admin_note,
        rejection_reason=dep.rejection_reason,
        created_at=dep.created_at,
    )


# =========================================================================
# USER-FACING: bank details + create deposit request
# =========================================================================

@user_router.get("/nigerian-deposit/bank-details")
def get_nigerian_bank_details(user: models.User = Depends(get_current_user)):
    """Static, public deposit instructions — safe to expose to any logged-in user."""
    return {
        "bank_name": NGN_BANK_NAME,
        "account_name": NGN_ACCOUNT_NAME,
        "account_number": NGN_ACCOUNT_NUMBER,
        "currency": "NGN",
        "max_upload_mb": MAX_PROOF_UPLOAD_MB,
        "allowed_extensions": sorted(ALLOWED_PROOF_EXTENSIONS),
        "min_deposit_ngn": MIN_DEPOSIT_NGN,
    }


@user_router.post("/nigerian-deposit", response_model=schemas.NigerianDepositOut)
async def create_nigerian_deposit(
    amount_ngn: float = Form(..., gt=0),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    if amount_ngn < MIN_DEPOSIT_NGN:
        raise HTTPException(status_code=400, detail=f"Minimum deposit is ₦{MIN_DEPOSIT_NGN:,.0f}")

    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_PROOF_EXTENSIONS or file.content_type not in ALLOWED_PROOF_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Only JPG, JPEG, PNG or WEBP images are allowed")

    # Duplicate-submission guard: a retry/double-click/network-retry within a
    # few seconds of an identical PENDING request (same user + same amount)
    # returns the existing request instead of creating a second row and
    # firing a second admin email/attachment for the same deposit.
    dedup_window = datetime.utcnow() - timedelta(seconds=20)
    existing = (
        db.query(models.NigerianDeposit)
        .filter(
            models.NigerianDeposit.user_id == user.id,
            models.NigerianDeposit.amount_ngn == amount_ngn,
            models.NigerianDeposit.status == models.NigerianDepositStatus.PENDING,
            models.NigerianDeposit.created_at >= dedup_window,
        )
        .order_by(models.NigerianDeposit.created_at.desc())
        .first()
    )
    if existing:
        return _to_out(existing, db)

    contents = await file.read()
    max_bytes = MAX_PROOF_UPLOAD_MB * 1024 * 1024
    if len(contents) == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(contents) > max_bytes:
        raise HTTPException(status_code=400, detail=f"File too large — max {MAX_PROOF_UPLOAD_MB}MB")

    os.makedirs(NIGERIAN_DEPOSIT_UPLOAD_DIR, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{ext}"
    stored_path = os.path.join(NIGERIAN_DEPOSIT_UPLOAD_DIR, stored_name)
    with open(stored_path, "wb") as f:
        f.write(contents)

    # amount_ngn here is exactly what the user typed — NOT trusted as final
    # truth. It is only ever converted to points inside approve_nigerian_deposit()
    # below, by an admin, after manually verifying the proof screenshot.
    dep = models.NigerianDeposit(
        user_id=user.id,
        amount_ngn=amount_ngn,
        proof_filename=stored_name,
        status=models.NigerianDepositStatus.PENDING,
    )
    db.add(dep)
    db.commit()
    db.refresh(dep)

    try:
        email_service.send_nigerian_deposit_admin_email(
            user_name=user.full_name, user_email=user.email, amount_ngn=amount_ngn,
            bank_name=NGN_BANK_NAME, account_name=NGN_ACCOUNT_NAME, account_number=NGN_ACCOUNT_NUMBER,
            deposit_id=dep.id, created_time=dep.created_at,
            proof_path=stored_path, proof_filename=stored_name,
        )
    except Exception as e:
        # Never let an email failure roll back or fail the deposit request itself.
        print("[nigerian_deposit_routes] admin email failed: " + str(e))

    return _to_out(dep, db)


# =========================================================================
# ADMIN-FACING: list, view proof, approve, reject
# =========================================================================

@admin_router.get("", response_model=list[schemas.NigerianDepositOut])
def list_nigerian_deposits(
    status: str = None,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    q = db.query(models.NigerianDeposit)
    if status:
        q = q.filter(models.NigerianDeposit.status == status)
    rows = q.order_by(models.NigerianDeposit.created_at.desc()).limit(300).all()
    return [_to_out(d, db) for d in rows]


@admin_router.get("/{deposit_id}/proof")
def get_nigerian_deposit_proof(
    deposit_id: str,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    dep = db.query(models.NigerianDeposit).filter_by(id=deposit_id).first()
    if not dep:
        raise HTTPException(status_code=404, detail="Deposit request not found")
    path = os.path.join(NIGERIAN_DEPOSIT_UPLOAD_DIR, dep.proof_filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Proof file not found")
    return FileResponse(path)


@admin_router.post("/{deposit_id}/approve")
def approve_nigerian_deposit(
    deposit_id: str,
    payload: schemas.NigerianDepositApproveRequest,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    # Lock the deposit row first — same with_for_update()-with-SQLite-fallback
    # pattern already used in wallet_routes.py / ledger_service.py — so two
    # rapid Approve clicks can't both pass the PENDING check below.
    try:
        dep = db.query(models.NigerianDeposit).filter_by(id=deposit_id).with_for_update().first()
    except Exception:
        dep = db.query(models.NigerianDeposit).filter_by(id=deposit_id).first()

    if not dep:
        raise HTTPException(status_code=404, detail="Deposit request not found")
    if dep.status != models.NigerianDepositStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Deposit is already {dep.status.value}")

    try:
        target_user = db.query(models.User).filter_by(id=dep.user_id).with_for_update().first()
    except Exception:
        target_user = db.query(models.User).filter_by(id=dep.user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # NGN is real money, credited 1:1 — NEVER converted to Points. This used
    # to divide by NGN_PER_POINT and credit points_balance, which was the
    # root cause of NGN deposits showing up as an unrelated Points figure
    # instead of a real Naira balance. Fixed: straight NGN credit.
    #
    # NEW: credit exactly what the admin typed in after checking the proof
    # screenshot (payload.approved_amount_ngn) — NOT dep.amount_ngn, which is
    # only what the user claimed when submitting the request and is never
    # trusted as final truth (e.g. user typed ₦5000 but the screenshot proof
    # actually shows ₦3000 was sent).
    ngn_amount = float(payload.approved_amount_ngn)

    target_user.ngn_balance = float(Decimal(str(target_user.ngn_balance)) + Decimal(str(ngn_amount)))

    db.add(models.Transaction(
        user_id=target_user.id,
        type=models.TransactionType.DEPOSIT_UNLOCK,
        amount=ngn_amount,
        currency="NGN",
        description=f"Nigerian bank deposit approved — ₦{ngn_amount} credited by admin (requested ₦{dep.amount_ngn}, deposit {dep.id})",
    ))

    ledger_service.credit_ledger(
        db, currency="NGN", amount=ngn_amount,
        tx_type=models.LedgerTxType.NGN_DEPOSIT, user_id=target_user.id, reference_id=dep.id,
    )

    db.add(models.Notification(
        user_id=target_user.id, type="DEPOSIT_SUCCESS",
        title="Deposit Approved",
        message=f"Your deposit has been approved.\n+₦{ngn_amount:,.0f}\nNew balance: ₦{target_user.ngn_balance:,.0f}",
    ))

    dep.status = models.NigerianDepositStatus.APPROVED
    # NOTE: this column is still named points_credited in the DB schema (no
    # migration performed — see delivery report) but now holds the real NGN
    # amount credited, 1:1, not a Points conversion.
    dep.points_credited = ngn_amount
    dep.approved_by = admin.id
    dep.approved_at = datetime.utcnow()

    # NEW (Feature 1 — Refer & Earn): pay out the referrer's reward the
    # FIRST time this referred user's deposit crosses the qualifying
    # threshold — never more than once per referred user, ever, enforced by
    # the unique constraint on ReferralReward.referred_user_id (a second
    # attempt would raise IntegrityError, not silently double-pay).
    if (
        target_user.referred_by_user_id
        and ngn_amount >= MIN_REFERRAL_QUALIFYING_DEPOSIT_NGN
        and not db.query(models.ReferralReward).filter_by(referred_user_id=target_user.id).first()
    ):
        try:
            referrer = db.query(models.User).filter_by(id=target_user.referred_by_user_id).with_for_update().first()
        except Exception:
            referrer = db.query(models.User).filter_by(id=target_user.referred_by_user_id).first()

        if referrer:
            reward_amount = get_referral_reward_amount(db)
            referrer.ngn_winnings_balance = float(Decimal(str(referrer.ngn_winnings_balance)) + Decimal(str(reward_amount)))

            db.add(models.ReferralReward(
                referrer_user_id=referrer.id,
                referred_user_id=target_user.id,
                reward_amount_ngn=reward_amount,
                status=models.ReferralRewardStatus.PAID,
                triggered_deposit_id=dep.id,
                paid_at=datetime.utcnow(),
            ))
            db.add(models.Transaction(
                user_id=referrer.id,
                type=models.TransactionType.REFERRAL_REWARD,
                amount=reward_amount,
                currency="NGN",
                description=f"Referral reward — {target_user.full_name} deposited ₦{ngn_amount:,.0f}",
            ))
            db.add(models.Notification(
                user_id=referrer.id, type="REFERRAL_REWARD",
                title="Referral Reward Earned!",
                message=f"₦{reward_amount:,.0f} was added to your Winnings Balance — your referral made a qualifying deposit.",
            ))

    db.commit()
    db.refresh(dep)
    db.refresh(target_user)

    return {
        "success": True, "message": "Deposit approved", "deposit_id": dep.id,
        "ngn_credited": ngn_amount, "new_balance": target_user.ngn_balance,
    }


@admin_router.post("/{deposit_id}/reject")
def reject_nigerian_deposit(
    deposit_id: str,
    payload: schemas.NigerianDepositRejectRequest,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    try:
        dep = db.query(models.NigerianDeposit).filter_by(id=deposit_id).with_for_update().first()
    except Exception:
        dep = db.query(models.NigerianDeposit).filter_by(id=deposit_id).first()

    if not dep:
        raise HTTPException(status_code=404, detail="Deposit request not found")
    if dep.status != models.NigerianDepositStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Deposit is already {dep.status.value}")

    dep.status = models.NigerianDepositStatus.REJECTED
    dep.rejection_reason = payload.reason
    dep.rejected_by = admin.id
    dep.rejected_at = datetime.utcnow()

    db.add(models.Notification(
        user_id=dep.user_id, type="DEPOSIT_REJECTED",
        title="Deposit Rejected",
        message=f"Your deposit request was rejected.\nReason: {payload.reason}",
    ))

    db.commit()
    db.refresh(dep)

    return {"success": True, "message": "Deposit rejected", "deposit_id": dep.id}


@admin_router.delete("/{deposit_id}")
def delete_nigerian_deposit(
    deposit_id: str,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """
    NEW (additive only): permanently deletes a deposit request record from the
    admin panel (any status — pending/approved/rejected). This only removes the
    row itself; it never changes user balances. A PENDING deposit hasn't
    touched the user's balance yet (credit only happens on approval), and an
    APPROVED/REJECTED deposit has already been fully settled — so there is
    nothing to refund or reverse here, unlike withdrawal deletion.
    """
    try:
        dep = db.query(models.NigerianDeposit).filter_by(id=deposit_id).with_for_update().first()
    except Exception:
        dep = db.query(models.NigerianDeposit).filter_by(id=deposit_id).first()

    if not dep:
        raise HTTPException(status_code=404, detail="Deposit request not found")

    db.delete(dep)
    db.commit()

    return {"success": True, "message": "Deposit deleted", "deposit_id": deposit_id}
