"""
إعدادات التطبيق العامة.
لاحقاً لو ودك تربط بوابة دفع حقيقية (USDT / P2P) كل ما عليك تعديله هنا
وفي payment_service.py بدون ما تلمس باقي الكود.
"""
import os
from datetime import timedelta

# --- الأمان ---
SECRET_KEY = os.environ.get("APP_SECRET_KEY", "CHANGE_THIS_SECRET_KEY_IN_PRODUCTION_1234567890")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # يوم كامل

# --- قاعدة البيانات ---
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./luckyspin.db")

# --- Binance Pay (إيداع) ---
BINANCE_PAY_API_KEY = os.environ.get("BINANCE_PAY_API_KEY", "")
BINANCE_PAY_API_SECRET = os.environ.get("BINANCE_PAY_API_SECRET", "")
BINANCE_PAY_MERCHANT_ID = os.environ.get("BINANCE_PAY_MERCHANT_ID", "")
BINANCE_PAY_BASE_URL = "https://bpay.binanceapi.com"

# --- Blockchain deposit addresses (per-user, HD wallet) ---
# *** لا تحط الـ mnemonic هنا مباشرة أبداً — خزنها بـ env variable أو secret manager ***
MASTER_SEED_PHRASE = os.environ.get("MASTER_SEED_PHRASE", "")  # 12/24 كلمة BIP39

SUPPORTED_DEPOSIT_CURRENCIES = ["USDT_TRC20", "BTC"]

# APIs مراقبة البلوكتشين (تحتاج تسجل حساب مجاني عندهم)
BLOCKCYPHER_TOKEN = os.environ.get("BLOCKCYPHER_TOKEN", "")   # قديم — غير مستخدم (BTC الآن عبر BitGo، شوف الأسفل)
# TRONGRID_API_KEY و USDT_TRC20_CONTRACT معرّفين بقسم "real multi-currency deposit/withdraw" بالأسفل

BLOCKCHAIN_POLL_INTERVAL_SECONDS = 30   # كل كم ثانية نفحص العناوين عن إيداعات جديدة
MIN_CONFIRMATIONS_BTC = 2
MIN_CONFIRMATIONS_TRC20 = 19

# --- Binance Withdraw API (سحب) — يحتاج مفتاح API منفصل بصلاحية Withdraw فقط ---
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET", "")
BINANCE_BASE_URL = "https://api.binance.com"
WITHDRAW_ASSET = "USDT"
WITHDRAW_NETWORK = "TRC20"

# سحوبات فوق هذا المبلغ تحتاج موافقة أدمن يدوية بدل الإرسال التلقائي (حماية من الاحتيال)
AUTO_WITHDRAW_MAX_USDT = 50.0

# نسبة تحويل: كم نقطة = 1 USDT (لازم تحدد هذا حسب اقتصاد لعبتك)
POINTS_PER_USDT = 100  # مثال: 100 نقطة = 1 USDT

# --- Admin Ledger / paid spin (NEW) ---
# سعر السبن الواحد بالنقاط وبما يعادله بالدولار — بنفس نسبة POINTS_PER_USDT أعلاه
SPIN_FEE_POINTS = 100          # 100 نقطة = 1$ لكل سبن
SPIN_FEE_USD = 1.0

# =========================================================================
# NEW: "Smart Dynamic Wheel" — variable play-amount spin, NGN-native.
# Fully additive: the original fixed 100-point POST /api/spin endpoint
# above is completely untouched and keeps working exactly as before (still
# Points-denominated, for the crypto side of the app). This section powers
# a SEPARATE endpoint (POST /api/spin/play) where a Nigerian player picks
# how much NAIRA to play — no Points, no conversion, real NGN in and out.
# =========================================================================
SPIN_PLAY_PRESETS_NGN = [1000, 5000, 10000, 15000, 20000, 30000]
SPIN_MIN_PLAY_NGN = 1000
SPIN_MAX_PLAY_NGN = 30000
SPIN_CUSTOM_AMOUNT_ALLOWED = True

# The wheel ALWAYS visually shows exactly these 11 segments, in this exact
# order, no matter what the player's play amount is — this is purely the
# graphic/marketing wheel face. Order here is what the frontend draws; it is
# NOT related to probability. The real, enforced rule lives in
# build_dynamic_prize_table() below: whatever the play amount is, only the
# segment values from this list that are <= play_amount are ever eligible to
# be picked as the prize — so ₦2,500/₦10,000/etc. are visible on the wheel
# during a ₦1,000 spin, but can mathematically never be the outcome of that
# spin. The backend is the only place that decides prize; result is always
# one of THESE exact values, never an arbitrary number, so the frontend can
# reliably spin the wheel to land on whichever value the backend returned.
WHEEL_DISPLAY_VALUES_NGN = [20000, 30000, 15000, 10000, 2500, 1000, 800, 400, 350, 200, 0]

# Deposit-tier probability boost — a small, capped nudge toward the bigger
# prizes for players who've deposited more NGN in total (lifetime, approved
# Nigerian bank transfers only — this is NGN-native, no Points involved).
# tier: (min_lifetime_ngn_deposited, label, top_prize_probability_multiplier)
SPIN_DEPOSIT_TIERS_NGN = [
    (0,     "Tier 0", 1.00),
    (5000,  "Tier 1", 1.00),
    (10000, "Tier 2", 1.10),
    (25000, "Tier 3", 1.20),
]

# =========================================================================
# NEW: USD-denominated "Smart Dynamic Wheel" — the exact same mechanism as
# the NGN block above (variable play amount, server-decided prize capped at
# play_amount, deposit-tier boost), but for players spinning with Crypto
# Balance (points_balance, fed by TRX/USDT/BTC deposits — see
# wallet_routes.py). Fully additive: nothing in the NGN block above is
# changed, reused, or affected by this.
# =========================================================================
SPIN_PLAY_PRESETS_USD = [1, 2, 10, 20, 50, 100, 200, 500, 1000]
SPIN_MIN_PLAY_USD = 1
SPIN_MAX_PLAY_USD = 5000

# Same rule as WHEEL_DISPLAY_VALUES_NGN above: the wheel graphic always shows
# this full fixed set, but only the values <= play_amount are ever eligible
# to actually be picked as the prize (enforced server-side in
# build_dynamic_prize_table) — so a $5,000 prize can be visible on the wheel
# during a $10 spin, but can mathematically never be that spin's outcome.
WHEEL_DISPLAY_VALUES_USD = [5000, 3000, 1000, 500, 200, 100, 50, 20, 15, 10, 9, 2, 1, 0]

# Deposit-tier probability boost for Crypto Balance players — same shape and
# same effect as SPIN_DEPOSIT_TIERS_NGN, thresholds in POINTS (100 points =
# $1, the same rate every other crypto feature in this app already uses)
# since that's the unit lifetime crypto deposits are tracked in.
SPIN_DEPOSIT_TIERS_POINTS = [
    (0,    "Tier 0", 1.00),
    (500,  "Tier 1", 1.00),
    (1000, "Tier 2", 1.10),
    (2500, "Tier 3", 1.20),
]

# العملات المدعومة حالياً لسحب الأدمن من الـ ledger (نفس عملة محفظة السحب الحالية فقط،
# لازم تطابق TRON_ADDRESS_CURRENCY_LABEL بالأسفل — نفس القيمة النصية لتفادي استيراد دائري)
LEDGER_SUPPORTED_CURRENCIES = ["TRX_NILE"]

WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://yourdomain.com/api/wallet/webhook/binance-pay")

# --- BitGo — ACTIVATED for BTC deposits/withdrawals (see below in the
#     "real multi-currency deposit/withdraw" section). Was previously fully
#     disconnected from the live flow while only TRON existed; now used
#     specifically for BTC, since BitGo (a real custody provider that holds
#     the actual keys itself) is far safer than this app hand-rolling BTC
#     key management/signing in one pass. TRON deposit/withdraw above is
#     completely untouched and does NOT go through BitGo. ***
# *** لا مفاتيح خاصة أو seed هنا أبداً — BitGo يدير المفاتيح، إحنا بس نتكلم مع API ***
BITGO_ACCESS_TOKEN = os.environ.get("BITGO_ACCESS_TOKEN", "")
BITGO_WALLET_ID = os.environ.get("BITGO_WALLET_ID", "")
BITGO_COIN = os.environ.get("BITGO_COIN", "tbtc")    # tbtc = Bitcoin TESTNET, btc = Bitcoin MAINNET
BITGO_ENV = os.environ.get("BITGO_ENV", "test")      # "test" أو "prod" — لازم "prod" فعلياً لـ Mainnet حقيقي
# بعض أنواع المحافظ (hot wallet مو custodial) تحتاج passphrase لفك تشفير المفتاح
# داخل BitGo نفسها وقت الإرسال. هذا مو seed ولا مفتاح خاص — نمرره فقط، BitGo يستخدمه.
BITGO_WALLET_PASSPHRASE = os.environ.get("BITGO_WALLET_PASSPHRASE", "")

BITGO_POLL_INTERVAL_SECONDS = 30   # كل كم ثانية نفحص محفظة BitGo عن إيداعات جديدة
BITGO_MIN_CONFIRMATIONS = 1        # عدد التأكيدات المطلوبة قبل ما نحسب الإيداع مؤكد
BITGO_COIN_DECIMALS = 6            # TRX/TRC20 = 6 خانات عشرية (لتحويل المبلغ لأصغر وحدة عند السحب)

# --- TRON Mainnet (Deposit HD Wallet + Withdrawal Wallet) ---
# *** Deposit addresses: derived locally from TRON_MASTER_SEED, no private keys stored in DB ***
# *** Withdrawal: separate dedicated hot wallet, NOT the same seed as deposit addresses ***
TRON_NETWORK = os.environ.get("TRON_NETWORK", "mainnet")
TRON_API_URL = os.environ.get("TRON_API_URL", "https://api.trongrid.io")
TRON_RPC_URL = os.environ.get("TRON_RPC_URL", "") or TRON_API_URL

TRON_MASTER_SEED = os.environ.get("TRON_MASTER_SEED", "")  # BIP39 mnemonic, deposit addresses only

# محفظة سحب منفصلة تماماً — على TRON Mainnet، أموال حقيقية
TRON_WITHDRAWAL_ADDRESS = os.environ.get("TRON_WITHDRAWAL_ADDRESS", "")
TRON_WITHDRAWAL_PRIVATE_KEY = os.environ.get("TRON_WITHDRAWAL_PRIVATE_KEY", "")

TRON_MIN_CONFIRMATIONS = int(os.environ.get("TRON_MIN_CONFIRMATIONS", "1"))
TRON_POLL_INTERVAL_SECONDS = int(os.environ.get("TRON_POLL_INTERVAL_SECONDS", "30"))
TRON_ADDRESS_CURRENCY_LABEL = "TRX_NILE"   # القيمة المخزنة بحقل currency بجدول UserWallet
SUN_PER_TRX = 1_000_000                     # TRX له 6 خانات عشرية (sun = أصغر وحدة)

# =========================================================================
# NEW: real multi-currency deposit/withdraw — TRX, USDT-TRC20, BTC.
# Fully additive: everything above (TRON_NETWORK, TRON_API_URL, the deposit
# HD wallet, the withdrawal wallet) is completely untouched. This section
# only adds the missing pieces (USDT token-transfer detection + config,
# BTC via BitGo, and price-based "$5 equivalent" minimums) on top of it.
# =========================================================================

# --- USDT-TRC20: same TRON address as TRX above, different asset on top ---
# TronGrid API key (used for BOTH native TRX and USDT-TRC20 calls once set) —
# now that TRON_API_URL defaults to real Mainnet, this is effectively
# REQUIRED for reasonable rate limits (get one free at https://www.trongrid.io).
TRONGRID_API_KEY = os.environ.get("TRONGRID_API_KEY", "")
# Official USDT-TRC20 contract address on TRON MAINNET (verified,
# publicly-documented Tether contract — the same value works correctly now
# that TRON_API_URL above actually points at Mainnet instead of Nile).
USDT_TRC20_CONTRACT = os.environ.get("USDT_TRC20_CONTRACT", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")
USDT_TRC20_DECIMALS = 6
USDT_MIN_CONFIRMATIONS = int(os.environ.get("USDT_MIN_CONFIRMATIONS", str(TRON_MIN_CONFIRMATIONS)))

# --- BTC: via BitGo (see bitgo_service.py) — NOT a locally-held private key.
# Real BTC key management (UTXO selection, fee estimation, signing) is a
# large, high-risk surface to hand-roll; BitGo is an established custody
# provider that holds the actual keys, which is the safer path here. This
# requires a real BitGo account — see README/report for exact ENV needed.
BTC_MIN_CONFIRMATIONS = int(os.environ.get("BTC_MIN_CONFIRMATIONS", "2"))
BTC_ADDRESS_CURRENCY_LABEL = "BTC"

# --- Minimum deposits, per currency, "$5 equivalent" via a REAL exchange
# rate (see price_service.py) — never a hardcoded guess. USDT is ~1:1 USD
# so it uses MIN_DEPOSIT_USD directly; TRX/BTC convert through a live price.
MIN_DEPOSIT_USD = float(os.environ.get("MIN_DEPOSIT_USD", "5"))
# Optional fixed-rate overrides — set these to skip the CoinGecko price
# lookup entirely (e.g. no outbound internet from this server, or you'd
# rather control the rate manually). Leave empty/0 to use live pricing.
TRX_USD_RATE_OVERRIDE = float(os.environ.get("TRX_USD_RATE", "0") or 0) or None
BTC_USD_RATE_OVERRIDE = float(os.environ.get("BTC_USD_RATE", "0") or 0) or None
PRICE_CACHE_SECONDS = int(os.environ.get("PRICE_CACHE_SECONDS", "120"))
PRICE_STALE_MAX_SECONDS = int(os.environ.get("PRICE_STALE_MAX_SECONDS", "3600"))

# Currencies this deployment actually supports for blockchain deposit/withdraw
# (NGN is separate — see the Nigerian deposit section further below).
CRYPTO_CURRENCIES = ["TRX", "USDT_TRC20", "BTC"]

# --- Outbound email: Email API over HTTPS/443 (NOT SMTP — Render Free blocks
#     outbound SMTP ports like 587/465, which is why this was switched off
#     smtplib entirely). Uses Resend's HTTPS API (https://api.resend.com/emails).
#     Get a free API key at https://resend.com — no port 587 involved anywhere.
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
# Sender identity Resend will show as "From". Must be an address on a domain
# you've verified in Resend, OR Resend's own onboarding sender
# ("LuckySpin <onboarding@resend.dev>") while testing. Not a secret — just
# not guessed here since it depends on your Resend account setup.
EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
ADMIN_NOTIFICATION_EMAIL = os.environ.get("ADMIN_NOTIFICATION_EMAIL", "vibeainlo@gmail.com")

# --- NEW: admin notification emails for signup / confirmed deposit / withdrawal ---
# Separate from ADMIN_NOTIFICATION_EMAIL above (which is only for withdrawal *requests*)
# so the existing withdrawal-request email flow is never touched.
ADMIN_EVENTS_EMAIL = os.environ.get("ADMIN_EVENTS_EMAIL", "luckyspin.notifications@gmail.com")

# --- إعدادات اللعبة (نقاط بدل فلوس حقيقية حالياً) ---
CURRENCY_NAME = "نقطة"          # اسم العملة الافتراضية المعروضة للمستخدم
CURRENCY_SYMBOL = "PT"           # الرمز المستخدم في الواجهة

SPIN_COOLDOWN_HOURS = 24         # فتره الانتظار بين كل سبن مجاني
REDEEM_UNLOCK_THRESHOLD = 500    # عدد النقاط اللي لازم "تودعها/تجمعها" عشان تفتح السحب/الاستبدال
MIN_WITHDRAW_AMOUNT = 100        # أقل عدد نقاط يقدر يسحبه المستخدم بعد فتح الحساب

# احتمالات وجوائز عجلة الحظ - تحسب في السيرفر فقط (منع أي تلاعب من المتصفح)
WHEEL_SEGMENTS = [
    {"label": "0.5", "value": 0.5, "weight": 25},
    {"label": "1",   "value": 1,   "weight": 22},
    {"label": "2",   "value": 2,   "weight": 20},
    {"label": "3",   "value": 3,   "weight": 15},
    {"label": "5",   "value": 5,   "weight": 10},
    {"label": "10",  "value": 10,  "weight": 5},
    {"label": "20",  "value": 20,  "weight": 3},
]

DAILY_BONUS_SCHEDULE = [0.10, 0.20, 0.30, 0.40, 0.50, 0.75, 1.00]  # نقاط لكل يوم متتالي (7 أيام)

# نظام VIP بناء على مجموع النقاط اللي جمعها المستخدم عبر الوقت (XP)
VIP_LEVELS = [
    {"name": "Bronze",   "min_xp": 0},
    {"name": "Silver",   "min_xp": 1000},
    {"name": "Gold",     "min_xp": 5000},
    {"name": "Platinum", "min_xp": 15000},
]

# =========================================================================
# NEW: Nigerian Bank Transfer deposit (manual, admin-approved) — additive
# only, does not touch POINTS_PER_USDT or any existing conversion above.
# =========================================================================
NGN_BANK_NAME = os.environ.get("NGN_BANK_NAME", "Moniepoint MFB")
NGN_ACCOUNT_NAME = os.environ.get("NGN_ACCOUNT_NAME", "Nnaemeka Micheal Ochonma")
NGN_ACCOUNT_NUMBER = os.environ.get("NGN_ACCOUNT_NUMBER", "5120736698")
# كم نايرا = نقطة واحدة. عدّلها من env حسب سعر الصرف الفعلي وقت التشغيل —
# لا يوجد سعر صرف NGN موجود سابقاً بالمشروع، فهذه القيمة أضيفت خصيصاً لهذه الميزة.
NGN_PER_POINT = float(os.environ.get("NGN_PER_POINT", "8"))
MIN_DEPOSIT_NGN = float(os.environ.get("MIN_DEPOSIT_NGN", "5000"))
MIN_WITHDRAWAL_NGN = float(os.environ.get("MIN_WITHDRAWAL_NGN", "5000"))

# NEW (Feature 1 — Refer & Earn):
# - MIN_REFERRAL_QUALIFYING_DEPOSIT_NGN: the referred user's deposit must be
#   at least this much for their referrer to get paid. Env-only (needs a
#   redeploy to change) — for a value an admin can change live from the
#   dashboard, see DEFAULT_REFERRAL_REWARD_NGN below instead.
# - DEFAULT_REFERRAL_REWARD_NGN: fallback reward amount used ONLY the first
#   time, before any admin has ever set one via PUT /api/admin/settings/referral-reward
#   — after that, the live value lives in the admin_settings table (see
#   ledger_service.get_referral_reward_amount), not here.
MIN_REFERRAL_QUALIFYING_DEPOSIT_NGN = float(os.environ.get("MIN_REFERRAL_QUALIFYING_DEPOSIT_NGN", "5000"))
DEFAULT_REFERRAL_REWARD_NGN = float(os.environ.get("DEFAULT_REFERRAL_REWARD_NGN", "500"))
NIGERIAN_DEPOSIT_UPLOAD_DIR = os.environ.get("NIGERIAN_DEPOSIT_UPLOAD_DIR", "uploads/nigerian_deposits")
MAX_PROOF_UPLOAD_MB = 5
ALLOWED_PROOF_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_PROOF_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}

# =========================================================================
# NEW: bootstrap the very first Admin account from environment variables —
# for hosts like Render's free tier that have no Shell/SSH access, so
# create_admin.py (which needs an interactive terminal) can't be run there.
# No email/password is ever hardcoded here: both come from env only, and
# both are optional — if either is unset, the bootstrap step in main.py
# simply does nothing (no crash, no accidental admin). See main.py's
# bootstrap_admin_from_env() for the actual create-or-promote logic.
# =========================================================================
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_FULL_NAME = os.environ.get("ADMIN_FULL_NAME", "Admin")
