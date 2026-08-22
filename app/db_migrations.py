"""
Auto-migration ضروري لأن هذا المشروع لا يستخدم Alembic — الجداول تُنشأ فقط
عبر Base.metadata.create_all(), وهذا الأخير **لا يضيف أعمدة جديدة لجدول
موجود مسبقاً**. أي عمود جديد أضيف لأي Model (مثال: users.ngn_winnings_balance)
يبقى غير موجود فعلياً في قاعدة بيانات Render القديمة، فيفشل أي SELECT
يذكره بـ UndefinedColumn.

هذا الملف يقارن أعمدة كل جدول في Base.metadata مع الأعمدة الموجودة فعلياً
في قاعدة البيانات الحية، ويضيف أي عمود ناقص عبر:

    ALTER TABLE "<table>" ADD COLUMN IF NOT EXISTS "<col>" <type> [DEFAULT ...]

قيود صريحة (مطابقة لمتطلبات الأمان):
  * PostgreSQL فقط — يتحقق من engine.dialect.name ويخرج فوراً لأي محرك آخر
    (SQLite المحلي للتطوير يبقى كما هو، بدون أي تعديل).
  * إضافي فقط: لا يحذف عمود، لا يحذف جدول، لا يلمس صف بيانات موجود.
  * لا يفرض NOT NULL على عمود جديد إلا لو نجحنا بحساب DEFAULT ثابت له
    (فيملأ Postgres الصفوف الموجودة بالـ default تلقائياً) — غير ذلك
    نضيفه NULLABLE حتى لا نكسر أي صف قديم، ونطبع تحذير بذلك.
  * أعمدة Enum يتم تخطيها (تحتاج CREATE TYPE يدوي) مع تحذير باللوج — لا
    يوجد حالياً أي عمود جديد من نوع Enum فهذا لا يؤثر على المشكلة الحالية.
  * IF NOT EXISTS يجعل تشغيل هذا الكود عدة مرات (كل Restart/Deploy) آمناً
    تماماً — التشغيل الثاني وما بعده = no-op.
  * لا ينشئ جداول جديدة (هذا عمل create_all()، ويُستدعى قبل هذه الدالة)
    ولا يلمس أي منطق Auth / JWT / Ledger / Deposit / Spin.
"""

import logging

from sqlalchemy import inspect, text
from sqlalchemy.types import Enum as SAEnum

logger = logging.getLogger("db_migrations")


def _render_default_literal(column):
    """يحاول يحوّل column.default الثابت (scalar) لنص SQL آمن. يرجع None لو
    ما قدر (مثال: default هو دالة زي datetime.utcnow — تُحسب وقت الإدراج
    داخل SQLAlchemy فقط، مو DEFAULT فعلي بقاعدة البيانات)."""
    default = column.default
    if default is None or not getattr(default, "is_scalar", False):
        return None

    value = default.arg
    if callable(value):
        return None

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return None


def run_startup_migrations(engine, base):
    """يفحص كل الجداول المعرّفة في `base.metadata` ويضيف أي عمود ناقص في
    قاعدة البيانات الحية. آمن للاستدعاء في كل startup، ولأي عدد مرات."""

    if engine.dialect.name != "postgresql":
        # SQLite (تطوير محلي) أو أي محرك آخر — لا نلمسه، بلا أي تغيير سلوك.
        return

    try:
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())

        with engine.begin() as conn:
            for table_name, table in base.metadata.tables.items():
                if table_name not in existing_tables:
                    # جدول جديد بالكامل — من مسؤولية create_all() فقط،
                    # ما نلمسه هنا (سيُنشأ بكل أعمدته الصحيحة مباشرة).
                    continue

                existing_cols = {c["name"] for c in inspector.get_columns(table_name)}

                for column in table.columns:
                    if column.name in existing_cols:
                        continue

                    if isinstance(column.type, SAEnum):
                        logger.warning(
                            "db_migrations: skipping enum column %s.%s — "
                            "needs a manual migration (CREATE TYPE).",
                            table_name, column.name,
                        )
                        continue

                    col_type_sql = column.type.compile(dialect=engine.dialect)
                    default_literal = _render_default_literal(column)

                    clauses = [col_type_sql]
                    if default_literal is not None:
                        clauses.append(f"DEFAULT {default_literal}")
                        if not column.nullable:
                            clauses.append("NOT NULL")
                    elif not column.nullable:
                        logger.warning(
                            "db_migrations: %s.%s is NOT NULL in the model "
                            "but has no static default — adding it as "
                            "NULLABLE to avoid breaking existing rows. "
                            "Review manually if it must be NOT NULL.",
                            table_name, column.name,
                        )

                    ddl = (
                        f'ALTER TABLE "{table_name}" '
                        f'ADD COLUMN IF NOT EXISTS "{column.name}" '
                        + " ".join(clauses)
                    )
                    logger.info("db_migrations: %s", ddl)
                    print(f"🛠️  db_migrations: {ddl}")
                    conn.execute(text(ddl))

    except Exception as exc:
        # نفس فلسفة bootstrap_admin_from_env في main.py: فشل هنا ما يجب
        # يوقف إقلاع التطبيق كامل — بس نطبع الخطأ بوضوح باللوج.
        logger.exception("db_migrations: startup migration failed")
        print(f"⚠️  db_migrations: startup migration failed: {exc}")
