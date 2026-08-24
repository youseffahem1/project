"""
منطق دفتر الأدمن (Admin Ledger) — كل التعامل مع رصيد المنصة يمر من هنا فقط،
حتى ما يتكرر منطق "زيادة/نقصان الرصيد + إنشاء سجل حركة" بأكثر من مكان.

لا يلمس أي جدول أو منطق موجود سابقاً (Transaction, Deposit, Withdrawal...) —
فقط يضيف على AdminLedger / LedgerTransaction الجديدين.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from . import models


def get_or_create_ledger(db: Session, currency: str, lock: bool = False) -> models.AdminLedger:
    q = db.query(models.AdminLedger).filter_by(currency=currency)
    if lock:
        try:
            q = q.with_for_update()
        except Exception:
            pass  # e.g. SQLite: no-op fallback, same pattern used in wallet_routes.py
    ledger = q.first()
    if ledger is None:
        ledger = models.AdminLedger(currency=currency, total_balance=0.0)
        db.add(ledger)
        db.flush()  # يحصل على id بدون commit كامل بعد، يسمح للمنادي يكمل بنفس المعاملة
    return ledger


def credit_ledger(
    db: Session,
    currency: str,
    amount: float,
    tx_type: models.LedgerTxType,
    user_id: str = None,
    reference_id: str = None,
    status: models.LedgerTxStatus = models.LedgerTxStatus.COMPLETED,
) -> models.LedgerTransaction:
    """يضيف amount (لازم تكون موجبة) لرصيد الأدمن ويسجل حركة. لا يعمل commit — المنادي يتحكم بالمعاملة."""
    if amount <= 0:
        raise ValueError("credit_ledger amount must be positive")

    ledger = get_or_create_ledger(db, currency, lock=True)
    ledger.total_balance = float(Decimal(str(ledger.total_balance)) + Decimal(str(amount)))
    ledger.updated_at = datetime.utcnow()

    tx = models.LedgerTransaction(
        type=tx_type, user_id=user_id, currency=currency, amount=float(amount),
        status=status, reference_id=reference_id,
    )
    db.add(tx)
    return tx


def debit_ledger(
    db: Session,
    currency: str,
    amount: float,
    tx_type: models.LedgerTxType,
    user_id: str = None,
    reference_id: str = None,
    status: models.LedgerTxStatus = models.LedgerTxStatus.PENDING,
) -> models.LedgerTransaction:
    """
    يخصم amount (لازم تكون موجبة) من رصيد الأدمن ويسجل حركة سالبة. يرفع ValueError
    لو الرصيد غير كافٍ (يمنع رصيد سالب). لا يعمل commit — المنادي يتحكم بالمعاملة.
    """
    if amount <= 0:
        raise ValueError("debit_ledger amount must be positive")

    ledger = get_or_create_ledger(db, currency, lock=True)
    current = Decimal(str(ledger.total_balance))
    requested = Decimal(str(amount))
    if requested > current:
        raise ValueError("Insufficient ledger balance")

    ledger.total_balance = float(current - requested)
    ledger.updated_at = datetime.utcnow()

    tx = models.LedgerTransaction(
        type=tx_type, user_id=user_id, currency=currency, amount=-float(amount),
        status=status, reference_id=reference_id,
    )
    db.add(tx)
    return tx


def user_has_crypto_deposited(db: Session, user_id: str) -> bool:
    """NEW: unlike user_has_deposited() below (which counts ANY deposit
    method, including NGN), this checks ONLY real crypto deposits (TRON
    blockchain TRX/USDT-TRC20/BTC, or the legacy Binance Pay flow) — used as
    the gate for the Crypto Balance ($) dynamic spin specifically, so an
    NGN-only depositor gets an accurate "deposit required" message instead
    of passing the gate and then failing on balance."""
    has_chain_deposit = (
        db.query(models.BlockchainDeposit.id)
        .filter_by(user_id=user_id, credited=True)
        .first()
        is not None
    )
    if has_chain_deposit:
        return True

    has_legacy_deposit = (
        db.query(models.Deposit.id)
        .filter_by(user_id=user_id, status=models.DepositStatus.CONFIRMED)
        .first()
        is not None
    )
    return has_legacy_deposit


def user_has_deposited(db: Session, user_id: str) -> bool:
    """يتحقق إذا كان للمستخدم أي إيداع مؤكد سابقاً (من أي مصدر — بلوكتشين، Binance Pay، أو تحويل بنكي نيجيري معتمد)."""
    has_chain_deposit = (
        db.query(models.BlockchainDeposit.id)
        .filter_by(user_id=user_id, credited=True)
        .first()
        is not None
    )
    if has_chain_deposit:
        return True

    has_legacy_deposit = (
        db.query(models.Deposit.id)
        .filter_by(user_id=user_id, status=models.DepositStatus.CONFIRMED)
        .first()
        is not None
    )
    if has_legacy_deposit:
        return True

    # NEW: an admin-approved Nigerian bank transfer counts as a real deposit
    # too — otherwise a user who only ever deposited this way would have
    # points_balance > 0 but /api/spin/status would still say has_deposited
    # = false and block them from spinning. Required for the feature to work.
    has_ngn_deposit = (
        db.query(models.NigerianDeposit.id)
        .filter_by(user_id=user_id, status=models.NigerianDepositStatus.APPROVED)
        .first()
        is not None
    )
    return has_ngn_deposit


def get_user_lifetime_deposit_points(db: Session, user_id: str) -> float:
    """Sum of Points ever actually credited to this user from confirmed
    CRYPTO deposits only (TRON blockchain + legacy Binance Pay). Used only
    to pick a deposit-tier for the Crypto Balance ($) dynamic spin's prize
    probabilities in spin_routes.py — never writes anything, never touches
    points_balance.

    NGN deposits are deliberately excluded here (see get_user_ngn_deposit_tier
    below for the NGN-side equivalent) — they used to leak in through
    NigerianDeposit.points_credited, a legacy column that despite its name
    stores the raw NGN amount, not an actual points conversion (see
    nigerian_deposit_routes.py). Including it here silently mixed NGN naira
    units into what's supposed to be a pure Points/USD tier total, which
    would corrupt the Crypto spin's deposit-tier boost. Removed."""
    from sqlalchemy import func
    from .config import POINTS_PER_USDT

    total = 0.0

    chain_total = (
        db.query(func.sum(models.BlockchainDeposit.points_credited))
        .filter_by(user_id=user_id, credited=True)
        .scalar()
    )
    total += float(chain_total or 0)

    legacy_total_usdt = (
        db.query(func.sum(models.Deposit.amount_usdt))
        .filter_by(user_id=user_id, status=models.DepositStatus.CONFIRMED)
        .scalar()
    )
    total += float(legacy_total_usdt or 0) * POINTS_PER_USDT

    return total


def get_user_deposit_tier(db: Session, user_id: str):
    """Returns (tier_label, multiplier) from config.SPIN_DEPOSIT_TIERS_POINTS
    based on this user's lifetime deposited Points."""
    from .config import SPIN_DEPOSIT_TIERS_POINTS

    lifetime = get_user_lifetime_deposit_points(db, user_id)
    label, multiplier = SPIN_DEPOSIT_TIERS_POINTS[0][1], SPIN_DEPOSIT_TIERS_POINTS[0][2]
    for min_points, tier_label, tier_multiplier in SPIN_DEPOSIT_TIERS_POINTS:
        if lifetime >= min_points:
            label, multiplier = tier_label, tier_multiplier
    return label, multiplier


def get_user_lifetime_ngn_deposits(db: Session, user_id: str) -> float:
    """Sum of real Naira ever approved for this user via the Nigerian bank
    transfer flow ONLY — no Points math anywhere in this function. Used to
    pick an NGN deposit-tier for the NGN-native dynamic spin
    (spin_routes.py's /api/spin/play family)."""
    from sqlalchemy import func

    ngn_total = (
        db.query(func.sum(models.NigerianDeposit.amount_ngn))
        .filter_by(user_id=user_id, status=models.NigerianDepositStatus.APPROVED)
        .scalar()
    )
    return float(ngn_total or 0)


def get_user_ngn_deposit_tier(db: Session, user_id: str):
    """Returns (tier_label, multiplier) from config.SPIN_DEPOSIT_TIERS_NGN
    based on this user's lifetime approved NGN deposits."""
    from .config import SPIN_DEPOSIT_TIERS_NGN

    lifetime = get_user_lifetime_ngn_deposits(db, user_id)
    label, multiplier = SPIN_DEPOSIT_TIERS_NGN[0][1], SPIN_DEPOSIT_TIERS_NGN[0][2]
    for min_ngn, tier_label, tier_multiplier in SPIN_DEPOSIT_TIERS_NGN:
        if lifetime >= min_ngn:
            label, multiplier = tier_label, tier_multiplier
    return label, multiplier


# =========================================================================
# NEW (Feature 1 — Refer & Earn): live-editable referral reward amount.
# Reads/writes the tiny generic AdminSetting key/value table (models.py) —
# this is what makes the reward amount admin-configurable AT RUNTIME (via
# GET/PUT /api/admin/settings/referral-reward) with no redeploy needed,
# unlike a plain env var.
# =========================================================================

REFERRAL_REWARD_SETTING_KEY = "referral_reward_ngn"


def get_referral_reward_amount(db: Session) -> float:
    from .config import DEFAULT_REFERRAL_REWARD_NGN

    row = db.query(models.AdminSetting).filter_by(key=REFERRAL_REWARD_SETTING_KEY).first()
    if row is None:
        return DEFAULT_REFERRAL_REWARD_NGN
    try:
        return float(row.value)
    except (TypeError, ValueError):
        return DEFAULT_REFERRAL_REWARD_NGN


def set_referral_reward_amount(db: Session, amount_ngn: float) -> float:
    from datetime import datetime

    row = db.query(models.AdminSetting).filter_by(key=REFERRAL_REWARD_SETTING_KEY).first()
    if row is None:
        row = models.AdminSetting(key=REFERRAL_REWARD_SETTING_KEY, value=str(amount_ngn))
        db.add(row)
    else:
        row.value = str(amount_ngn)
        row.updated_at = datetime.utcnow()
    db.commit()
    return amount_ngn


# =========================================================================
# NEW: Admin Win Boost — a live-toggleable flag stored in the same generic
# AdminSetting key/value table as the referral reward above. While enabled,
# NGN spins made by an ADMIN account resolve to the largest prize configured
# across the active NGN tiers instead of the normal weighted draw (see
# spin_routes.py / spin_tier_service.py). Never affects non-admin users, and
# the flag is re-checked server-side at spin time — nothing is trusted from
# the client beyond its authenticated identity.
# =========================================================================

ADMIN_WIN_BOOST_SETTING_KEY = "admin_win_boost_enabled"


def is_admin_win_boost_enabled(db: Session) -> bool:
    """Defaults to False when the admin has never toggled it — the boost is
    strictly opt-in, so a fresh deployment behaves exactly as before."""
    row = db.query(models.AdminSetting).filter_by(key=ADMIN_WIN_BOOST_SETTING_KEY).first()
    if row is None:
        return False
    return str(row.value).strip().lower() in ("1", "true", "yes", "on")


def set_admin_win_boost_enabled(db: Session, enabled: bool) -> bool:
    from datetime import datetime

    row = db.query(models.AdminSetting).filter_by(key=ADMIN_WIN_BOOST_SETTING_KEY).first()
    if row is None:
        row = models.AdminSetting(key=ADMIN_WIN_BOOST_SETTING_KEY, value=str(bool(enabled)))
        db.add(row)
    else:
        row.value = str(bool(enabled))
        row.updated_at = datetime.utcnow()
    db.commit()
    return bool(enabled)


# NEW: the exact NGN amount an admin wins per boosted spin. Optional — when
# unset (or <= 0), boosted spins fall back to the largest prize configured
# across the active NGN tiers (the original behavior).
ADMIN_WIN_BOOST_AMOUNT_KEY = "admin_win_boost_amount_ngn"


def get_admin_win_boost_amount(db: Session):
    """Returns the admin's configured winning amount, or None when no valid
    custom amount has ever been saved (any stored value that fails to parse
    or is <= 0 is treated exactly like 'not set' — never trusted blindly)."""
    row = db.query(models.AdminSetting).filter_by(key=ADMIN_WIN_BOOST_AMOUNT_KEY).first()
    if row is None:
        return None
    try:
        amount = float(row.value)
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def set_admin_win_boost_amount(db: Session, amount_ngn: float) -> float:
    from datetime import datetime

    row = db.query(models.AdminSetting).filter_by(key=ADMIN_WIN_BOOST_AMOUNT_KEY).first()
    if row is None:
        row = models.AdminSetting(key=ADMIN_WIN_BOOST_AMOUNT_KEY, value=str(float(amount_ngn)))
        db.add(row)
    else:
        row.value = str(float(amount_ngn))
        row.updated_at = datetime.utcnow()
    db.commit()
    return float(amount_ngn)
