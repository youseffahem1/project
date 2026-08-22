"""
NEW (additive only, Prize Tiers): admin CRUD for the play-amount-bracket
prize tables powering the NGN Smart Dynamic Wheel. Protected by the exact
same get_current_admin dependency every other admin endpoint already
uses — no changes to authentication anywhere. Does not touch any other
route file; spin_routes.py only READS what's written here (via
spin_tier_service.py).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..auth import get_current_admin

router = APIRouter(prefix="/api/admin/spin-tiers", tags=["admin-spin-tiers"])


def _validate_prizes(prizes: list[schemas.SpinPrizeValueIn]):
    if not prizes:
        raise HTTPException(status_code=400, detail="A tier needs at least one prize value")
    if not any(p.prize_amount == 0 for p in prizes):
        raise HTTPException(status_code=400, detail="Every tier must include a ₦0 (no-win) outcome")
    if len(prizes) != len({p.prize_amount for p in prizes}):
        raise HTTPException(status_code=400, detail="Duplicate prize amounts are not allowed within one tier")


@router.get("", response_model=list[schemas.SpinPrizeTierOut])
def list_tiers(
    currency: str = "NGN",
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    return (
        db.query(models.SpinPrizeTier)
        .filter(models.SpinPrizeTier.currency == currency)
        .order_by(models.SpinPrizeTier.min_play_amount.asc())
        .all()
    )


@router.post("", response_model=schemas.SpinPrizeTierOut)
def create_tier(
    payload: schemas.SpinPrizeTierIn,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    _validate_prizes(payload.prizes)

    existing = (
        db.query(models.SpinPrizeTier.id)
        .filter_by(currency=payload.currency, min_play_amount=payload.min_play_amount)
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="A tier with this play amount already exists for this currency")

    tier = models.SpinPrizeTier(
        currency=payload.currency, min_play_amount=payload.min_play_amount,
        label=payload.label, is_active=payload.is_active,
    )
    db.add(tier)
    db.flush()
    for p in payload.prizes:
        db.add(models.SpinPrizeValue(tier_id=tier.id, prize_amount=p.prize_amount, weight=p.weight))
    db.commit()
    db.refresh(tier)
    return tier


@router.put("/{tier_id}", response_model=schemas.SpinPrizeTierOut)
def update_tier(
    tier_id: str,
    payload: schemas.SpinPrizeTierIn,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    tier = db.query(models.SpinPrizeTier).filter_by(id=tier_id).first()
    if not tier:
        raise HTTPException(status_code=404, detail="Tier not found")
    _validate_prizes(payload.prizes)

    dup = (
        db.query(models.SpinPrizeTier.id)
        .filter(
            models.SpinPrizeTier.currency == payload.currency,
            models.SpinPrizeTier.min_play_amount == payload.min_play_amount,
            models.SpinPrizeTier.id != tier_id,
        )
        .first()
    )
    if dup:
        raise HTTPException(status_code=400, detail="Another tier already uses this play amount for this currency")

    tier.currency = payload.currency
    tier.min_play_amount = payload.min_play_amount
    tier.label = payload.label
    tier.is_active = payload.is_active

    # Replace the whole prize list atomically — simplest, safest admin UX:
    # edit the full table for this tier in one form, save once. The
    # cascade="all, delete-orphan" on SpinPrizeTier.prizes (models.py)
    # means removing them from the relationship here is what actually
    # deletes the old rows.
    for old in list(tier.prizes):
        db.delete(old)
    db.flush()
    for p in payload.prizes:
        db.add(models.SpinPrizeValue(tier_id=tier.id, prize_amount=p.prize_amount, weight=p.weight))

    db.commit()
    db.refresh(tier)
    return tier


@router.delete("/{tier_id}")
def delete_tier(
    tier_id: str,
    db: Session = Depends(get_db),
    admin: models.User = Depends(get_current_admin),
):
    tier = db.query(models.SpinPrizeTier).filter_by(id=tier_id).first()
    if not tier:
        raise HTTPException(status_code=404, detail="Tier not found")
    db.delete(tier)
    db.commit()
    return {"success": True}
