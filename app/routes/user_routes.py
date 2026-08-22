from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import desc

from .. import models, schemas
from ..database import get_db
from ..auth import get_current_user
from ..game_logic import get_vip_level

router = APIRouter(prefix="/api/user", tags=["user"])


def _to_user_out(user: models.User) -> schemas.UserOut:
    return schemas.UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        points_balance=user.points_balance,
        locked_points=user.locked_points,
        lifetime_xp=user.lifetime_xp,
        is_unlocked=user.is_unlocked,
        vip_level=get_vip_level(user.lifetime_xp),
        created_at=user.created_at,
        ngn_balance=user.ngn_balance,
        ngn_winnings_balance=user.ngn_winnings_balance,
        usd_winnings_balance=user.usd_winnings_balance,
    )


@router.get("/me", response_model=schemas.UserOut)
def get_me(user: models.User = Depends(get_current_user)):
    return _to_user_out(user)


@router.get("/history", response_model=list[schemas.TransactionOut])
def get_history(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    txs = (
        db.query(models.Transaction)
        .filter(models.Transaction.user_id == user.id)
        .order_by(desc(models.Transaction.created_at))
        .limit(100)
        .all()
    )
    return txs


@router.get("/leaderboard")
def leaderboard(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    top = (
        db.query(models.User)
        .order_by(desc(models.User.lifetime_xp))
        .limit(10)
        .all()
    )
    rows = [
        {"rank": i + 1, "full_name": u.full_name, "lifetime_xp": u.lifetime_xp}
        for i, u in enumerate(top)
    ]
    # رتبة المستخدم الحالي
    all_ranked = db.query(models.User).order_by(desc(models.User.lifetime_xp)).all()
    my_rank = next((i + 1 for i, u in enumerate(all_ranked) if u.id == user.id), None)
    return {"top": rows, "my_rank": my_rank, "my_xp": user.lifetime_xp}
