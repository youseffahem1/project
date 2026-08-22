from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from sqlalchemy.orm import Session

from datetime import datetime
import secrets
import string

from .. import models, schemas
from ..database import get_db
from ..auth import hash_password, verify_password, create_access_token
from .. import email_service
from fastapi import BackgroundTasks

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _generate_unique_referral_code(db: Session) -> str:
    """NEW (Feature 1): 8-char alphanumeric code, checked for collisions
    against the DB (astronomically unlikely, but checked anyway rather than
    trusted blindly)."""
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(20):
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        if not db.query(models.User).filter_by(referral_code=code).first():
            return code
    raise RuntimeError("Could not generate a unique referral code")


@router.post("/signup", response_model=schemas.TokenResponse)
def signup(
    payload: schemas.SignupRequest,
    db: Session = Depends(get_db),
background_tasks: BackgroundTasks = None,):
    existing = db.query(models.User).filter(
        models.User.email == payload.email.lower()
    ).first()

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Email already in use"
        )

    # NEW (Feature 1): resolve the referrer (if a valid code was given)
    # BEFORE creating the user, so we never end up with a half-linked
    # referral. An unknown/invalid code is silently ignored — it doesn't
    # block signup, matching how most referral systems behave.
    referred_by_user_id = None
    if payload.referral_code:
        referrer = db.query(models.User).filter_by(referral_code=payload.referral_code.strip().upper()).first()
        if referrer:
            referred_by_user_id = referrer.id

    user = models.User(
        email=payload.email.lower(),
        full_name=payload.full_name.strip(),
        password_hash=hash_password(payload.password),
        referral_code=_generate_unique_referral_code(db),
        referred_by_user_id=referred_by_user_id,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    # Send admin notification AFTER signup without blocking the response
    background_tasks.add_task(
        email_service.send_registration_email,
        user_name=user.full_name,
        user_email=user.email,
        signup_time=datetime.utcnow(),
    )

    token = create_access_token({"sub": user.id})
    return schemas.TokenResponse(access_token=token)


@router.post("/login", response_model=schemas.TokenResponse)
def login(payload: schemas.LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == payload.email.lower()).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account is suspended")

    token = create_access_token({"sub": user.id})
    return schemas.TokenResponse(access_token=token)

@router.post("/token")
def token_login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    user = db.query(models.User).filter(
        models.User.email == form_data.username.lower()
    ).first()

    if not user or not verify_password(
        form_data.password,
        user.password_hash
    ):
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password"
        )

    if not user.is_active:
        raise HTTPException(
            status_code=403,
            detail="This account is suspended"
        )

    token = create_access_token({"sub": user.id})

    return {
        "access_token": token,
        "token_type": "bearer"
    }
