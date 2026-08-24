from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=80)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    referral_code: Optional[str] = Field(default=None, max_length=32)  # NEW (Feature 1): optional — who referred them


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
    ngn_winnings_balance: float = 0.0   # NEW: NGN spin winnings only — the only NGN balance withdrawable
    usd_winnings_balance: float = 0.0   # NEW: Crypto/USD spin winnings only — separate from points_balance

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
    new_balance: float      # Main Playing Balance AFTER this spin (deposits only — never includes winnings)
    new_winnings_balance: float = 0.0  # NEW: Winnings Balance AFTER this spin — the only balance withdrawable
    transaction_id: str
    status: str = "WIN"  # "WIN" or "LOSS" — set by the route from winning_amount > 0


class TransferWinningsRequest(BaseModel):
    # NEW (Feature 5): move some/all of Winnings Balance into Main/Playing
    # Balance. amount is optional — omit it to transfer the FULL winnings
    # balance in one go (matches the "Transfer" button UX in the spec).
    amount_ngn: Optional[float] = Field(default=None, gt=0)


class TransferWinningsResult(BaseModel):
    success: bool = True
    transferred_amount: float
    new_main_balance: float
    new_winnings_balance: float
    transaction_id: str


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


class NigerianDepositApproveRequest(BaseModel):
    # NEW: the admin opens the payment proof screenshot, reads the REAL amount
    # that actually shows in it, and types it here. This is the amount that
    # gets credited — NOT necessarily what the user originally typed in
    # amount_ngn (e.g. user claimed ₦5000 but the screenshot only shows ₦3000).
    approved_amount_ngn: float = Field(gt=0)


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


# =========================================================================
# NEW (Prize Tiers): admin-configurable prize tables for the NGN Smart
# Dynamic Wheel — see models.SpinPrizeTier/SpinPrizeValue, spin_tier_
# service.py, and routes/admin_spin_routes.py.
# =========================================================================

class SpinPrizeValueIn(BaseModel):
    prize_amount: float = Field(ge=0)
    weight: float = Field(gt=0)


class SpinPrizeValueOut(BaseModel):
    id: str
    prize_amount: float
    weight: float

    class Config:
        from_attributes = True


class SpinPrizeTierIn(BaseModel):
    currency: str = Field(default="NGN")
    min_play_amount: float = Field(gt=0)
    label: Optional[str] = Field(default=None, max_length=80)
    is_active: bool = True
    prizes: list[SpinPrizeValueIn]


class SpinPrizeTierOut(BaseModel):
    id: str
    currency: str
    min_play_amount: float
    label: Optional[str]
    is_active: bool
    prizes: list[SpinPrizeValueOut]

    class Config:
        from_attributes = True


# =============================================================================
# NEW (additive only): Admin Users list/search + manual Winnings Balance
# grants. Reuses the existing Transaction table as the audit trail (see
# models.py: Transaction.admin_id / Transaction.reason) — no new balance
# system, per spec.
# =============================================================================

class AdminUserOut(BaseModel):
    id: str
    full_name: str
    email: str
    ngn_balance: float = 0.0             # "Main Balance" (Main/Playing Balance)
    ngn_winnings_balance: float = 0.0    # "Winnings Balance"
    is_admin: bool
    created_at: datetime

    class Config:
        from_attributes = True


class AdminGrantWinningsRequest(BaseModel):
    amount: float = Field(gt=0)
    reason: str = Field(min_length=2, max_length=300)


class AdminGrantWinningsResult(BaseModel):
    success: bool = True
    transaction_id: str
    user_id: str
    admin_id: str
    amount: float
    currency: str
    reason: str
    new_main_balance: float
    new_winnings_balance: float
    created_at: datetime


class AdminGrantHistoryOut(BaseModel):
    transaction_id: str
    user_id: str
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    admin_id: Optional[str] = None
    admin_name: Optional[str] = None
    amount: float
    currency: str
    reason: Optional[str] = None
    created_at: datetime


# =========================================================================
# NEW (additive only, admin-only): Admin Win Boost — NGN wheel.
# =========================================================================

class AdminWinBoostToggleRequest(BaseModel):
    enabled: bool


class AdminWinBoostAmountRequest(BaseModel):
    custom_amount: Optional[float] = Field(default=None, gt=0)


class AdminWinBoostOut(BaseModel):
    enabled: bool
    custom_amount: Optional[float] = None
    message: Optional[str] = None
