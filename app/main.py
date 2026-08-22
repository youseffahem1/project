from dotenv import load_dotenv

load_dotenv()
import re
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import asyncio

from .database import Base, engine, SessionLocal
from .config import DATABASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD, ADMIN_FULL_NAME
from . import models
from .auth import hash_password
from .routes import auth_routes, user_routes, spin_routes, wallet_routes, admin_routes, nigerian_deposit_routes, nigerian_withdrawal_routes, referral_routes
from . import blockchain_monitor
from . import withdrawal_monitor
from .startup_migrations import run_startup_column_migrations

# --- تحذير واضح باللوج لو السيرفر شغال بدون DATABASE_URL (يعني sqlite محلي) ---
# فقط طباعة تحذيرية — ما توقف السيرفر ولا تغيّر أي سلوك، بس تساعدك تكتشف
# غلطة نسيان ضبط DATABASE_URL على Render قبل ما توصل لمرحلة "ليش بياناتي راحت".
if DATABASE_URL.startswith("sqlite"):
    print(
        "⚠️  WARNING: DATABASE_URL is not set — falling back to local SQLite "
        f"({DATABASE_URL}). This is fine for local development, but on Render "
        "(or any host with an ephemeral filesystem) this means your data does "
        "NOT persist between deploys/restarts. Set DATABASE_URL to your "
        "PostgreSQL connection string in the Render dashboard's Environment tab."
    )

# --- إنشاء الجداول ---
Base.metadata.create_all(bind=engine)

# --- NEW: يضيف تلقائيًا أي عمود ناقص على جدول موجود أصلاً (create_all
# فوق ما بيعمل هيك — بينشئ جداول جديدة بس، مو أعمدة على جدول قديم).
# آمن ويعمل تلقائيًا بكل Startup/Deploy، بدون Render Shell وبدون سكربت
# منفصل. راجع app/startup_migrations.py للتفاصيل الكاملة.
run_startup_column_migrations(engine)

# --- Rate limiting عام لكل الـ API (حماية من هجمات bruteforce / سبام) ---
limiter = Limiter(key_func=get_remote_address, default_limits=["120/minute"])

app = FastAPI(title="LuckySpin API (Points Edition)")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# --- CORS: عدّل origins حسب دومينك عند النشر ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://project-wae1.onrender.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    import traceback
    traceback.print_exc()

    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)}
    )


app.include_router(auth_routes.router)
app.include_router(user_routes.router)
app.include_router(spin_routes.router)
app.include_router(wallet_routes.router)
app.include_router(admin_routes.router)
app.include_router(nigerian_deposit_routes.user_router)
app.include_router(nigerian_deposit_routes.admin_router)
app.include_router(nigerian_withdrawal_routes.user_router)
app.include_router(nigerian_withdrawal_routes.admin_router)
app.include_router(referral_routes.user_router)
app.include_router(referral_routes.admin_router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.on_event("startup")
def seed_shop_items():
    db = SessionLocal()
    try:
        if db.query(models.ShopItem).count() == 0:
            items = [
                models.ShopItem(name="50 Points Card", cost_points=500, category="Cash"),
                models.ShopItem(name="100 Points Card", cost_points=1000, category="Cash"),
                models.ShopItem(name="500 Points Card", cost_points=5000, category="Cash"),
                models.ShopItem(name="Instant Extra Spin", cost_points=300, category="Bonus"),
                models.ShopItem(name="Next Win Multiplier", cost_points=800, category="Special"),
            ]
            db.add_all(items)
            db.commit()
    finally:
        db.close()


@app.on_event("startup")
def bootstrap_admin_from_env():
    """
    NEW: create-or-promote the very first Admin account from ADMIN_EMAIL /
    ADMIN_PASSWORD environment variables — for hosts like Render's free tier
    that have no Shell/SSH access to run create_admin.py interactively.

    Idempotent, safe to run on every single startup/deploy:
      - ADMIN_EMAIL/ADMIN_PASSWORD unset or blank -> does nothing, logs one
        line, does not crash the app.
      - Email that already exists and is already is_admin=True -> no-op.
      - Email that already exists but is_admin=False -> promotes it to
        is_admin=True. Existing password_hash is left completely untouched
        (this never resets a password for a pre-existing account).
      - Email that doesn't exist yet -> creates a new user with is_admin=True,
        password hashed with the exact same hash_password() every normal
        signup already uses.

    The password value itself is NEVER printed/logged, and this has no API
    endpoint of its own — nothing here is reachable over HTTP, and no
    existing endpoint or permission check changes.
    """
    admin_email = (ADMIN_EMAIL or "").strip().lower()
    admin_password = ADMIN_PASSWORD or ""

    if not admin_email and not admin_password:
        # Most common case (env vars simply not set) — stay silent-ish, one line.
        print("ℹ️  Admin bootstrap: ADMIN_EMAIL/ADMIN_PASSWORD not set — skipping.")
        return

    if not admin_email or not admin_password:
        print("⚠️  Admin bootstrap: both ADMIN_EMAIL and ADMIN_PASSWORD must be set together — skipping (neither used).")
        return

    email_re = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    if not email_re.match(admin_email):
        print("⚠️  Admin bootstrap: ADMIN_EMAIL doesn't look like a valid email — skipping.")
        return

    if len(admin_password) < 8:
        print("⚠️  Admin bootstrap: ADMIN_PASSWORD must be at least 8 characters — skipping.")
        return

    db = SessionLocal()
    try:
        existing = db.query(models.User).filter(models.User.email == admin_email).first()
        if existing:
            if existing.is_admin:
                print(f"ℹ️  Admin bootstrap: {admin_email} is already an admin — nothing to do.")
            else:
                existing.is_admin = True
                db.commit()
                print(f"✅ Admin bootstrap: promoted existing user {admin_email} to admin.")
        else:
            user = models.User(
                email=admin_email,
                password_hash=hash_password(admin_password),
                full_name=ADMIN_FULL_NAME or "Admin",
                is_admin=True,
                is_active=True,
            )
            db.add(user)
            db.commit()
            print(f"✅ Admin bootstrap: created new admin user {admin_email}.")
    except Exception as exc:
        # Defensive: a bootstrap failure must never take down the whole app,
        # and must never leak the password — only the exception message.
        db.rollback()
        print(f"⚠️  Admin bootstrap failed: {exc}")
    finally:
        db.close()


@app.on_event("startup")
async def start_tron_deposit_monitor():
    # يشتغل بالخلفية طول عمر السيرفر، يفحص عناوين إيداع TRON كل TRON_POLL_INTERVAL_SECONDS
    # عن معاملات جديدة مؤكدة على Nile ويحدث رصيد المستخدمين تلقائياً (شوف blockchain_monitor.py)
    asyncio.create_task(blockchain_monitor.start_polling_loop())


@app.on_event("startup")
async def start_tron_withdrawal_monitor():
    # NEW: يفحص كل السحوبات بحالة PROCESSING دورياً ويحدثها لـ COMPLETED بمجرد
    # تأكيدها على السلسلة، حتى لو ما حد ناداه عبر /withdraw/status (شوف withdrawal_monitor.py)
    asyncio.create_task(withdrawal_monitor.start_polling_loop())
