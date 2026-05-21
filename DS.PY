import asyncio
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Optional

import aiosqlite
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PicklePersistence,
    filters,
)

# ================= CONFIG =================
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6637599913"))
DB_PATH = os.getenv("DB_PATH", "bot.db")
PERSISTENCE_PATH = os.getenv("PERSISTENCE_PATH", "bot_state.pkl")

URL_PATTERN = re.compile(r"^(https?://)\S+$", re.IGNORECASE)
DAILY_COOLDOWN_SECONDS = 86400
MIN_VISIT_SECONDS = 8
RATE_LIMIT_WINDOW = 10
RATE_LIMIT_MAX = 10

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.FileHandler("logs.txt", encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("click-earn-bot")


@dataclass
class DB:
    conn: Optional[aiosqlite.Connection] = None


db = DB()


async def init_db() -> None:
    db.conn = await aiosqlite.connect(DB_PATH)
    db.conn.row_factory = aiosqlite.Row
    await db.conn.execute("PRAGMA journal_mode=WAL")
    await db.conn.execute("PRAGMA synchronous=NORMAL")
    await db.conn.execute("PRAGMA temp_store=MEMORY")
    await db.conn.execute("PRAGMA cache_size=-20000")

    await db.conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            minutes INTEGER DEFAULT 0,
            total_clicks INTEGER DEFAULT 0,
            referrals INTEGER DEFAULT 0,
            invited_by INTEGER,
            last_daily INTEGER DEFAULT 0,
            vip INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            last_active INTEGER DEFAULT 0,
            points INTEGER DEFAULT 0,
            today_views INTEGER DEFAULT 0,
            today_refs INTEGER DEFAULT 0,
            last_task_reset INTEGER DEFAULT 0,
            last_login_bonus INTEGER DEFAULT 0,
            streak_days INTEGER DEFAULT 0,
            spam_score INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner INTEGER,
            url TEXT,
            expires INTEGER
        );
        CREATE TABLE IF NOT EXISTS click_history (
            user_id INTEGER,
            link_id INTEGER,
            clicked_at INTEGER,
            UNIQUE(user_id, link_id)
        );
        CREATE TABLE IF NOT EXISTS admin_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT,
            added_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS admin_click_history (
            user_id INTEGER,
            link_id INTEGER,
            clicked_at INTEGER,
            UNIQUE(user_id, link_id)
        );
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            status TEXT DEFAULT 'pending',
            note TEXT,
            requested_at INTEGER,
            usdt_address TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS spam_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            reason TEXT,
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS paid_ads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner INTEGER,
            url TEXT,
            expires INTEGER,
            priority INTEGER DEFAULT 1,
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS point_shop (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            points_cost INTEGER,
            minutes_reward INTEGER,
            active INTEGER DEFAULT 1
        );

        CREATE INDEX IF NOT EXISTS idx_users_last_active ON users(last_active);
        CREATE INDEX IF NOT EXISTS idx_links_expires_owner ON links(expires, owner);
        CREATE INDEX IF NOT EXISTS idx_paid_ads_expires_priority ON paid_ads(expires, priority);
        CREATE INDEX IF NOT EXISTS idx_click_history_user_link ON click_history(user_id, link_id);
        CREATE INDEX IF NOT EXISTS idx_admin_click_history_user_link ON admin_click_history(user_id, link_id);
        CREATE INDEX IF NOT EXISTS idx_withdrawals_status ON withdrawals(status, requested_at);
        CREATE INDEX IF NOT EXISTS idx_spam_log_user ON spam_log(user_id, created_at);
        """
    )
    defaults = {
        "referral_reward": "1",
        "view_reward": "1",
        "view_points": "1",
        "wait_seconds": str(MIN_VISIT_SECONDS),
        "daily_task_referrals": "3",
        "daily_task_views": "5",
        "daily_task_reward_points": "10",
    }
    for k, v in defaults.items():
        await db.conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?,?)", (k, v))
    await db.conn.execute("INSERT OR IGNORE INTO point_shop (id,title,points_cost,minutes_reward,active) VALUES (1,'10 نقاط = 1 دقيقة',10,1,1)")
    await db.conn.commit()


def now_ts() -> int:
    return int(time.time())


async def q1(sql: str, params=()):
    async with db.conn.execute(sql, params) as cur:
        return await cur.fetchone()


async def qall(sql: str, params=()):
    async with db.conn.execute(sql, params) as cur:
        return await cur.fetchall()


async def exec_commit(sql: str, params=()):
    await db.conn.execute(sql, params)
    await db.conn.commit()


async def get_setting(key: str, default: str) -> str:
    row = await q1("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else default


async def ensure_user(uid: int, ref: Optional[int] = None):
    if await q1("SELECT user_id FROM users WHERE user_id=?", (uid,)):
        return
    await db.conn.execute(
        "INSERT INTO users (user_id, minutes, invited_by, last_task_reset) VALUES (?, ?, ?, ?)",
        (uid, 1 if ref else 0, ref if ref and ref != uid else None, now_ts()),
    )
    if ref and ref != uid:
        await db.conn.execute(
            "UPDATE users SET referrals=referrals+1, points=points+1, today_refs=today_refs+1 WHERE user_id=?",
            (ref,),
        )
    await db.conn.commit()


async def is_banned(uid: int) -> bool:
    row = await q1("SELECT banned FROM users WHERE user_id=?", (uid,))
    return bool(row and row["banned"])


async def touch_user(uid: int):
    await exec_commit("UPDATE users SET last_active=? WHERE user_id=?", (now_ts(), uid))


async def log_spam(uid: int, reason: str):
    await db.conn.execute("INSERT INTO spam_log (user_id, reason, created_at) VALUES (?,?,?)", (uid, reason, now_ts()))
    await db.conn.execute("UPDATE users SET spam_score=spam_score+1 WHERE user_id=?", (uid,))
    await db.conn.commit()
    logger.warning("spam uid=%s reason=%s", uid, reason)


def is_rate_limited(context: ContextTypes.DEFAULT_TYPE, uid: int) -> bool:
    logs = context.application.bot_data.setdefault("rate", {})
    bucket = logs.get(uid, [])
    t = time.time()
    bucket = [x for x in bucket if t - x <= RATE_LIMIT_WINDOW]
    bucket.append(t)
    logs[uid] = bucket
    return len(bucket) > RATE_LIMIT_MAX


def main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 تخطي رابط", callback_data="skip")],
        [InlineKeyboardButton("✅ المهام اليومية", callback_data="tasks")],
        [InlineKeyboardButton("💸 طلب سحب USDT", callback_data="withdraw")],
        [InlineKeyboardButton("📊 إحصائياتي", callback_data="stats")],
        [InlineKeyboardButton("🎁 الهدية اليومية", callback_data="daily")],
    ])


def make_captcha(action: str):
    a, b = random.randint(1, 9), random.randint(1, 9)
    return {"action": action, "answer": a + b, "expires": now_ts() + 120}, f"🤖 تحقق أمني: {a} + {b} = ؟"


async def require_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str) -> bool:
    token, text = make_captcha(action)
    context.user_data["captcha"] = token
    await update.effective_message.reply_text(text)
    return False


async def apply_referral_commission(user_id: int, base_points: int):
    user = await q1("SELECT invited_by FROM users WHERE user_id=?", (user_id,))
    if not user or not user["invited_by"]:
        return
    l1 = user["invited_by"]
    l1_bonus = max(1, int(base_points * 0.10))
    await db.conn.execute("UPDATE users SET points=points+? WHERE user_id=?", (l1_bonus, l1))

    parent = await q1("SELECT invited_by FROM users WHERE user_id=?", (l1,))
    if parent and parent["invited_by"]:
        l2 = parent["invited_by"]
        l2_bonus = max(1, int(base_points * 0.05))
        await db.conn.execute("UPDATE users SET points=points+? WHERE user_id=?", (l2_bonus, l2))
    await db.conn.commit()


async def process_auto_withdrawal(address, amount):
    await asyncio.sleep(0.5)
    logger.info("AUTO_WITHDRAW_PLACEHOLDER address=%s amount=%s", address, amount)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_rate_limited(context, uid):
        await update.message.reply_text("⛔ طلبات كثيرة، حاول لاحقًا")
        return
    ref = int(context.args[0]) if context.args and context.args[0].isdigit() else None
    await ensure_user(uid, ref)
    await touch_user(uid)
    await update.message.reply_text("✅ أهلاً بك", reply_markup=main_kb())


async def skip_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if is_rate_limited(context, uid):
        await log_spam(uid, "rapid_requests")
        await q.edit_message_text("⛔ بطيء شوي")
        return
    row = await q1("SELECT id,url FROM paid_ads WHERE expires>? ORDER BY priority DESC,id ASC LIMIT 1", (now_ts(),))
    if not row:
        await q.edit_message_text("لا يوجد روابط")
        return
    context.user_data["pending"] = {"type": "ad", "link_id": row["id"], "shown_at": now_ts()}
    await q.edit_message_text(
        f"🔗 {row['url']}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ تم الدخول", callback_data=f"confirm_ad_{row['id']}")]]),
    )


async def confirm_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    pending = context.user_data.get("pending")
    if not pending:
        await q.edit_message_text("لا يوجد رابط نشط")
        return
    wait_seconds = int(await get_setting("wait_seconds", str(MIN_VISIT_SECONDS)))
    if now_ts() - pending.get("shown_at", 0) < wait_seconds:
        await log_spam(uid, "early_confirm")
        await q.answer("تأكيد مبكر", show_alert=True)
        return
    points = int(await get_setting("view_points", "1"))
    await db.conn.execute("UPDATE users SET points=points+?, minutes=minutes+1,total_clicks=total_clicks+1,today_views=today_views+1 WHERE user_id=?", (points, uid))
    await db.conn.commit()
    await apply_referral_commission(uid, points)
    context.user_data.pop("pending", None)
    await q.edit_message_text("✅ تم احتساب المكافأة", reply_markup=main_kb())


async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    if q:
        await q.answer()
    row = await q1("SELECT last_login_bonus,streak_days FROM users WHERE user_id=?", (uid,))
    now = now_ts()
    last = row["last_login_bonus"] if row else 0
    streak = row["streak_days"] if row else 0
    if now - last < DAILY_COOLDOWN_SECONDS:
        msg = "⏳ حصلت على الهدية اليوم"
        if q:
            await q.edit_message_text(msg, reply_markup=main_kb())
        else:
            await update.message.reply_text(msg)
        return
    if last and (now - last) <= (2 * DAILY_COOLDOWN_SECONDS):
        streak += 1
    else:
        streak = 1
    reward = streak * 2
    await db.conn.execute("UPDATE users SET points=points+?, last_login_bonus=?, streak_days=? WHERE user_id=?", (reward, now, streak, uid))
    await db.conn.commit()
    msg = f"🎁 مكافأة يومية: +{reward} نقطة (streak: {streak})"
    if q:
        await q.edit_message_text(msg, reply_markup=main_kb())
    else:
        await update.message.reply_text(msg)


async def tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("✅ اضغط للاستلام", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("استلام", callback_data="tasks_claim")]]))


async def tasks_claim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    cap = context.user_data.get("captcha")
    if not cap or cap.get("action") != "tasks_claim":
        context.user_data["captcha"] = make_captcha("tasks_claim")[0]
        c = context.user_data["captcha"]
        await q.edit_message_text(f"🤖 تحقق أولًا: {c['answer'] - random.randint(0,0)}؟ أرسل الإجابة برسالة.")
        return
    await q.edit_message_text("أرسل جواب الكابتشا في رسالة نصية")


async def withdraw_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    if q:
        await q.answer()
    cap = context.user_data.get("captcha")
    if not cap or cap.get("action") != "withdraw":
        token, text = make_captcha("withdraw")
        context.user_data["captcha"] = token
        if q:
            await q.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        context.user_data["awaiting_withdraw_captcha"] = True
        return
    pending = await q1("SELECT id FROM withdrawals WHERE user_id=? AND status='pending' LIMIT 1", (uid,))
    if pending:
        await update.effective_message.reply_text("⚠️ لديك طلب معلق")
        return
    context.user_data["awaiting_withdraw_address"] = True
    await update.effective_message.reply_text("أرسل: العنوان | المبلغ")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    if await is_banned(uid):
        await update.message.reply_text("🚫 محظور")
        return
    await ensure_user(uid)
    await touch_user(uid)

    # Captcha answer handler
    cap = context.user_data.get("captcha")
    if cap and text.isdigit() and now_ts() <= cap.get("expires", 0):
        if int(text) == cap.get("answer"):
            action = cap.get("action")
            context.user_data.pop("captcha", None)
            if action == "withdraw":
                context.user_data["awaiting_withdraw_address"] = True
                context.user_data.pop("awaiting_withdraw_captcha", None)
                await update.message.reply_text("✅ تم التحقق. أرسل: العنوان | المبلغ")
                return
            if action == "tasks_claim":
                reward = int(await get_setting("daily_task_reward_points", "10"))
                await db.conn.execute("UPDATE users SET points=points+? WHERE user_id=?", (reward, uid))
                await db.conn.commit()
                await apply_referral_commission(uid, reward)
                await update.message.reply_text(f"✅ تم منحك {reward} نقطة")
                return
        else:
            await update.message.reply_text("❌ إجابة خاطئة")
            return

    if context.user_data.get("awaiting_withdraw_address"):
        context.user_data["awaiting_withdraw_address"] = False
        if "|" not in text:
            await update.message.reply_text("صيغة غير صحيحة")
            return
        address, amount_str = [x.strip() for x in text.split("|", 1)]
        if not address or not amount_str.isdigit() or int(amount_str) <= 0:
            await update.message.reply_text("بيانات غير صالحة")
            return
        amount = int(amount_str)
        user = await q1("SELECT vip FROM users WHERE user_id=?", (uid,))
        if user and user["vip"] == 1:
            await process_auto_withdrawal(address, amount)
            await db.conn.execute(
                "INSERT INTO withdrawals (user_id, amount, status, note, requested_at, usdt_address) VALUES (?, ?, 'paid', 'auto_vip', ?, ?)",
                (uid, amount, now_ts(), address),
            )
        else:
            await db.conn.execute(
                "INSERT INTO withdrawals (user_id, amount, status, note, requested_at, usdt_address) VALUES (?, ?, 'pending', '', ?, ?)",
                (uid, amount, now_ts(), address),
            )
        await db.conn.commit()
        await update.message.reply_text("✅ تم تسجيل طلب السحب")
        return

    if text == "📊 إحصائياتي":
        row = await q1("SELECT points,minutes,total_clicks FROM users WHERE user_id=?", (uid,))
        await update.message.reply_text(f"🪙 {row['points']} | ⏱ {row['minutes']} | ✅ {row['total_clicks']}")


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_ID:
        await q.edit_message_text("غير مصرح")
        return
    users = (await q1("SELECT COUNT(*) c FROM users"))["c"]
    active = (await q1("SELECT COUNT(*) c FROM users WHERE last_active>=?", (now_ts()-86400,)))["c"]
    wd = (await q1("SELECT COUNT(*) c FROM withdrawals"))["c"]
    await q.edit_message_text(f"users={users}\nactive={active}\nwithdrawals={wd}")


async def backup_db_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        path = f"backup_{time.strftime('%Y%m%d_%H%M%S', time.gmtime())}.db"
        async with aiosqlite.connect(path) as bck:
            await db.conn.backup(bck)
        logger.info("backup created %s", path)
    except Exception:
        logger.exception("backup failed")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled error: %s", context.error)


async def post_init(app):
    await init_db()


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")
    persistence = PicklePersistence(filepath=PERSISTENCE_PATH)
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .persistence(persistence)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(skip_link, pattern=r"^skip$"))
    app.add_handler(CallbackQueryHandler(confirm_click, pattern=r"^confirm_(ad_)?\d+$"))
    app.add_handler(CallbackQueryHandler(tasks, pattern=r"^tasks$"))
    app.add_handler(CallbackQueryHandler(tasks_claim, pattern=r"^tasks_claim$"))
    app.add_handler(CallbackQueryHandler(withdraw_request, pattern=r"^withdraw$"))
    app.add_handler(CallbackQueryHandler(daily, pattern=r"^daily$"))
    app.add_handler(CallbackQueryHandler(admin_stats, pattern=r"^a_stats$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_error_handler(on_error)
    app.job_queue.run_repeating(backup_db_job, interval=21600, first=300)

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
