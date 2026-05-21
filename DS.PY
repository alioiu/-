import asyncio
import logging
import os
import re
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

# ================= CONFIG =================
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6637599913"))
DB_PATH = os.getenv("DB_PATH", "bot.db")
LOG_PATH = os.getenv("LOG_PATH", "logs.txt")

URL_PATTERN = re.compile(r"^(https?://)\S+$", re.IGNORECASE)
DAILY_COOLDOWN_SECONDS = 86400
MIN_VISIT_SECONDS = 8
CONFIRM_COOLDOWN_SECONDS = 3
RATE_LIMIT_WINDOW = 10
RATE_LIMIT_MAX_REQUESTS = 7
AUTO_BAN_SPAM_STRIKES = 8

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("bot")

# ================= DATABASE =================
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
cur = db.cursor()
cur.execute("PRAGMA journal_mode=WAL")
cur.execute("PRAGMA synchronous=NORMAL")
cur.execute("PRAGMA temp_store=MEMORY")
cur.execute("PRAGMA cache_size=-20000")


@contextmanager
def tx():
    try:
        yield
        db.commit()
    except Exception:
        db.rollback()
        raise


def init_db() -> None:
    with tx():
        cur.execute("""CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, minutes INTEGER DEFAULT 0, total_clicks INTEGER DEFAULT 0, referrals INTEGER DEFAULT 0, invited_by INTEGER, last_daily INTEGER DEFAULT 0, vip INTEGER DEFAULT 0, banned INTEGER DEFAULT 0, level INTEGER DEFAULT 1, last_active INTEGER DEFAULT 0, points INTEGER DEFAULT 0, today_views INTEGER DEFAULT 0, today_refs INTEGER DEFAULT 0, last_task_reset INTEGER DEFAULT 0, last_login_bonus INTEGER DEFAULT 0, spam_score INTEGER DEFAULT 0)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS links (id INTEGER PRIMARY KEY AUTOINCREMENT, owner INTEGER, url TEXT, expires INTEGER)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS paid_ads (id INTEGER PRIMARY KEY AUTOINCREMENT, owner INTEGER, url TEXT, expires INTEGER, priority INTEGER DEFAULT 1, created_at INTEGER)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS click_history (user_id INTEGER, link_id INTEGER, clicked_at INTEGER, UNIQUE(user_id, link_id))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS admin_links (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT, added_at INTEGER)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS admin_click_history (user_id INTEGER, link_id INTEGER, clicked_at INTEGER, UNIQUE(user_id, link_id))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS withdrawals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount INTEGER, status TEXT DEFAULT 'pending', note TEXT, requested_at INTEGER, usdt_address TEXT)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS spam_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, reason TEXT, created_at INTEGER)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS point_shop (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, points_cost INTEGER, minutes_reward INTEGER, active INTEGER DEFAULT 1)""")
        cur.executescript("""
        CREATE INDEX IF NOT EXISTS idx_links_owner_expires ON links(owner, expires);
        CREATE INDEX IF NOT EXISTS idx_paid_ads_expires_priority ON paid_ads(expires, priority);
        CREATE INDEX IF NOT EXISTS idx_click_history_user ON click_history(user_id, link_id);
        CREATE INDEX IF NOT EXISTS idx_admin_click_user ON admin_click_history(user_id, link_id);
        CREATE INDEX IF NOT EXISTS idx_users_active ON users(last_active);
        CREATE INDEX IF NOT EXISTS idx_withdrawals_status ON withdrawals(status, requested_at);
        CREATE INDEX IF NOT EXISTS idx_spam_log_user ON spam_log(user_id, created_at);
        """)
        for k, v in {"referral_reward": "1", "view_reward": "1", "wait_seconds": str(MIN_VISIT_SECONDS), "registration_enabled": "1", "view_points": "1", "daily_task_referrals": "3", "daily_task_views": "5", "daily_task_reward_points": "10", "daily_login_points": "2"}.items():
            cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        cur.execute("INSERT OR IGNORE INTO point_shop (id,title,points_cost,minutes_reward,active) VALUES (1,'10 نقاط = 1 دقيقة',10,1,1)")


def now_ts() -> int:
    return int(time.time())


def get_setting(key: str, default: str) -> str:
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    return row["value"] if row else default


@dataclass
class SafetyState:
    recent_actions: dict[int, deque]


safety_state = SafetyState(recent_actions=defaultdict(deque))


def log_spam(uid: int, reason: str) -> None:
    with tx():
        cur.execute("INSERT INTO spam_log (user_id, reason, created_at) VALUES (?, ?, ?)", (uid, reason, now_ts()))
        cur.execute("UPDATE users SET spam_score=spam_score+1 WHERE user_id=?", (uid,))
        cur.execute("SELECT spam_score FROM users WHERE user_id=?", (uid,))
        row = cur.fetchone()
        if row and row["spam_score"] >= AUTO_BAN_SPAM_STRIKES:
            cur.execute("UPDATE users SET banned=1 WHERE user_id=?", (uid,))
            logger.warning("auto-banned user=%s", uid)


def rate_limited(uid: int) -> bool:
    q = safety_state.recent_actions[uid]
    now = time.time()
    while q and (now - q[0]) > RATE_LIMIT_WINDOW:
        q.popleft()
    q.append(now)
    return len(q) > RATE_LIMIT_MAX_REQUESTS


def ensure_user(uid: int, ref: int | None = None) -> None:
    cur.execute("SELECT 1 FROM users WHERE user_id=?", (uid,))
    if cur.fetchone():
        return
    with tx():
        cur.execute("INSERT INTO users (user_id, minutes, invited_by, last_task_reset) VALUES (?, ?, ?, ?)", (uid, 1 if ref else 0, ref if ref != uid else None, now_ts()))
        if ref and ref != uid:
            cur.execute("UPDATE users SET minutes=minutes+1, referrals=referrals+1, points=points+1 WHERE user_id=?", (ref,))


def is_banned(uid: int) -> bool:
    cur.execute("SELECT banned FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    return bool(row and row["banned"])


def main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔗 تخطي رابط", callback_data="skip")],[InlineKeyboardButton("➕ إضافة رابط", callback_data="add")],[InlineKeyboardButton("🪙 نقاطي", callback_data="points")],[InlineKeyboardButton("✅ المهام", callback_data="tasks")],[InlineKeyboardButton("💸 سحب", callback_data="withdraw")],[InlineKeyboardButton("📌 إعلان مدفوع", callback_data="ad_buy")],[InlineKeyboardButton("📊 إحصائياتي", callback_data="stats"), InlineKeyboardButton("🏆 الترتيب", callback_data="top")],[InlineKeyboardButton("🎁 الهدية اليومية", callback_data="daily"), InlineKeyboardButton("👥 رابط الإحالة", callback_data="ref")]])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if rate_limited(uid):
        await update.message.reply_text("⛔ طلبات سريعة جدًا، حاول بعد قليل.")
        return
    if is_banned(uid):
        await update.message.reply_text("🚫 حسابك محظور.")
        return
    ref = int(context.args[0]) if context.args and context.args[0].isdigit() else None
    ensure_user(uid, ref)
    with tx():
        cur.execute("UPDATE users SET last_active=? WHERE user_id=?", (now_ts(), uid))
    await update.message.reply_text("✨ أهلاً بك! البوت جاهز بشكل احترافي وآمن.", reply_markup=main_kb())


async def skip_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    await q.answer()
    if rate_limited(uid):
        log_spam(uid, "rapid_requests")
        await q.edit_message_text("⛔ تم رصد طلبات سريعة.", reply_markup=main_kb())
        return
    cur.execute("SELECT id,url FROM paid_ads WHERE expires>? ORDER BY priority DESC,id ASC LIMIT 1", (now_ts(),))
    row = cur.fetchone()
    if not row:
        await q.edit_message_text("لا توجد روابط متاحة الآن.", reply_markup=main_kb())
        return
    token = os.urandom(8).hex()
    context.user_data["pending"] = {"link_id": row["id"], "shown_at": now_ts(), "session": token}
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ تم الدخول", callback_data=f"confirm_{row['id']}_{token}")]])
    await q.edit_message_text(f"🔗 ادخل الرابط:\n{row['url']}\n\nثم أكد بعد الانتظار.", reply_markup=kb)


async def confirm_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    parts = q.data.split("_")
    if len(parts) != 3:
        log_spam(uid, "fake_confirm")
        await q.edit_message_text("❌ تأكيد غير صالح.", reply_markup=main_kb())
        return
    _, link_id_s, session = parts
    if not link_id_s.isdigit():
        await q.edit_message_text("❌ طلب غير صالح.", reply_markup=main_kb())
        return
    pending = context.user_data.get("pending") or {}
    if pending.get("session") != session or pending.get("link_id") != int(link_id_s):
        log_spam(uid, "session_mismatch")
        await q.edit_message_text("⚠️ فشل التحقق من الجلسة.", reply_markup=main_kb())
        return
    if now_ts() - pending.get("shown_at", now_ts()) < int(get_setting("wait_seconds", str(MIN_VISIT_SECONDS))):
        log_spam(uid, "early_confirm")
        await q.edit_message_text("⏳ تأكيد مبكر جدًا.", reply_markup=main_kb())
        return
    with tx():
        cur.execute("UPDATE users SET minutes=minutes+1, points=points+1,total_clicks=total_clicks+1,today_views=today_views+1 WHERE user_id=?", (uid,))
    context.user_data.pop("pending", None)
    await q.edit_message_text("✅ تم احتساب المكافأة.", reply_markup=main_kb())


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    if rate_limited(uid):
        log_spam(uid, "flood")
        await update.message.reply_text("⛔ تم تفعيل الحماية ضد Flood/Spam.")
        return
    text = (update.message.text or "").strip()
    if text == "📊 إحصائياتي":
        cur.execute("SELECT minutes,total_clicks,referrals,points FROM users WHERE user_id=?", (uid,))
        u = cur.fetchone()
        await update.message.reply_text(f"📊 رصيدك: {u['minutes']} دقيقة\n✅ نقرات: {u['total_clicks']}\n👥 إحالات: {u['referrals']}\n🪙 نقاط: {u['points']}")


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if update.effective_user.id != ADMIN_ID:
        await q.edit_message_text("غير مصرح")
        return
    now = now_ts()
    day = now - 86400
    cur.execute("SELECT COUNT(*) c FROM users")
    users = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) c FROM users WHERE last_active>=?", (day,))
    active = cur.fetchone()["c"]
    cur.execute("SELECT COALESCE(SUM(today_views),0) c FROM users")
    daily_links = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) c FROM withdrawals")
    w = cur.fetchone()["c"]
    cur.execute("SELECT user_id,total_clicks FROM users ORDER BY total_clicks DESC LIMIT 5")
    top = "\n".join([f"{r['user_id']} ({r['total_clicks']})" for r in cur.fetchall()]) or "-"
    await q.edit_message_text(f"📊 Users: {users}\n🟢 Active(24h): {active}\n🔗 Daily views: {daily_links}\n💸 Withdrawals: {w}\n🔥 Top:\n{top}")


async def backup_job(context: ContextTypes.DEFAULT_TYPE):
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    path = f"backup_{stamp}.db"
    try:
        b = sqlite3.connect(path)
        db.backup(b)
        b.close()
        logger.info("database backup created: %s", path)
    except Exception as e:
        logger.exception("backup failed: %s", e)


async def broadcast_job(context: ContextTypes.DEFAULT_TYPE, msg: str):
    cur.execute("SELECT user_id FROM users WHERE banned=0")
    for row in cur.fetchall():
        try:
            await context.bot.send_message(chat_id=row["user_id"], text=msg)
            await asyncio.sleep(0.05)
        except TelegramError as e:
            logger.warning("broadcast fail user=%s err=%s", row["user_id"], e)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled error: %s", context.error)


def main() -> None:
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it in environment variables.")
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(skip_link, pattern=r"^skip$"))
    app.add_handler(CallbackQueryHandler(confirm_click, pattern=r"^confirm_\d+_[a-f0-9]+$"))
    app.add_handler(CallbackQueryHandler(admin_stats, pattern=r"^a_stats$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(on_error)
    app.job_queue.run_repeating(backup_job, interval=21600, first=60)
    logger.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
