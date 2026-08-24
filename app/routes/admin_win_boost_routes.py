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
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..auth import get_current_admin
from ..ledger_service import is_admin_win_boost_enabled, set_admin_win_boost_enabled

router = APIRouter(prefix="/api/admin/win-boost", tags=["admin-win-boost"])


class WinBoostToggle(BaseModel):
    enabled: bool


@router.get("")
def get_win_boost(
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    return {"enabled": is_admin_win_boost_enabled(db)}


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
            "Win Boost ON — your NGN spins now land the largest configured prize"
            if enabled
            else "Win Boost OFF — NGN spins use the normal prize tables again"
        ),
    }
