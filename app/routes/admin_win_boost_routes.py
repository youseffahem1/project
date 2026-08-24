"""
NEW (additive only, admin-only): Admin Win Boost — NGN wheel.

Protected by the exact same get_current_admin dependency every other
admin endpoint already uses — no changes to authentication anywhere.
Does not touch spin_tier_service.py, ledger_service.py, or any existing
route file except one small, clearly-scoped read in
spin_routes.py._spin_play_ngn (see the comment there).
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas, admin_win_boost_service
from ..database import get_db
from ..auth import get_current_admin

router = APIRouter(prefix="/api/admin/win-boost", tags=["admin-win-boost"])


@router.get("", response_model=schemas.AdminWinBoostOut)
def get_win_boost(
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    s = admin_win_boost_service.get_settings(db)
    return schemas.AdminWinBoostOut(enabled=s.enabled, custom_amount=s.custom_amount)


@router.post("", response_model=schemas.AdminWinBoostOut)
def toggle_win_boost(
    payload: schemas.AdminWinBoostToggleRequest,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    s = admin_win_boost_service.set_enabled(db, payload.enabled, admin.id)
    return schemas.AdminWinBoostOut(
        enabled=s.enabled,
        custom_amount=s.custom_amount,
        message=f"Admin Win Boost turned {'ON' if s.enabled else 'OFF'} — "
                f"{'you' if s.enabled else 'you no longer'} can win more than your play amount on the NGN wheel.",
    )


@router.put("/amount", response_model=schemas.AdminWinBoostOut)
def set_win_boost_amount(
    payload: schemas.AdminWinBoostAmountRequest,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    s = admin_win_boost_service.set_custom_amount(db, payload.amount, admin.id)
    return schemas.AdminWinBoostOut(enabled=s.enabled, custom_amount=s.custom_amount)
