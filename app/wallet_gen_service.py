"""
توليد عنوان إيداع TRON فريد لكل مستخدم من محفظة HD واحدة (BIP44)، بدل ما تحتاج
تنشئ محفظة منفصلة يدوياً لكل مستخدم. كل مستخدم ياخذ derivation_index فريد،
ونفس الـ mnemonic دايماً يرجع نفس العنوان لنفس الـ index (deterministic).

*** أمان حرج ***
TRON_MASTER_SEED هو مفتاح كل عناوين الإيداع. لا تحطه بالكود ولا بملف .env عادي
على نفس السيرفر بالإنتاج — استخدم secret manager. هذا الملف لا يخزن ولا يطبع
ولا يرجع الـ private key أو الـ seed لأي API أبداً — فقط العنوان العام.

يحتاج: pip install bip-utils
"""
from sqlalchemy.exc import IntegrityError
from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes

from .config import TRON_MASTER_SEED, TRON_ADDRESS_CURRENCY_LABEL


def _derive_tron_address(index: int) -> str:
    if not TRON_MASTER_SEED:
        raise RuntimeError("TRON_MASTER_SEED غير معرّف في متغيرات البيئة")
    seed_bytes = Bip39SeedGenerator(TRON_MASTER_SEED).Generate()
    bip44 = Bip44.FromSeed(seed_bytes, Bip44Coins.TRON)
    account = bip44.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT)
    return account.AddressIndex(index).PublicKey().ToAddress()


def get_or_create_user_tron_address(db, user_id: str) -> str:
    """
    يرجع عنوان TRON الدائم للمستخدم. يولّده أول مرة فقط ويخزنه بقاعدة البيانات،
    بعدين يرجعه من قاعدة البيانات مباشرة — نفس العنوان دايماً لنفس المستخدم،
    حتى لو استُدعيت الدالة 100 مرة.
    """
    from . import models

    existing = (
        db.query(models.UserWallet)
        .filter_by(user_id=user_id, currency=TRON_ADDRESS_CURRENCY_LABEL)
        .first()
    )
    if existing:
        return existing.address

    # --- اختيار derivation_index فريد بدون race condition ---
    # نحاول index تلو الآخر بدءاً من عدد العناوين الحالية؛ لو صار تعارض تزامني
    # (طلبين بنفس اللحظة) نمسك IntegrityError من قيد التفرد ونجرب index تالي.
    base_index = db.query(models.UserWallet).filter_by(currency=TRON_ADDRESS_CURRENCY_LABEL).count()

    last_error = None
    for attempt in range(10):
        index = base_index + attempt
        address = _derive_tron_address(index)
        wallet = models.UserWallet(
            user_id=user_id, currency=TRON_ADDRESS_CURRENCY_LABEL,
            address=address, derivation_index=index,
        )
        db.add(wallet)
        try:
            db.commit()
            return address
        except IntegrityError as e:
            db.rollback()
            last_error = e
            # طلب متزامن آخر أخذ نفس الـ index أو نفس العنوان — نجرب التالي
            continue

    raise RuntimeError(f"Could not allocate a unique TRON derivation index: {last_error}")


# =========================================================================
# NEW: BTC deposit address, via BitGo (a real custody provider that holds
# the actual keys itself — see bitgo_service.py). Fully additive; the TRON
# functions above are completely untouched. USDT-TRC20 needs NO separate
# address here — it reuses the same TRON address as get_or_create_user_
# tron_address() above, since TRC20 tokens live on the TRON address itself.
# =========================================================================

def get_or_create_user_btc_address(db, user_id: str) -> str:
    """
    Returns this user's permanent BTC deposit address. First call asks
    BitGo for a brand-new receive address on our one BitGo wallet (labeled
    with the user's id so it's identifiable), stores it, and every call
    after that returns the same stored address — never asks BitGo twice for
    the same user. No private key ever touches this app; BitGo holds it.
    """
    from . import models
    from .services import bitgo_service
    from .config import BTC_ADDRESS_CURRENCY_LABEL

    existing = (
        db.query(models.UserWallet)
        .filter_by(user_id=user_id, currency=BTC_ADDRESS_CURRENCY_LABEL)
        .first()
    )
    if existing:
        return existing.address

    result = bitgo_service.create_receive_address(label=f"user:{user_id}")
    address = result.get("address")
    if not address:
        raise RuntimeError(f"BitGo did not return an address: {result}")

    # derivation_index is meaningless for BitGo (it manages key derivation
    # internally) — but UserWallet has a UNIQUE(currency, derivation_index)
    # constraint, so we still need a distinct value per BTC user or the
    # SECOND BTC signup would collide with the first. Reuse the same
    # count-then-retry-on-conflict pattern as the TRON address function
    # above, purely to satisfy that constraint safely under concurrency.
    base_index = db.query(models.UserWallet).filter_by(currency=BTC_ADDRESS_CURRENCY_LABEL).count()
    last_error = None
    for attempt in range(10):
        wallet = models.UserWallet(
            user_id=user_id, currency=BTC_ADDRESS_CURRENCY_LABEL,
            address=address, derivation_index=base_index + attempt,
        )
        db.add(wallet)
        try:
            db.commit()
            return address
        except IntegrityError as e:
            db.rollback()
            last_error = e
            # قد يكون التعارض لأن مستخدم آخر أنشأ عنوان BTC له بنفس اللحظة —
            # تحقق أولاً إذا هذا المستخدم نفسه صار له عنوان بالفعل
            existing = (
                db.query(models.UserWallet)
                .filter_by(user_id=user_id, currency=BTC_ADDRESS_CURRENCY_LABEL)
                .first()
            )
            if existing:
                return existing.address
            continue

    raise RuntimeError(f"Could not allocate a unique BTC wallet row: {last_error}")
