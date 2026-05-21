# Telegram Bot (Production Ready)

## التشغيل المحلي
1. أنشئ بيئة:
```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
2. انسخ الإعدادات:
```bash
cp .env.example .env
```
3. عدّل `BOT_TOKEN` في `.env`.
4. شغّل:
```bash
export $(cat .env | xargs)
python DS.PY
```

## تغيير التوكن
لا تضع التوكن داخل الكود. استخدم المتغير:
`BOT_TOKEN`.

## النشر
### Render / Railway / Replit / VPS
- Start Command: `python DS.PY`
- Python Version: `3.11+`
- أضف متغيرات البيئة من `.env.example`.
- اربط Persistent Disk لحفظ `bot.db` و `logs.txt`.

## ملاحظات
- يتم عمل نسخة احتياطية تلقائية كل 6 ساعات.
- السجلات تُحفظ في `logs.txt`.
