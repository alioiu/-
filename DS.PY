import sqlite3
import time
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

TOKEN = "7910621452:AAHnrtKDSkkXFsWwVzQEf4509_fK_j5_Jjc"
ADMIN_ID = 123456789

# ================= DATABASE =================
db = sqlite3.connect("bot.db", check_same_thread=False)
cur = db.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    minutes INTEGER DEFAULT 0,
    total_clicks INTEGER DEFAULT 0,
    referrals INTEGER DEFAULT 0,
    invited_by INTEGER,
    last_daily INTEGER DEFAULT 0
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner INTEGER,
    url TEXT,
    expires INTEGER
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS click_history (
    user_id INTEGER,
    link_id INTEGER,
    clicked_at INTEGER,
    UNIQUE(user_id, link_id)
)
""")
db.commit()

# ================= KEYBOARDS =================
def main_kb(uid=None):
    ref_btn = InlineKeyboardButton(
        "👥 رابط الإحالة",
        callback_data="ref"
    )
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 تخطي رابط", callback_data="skip")],
        [InlineKeyboardButton("➕ إضافة رابط", callback_data="add")],
        [
            InlineKeyboardButton("📊 إحصائياتي", callback_data="stats"),
            InlineKeyboardButton("🏆 الترتيب", callback_data="top")
        ],
        [
            InlineKeyboardButton("🎁 الهدية اليومية", callback_data="daily"),
            ref_btn
        ]
    ])

def admin_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 إحصائيات البوت", callback_data="a_stats")],
        [InlineKeyboardButton("📢 رسالة جماعية", callback_data="a_bc")],
        [InlineKeyboardButton("🏆 ترتيب الأعضاء", callback_data="a_top")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back")]
    ])

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ref = int(context.args[0]) if context.args and context.args[0].isdigit() else None

    cur.execute("SELECT user_id FROM users WHERE user_id=?", (uid,))
    exists = cur.fetchone()

    if not exists:
        cur.execute(
            "INSERT INTO users (user_id, minutes, invited_by) VALUES (?, ?, ?)",
            (uid, 1 if ref else 0, ref if ref != uid else None)
        )

        if ref and ref != uid:
            cur.execute(
                "UPDATE users SET minutes = minutes + 1, referrals = referrals + 1 WHERE user_id=?",
                (ref,)
            )
        db.commit()

    await update.message.reply_text(
        "🤖 مرحبًا بك في بوت تبادل نقرات الروابط\n"
        "⏱ اكسب دقائق وفعّل رابطك\n"
        "👥 ادعُ أصدقاءك لتحصل على دقائق إضافية",
        reply_markup=main_kb(uid)
    )

# ================= REF LINK =================
async def referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id

    link = f"https://t.me/{context.bot.username}?start={uid}"

    await q.edit_message_text(
        f"👥 *رابط الإحالة الخاص بك:*\n\n{link}\n\n"
        "🎁 كل صديق = 1 دقيقة",
        parse_mode="Markdown",
        reply_markup=main_kb(uid)
    )

# ================= باقي الدوال (مختصرة) =================
# ⚠️ باقي الكود (skip, confirm, add_link, stats, top, daily, admin...)
# هو نفسه من الرد السابق بدون أي تغيير

# ================= RUN =================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", lambda u, c: u.message.reply_text("لوحة الأدمن")))

app.add_handler(CallbackQueryHandler(referral, "^ref$"))

# (أضف هنا باقي الـ handlers كما هي من الكود السابق)

app.run_polling()
