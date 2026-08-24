import uuid
import enum
from datetime import datetime
from sqlalchemy import (
    Column, String, Float, Integer, DateTime, ForeignKey, Enum, Boolean, Text, UniqueConstraint
)
from sqlalchemy.orm import relationship
from .database import Base


def gen_id():
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=gen_id)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # الرصيد
    points_balance = Column(Float, default=0.0)     # نقاط قابلة للاستبدال/السحب (نظام Crypto/Spin القديم)
    locked_points = Column(Float, default=0.0)       # نقاط أرباح مقفولة لحد ما يفتح الحساب
    lifetime_xp = Column(Float, default=0.0)         # مجموع كل النقاط المكتسبة (لتحديد VIP)

    # NEW: رصيد NGN حقيقي ومنفصل تمامًا عن points_balance — لا تحويل بينهما
    # إطلاقًا. كل إيداع/سحب/سبن نيجيري يتعامل مع هذا الحقل فقط.
    ngn_balance = Column(Float, default=0.0)

    # NEW: فصل رصيد اللعب عن رصيد الأرباح — Main Playing Balance مقابل
    # Winnings Balance. ngn_balance أعلاه أصبح الآن "Main Playing Balance"
    # فقط: الإيداعات تزيده، السبن يخصم منه فقط — لا يزيد أبداً من ربح سبن.
    # كل فوز من السبن يروح لهذا الحقل الجديد بدلاً منه، وهو الرصيد الوحيد
    # القابل للسحب (نظام سحب NGN الحالي يتحقق منه، مو من ngn_balance).
    ngn_winnings_balance = Column(Float, default=0.0)

    # NEW: نفس الفكرة لرصيد الكريبتو ($ USD، المخزّن أصلاً كنقاط points_balance
    # مقسومة على POINTS_PER_USDT) — فوز سبن الكريبتو يروح هنا فقط، بالدولار
    # مباشرة، مو نقاط. ملاحظة: نظام سحب الكريبتو الحالي (wallet_routes.py)
    # ما زال يسحب من points_balance كما هو — لم يُعدَّل بهذا التغيير (نطاق
    # التعديل الحالي هو السبن فقط، ونظام سحب الكريبتو ميزة منفصلة قائمة).
    usd_winnings_balance = Column(Float, default=0.0)

    # NEW (Feature 1 — Refer & Earn): every user gets a permanent, unique
    # referral code generated at signup (see auth_routes.py). referred_by
    # is set once, at signup time, if the new user signed up with someone
    # else's code — never editable afterward.
    referral_code = Column(String, unique=True, index=True, nullable=True)
    referred_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)

    is_unlocked = Column(Boolean, default=False)     # فتح ميزة الاستبدال/السحب
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)

    last_spin_at = Column(DateTime, nullable=True)
    last_daily_bonus_at = Column(DateTime, nullable=True)
    daily_bonus_streak = Column(Integer, default=0)

    spins = relationship("SpinHistory", back_populates="user")
    transactions = relationship("Transaction", back_populates="user", foreign_keys="Transaction.user_id")


class SpinHistory(Base):
    __tablename__ = "spin_history"

    id = Column(String, primary_key=True, default=gen_id)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    result_label = Column(String, nullable=False)
    result_value = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="spins")


class TransactionType(str, enum.Enum):
    SPIN_WIN = "SPIN_WIN"
    DAILY_BONUS = "DAILY_BONUS"
    DEPOSIT_UNLOCK = "DEPOSIT_UNLOCK"   # "إيداع" نقاط رمزي لفتح الاستبدال (يحاكي مستقبلاً بوابة دفع حقيقية)
    REDEEM = "REDEEM"                   # استبدال نقاط بمكافأة من المتجر
    WITHDRAW = "WITHDRAW"               # سحب نقاط (لاحقاً = تحويل حقيقي)
    ACHIEVEMENT = "ACHIEVEMENT"
    SPIN_FEE = "SPIN_FEE"                # NEW: خصم رسوم السبن ($1) من رصيد المستخدم
    WINNINGS_TRANSFER = "WINNINGS_TRANSFER"  # NEW: نقل من Winnings Balance إلى Main/Playing Balance
    REFERRAL_REWARD = "REFERRAL_REWARD"      # NEW: مكافأة إحالة صديق بعد إيداعه المؤهل
    ADMIN_GRANT = "ADMIN_GRANT"               # NEW: إضافة يدوية لـ Winnings Balance من الأدمن (Bonus/Manual Reward)


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(String, primary_key=True, default=gen_id)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    type = Column(Enum(TransactionType), nullable=False)
    amount = Column(Float, nullable=False)   # موجب = إضافة / سالب = خصم
    # NEW: أي عملة هذا amount فعليًا — "POINTS" (الافتراضي، يحافظ على كل
    # السجلات القديمة كما هي بدون أي تغيير بالمعنى) أو "NGN" للمعاملات
    # النيجيرية الحقيقية. هذا يمنع اختلاط رقم NGN مع رقم Points بنفس الجدول
    # بدون تمييز.
    currency = Column(String, default="POINTS", nullable=False)
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # NEW (Admin Grant Winnings): only ever set for type=ADMIN_GRANT rows —
    # every other existing Transaction type/row leaves both null, completely
    # unaffected. Reuses this table as the audit trail instead of a new one.
    # Never updated after creation (no PUT/PATCH endpoint touches a
    # Transaction row anywhere in the app) — the id column above already
    # serves as this record's permanent Transaction ID.
    admin_id = Column(String, ForeignKey("users.id"), nullable=True)
    reason = Column(String, nullable=True)

    user = relationship("User", back_populates="transactions", foreign_keys=[user_id])


class BitGoDepositAddress(Base):
    """عنوان إيداع BitGo فريد لكل مستخدم لكل عملة — BitGo يولّده ويدير مفاتيحه بالكامل"""
    __tablename__ = "bitgo_deposit_addresses"

    id = Column(String, primary_key=True, default=gen_id)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    coin = Column(String, nullable=False)                 # مثال: "ttrx"
    address = Column(String, nullable=False, unique=True)
    bitgo_address_id = Column(String, nullable=True)      # معرف العنوان داخل BitGo (id من الرد)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserWallet(Base):
    """عنوان إيداع TRON فريد لكل مستخدم — يُشتق مرة واحدة من TRON HD wallet ويُخزن هنا فقط
    (الـ private key نفسه أبداً ما يُخزن — يُشتق وقت الحاجة من TRON_MASTER_SEED + derivation_index)"""
    __tablename__ = "user_wallets"
    __table_args__ = (
        UniqueConstraint("user_id", "currency", name="uq_user_wallet_user_currency"),
        UniqueConstraint("currency", "derivation_index", name="uq_user_wallet_currency_index"),
    )

    id = Column(String, primary_key=True, default=gen_id)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    currency = Column(String, nullable=False)       # "TRX_NILE" حالياً (كان يدعم BTC/USDT_TRC20 سابقاً)
    address = Column(String, nullable=False, unique=True)
    derivation_index = Column(Integer, nullable=False)  # index فريد لكل مستخدم بمشتق HD wallet
    created_at = Column(DateTime, default=datetime.utcnow)


class BlockchainDeposit(Base):
    """سجل كل إيداع اكتشفناه على البلوكتشين — tx_hash فريد يمنع الاحتساب المزدوج"""
    __tablename__ = "blockchain_deposits"

    id = Column(String, primary_key=True, default=gen_id)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    currency = Column(String, nullable=False)
    tx_hash = Column(String, nullable=False, unique=True)
    sender = Column(String, nullable=True)           # عنوان المرسل (استخرج من الشبكة)
    receiver = Column(String, nullable=True)          # عنوان الاستلام (عنوان المستخدم)
    block_number = Column(Integer, nullable=True)
    amount_crypto = Column(Float, nullable=False)
    amount_usdt_equivalent = Column(Float, nullable=False)
    points_credited = Column(Float, nullable=False)
    confirmations = Column(Integer, default=0)
    status = Column(String, default="CONFIRMED")      # CONFIRMED فقط نضيف السجل بعدها أصلاً
    credited = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(String, primary_key=True, default=gen_id)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    type = Column(String, nullable=False)   # "DEPOSIT_SUCCESS", "WITHDRAW_SUBMITTED"...
    title = Column(String, nullable=False)
    message = Column(String, nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class DepositStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class Deposit(Base):
    __tablename__ = "deposits"

    id = Column(String, primary_key=True, default=gen_id)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    amount_usdt = Column(Float, nullable=False)
    prepay_id = Column(String, nullable=True)        # من Binance Pay
    checkout_url = Column(String, nullable=True)
    status = Column(Enum(DepositStatus), default=DepositStatus.PENDING)
    created_at = Column(DateTime, default=datetime.utcnow)
    confirmed_at = Column(DateTime, nullable=True)


class WithdrawalStatus(str, enum.Enum):
    PENDING = "PENDING"                 # تم إنشاء الطلب، لسه ما انبعث لـ BitGo
    PENDING_REVIEW = "PENDING_REVIEW"   # سحوبات كبيرة تنتظر موافقة أدمن
    PROCESSING = "PROCESSING"           # انبعث لـ BitGo، بانتظار تأكيد الشبكة
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class Withdrawal(Base):
    __tablename__ = "withdrawals"

    id = Column(String, primary_key=True, default=gen_id)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    amount_usdt = Column(Float, nullable=False)   # USD-equivalent amount (kept for backwards compat with the original TRX-only flow)
    address = Column(String, nullable=False)
    binance_withdraw_id = Column(String, nullable=True)   # قديم من BitGo/Binance، غير مستخدم الآن
    provider_ref = Column(String, nullable=True)          # قديم من BitGo، غير مستخدم الآن
    tx_hash = Column(String, nullable=True)                # TRON tx hash (TRX/USDT-TRC20) or BitGo txid (BTC)
    idempotency_key = Column(String, nullable=True, unique=True)  # يمنع تكرار نفس طلب السحب
    status = Column(Enum(WithdrawalStatus), default=WithdrawalStatus.PENDING)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    # NEW (additive): which rail this withdrawal actually went out on, and the
    # real native-currency amount sent — previously every withdrawal was
    # implicitly TRX and amount_usdt doubled as "amount in TRX". Now that
    # USDT-TRC20 and BTC are real options too, we need to know which is which.
    # Both nullable/defaulted so every pre-existing row (all TRX) stays valid.
    currency = Column(String, nullable=True, default="TRX_NILE")
    amount_native = Column(Float, nullable=True)   # e.g. actual BTC or USDT amount sent (None for old TRX rows, which used amount_usdt for this)


class LedgerTxType(str, enum.Enum):
    SPIN_FEE = "SPIN_FEE"
    ADMIN_WITHDRAWAL = "ADMIN_WITHDRAWAL"
    ADJUSTMENT = "ADJUSTMENT"
    NGN_DEPOSIT = "NGN_DEPOSIT"  # NEW: Nigerian bank transfer deposit, credited to ledger on admin approval
    NGN_WITHDRAWAL = "NGN_WITHDRAWAL"  # NEW: Nigerian bank transfer withdrawal, debited from ledger on admin approval


class LedgerTxStatus(str, enum.Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AdminLedger(Base):
    """محفظة/دفتر أرباح المنصة — سجل واحد لكل عملة (مثلاً TRX_NILE)."""
    __tablename__ = "admin_ledger"

    id = Column(String, primary_key=True, default=gen_id)
    currency = Column(String, nullable=False, unique=True)
    total_balance = Column(Float, default=0.0)   # الرصيد المتاح الحالي بهذه العملة
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LedgerTransaction(Base):
    """كل حركة على دفتر الأدمن: رسوم سبن، سحوبات أدمن، تعديلات يدوية."""
    __tablename__ = "ledger_transactions"

    id = Column(String, primary_key=True, default=gen_id)
    type = Column(Enum(LedgerTxType), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)   # null لسحوبات الأدمن/التعديلات
    currency = Column(String, nullable=False)
    amount = Column(Float, nullable=False)   # موجب = إضافة لرصيد الأدمن / سالب = خصم (سحب)
    status = Column(Enum(LedgerTxStatus), default=LedgerTxStatus.COMPLETED)
    reference_id = Column(String, nullable=True)   # مثلاً id سبن أو id سحب مرتبط
    tx_hash = Column(String, nullable=True)         # عند سحب أدمن فعلي على البلوكتشين
    created_at = Column(DateTime, default=datetime.utcnow)


class ShopItem(Base):
    __tablename__ = "shop_items"

    id = Column(String, primary_key=True, default=gen_id)
    name = Column(String, nullable=False)
    cost_points = Column(Float, nullable=False)
    category = Column(String, default="All")
    is_active = Column(Boolean, default=True)


# =========================================================================
# NEW (additive only — no existing table/column touched): Nigerian bank
# transfer deposits. A user submits a manual bank transfer + proof image;
# an admin reviews and approves/rejects it. Nothing here changes how
# blockchain deposits, spins, withdrawals, or the Admin Ledger already work.
# =========================================================================

class NigerianDepositStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class NigerianDeposit(Base):
    __tablename__ = "nigerian_deposits"

    id = Column(String, primary_key=True, default=gen_id)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    amount_ngn = Column(Float, nullable=False)              # ما يدخله المستخدم — ليس مصدر الحقيقة النهائي
    points_credited = Column(Float, nullable=True)          # يُحسب ويُعبّأ فقط عند الموافقة (Backend يحسبه، مو Frontend)
    proof_filename = Column(String, nullable=False)         # اسم الملف المخزّن على القرص (uploads/nigerian_deposits/)
    status = Column(Enum(NigerianDepositStatus), default=NigerianDepositStatus.PENDING, nullable=False)
    admin_note = Column(String, nullable=True)
    approved_by = Column(String, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    rejected_by = Column(String, ForeignKey("users.id"), nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    rejection_reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", foreign_keys=[user_id])


# =========================================================================
# NEW (additive only): Nigerian bank transfer WITHDRAWALS — the mirror of
# NigerianDeposit above. User requests a payout to their own bank account
# (name + account number + amount); the requested amount is held (deducted
# from user.ngn_balance) immediately on request so it can't be double-spent
# by a second pending request, then an admin manually sends the bank
# transfer and marks it Approved, or Rejects it (which refunds the hold).
# Nothing here touches NigerianDeposit, the crypto/points withdrawal system,
# or any existing table/column.
# =========================================================================

class NigerianWithdrawalStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class NigerianWithdrawal(Base):
    __tablename__ = "nigerian_withdrawals"

    id = Column(String, primary_key=True, default=gen_id)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    amount_ngn = Column(Float, nullable=False)
    account_name = Column(String, nullable=False)
    account_number = Column(String, nullable=False)
    bank_name = Column(String, nullable=True)
    status = Column(Enum(NigerianWithdrawalStatus), default=NigerianWithdrawalStatus.PENDING, nullable=False)
    admin_note = Column(String, nullable=True)
    approved_by = Column(String, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    rejected_by = Column(String, ForeignKey("users.id"), nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    rejection_reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", foreign_keys=[user_id])


# =========================================================================
# NEW (additive only): Refer & Earn.
# - AdminSetting: tiny generic key/value store so the referral reward
#   amount (and future admin-tunable numbers) can change at runtime from
#   an admin endpoint, with no redeploy/env var edit needed. Doesn't touch
#   or replace config.py's env-based settings — this is only for values an
#   admin needs to change live from the dashboard.
# - ReferralReward: one row per (referrer, referred) pair, unique on
#   referred_user_id — this is what makes "reward exactly once per
#   referral" enforceable at the database level, not just in application
#   logic (a duplicate insert attempt fails outright).
# =========================================================================

class AdminSetting(Base):
    __tablename__ = "admin_settings"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ReferralRewardStatus(str, enum.Enum):
    PENDING = "PENDING"   # referred user signed up, hasn't deposited ≥ threshold yet
    PAID = "PAID"          # threshold deposit happened, reward credited


class ReferralReward(Base):
    __tablename__ = "referral_rewards"
    __table_args__ = (UniqueConstraint("referred_user_id", name="uq_referral_reward_referred_user"),)

    id = Column(String, primary_key=True, default=gen_id)
    referrer_user_id = Column(String, ForeignKey("users.id"), nullable=False)
    referred_user_id = Column(String, ForeignKey("users.id"), nullable=False)
    reward_amount_ngn = Column(Float, nullable=True)   # set once PAID; NULL while PENDING
    status = Column(Enum(ReferralRewardStatus), default=ReferralRewardStatus.PENDING, nullable=False)
    triggered_deposit_id = Column(String, ForeignKey("nigerian_deposits.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    paid_at = Column(DateTime, nullable=True)

    referrer = relationship("User", foreign_keys=[referrer_user_id])
    referred = relationship("User", foreign_keys=[referred_user_id])


# =========================================================================
# NEW (Prize Tiers): distinct, admin-configurable prize tables per
# play-amount bracket for the NGN Smart Dynamic Wheel. Replaces the old
# "one universal WHEEL_DISPLAY_VALUES_NGN list, filtered by <= play_amount"
# approach for NGN specifically — that old approach is WHY every deposit
# level looked almost identical: the same small values were always
# eligible and always dominated the probability mass, no matter how big
# play_amount was. These two tables are additive only: game_logic.py's
# build_dynamic_prize_table/resolve_dynamic_spin (and everything in
# config.py's WHEEL_DISPLAY_VALUES_NGN/USD) are completely untouched and
# still power the Crypto ($) spin exactly as before — see spin_tier_
# service.py and spin_routes.py for how NGN was switched over to this.
# =========================================================================

class SpinPrizeTier(Base):
    __tablename__ = "spin_prize_tiers"

    id = Column(String, primary_key=True, default=gen_id)
    currency = Column(String, nullable=False, default="NGN")
    # This tier applies to any play_amount >= min_play_amount, up to (but
    # not including) the next active tier's min_play_amount — i.e. the
    # HIGHEST tier whose min_play_amount a given play_amount still meets.
    min_play_amount = Column(Float, nullable=False)
    label = Column(String, nullable=True)   # purely descriptive, e.g. "₦5,000 Tier" — shown in the admin dashboard only
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    prizes = relationship(
        "SpinPrizeValue", back_populates="tier",
        cascade="all, delete-orphan", order_by="SpinPrizeValue.prize_amount",
    )


class SpinPrizeValue(Base):
    """One possible prize amount within a tier, with its own relative
    probability weight — admin-editable. Weights are RELATIVE, not
    percentages: they're normalized (weight / sum of all weights in this
    tier) at resolve-time, so an admin can add/remove/reweight rows without
    needing every tier's weights to add up to any particular total."""
    __tablename__ = "spin_prize_values"

    id = Column(String, primary_key=True, default=gen_id)
    tier_id = Column(String, ForeignKey("spin_prize_tiers.id"), nullable=False)
    prize_amount = Column(Float, nullable=False)
    weight = Column(Float, nullable=False, default=1.0)

    tier = relationship("SpinPrizeTier", back_populates="prizes")


class AdminWinBoostSetting(Base):
    """Singleton row (admin-only, additive, NGN wheel only).

    When enabled, admin accounts — and ONLY admin accounts — bypass the
    normal "prize can never exceed play_amount" hard cap on the NGN Smart
    Dynamic Wheel, so an admin can test/demo winning more than they
    played. This scope guarantee is enforced entirely in spin_routes.py
    (checking user.is_admin before ever consulting this row) — a non-admin
    player's spin never even queries this table, regardless of its value.
    Turning it back OFF makes the admin's own spins go through the exact
    same spin_tier_service.resolve_tiered_spin() path every normal player
    always uses — no special-casing left active anywhere.
    """
    __tablename__ = "admin_win_boost_settings"

    id = Column(String, primary_key=True, default=gen_id)
    enabled = Column(Boolean, default=False, nullable=False)
    # None -> resolve_admin_boosted_spin() falls back to "largest prize
    # configured anywhere". Set -> that exact amount is awarded every time.
    custom_amount = Column(Float, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_admin_id = Column(String, ForeignKey("users.id"), nullable=True)
