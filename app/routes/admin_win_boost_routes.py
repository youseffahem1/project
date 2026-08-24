"""
NEW (additive only): Admin Win Boost toggle for the NGN Smart Dynamic Wheel.

While enabled, NGN spins made by an ADMIN account resolve to the largest
prize configured across all active NGN tiers — regardless of play amount —
so the operator can play a tiny amount and still land the top prize. The
flag lives in the generic AdminSetting key/value table (the same mechanism
as the referral-reward setting), applies to ADMIN accounts ONLY, and is
re-checked server-side at spin time; regular players' odds, outcomes, and
balances are never touched.

Protected by the exact same get_current_admin dependency every other admin
endpoint already uses — no changes to authentication anywhere. Does not
touch any other route file; spin_routes.py only READS what's written here
(via ledger_service).
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..auth import get_current_admin
from ..ledger_service import (
    is_admin_win_boost_enabled,
    set_admin_win_boost_enabled,
    get_admin_win_boost_amount,
    set_admin_win_boost_amount,
)

router = APIRouter(prefix="/api/admin/win-boost", tags=["admin-win-boost"])


class WinBoostToggle(BaseModel):
    enabled: bool


class WinBoostAmount(BaseModel):
    amount: float = Field(gt=0)


@router.get("")
def get_win_boost(
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    return {
        "enabled": is_admin_win_boost_enabled(db),
        "custom_amount": get_admin_win_boost_amount(db),
    }


@router.post("")
def set_win_boost(
    payload: WinBoostToggle,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    enabled = set_admin_win_boost_enabled(db, payload.enabled)
    return {
        "enabled": enabled,
        "message": (
            "Win Boost ON — your NGN spins now land your configured prize"
            if enabled
            else "Win Boost OFF — NGN spins use the normal prize tables again"
        ),
    }


@router.put("/amount")
def update_win_boost_amount(
    payload: WinBoostAmount,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    """Saves the exact NGN amount the admin wins per boosted spin (the
    dashboard field's 'Done' button). Takes effect on the next spin while
    the boost toggle is ON — it never flips the toggle itself."""
    amount = set_admin_win_boost_amount(db, payload.amount)
    return {"custom_amount": amount, "message": f"Winning amount saved: ₦{amount:,.2f}"}
