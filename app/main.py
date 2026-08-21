from dotenv import load_dotenv

load_dotenv()
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import asyncio

from .database import Base, engine, SessionLocal
from .config import DATABASE_URL
from . import models
from .routes import auth_routes, user_routes, spin_routes, wallet_routes, admin_routes, nigerian_deposit_routes, nigerian_withdrawal_routes
from . import blockchain_monitor
from . import withdrawal_monitor

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
async def start_tron_deposit_monitor():
    # يشتغل بالخلفية طول عمر السيرفر، يفحص عناوين إيداع TRON كل TRON_POLL_INTERVAL_SECONDS
    # عن معاملات جديدة مؤكدة على Nile ويحدث رصيد المستخدمين تلقائياً (شوف blockchain_monitor.py)
    asyncio.create_task(blockchain_monitor.start_polling_loop())


@app.on_event("startup")
async def start_tron_withdrawal_monitor():
    # NEW: يفحص كل السحوبات بحالة PROCESSING دورياً ويحدثها لـ COMPLETED بمجرد
    # تأكيدها على السلسلة، حتى لو ما حد ناداه عبر /withdraw/status (شوف withdrawal_monitor.py)
    asyncio.create_task(withdrawal_monitor.start_polling_loop())
