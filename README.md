# LuckySpin API — Points Edition

## تشغيل محلي

```bash
cd backend
python3 -m venv venv
source venv/bin/activate        # على ويندوز: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

بعدها الـ API شغال على: `http://localhost:8000`
والتوثيق التفاعلي (Swagger): `http://localhost:8000/docs`

## متغيرات بيئة مهمة (production)
- `APP_SECRET_KEY` — سر JWT، غيّره لقيمة عشوائية طويلة قبل النشر (لا تستخدم القيمة الافتراضية).
- `DATABASE_URL` — افتراضياً SQLite محلي، لو تبي تنتقل لـ PostgreSQL:
  `postgresql://user:pass@host:5432/dbname`

### Binance Pay (الإيداع)
- `BINANCE_PAY_API_KEY`
- `BINANCE_PAY_API_SECRET`
- `BINANCE_PAY_MERCHANT_ID`
- `WEBHOOK_URL` — رابط الـwebhook الحقيقي بعد النشر، مثال:
  `https://yourdomain.com/api/wallet/webhook/binance-pay`

تحصل عليهم من: Binance Merchant Dashboard → API Management
(يتطلب حساب Binance Merchant موثّق).

### Binance Withdraw API (السحب)
- `BINANCE_API_KEY` / `BINANCE_API_SECRET` — **مفتاح منفصل تماماً** عن Binance Pay،
  فعّل عليه صلاحية Withdraw فقط، وفعّل IP Whitelist لسيرفرك بالضبط من إعدادات Binance.
  **لا تستخدم مفتاح بصلاحية Trade أو Transfer الكاملة.**

### عناوين الإيداع الفردية (BTC + USDT-TRC20)
- `MASTER_SEED_PHRASE` — عبارة استرجاع (mnemonic) من 12/24 كلمة تولّد منها كل
  عناوين المستخدمين. **هذا أهم سر بالمشروع كامل** — خزّنه بـ secret manager
  (AWS Secrets Manager / Vault) مو ملف `.env` عادي على نفس السيرفر.
- `BLOCKCYPHER_TOKEN` — من blockcypher.com (مجاني)، لمراقبة عناوين BTC.
- `TRONGRID_API_KEY` — من trongrid.io (مجاني)، لمراقبة عناوين USDT-TRC20.
- المراقبة تشتغل تلقائياً بالخلفية كل `BLOCKCHAIN_POLL_INTERVAL_SECONDS`
  (افتراضياً 30 ثانية) — شوف `blockchain_monitor.py`.

### إشعارات البريد عند طلب سحب
- `SMTP_USER` / `SMTP_PASSWORD` — حساب Gmail يرسل منه، لازم "App Password"
  (Settings → Security → App Passwords بحساب Google) مو كلمة المرور العادية.
- `ADMIN_NOTIFICATION_EMAIL` — افتراضياً `vibeainlo@gmail.com`، يوصله إيميل
  بكل طلب سحب فوراً.

## احتياجات إضافية قبل التشغيل الفعلي
1. `pip install bip-utils` لتوليد عناوين BTC/USDT (مضافة بـ requirements.txt).
2. مراقبة TronGrid حالياً تفترض أي معاملة واردة موجودة = مؤكدة بعدد التأكيدات
   المطلوب؛ بالإنتاج الفعلي راجع توثيق TronGrid لحساب التأكيدات الحقيقية بدقة
   قبل ما تعتمد عليها بمبالغ كبيرة.
3. جرّب المسار كامل بمبلغ صغير جداً أول مرة: إيداع → إشعار → سحب → إيميل،
   قبل ما تفتح الموقع للمستخدمين.

## نقاط مهمة قبل تفعيل السحب الحقيقي
1. `AUTO_WITHDRAW_MAX_USDT` في `config.py` يحدد أقصى مبلغ يُرسل تلقائياً؛
   أي طلب أكبر يوقف بحالة `PENDING_REVIEW` وينتظر موافقتك اليدوية — راجعه دورياً
   أو ابني له لوحة أدمن بسيطة.
2. تحقق التوقيع (`verify_pay_webhook`) **إلزامي** — بدونه أي شخص يقدر يرسل
   webhook مزوّر ويضيف رصيد وهمي لنفسه.
3. `withdrawOrderId` الفريد (نستخدم id السحب من قاعدة بياناتنا) يمنع تكرار
   إرسال نفس عملية السحب مرتين لو صار retry.
4. راجع [توثيق Binance Pay](https://developers.binance.com/docs/binance-pay/introduction)
   و[توثيق Binance Withdraw](https://developers.binance.com/docs/wallet/capital/withdraw)
   دايماً — الحقول والتوقيع ممكن تتغير مع تحديثات Binance.
5. تأكد من متطلبات الترخيص المحلية عندك قبل تفعيل فلوس حقيقية على منصة فيها
   عنصر حظ/سبن — هذا يختلف حسب الدولة.

## ملاحظات أمان مبنية في الكود
- كلمات المرور مشفرة bcrypt، أبداً ما تُخزن نص صريح.
- JWT بصلاحية محدودة (24 ساعة) قابلة للتعديل في `config.py`.
- نتيجة السبن والبونص اليومي تُحسب **بالكامل في السيرفر** — الفرونت اند لا يقرر أي نتيجة (anti-cheat).
- فترة الانتظار بين السبنات تُتحقق من قاعدة البيانات (`last_spin_at`) وليس من localStorage أو الفرونت اند، فمايمكن التلاعب فيها من المتصفح.
- Rate limiting عام على كل الـ API (120 طلب/دقيقة) لمنع bruteforce وسبام.
- Security headers (X-Frame-Options, X-Content-Type-Options...).
- CORS مقفول على `*` مؤقتاً — **لازم** تحدده لدومين موقعك الحقيقي قبل النشر.

## قاعدة البيانات: SQLite (محلي) مقابل PostgreSQL (إنتاج)

المشروع أصلاً مبني ليقرأ اتصال قاعدة البيانات بالكامل من متغير بيئة واحد:
`DATABASE_URL` (في `app/config.py`). إذا هذا المتغير غير موجود بالبيئة، يرجع
تلقائياً لملف SQLite محلي (`sqlite:///./luckyspin.db`) — وهذا **فقط**
للتطوير المحلي. أي منصة استضافة فيها filesystem مؤقت (Render من ضمنها) يعني
إن ملف الـ SQLite هذا يُمسح مع كل إعادة نشر/إعادة تشغيل — فلازم PostgreSQL
حقيقي بالإنتاج، مو خيار اختياري.

كل الجداول (Users, Wallets/UserWallet, AdminLedger/LedgerTransaction,
NigerianDeposit, NigerianWithdrawal, Withdrawal/BlockchainDeposit,
SpinHistory, Transaction, Notification...) معرّفة بـ SQLAlchemy ORM بدون أي
كود خاص بـ SQLite — فتعمل على PostgreSQL بدون أي تعديل على `models.py` أو
أي منطق موجود، فقط بتغيير `DATABASE_URL`.

### Deploying on Render

1. **أنشئ قاعدة بيانات PostgreSQL على Render** (Render Dashboard → New →
   PostgreSQL). بعد الإنشاء، انسخ قيمة **Internal Database URL** (لو الـ
   Web Service على نفس حساب Render — أسرع وبدون رسوم نقل بيانات) أو
   **External Database URL** (لو تتصل من خارج Render).

2. **DATABASE_URL** — في إعدادات الـ Web Service الخاص بالـ backend
   (Environment tab)، أضف:
   ```
   DATABASE_URL=<القيمة اللي نسختها من Render Postgres — تبدأ عادة بـ postgres://>
   ```
   لا داعي تعدلها يدوياً لو بدأت بـ `postgres://` بدل `postgresql://` —
   الكود يحولها تلقائياً (`app/database.py`).

3. **Build Command:**
   ```
   pip install -r requirements.txt
   ```

4. **Start Command:**
   ```
   uvicorn app.main:app --host 0.0.0.0 --port $PORT
   ```
   (Render يمرر منفذ التشغيل تلقائياً عبر متغير `$PORT` — لا تثبّت رقم منفذ
   يدوياً.)

5. الجداول تُنشأ تلقائياً أول ما السيرفر يشتغل (`Base.metadata.create_all`
   في `main.py`) — ما تحتاج تشغّل أي migration يدوي لإنشاء الجداول نفسها،
   فقط لنقل البيانات القديمة (الخطوة التالية) لو عندك بيانات حقيقية بملف
   SQLite تبي تحافظ عليها.

### نقل البيانات من SQLite الحالي (لو عندك بيانات مهمة)

سكربت جاهز: `migrate_sqlite_to_postgres.py` — ينسخ كل صف من كل جدول من
ملف SQLite المحلي إلى قاعدة PostgreSQL المضبوطة حالياً بـ `DATABASE_URL`،
بترتيب يحترم العلاقات (Foreign Keys) تلقائياً. آمن تشغله أكثر من مرة (يتخطى
أي صف موجود أصلاً بنفس الـ id بدل ما يكرره).

```bash
# تأكد DATABASE_URL بالبيئة الحالية يشاور على PostgreSQL (الوجهة)، ثم:
python migrate_sqlite_to_postgres.py --sqlite ./luckyspin.db
```

**لم يتم اختبار هذا السكربت فعلياً على قاعدة PostgreSQL حقيقية** — لا يوجد
اتصال بقاعدة بيانات حية بالبيئة اللي كُتب فيها هذا الكود. جرّبه أول مرة على
قاعدة Postgres تجريبية (مو الإنتاج مباشرة) وتحقق من عدد الصفوف بكل جدول
يطابق قبل ما تعتمد عليه بالكامل.

### إنشاء أول Admin بعد النشر

سكربت تفاعلي آمن: `create_admin.py` — يسألك عن الإيميل وكلمة المرور مباشرة
(كلمة المرور مخفية بالكتابة عبر `getpass`، ما تنطبع ولا تُسجّل بأي log)،
ويشفرها بنفس آلية bcrypt المستخدمة بباقي المشروع. **لا يوجد أي إيميل أو
باسورد ثابت بالكود.**

على Render: افتح تبويب **Shell** لخدمة الـ backend بعد ما يصير Deploy ناجح،
وشغّل:
```bash
python create_admin.py
```
- لو الإيميل موجود أصلاً كمستخدم عادي → يسألك تأكيد ترقيته لـ admin (مع
  خيار تغيير الباسورد لو تبي).
- لو الإيميل جديد → ينشئ حساب admin جديد كامل.

