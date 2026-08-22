"""
NEW (additive only): admin-only endpoints to list/search all registered
users and manually grant Winnings Balance to any one of them. Protected by
the exact same get_current_admin dependency every other admin endpoint in
this app already uses (see admin_routes.py / admin_spin_routes.py) — nothing
about authentication itself changes.

Reuses the EXISTING Transaction table as the audit trail for every grant
(two new nullable columns added in models.py: Transaction.admin_id and
Transaction.reason — see startup_migrations.py for the safe, additive
column migration) instead of creating a new balance/ledger system, per spec.

This file NEVER touches ngn_balance (Main/Playing Balance) — only
ngn_winnings_balance. It also never calls anything from
nigerian_deposit_routes.py or blockchain_monitor.py, so a grant here can
never fire a deposit notification or a deposit email — the only
notification created below is explicitly labeled as a bonus, not a deposit.
"""
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..auth import get_current_admin

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])


def _to_grant_history_out(tx: models.Transaction, db: Session) -> schemas.AdminGrantHistoryOut:
    admin_user = db.query(models.User).filter_by(id=tx.admin_id).first() if tx.admin_id else None
    target_user = db.query(models.User).filter_by(id=tx.user_id).first()
    return schemas.AdminGrantHistoryOut(
        transaction_id=tx.id, user_id=tx.user_id,
        user_name=target_user.full_name if target_user else None,
        user_email=target_user.email if target_user else None,
        admin_id=tx.admin_id, admin_name=admin_user.full_name if admin_user else None,
        amount=tx.amount, currency=tx.currency, reason=tx.reason, created_at=tx.created_at,
    )


@router.get("", response_model=list[schemas.AdminUserOut])
def list_users(
    search: str = "",
    limit: int = 100,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """Lists all registered users, newest first. `search` matches (case-
    insensitive) against full name or email, OR an exact User ID — covers
    'search by name, email, or User ID' in one field."""
    q = db.query(models.User)
    search = (search or "").strip()
    if search:
        like = f"%{search}%"
        q = q.filter(or_(
            models.User.full_name.ilike(like),
            models.User.email.ilike(like),
            models.User.id == search,
        ))
    limit = max(1, min(limit, 300))
    return q.order_by(models.User.created_at.desc()).limit(limit).all()


@router.get("/grants", response_model=list[schemas.AdminGrantHistoryOut])
def all_grants(
    limit: int = 200,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """Global feed of every manual Winnings grant ever made by any admin,
    newest first — 'when and how much I added to each user', across all
    users at once."""
    limit = max(1, min(limit, 500))
    rows = (
        db.query(models.Transaction)
        .filter_by(type=models.TransactionType.ADMIN_GRANT)
        .order_by(models.Transaction.created_at.desc())
        .limit(limit)
        .all()
    )
    return [_to_grant_history_out(tx, db) for tx in rows]


@router.get("/{user_id}/grants", response_model=list[schemas.AdminGrantHistoryOut])
def user_grant_history(
    user_id: str,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """Same as above, filtered to one user — for the per-user admin view."""
    rows = (
        db.query(models.Transaction)
        .filter_by(user_id=user_id, type=models.TransactionType.ADMIN_GRANT)
        .order_by(models.Transaction.created_at.desc())
        .all()
    )
    return [_to_grant_history_out(tx, db) for tx in rows]


@router.post("/{user_id}/grant-winnings", response_model=schemas.AdminGrantWinningsResult)
def grant_winnings(
    user_id: str,
    payload: schemas.AdminGrantWinningsRequest,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    # Server-side is the only source of truth: which admin is resolved from
    # the verified JWT (get_current_admin), which user from the :user_id
    # path param, and the amount is re-validated below regardless of what
    # schemas.AdminGrantWinningsRequest already enforces (gt=0) — defense
    # in depth, never trusting a single validation layer for money.
    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="A reason is required")

    amount = Decimal(str(payload.amount))
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")

    try:
        target = db.query(models.User).filter_by(id=user_id).with_for_update().first()
    except Exception:
        db.rollback()
        target = db.query(models.User).filter_by(id=user_id).first()  # SQLite fallback, same pattern as elsewhere

    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Winnings Balance ONLY — never Main/Playing Balance (ngn_balance is not
    # touched anywhere in this function).
    new_winnings = Decimal(str(target.ngn_winnings_balance)) + amount
    target.ngn_winnings_balance = float(new_winnings)

    tx = models.Transaction(
        user_id=target.id,
        type=models.TransactionType.ADMIN_GRANT,
        amount=float(amount),
        currency="NGN",
        description=f"Admin bonus: {reason}",
        admin_id=admin.id,
        reason=reason,
    )
    db.add(tx)

    # Explicitly a bonus notification, never a deposit one — this route
    # never calls anything from the deposit code paths, so no deposit
    # email/notification can ever fire because of this action.
    db.add(models.Notification(
        user_id=target.id, type="ADMIN_GRANT", title="Bonus Added",
        message=f"₦{float(amount):,.2f} was added to your Winnings Balance. Reason: {reason}",
    ))

    db.commit()
    db.refresh(tx)
    db.refresh(target)

    return schemas.AdminGrantWinningsResult(
        transaction_id=tx.id, user_id=target.id, admin_id=admin.id,
        amount=float(amount), currency="NGN", reason=reason,
        new_main_balance=target.ngn_balance, new_winnings_balance=target.ngn_winnings_balance,
        created_at=tx.created_at,
    )
