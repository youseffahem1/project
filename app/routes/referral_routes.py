"""
NEW (additive only): Refer & Earn.

- Every user already gets a permanent referral_code at signup (see
  auth_routes.py). This file only exposes it, lists who they've referred
  and the status of each, and lets an admin change the live reward amount.
- The actual reward payout logic lives in nigerian_deposit_routes.py's
  approve_nigerian_deposit() — it fires exactly once per referred user
  (enforced by a unique DB constraint on ReferralReward.referred_user_id),
  the moment their FIRST qualifying deposit (>= MIN_REFERRAL_QUALIFYING_DEPOSIT_NGN)
  is approved by an admin. Nothing here credits balances directly.

This file does NOT touch auth_routes.py, wallet_routes.py, spin_routes.py,
nigerian_deposit_routes.py's existing behavior (only reads from what it
already writes), or any other existing endpoint/table/column.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..auth import get_current_user, get_current_admin
from ..config import MIN_REFERRAL_QUALIFYING_DEPOSIT_NGN
from ..ledger_service import get_referral_reward_amount, set_referral_reward_amount

user_router = APIRouter(prefix="/api/referral", tags=["referral"])
admin_router = APIRouter(prefix="/api/admin/settings", tags=["admin-settings"])


@user_router.get("/me")
def my_referral_info(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    # Backfill for any account created before this feature shipped — old
    # rows have referral_code = NULL since adding a column doesn't retroactively
    # populate existing users (see delivery report: no migration was run).
    if not user.referral_code:
        from ..routes.auth_routes import _generate_unique_referral_code
        user.referral_code = _generate_unique_referral_code(db)
        db.commit()
        db.refresh(user)

    rewards = (
        db.query(models.ReferralReward)
        .filter_by(referrer_user_id=user.id)
        .order_by(models.ReferralReward.created_at.desc())
        .all()
    )
    referred_ids = {r.referred_user_id for r in rewards}
    # Also include people who signed up with this code but haven't hit the
    # deposit threshold yet — they have no ReferralReward row at all.
    pending_referred = (
        db.query(models.User)
        .filter_by(referred_by_user_id=user.id)
        .filter(~models.User.id.in_(referred_ids) if referred_ids else True)
        .all()
    )

    referrals = []
    for r in rewards:
        referred = r.referred
        referrals.append({
            "full_name": referred.full_name if referred else "Unknown",
            "status": r.status.value if hasattr(r.status, "value") else r.status,
            "reward_amount_ngn": r.reward_amount_ngn,
            "joined_at": referred.created_at if referred else None,
            "paid_at": r.paid_at,
        })
    for u in pending_referred:
        referrals.append({
            "full_name": u.full_name,
            "status": "PENDING",
            "reward_amount_ngn": None,
            "joined_at": u.created_at,
            "paid_at": None,
        })
    referrals.sort(key=lambda x: x["joined_at"] or "", reverse=True)

    total_earned = sum(r.reward_amount_ngn or 0 for r in rewards if r.status == models.ReferralRewardStatus.PAID)

    return {
        "referral_code": user.referral_code,
        "reward_amount_ngn": get_referral_reward_amount(db),
        "min_qualifying_deposit_ngn": MIN_REFERRAL_QUALIFYING_DEPOSIT_NGN,
        "total_earned_ngn": total_earned,
        "referrals": referrals,
    }


class ReferralRewardSetting(BaseModel):
    amount_ngn: float = Field(gt=0)


@admin_router.get("/referral-reward")
def get_referral_reward_setting(
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    return {"amount_ngn": get_referral_reward_amount(db)}


@admin_router.put("/referral-reward")
def update_referral_reward_setting(
    payload: ReferralRewardSetting,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    new_amount = set_referral_reward_amount(db, payload.amount_ngn)
    return {"amount_ngn": new_amount}
