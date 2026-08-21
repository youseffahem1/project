from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=80)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: str
    email: str
    full_name: str
    points_balance: float
    locked_points: float
    lifetime_xp: float
    is_unlocked: bool
    vip_level: str
    created_at: datetime
    ngn_balance: float = 0.0   # NEW: real Naira balance, separate from points_balance

    class Config:
        from_attributes = True


class SpinResult(BaseModel):
    success: bool = True
    label: str
    value: float
    fee_charged: float = 0.0
    new_balance: float
    new_locked: float
    next_spin_at: Optional[datetime] = None


# =========================================================================
# NEW: variable play-amount "Smart Dynamic Wheel" — additive, does not
# touch SpinResult above (still used by the original fixed POST /api/spin).
# =========================================================================

class DynamicSpinRequest(BaseModel):
    play_amount: float = Field(gt=0)
    currency: str = Field(default="NGN")   # NEW: "NGN" or "USD" — which balance to play with; never mixed


class DynamicSpinResult(BaseModel):
    success: bool = True
    currency: str = "NGN"   # NEW: "NGN" or "USD" — tells the frontend which symbol to show, never guessed client-side
    play_amount: float
    winning_amount: float   # renamed from `prize` — same meaning, this is the exact contract name requested
    new_balance: float
    transaction_id: str
    status: str = "WIN"  # "WIN" or "LOSS" — set by the route from winning_amount > 0


class TransactionOut(BaseModel):
    id: str
    type: str
    amount: float
    currency: str = "POINTS"   # NEW: "POINTS" or "NGN" — tells the frontend which unit `amount` is in
    description: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class UnlockRequest(BaseModel):
    amount: float = Field(gt=0)


class RedeemRequest(BaseModel):
    item_id: str


class DepositInitRequest(BaseModel):
    amount_usdt: float = Field(gt=0, le=10000)


class DepositInitResponse(BaseModel):
    deposit_id: str
    checkout_url: str
    qr_code: Optional[str] = None
    expire_time: Optional[int] = None


class WithdrawRequest(BaseModel):
    amount_points: float = Field(gt=0)
    address: str = Field(min_length=10, max_length=120)
    currency: str = Field(default="USDT_TRC20")
    idempotency_key: Optional[str] = Field(default=None, max_length=100)


class LedgerTransactionOut(BaseModel):
    id: str
    type: str
    user_id: Optional[str]
    currency: str
    amount: float
    status: str
    reference_id: Optional[str]
    tx_hash: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class AdminLedgerOut(BaseModel):
    currency: str
    total_balance: float
    total_deposits: float
    total_spin_revenue: float
    total_withdrawals: float
    available_balance: float
    transactions: list[LedgerTransactionOut]


class AdminWithdrawRequest(BaseModel):
    currency: str
    amount: float = Field(gt=0)
    destination_address: str = Field(min_length=10, max_length=120)
    idempotency_key: Optional[str] = Field(default=None, max_length=100)


class NotificationOut(BaseModel):
    id: str
    type: str
    title: str
    message: str
    is_read: bool
    created_at: datetime

    class Config:
        from_attributes = True


# =========================================================================
# NEW: Nigerian Bank Transfer deposit — additive only.
# =========================================================================

class NigerianDepositOut(BaseModel):
    id: str
    user_id: str
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    amount_ngn: float
    points_credited: Optional[float] = None
    status: str
    admin_note: Optional[str] = None
    rejection_reason: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class NigerianDepositRejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class NigerianWithdrawalOut(BaseModel):
    id: str
    user_id: str
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    amount_ngn: float
    account_name: str
    account_number: str
    bank_name: Optional[str] = None
    status: str
    admin_note: Optional[str] = None
    rejection_reason: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class NigerianWithdrawalCreateRequest(BaseModel):
    amount_ngn: float = Field(gt=0)
    account_name: str = Field(min_length=2, max_length=120)
    account_number: str = Field(min_length=4, max_length=40)
    bank_name: Optional[str] = Field(default=None, max_length=120)


class NigerianWithdrawalRejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
