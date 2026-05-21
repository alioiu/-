import sqlite3
import time
import re
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = "7910621452:AAHnrtKDSkkXFsWwVzQEf4509_fK_j5_Jjc"
ADMIN_ID = 6637599913

URL_PATTERN = re.compile(r"^(https?://)\S+$", re.IGNORECASE)
DAILY_COOLDOWN_SECONDS = 60 * 60 * 24
MIN_VISIT_SECONDS = 8
CONFIRM_COOLDOWN_SECONDS = 3

# ================= DATABASE =================
db = sqlite3.connect("bot.db", check_same_thread=False)
cur = db.cursor()

cur.execute(
    """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    minutes INTEGER DEFAULT 0,
    total_clicks INTEGER DEFAULT 0,
    referrals INTEGER DEFAULT 0,
    invited_by INTEGER,
    last_daily INTEGER DEFAULT 0
)
"""
)

cur.execute(
    """
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner INTEGER,
    url TEXT,
    expires INTEGER
)
"""
)

cur.execute("PRAGMA table_info(links)")
link_columns = {row[1] for row in cur.fetchall()}
if "owner" not in link_columns:
    cur.execute("ALTER TABLE links ADD COLUMN owner INTEGER")
if "expires" not in link_columns:
    cur.execute("ALTER TABLE links ADD COLUMN expires INTEGER")

cur.execute(
    """
CREATE TABLE IF NOT EXISTS click_history (
    user_id INTEGER,
    link_id INTEGER,
    clicked_at INTEGER,
    UNIQUE(user_id, link_id)
)
"""
)

cur.execute(
    """
CREATE TABLE IF NOT EXISTS admin_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT,
    added_at INTEGER
)
"""
)

cur.execute(
    """
CREATE TABLE IF NOT EXISTS admin_click_history (
    user_id INTEGER,
    link_id INTEGER,
    clicked_at INTEGER,
    UNIQUE(user_id, link_id)
)
"""
)


cur.execute("PRAGMA table_info(users)")
user_columns = {row[1] for row in cur.fetchall()}
if "vip" not in user_columns:
    cur.execute("ALTER TABLE users ADD COLUMN vip INTEGER DEFAULT 0")
if "banned" not in user_columns:
    cur.execute("ALTER TABLE users ADD COLUMN banned INTEGER DEFAULT 0")
if "level" not in user_columns:
    cur.execute("ALTER TABLE users ADD COLUMN level INTEGER DEFAULT 1")
if "last_active" not in user_columns:
    cur.execute("ALTER TABLE users ADD COLUMN last_active INTEGER DEFAULT 0")
if "points" not in user_columns:
    cur.execute("ALTER TABLE users ADD COLUMN points INTEGER DEFAULT 0")
if "today_views" not in user_columns:
    cur.execute("ALTER TABLE users ADD COLUMN today_views INTEGER DEFAULT 0")
if "today_refs" not in user_columns:
    cur.execute("ALTER TABLE users ADD COLUMN today_refs INTEGER DEFAULT 0")
if "last_task_reset" not in user_columns:
    cur.execute("ALTER TABLE users ADD COLUMN last_task_reset INTEGER DEFAULT 0")
if "last_login_bonus" not in user_columns:
    cur.execute("ALTER TABLE users ADD COLUMN last_login_bonus INTEGER DEFAULT 0")

cur.execute(
    """
CREATE TABLE IF NOT EXISTS withdrawals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount INTEGER,
    status TEXT DEFAULT 'pending',
    note TEXT,
    requested_at INTEGER
)
"""
)

cur.execute("PRAGMA table_info(withdrawals)")
withdraw_columns = {row[1] for row in cur.fetchall()}
if "usdt_address" not in withdraw_columns:
    cur.execute("ALTER TABLE withdrawals ADD COLUMN usdt_address TEXT")

cur.execute(
    """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
"""
)

for k, v in {
    "referral_reward": "1",
    "view_reward": "1",
    "wait_seconds": str(MIN_VISIT_SECONDS),
    "registration_enabled": "1",
    "protection_level": "2",
    "view_points": "1",
    "daily_login_points": "2",
    "daily_task_referrals": "3",
    "daily_task_views": "5",
    "daily_task_reward_points": "10",
    "vip_profit_multiplier": "2",
    "level_bonus_minutes": "2",
    "points_to_minutes": "10",
}.items():
    cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

cur.execute(
    """
CREATE TABLE IF NOT EXISTS spam_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    reason TEXT,
    created_at INTEGER
)
"""
)

cur.execute(
    """
CREATE TABLE IF NOT EXISTS paid_ads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner INTEGER,
    url TEXT,
    expires INTEGER,
    priority INTEGER DEFAULT 1,
    created_at INTEGER
)
"""
)

cur.execute(
    """
CREATE TABLE IF NOT EXISTS point_shop (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    points_cost INTEGER,
    minutes_reward INTEGER,
    active INTEGER DEFAULT 1
)
"""
)

cur.execute("INSERT OR IGNORE INTO point_shop (id, title, points_cost, minutes_reward, active) VALUES (1, '10 نقاط = 1 دقيقة', 10, 1, 1)")
cur.execute("INSERT OR IGNORE INTO point_shop (id, title, points_cost, minutes_reward, active) VALUES (2, '50 نقاط = 6 دقائق', 50, 6, 1)")
db.commit()


# ================= HELPERS =================
def now_ts() -> int:
    return int(time.time())


def get_setting(key: str, default: str) -> str:
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    cur.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    db.commit()


def touch_user(uid: int) -> None:
    cur.execute("UPDATE users SET last_active=? WHERE user_id=?", (now_ts(), uid))
    db.commit()


def is_banned(uid: int) -> bool:
    cur.execute("SELECT banned FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    return bool(row and row[0])


def log_spam(uid: int, reason: str) -> None:
    cur.execute(
        "INSERT INTO spam_log (user_id, reason, created_at) VALUES (?, ?, ?)",
        (uid, reason, now_ts()),
    )
    db.commit()


def reset_daily_counters_if_needed(uid: int) -> None:
    now = now_ts()
    cur.execute("SELECT last_task_reset FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    last_reset = row[0] if row and row[0] else 0
    if now - last_reset >= 86400:
        cur.execute(
            "UPDATE users SET today_views=0, today_refs=0, last_task_reset=? WHERE user_id=?",
            (now, uid),
        )
        db.commit()


def apply_level_up(uid: int) -> None:
    cur.execute("SELECT referrals, level FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    if not row:
        return
    referrals, level = row
    desired_level = max(1, referrals // 50 + 1)
    if desired_level <= level:
        return
    bonus = int(get_setting("level_bonus_minutes", "2"))
    bonus_total = (desired_level - level) * bonus
    cur.execute(
        "UPDATE users SET level=?, minutes=minutes+? WHERE user_id=?",
        (desired_level, bonus_total, uid),
    )
    db.commit()


def reward_multiplier(uid: int) -> int:
    cur.execute("SELECT vip, level FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    if not row:
        return 1
    vip, level = row
    mult = 1
    if vip:
        mult *= int(get_setting("vip_profit_multiplier", "2"))
    if level > 1:
        mult += (level - 1) // 5
    return max(1, mult)


def main_kb():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔗 تخطي رابط", callback_data="skip")],
            [InlineKeyboardButton("➕ إضافة رابط", callback_data="add")],
            [InlineKeyboardButton("🪙 نقاطي / المتجر", callback_data="points")],
            [InlineKeyboardButton("✅ المهام اليومية", callback_data="tasks")],
            [InlineKeyboardButton("💸 طلب سحب USDT", callback_data="withdraw")],
            [InlineKeyboardButton("📌 إعلان مدفوع", callback_data="ad_buy")],
            [
                InlineKeyboardButton("📊 إحصائياتي", callback_data="stats"),
                InlineKeyboardButton("🏆 الترتيب", callback_data="top"),
            ],
            [
                InlineKeyboardButton("🎁 الهدية اليومية", callback_data="daily"),
                InlineKeyboardButton("👥 رابط الإحالة", callback_data="ref"),
            ],
        ]
    )


def main_reply_kb():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🔗 تخطي رابط"), KeyboardButton("➕ إضافة رابط")],
            [KeyboardButton("🪙 نقاطي / المتجر"), KeyboardButton("✅ المهام اليومية")],
            [KeyboardButton("💸 طلب سحب USDT"), KeyboardButton("📌 إعلان مدفوع")],
            [KeyboardButton("📊 إحصائياتي"), KeyboardButton("🏆 الترتيب")],
            [KeyboardButton("🎁 الهدية اليومية"), KeyboardButton("👥 رابط الإحالة")],
        ],
        resize_keyboard=True,
    )


def admin_kb():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 إحصائيات البوت", callback_data="a_stats")],
            [InlineKeyboardButton("📢 نظام البث", callback_data="a_bc")],
            [InlineKeyboardButton("📈 نظام الترتيب", callback_data="a_top")],
            [InlineKeyboardButton("👤 إدارة مستخدم", callback_data="a_user_manage")],
            [InlineKeyboardButton("💰 إدارة السحب", callback_data="a_withdrawals")],
            [InlineKeyboardButton("🛠️ إعدادات عامة", callback_data="a_settings")],
            [InlineKeyboardButton("🚨 الحماية", callback_data="a_protection")],
            [InlineKeyboardButton("➕ إضافة رابط ثابت", callback_data="a_add_link")],
            [InlineKeyboardButton("🗑 حذف رابط ثابت", callback_data="a_del_link")],
            [InlineKeyboardButton("📄 قائمة الروابط", callback_data="a_list_links")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back")],
        ]
    )


def admin_reply_kb():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📊 إحصائيات البوت")],
            [KeyboardButton("📢 نظام البث")],
            [KeyboardButton("📈 نظام الترتيب")],
            [KeyboardButton("👤 إدارة مستخدم")],
            [KeyboardButton("💰 إدارة السحب")],
            [KeyboardButton("🛠️ إعدادات عامة")],
            [KeyboardButton("🚨 الحماية")],
            [KeyboardButton("➕ إضافة رابط ثابت")],
            [KeyboardButton("🗑 حذف رابط ثابت")],
            [KeyboardButton("📄 قائمة الروابط")],
            [KeyboardButton("🔙 رجوع")],
        ],
        resize_keyboard=True,
    )


def ensure_user(uid: int, ref: int | None = None) -> tuple[bool, bool]:
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (uid,))
    exists = cur.fetchone()
    if exists:
        return False, False

    cur.execute(
        "INSERT INTO users (user_id, minutes, invited_by) VALUES (?, ?, ?)",
        (uid, 1 if ref else 0, ref if ref != uid else None),
    )

    referral_reward = int(get_setting("referral_reward", "1"))
    referral_points = int(get_setting("view_points", "1"))
    rewarded_ref = False
    if ref and ref != uid:
        cur.execute(
            "UPDATE users SET minutes = minutes + ?, points = points + ?, referrals = referrals + 1, today_refs = today_refs + 1 WHERE user_id=?",
            (referral_reward, referral_points, ref),
        )
        rewarded_ref = cur.rowcount > 0
    db.commit()
    if rewarded_ref:
        reset_daily_counters_if_needed(ref)
        apply_level_up(ref)
    return True, rewarded_ref


def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID


def get_user(uid: int):
    cur.execute(
        "SELECT minutes, total_clicks, referrals, last_daily, points, level, vip FROM users WHERE user_id=?",
        (uid,),
    )
    return cur.fetchone()


def get_next_link(uid: int):
    cur.execute(
        """
        SELECT id, url
        FROM paid_ads
        WHERE expires > ?
        ORDER BY priority DESC, id ASC
        LIMIT 1
        """,
        (now_ts(),),
    )
    ad_link = cur.fetchone()
    if ad_link:
        return ("ad", ad_link[0], ad_link[1])

    cur.execute(
        """
        SELECT id, url
        FROM admin_links
        WHERE id NOT IN (SELECT link_id FROM admin_click_history WHERE user_id=?)
        ORDER BY id ASC
        LIMIT 1
        """,
        (uid,),
    )
    admin_link = cur.fetchone()
    if admin_link:
        return ("admin", admin_link[0], admin_link[1])

    cur.execute(
        """
        SELECT l.id, l.url, l.owner
        FROM links
        JOIN users u ON u.user_id = l.owner
        WHERE l.owner != ?
          AND l.expires > ?
          AND l.id NOT IN (SELECT link_id FROM click_history WHERE user_id=?)
        ORDER BY u.vip DESC, u.level DESC, l.id ASC
        LIMIT 1
        """,
        (uid, now_ts(), uid),
    )
    user_link = cur.fetchone()
    if user_link:
        return ("user", user_link[0], user_link[1], user_link[2])
    return None


# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_banned(uid):
        await update.message.reply_text("🚫 أنت محظور من استخدام البوت.")
        return

    ref = int(context.args[0]) if context.args and context.args[0].isdigit() else None
    if get_setting("registration_enabled", "1") == "0":
        cur.execute("SELECT user_id FROM users WHERE user_id=?", (uid,))
        if not cur.fetchone():
            await update.message.reply_text("⛔ التسجيل متوقف حاليًا من الإدارة.")
            return

    created, rewarded_ref = ensure_user(uid, ref)
    touch_user(uid)

    if created and ref and rewarded_ref:
        try:
            await context.bot.send_message(
                chat_id=ref,
                text=(
                    "🎉 شخص جديد دخل برابط الإحالة الخاص بك.\n"
                    "✅ تم احتساب 1 دقيقة لك."
                ),
            )
        except Exception:
            pass

    await update.message.reply_text(
        "🤖 مرحبًا بك في بوت تبادل نقرات الروابط\n"
        "⏱ اكسب دقائق وفعّل رابطك\n"
        "👥 ادعُ أصدقاءك لتحصل على دقائق إضافية",
        reply_markup=main_reply_kb(),
    )


# ================= REF LINK =================
async def referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    link = f"https://t.me/{context.bot.username}?start={uid}"
    text = (
        f"👥 رابط الإحالة الخاص بك:\n\n{link}\n\n"
        "🎁 كل صديق = 1 دقيقة"
    )
    if q:
        await q.answer()
        await q.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=main_kb(),
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=main_reply_kb(),
        )


# ================= LINKS =================
async def skip_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    if "pending" in context.user_data:
        if q:
            await q.answer("⏳ أكمل التخطي الحالي أولًا", show_alert=True)
        else:
            await update.message.reply_text("⏳ أكمل التخطي الحالي أولًا.")
        return
    link = get_next_link(uid)
    if not link:
        if q:
            await q.answer()
            await q.edit_message_text("لا يوجد روابط متاحة حاليًا.", reply_markup=main_kb())
        else:
            await update.message.reply_text(
                "لا يوجد روابط متاحة حاليًا.", reply_markup=main_reply_kb()
            )
        return

    if link[0] == "ad":
        _, link_id, url = link
        context.user_data["pending"] = {"type": "ad", "link_id": link_id, "shown_at": now_ts()}
        confirm_data = f"confirm_ad_{link_id}"
    elif link[0] == "admin":
        _, link_id, url = link
        context.user_data["pending"] = {"type": "admin", "link_id": link_id, "shown_at": now_ts()}
        confirm_data = f"confirm_admin_{link_id}"
    else:
        _, link_id, url, owner = link
        context.user_data["pending"] = {"type": "user", "link_id": link_id, "shown_at": now_ts()}
        confirm_data = f"confirm_{link_id}"
    buttons = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ تم الدخول", callback_data=confirm_data)],
            [InlineKeyboardButton("🔗 تخطي", callback_data="skip")],
        ]
    )
    text = (
        f"🔗 الرابط التالي:\n{url}\n\n"
        "بعد الدخول اضغط (تم الدخول) لتحصل على دقيقة."
    )
    if q:
        await q.answer()
        await q.edit_message_text(text, reply_markup=buttons)
    else:
        await update.message.reply_text(text, reply_markup=buttons)


async def confirm_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    now = now_ts()

    wait_seconds = int(get_setting("wait_seconds", str(MIN_VISIT_SECONDS)))
    view_reward = int(get_setting("view_reward", "1"))
    view_points = int(get_setting("view_points", "1"))
    mult = reward_multiplier(uid)
    reward_minutes = view_reward * mult
    reward_points = view_points * mult

    last_confirm = context.user_data.get("last_confirm_at", 0)
    if now - last_confirm < CONFIRM_COOLDOWN_SECONDS:
        log_spam(uid, "rapid_confirm")
        await q.answer("⏳ لا تضغط بسرعة، حاول بعد ثوانٍ قليلة.", show_alert=True)
        return

    pending = context.user_data.get("pending")
    if not isinstance(pending, dict) or "type" not in pending or "link_id" not in pending:
        await q.edit_message_text("❌ لا يوجد رابط نشط للتأكيد حاليًا.", reply_markup=main_kb())
        return

    shown_at = pending.get("shown_at", now)
    if now - shown_at < wait_seconds:
        log_spam(uid, "early_confirm")
        await q.answer(
            f"⚠️ تأكيد مبكر جدًا. انتظر {wait_seconds} ثوانٍ على الأقل داخل الرابط.",
            show_alert=True,
        )
        return

    if q.data.startswith("confirm_ad_"):
        link_id = q.data.replace("confirm_ad_", "")
        if not link_id.isdigit():
            await q.edit_message_text("خطأ في الطلب.", reply_markup=main_kb())
            return
        link_id = int(link_id)
        if pending.get("type") != "ad" or pending.get("link_id") != link_id:
            log_spam(uid, "callback_mismatch_ad")
            await q.edit_message_text("❌ رابط التأكيد غير مطابق للرابط الحالي.", reply_markup=main_kb())
            return
        cur.execute("UPDATE users SET minutes = minutes + ?, points = points + ?, total_clicks = total_clicks + 1, today_views = today_views + 1 WHERE user_id=?", (reward_minutes, reward_points, uid))
        db.commit()
        context.user_data["last_confirm_at"] = now
        context.user_data.pop("pending", None)
        await q.edit_message_text("✅ تم احتساب المكافأة (دقائق + نقاط)!", reply_markup=main_kb())
        return

    if q.data.startswith("confirm_admin_"):
        link_id = q.data.replace("confirm_admin_", "")
        if not link_id.isdigit():
            await q.edit_message_text("خطأ في الطلب.", reply_markup=main_kb())
            return
        link_id = int(link_id)
        if pending.get("type") != "admin" or pending.get("link_id") != link_id:
            log_spam(uid, "callback_mismatch_admin")
            await q.edit_message_text("❌ رابط التأكيد غير مطابق للرابط الحالي.", reply_markup=main_kb())
            return
        cur.execute("SELECT url FROM admin_links WHERE id=?", (link_id,))
        row = cur.fetchone()
        if not row:
            await q.edit_message_text("هذا الرابط غير متاح.", reply_markup=main_kb())
            return
        cur.execute(
            "INSERT OR IGNORE INTO admin_click_history (user_id, link_id, clicked_at) VALUES (?, ?, ?)",
            (uid, link_id, now),
        )
        if cur.rowcount == 0:
            await q.edit_message_text("تم احتساب هذا الرابط سابقًا.", reply_markup=main_kb())
            return
        cur.execute(
            "UPDATE users SET minutes = minutes + ?, points = points + ?, total_clicks = total_clicks + 1, today_views = today_views + 1 WHERE user_id=?",
            (reward_minutes, reward_points, uid),
        )
        db.commit()
        context.user_data["last_confirm_at"] = now
        context.user_data.pop("pending", None)
        await q.edit_message_text("✅ تم احتساب دقيقة لك!", reply_markup=main_kb())
        return

    payload = q.data.split("_")
    if len(payload) != 2 or not payload[1].isdigit():
        await q.edit_message_text("خطأ في الطلب.", reply_markup=main_kb())
        return

    link_id = int(payload[1])
    if pending.get("type") != "user" or pending.get("link_id") != link_id:
        log_spam(uid, "callback_mismatch_user")
        await q.edit_message_text("❌ رابط التأكيد غير مطابق للرابط الحالي.", reply_markup=main_kb())
        return
    cur.execute(
        "SELECT owner FROM links WHERE id=? AND expires > ?",
        (link_id, now),
    )
    row = cur.fetchone()
    if not row:
        await q.edit_message_text("هذا الرابط غير متاح.", reply_markup=main_kb())
        return
    owner_id = row[0]

    cur.execute(
        "INSERT OR IGNORE INTO click_history (user_id, link_id, clicked_at) VALUES (?, ?, ?)",
        (uid, link_id, now),
    )
    if cur.rowcount == 0:
        await q.edit_message_text("تم احتساب هذا الرابط سابقًا.", reply_markup=main_kb())
        return

    cur.execute(
        "UPDATE users SET minutes = minutes + ?, points = points + ?, total_clicks = total_clicks + 1, today_views = today_views + 1 WHERE user_id=?",
        (reward_minutes, reward_points, uid),
    )
    cur.execute(
        "UPDATE users SET minutes = minutes + ?, points = points + ? WHERE user_id=?",
        (reward_minutes, reward_points, owner_id),
    )
    db.commit()
    try:
        await context.bot.send_message(
            chat_id=owner_id,
            text=(
                "🔔 قام أحد المستخدمين بالنقر/التخطي لرابطك.\n"
                "✅ تمت إضافة 1 دقيقة لك."
            ),
        )
    except Exception:
        pass
    context.user_data["last_confirm_at"] = now
    context.user_data.pop("pending", None)
    await q.edit_message_text("✅ تم احتساب دقيقة لك!", reply_markup=main_kb())


async def add_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    ensure_user(uid)
    user = get_user(uid)
    if not user:
        if q:
            await q.answer()
            await q.edit_message_text("تعذر العثور على بياناتك.", reply_markup=main_kb())
        else:
            await update.message.reply_text(
                "تعذر العثور على بياناتك.", reply_markup=main_reply_kb()
            )
        return

    minutes = user[0]
    if minutes < 20:
        if q:
            await q.answer()
            await q.edit_message_text("❌ تحتاج 20 دقيقة لإضافة رابط.", reply_markup=main_kb())
        else:
            await update.message.reply_text("❌ تحتاج 20 دقيقة لإضافة رابط.", reply_markup=main_reply_kb())
        return

    context.user_data["awaiting_link"] = True
    if q:
        await q.answer()
        await q.edit_message_text(
            "أرسل الرابط الآن (يجب أن يبدأ بـ http أو https).",
            reply_markup=main_kb(),
        )
    else:
        await update.message.reply_text(
            "أرسل الرابط الآن (يجب أن يبدأ بـ http أو https).",
            reply_markup=main_reply_kb(),
        )


async def handle_link_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_link"):
        return

    uid = update.effective_user.id
    url = update.message.text.strip()
    if not URL_PATTERN.match(url):
        await update.message.reply_text("الرجاء إرسال رابط صحيح.")
        return

    user = get_user(uid)
    if not user:
        await update.message.reply_text("تعذر العثور على بياناتك.")
        return

    minutes = user[0]
    if minutes < 20:
        await update.message.reply_text("❌ تحتاج 20 دقيقة لإضافة رابط.")
        return

    expires = now_ts() + 20 * 60
    cur.execute(
        "INSERT INTO links (owner, url, expires) VALUES (?, ?, ?)",
        (uid, url, expires),
    )
    cur.execute("UPDATE users SET minutes = minutes - 20 WHERE user_id=?", (uid,))
    db.commit()
    context.user_data["awaiting_link"] = False
    await update.message.reply_text(
        "✅ تم إضافة الرابط وسيظل نشطًا لمدة 20 دقيقة.",
        reply_markup=main_reply_kb(),
    )


# ================= STATS =================
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    user = get_user(uid)
    if not user:
        if q:
            await q.answer()
            await q.edit_message_text("لا توجد بيانات.", reply_markup=main_kb())
        else:
            await update.message.reply_text(
                "لا توجد بيانات.", reply_markup=main_reply_kb()
            )
        return

    minutes, clicks, referrals, _, points, level, vip = user
    text = (
        "📊 إحصائياتك:\n"
        f"⏱ الدقائق: {minutes}\n"
        f"✅ النقرات: {clicks}\n"
        f"👥 الإحالات: {referrals}\n"
        f"🪙 النقاط: {points}\n"
        f"🏷 المستوى: {level}\n"
        f"💎 VIP: {'نعم' if vip else 'لا'}"
    )
    if q:
        await q.answer()
        await q.edit_message_text(text, reply_markup=main_kb())
    else:
        await update.message.reply_text(text, reply_markup=main_reply_kb())


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    cur.execute(
        "SELECT user_id, minutes FROM users ORDER BY minutes DESC LIMIT 10"
    )
    rows = cur.fetchall()
    if not rows:
        if q:
            await q.answer()
            await q.edit_message_text("لا يوجد ترتيب بعد.", reply_markup=main_kb())
        else:
            await update.message.reply_text(
                "لا يوجد ترتيب بعد.", reply_markup=main_reply_kb()
            )
        return

    lines = []
    for idx, (user_id, minutes) in enumerate(rows, start=1):
        lines.append(f"{idx}. {user_id} — {minutes} دقيقة")
    text = "🏆 أفضل 10:\n" + "\n".join(lines)
    if q:
        await q.answer()
        await q.edit_message_text(text, reply_markup=main_kb())
    else:
        await update.message.reply_text(text, reply_markup=main_reply_kb())


# ================= DAILY =================
async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    user = get_user(uid)
    if not user:
        if q:
            await q.answer()
            await q.edit_message_text("لا توجد بيانات.", reply_markup=main_kb())
        else:
            await update.message.reply_text(
                "لا توجد بيانات.", reply_markup=main_reply_kb()
            )
        return

    last_daily = user[3]
    now = now_ts()
    if now - last_daily < DAILY_COOLDOWN_SECONDS:
        remaining = DAILY_COOLDOWN_SECONDS - (now - last_daily)
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        text = f"انتظر {hours} ساعة و {minutes} دقيقة للحصول على هدية جديدة."
        if q:
            await q.answer()
            await q.edit_message_text(text, reply_markup=main_kb())
        else:
            await update.message.reply_text(text, reply_markup=main_reply_kb())
        return

    cur.execute(
        "UPDATE users SET minutes = minutes + 1, last_daily = ? WHERE user_id=?",
        (now, uid),
    )
    db.commit()
    text = "🎁 تم إضافة دقيقة كهدية يومية!"
    if q:
        await q.answer()
        await q.edit_message_text(text, reply_markup=main_kb())
    else:
        await update.message.reply_text(text, reply_markup=main_reply_kb())


# ================= ADMIN =================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ هذا الأمر مخصص للإدارة فقط.")
        return
    await update.message.reply_text("لوحة الأدمن:", reply_markup=admin_reply_kb())


async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        q = update.callback_query
        await q.answer()
        await q.edit_message_text("تم الرجوع.", reply_markup=main_kb())
    else:
        await update.message.reply_text("تم الرجوع.", reply_markup=main_reply_kb())


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    if not is_admin(uid):
        if q:
            await q.answer("غير مصرح.", show_alert=True)
        else:
            await update.message.reply_text("❌ هذا الأمر مخصص للإدارة فقط.")
        return

    now = now_ts()
    day_ago = now - 86400
    cur.execute("SELECT COUNT(*) FROM users")
    users_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE last_active >= ?", (day_ago,))
    active_today = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(SUM(referrals), 0) FROM users")
    total_referrals = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(SUM(minutes), 0) FROM users")
    total_minutes = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE vip=1")
    vip_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE banned=1")
    banned_count = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(SUM(amount), 0) FROM withdrawals WHERE status='paid'")
    total_earnings = cur.fetchone()[0]

    text = (
        "📊 إحصائيات شاملة\n"
        f"👥 إجمالي المستخدمين: {users_count}\n"
        f"🟢 النشطون اليوم: {active_today}\n"
        f"🤝 إجمالي الإحالات: {total_referrals}\n"
        f"⏱ إجمالي الدقائق: {total_minutes}\n"
        f"💎 عدد VIP: {vip_count}\n"
        f"🚫 عدد المحظورين: {banned_count}\n"
        f"💵 إجمالي المدفوعات: {total_earnings} USDT"
    )
    if q:
        await q.answer()
        await q.edit_message_text(text, reply_markup=admin_kb())
    else:
        await update.message.reply_text(text, reply_markup=admin_reply_kb())


async def admin_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    if not is_admin(uid):
        if q:
            await q.answer("غير مصرح.", show_alert=True)
        else:
            await update.message.reply_text("❌ هذا الأمر مخصص للإدارة فقط.")
        return

    cur.execute("SELECT user_id, referrals FROM users ORDER BY referrals DESC LIMIT 10")
    refs = cur.fetchall()
    cur.execute("SELECT user_id, total_clicks FROM users ORDER BY total_clicks DESC LIMIT 10")
    active = cur.fetchall()
    ref_lines = [f"{i}. {u} — {r}" for i, (u, r) in enumerate(refs, 1)] or ["لا يوجد"]
    act_lines = [f"{i}. {u} — {c}" for i, (u, c) in enumerate(active, 1)] or ["لا يوجد"]
    text = "📈 نظام الترتيب:\n\n🏅 أفضل 10 إحالات:\n" + "\n".join(ref_lines) + "\n\n🔥 أفضل 10 نشاط:\n" + "\n".join(act_lines)


    if q:
        await q.answer()
        await q.edit_message_text(text, reply_markup=admin_kb())
    else:
        await update.message.reply_text(text, reply_markup=admin_reply_kb())


async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    if not is_admin(uid):
        if q:
            await q.answer("غير مصرح.", show_alert=True)
        else:
            await update.message.reply_text("❌ هذا الأمر مخصص للإدارة فقط.")
        return
    if q:
        await admin_broadcast_menu(update, context)
    else:
        await update.message.reply_text("استخدم لوحة الأدمن لاختيار نوع البث.", reply_markup=admin_reply_kb())


async def admin_add_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    if not is_admin(uid):
        if q:
            await q.answer("غير مصرح.", show_alert=True)
        else:
            await update.message.reply_text("❌ هذا الأمر مخصص للإدارة فقط.")
        return
    context.user_data["awaiting_admin_link"] = True
    if q:
        await q.answer()
        await q.edit_message_text("أرسل الرابط المختصر لإضافته بشكل دائم.")
    else:
        await update.message.reply_text("أرسل الرابط المختصر لإضافته بشكل دائم.", reply_markup=admin_reply_kb())


async def admin_delete_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    if not is_admin(uid):
        if q:
            await q.answer("غير مصرح.", show_alert=True)
        else:
            await update.message.reply_text("❌ هذا الأمر مخصص للإدارة فقط.")
        return
    cur.execute("SELECT id, url FROM admin_links ORDER BY id ASC LIMIT 20")
    rows = cur.fetchall()
    if not rows:
        message = "لا توجد روابط ثابتة."
        if q:
            await q.answer()
            await q.edit_message_text(message, reply_markup=admin_kb())
        else:
            await update.message.reply_text(message, reply_markup=admin_reply_kb())
        return
    buttons = [[InlineKeyboardButton(f"حذف {row[0]}", callback_data=f"admin_del_{row[0]}")] for row in rows]
    if q:
        await q.answer()
        await q.edit_message_text("اختر رابطًا للحذف:", reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update.message.reply_text("اختر رابطًا للحذف:", reply_markup=InlineKeyboardMarkup(buttons))


async def admin_list_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    if not is_admin(uid):
        if q:
            await q.answer("غير مصرح.", show_alert=True)
        else:
            await update.message.reply_text("❌ هذا الأمر مخصص للإدارة فقط.")
        return
    cur.execute("SELECT id, url FROM admin_links ORDER BY id ASC LIMIT 50")
    rows = cur.fetchall()
    message = "لا توجد روابط ثابتة." if not rows else "📄 الروابط الثابتة:\n" + "\n".join([f"{row[0]}. {row[1]}" for row in rows])
    if q:
        await q.answer()
        await q.edit_message_text(message, reply_markup=admin_kb())
    else:
        await update.message.reply_text(message, reply_markup=admin_reply_kb())


async def admin_delete_link_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    if not is_admin(uid):
        if q:
            await q.answer("غير مصرح.", show_alert=True)
        else:
            await update.message.reply_text("❌ هذا الأمر مخصص للإدارة فقط.")
        return
    payload = q.data.replace("admin_del_", "")
    if not payload.isdigit():
        await q.edit_message_text("خطأ في الطلب.", reply_markup=admin_kb())
        return
    link_id = int(payload)
    cur.execute("DELETE FROM admin_links WHERE id=?", (link_id,))
    cur.execute("DELETE FROM admin_click_history WHERE link_id=?", (link_id,))
    db.commit()
    await q.answer()
    await q.edit_message_text("✅ تم حذف الرابط.", reply_markup=admin_kb())


async def handle_admin_link_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_admin_link"):
        return
    uid = update.effective_user.id
    if not is_admin(uid):
        context.user_data["awaiting_admin_link"] = False
        await update.message.reply_text("❌ هذا الأمر مخصص للإدارة فقط.")
        return
    url = update.message.text.strip()
    if not URL_PATTERN.match(url):
        await update.message.reply_text("الرجاء إرسال رابط صحيح.")
        return
    cur.execute("INSERT INTO admin_links (url, added_at) VALUES (?, ?)", (url, now_ts()))
    db.commit()
    context.user_data["awaiting_admin_link"] = False
    await update.message.reply_text("✅ تم إضافة الرابط الثابت.", reply_markup=admin_reply_kb())


async def admin_user_manage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    if not is_admin(uid):
        if q:
            await q.answer("غير مصرح.", show_alert=True)
        else:
            await update.message.reply_text("❌ هذا الأمر مخصص للإدارة فقط.")
        return
    context.user_data["awaiting_user_id"] = True
    if q:
        await q.answer()
        await q.edit_message_text("أرسل ID المستخدم لإدارته.")
    else:
        await update.message.reply_text("أرسل ID المستخدم لإدارته.", reply_markup=admin_reply_kb())


async def admin_show_user_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, target_id: int):
    cur.execute("SELECT minutes, referrals, total_clicks, vip, banned, level FROM users WHERE user_id=?", (target_id,))
    row = cur.fetchone()
    if not row:
        await update.message.reply_text("المستخدم غير موجود.", reply_markup=admin_reply_kb())
        return
    minutes, refs, clicks, vip, banned, level = row
    text = (
        f"👤 المستخدم: {target_id}\n"
        f"⏱ الرصيد: {minutes}\n"
        f"🤝 إحالات: {refs}\n"
        f"✅ نقرات: {clicks}\n"
        f"🏷 المستوى: {level}\n"
        f"💎 VIP: {'نعم' if vip else 'لا'}\n"
        f"🚫 محظور: {'نعم' if banned else 'لا'}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ تعديل الرصيد", callback_data=f"u_addm_{target_id}")],
        [InlineKeyboardButton("🏷 تعديل المستوى", callback_data=f"u_level_{target_id}")],
        [InlineKeyboardButton("💎 إعطاء VIP", callback_data=f"u_vip_on_{target_id}"), InlineKeyboardButton("❌ سحب VIP", callback_data=f"u_vip_off_{target_id}")],
        [InlineKeyboardButton("🚫 حظر", callback_data=f"u_ban_{target_id}"), InlineKeyboardButton("✅ فك الحظر", callback_data=f"u_unban_{target_id}")],
        [InlineKeyboardButton("🗑 حذف الحساب", callback_data=f"u_del_{target_id}")],
    ])
    await update.message.reply_text(text, reply_markup=kb)


async def admin_user_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    if not is_admin(uid):
        await q.answer("غير مصرح.", show_alert=True)
        return
    data = q.data.split("_")
    await q.answer()
    if len(data) < 3:
        await q.edit_message_text("خطأ في الطلب.", reply_markup=admin_kb())
        return
    action = "_".join(data[1:-1])
    target_id = int(data[-1])

    if action == "addm":
        context.user_data["awaiting_add_minutes"] = target_id
        await q.edit_message_text("أرسل قيمة الرصيد (يمكن بالسالب).")
        return
    if action == "level":
        context.user_data["awaiting_level"] = target_id
        await q.edit_message_text("أرسل المستوى الجديد (رقم).")
        return
    if action == "vip_on":
        cur.execute("UPDATE users SET vip=1 WHERE user_id=?", (target_id,))
    elif action == "vip_off":
        cur.execute("UPDATE users SET vip=0 WHERE user_id=?", (target_id,))
    elif action == "ban":
        cur.execute("UPDATE users SET banned=1 WHERE user_id=?", (target_id,))
    elif action == "unban":
        cur.execute("UPDATE users SET banned=0 WHERE user_id=?", (target_id,))
    elif action == "del":
        cur.execute("DELETE FROM users WHERE user_id=?", (target_id,))
        cur.execute("DELETE FROM links WHERE owner=?", (target_id,))
        cur.execute("DELETE FROM click_history WHERE user_id=?", (target_id,))
    db.commit()
    await q.edit_message_text("✅ تم تنفيذ العملية.", reply_markup=admin_kb())


async def admin_withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    if not is_admin(uid):
        if q:
            await q.answer("غير مصرح.", show_alert=True)
        else:
            await update.message.reply_text("❌ هذا الأمر مخصص للإدارة فقط.")
        return
    cur.execute("SELECT id, user_id, amount, usdt_address FROM withdrawals WHERE status='pending' ORDER BY id ASC LIMIT 20")
    rows = cur.fetchall()
    if not rows:
        msg = "لا توجد طلبات سحب معلقة."
        if q:
            await q.answer(); await q.edit_message_text(msg, reply_markup=admin_kb())
        else:
            await update.message.reply_text(msg, reply_markup=admin_reply_kb())
        return
    buttons = []
    lines = ["💰 طلبات السحب:"]
    for wid, user_id, amount, usdt_address in rows:
        lines.append(f"#{wid} - {user_id} - {amount} - {usdt_address or '-'}")
        buttons.append([InlineKeyboardButton(f"✅ قبول #{wid}", callback_data=f"wd_ok_{wid}"), InlineKeyboardButton(f"❌ رفض #{wid}", callback_data=f"wd_no_{wid}")])
        buttons.append([InlineKeyboardButton(f"📝 ملاحظة #{wid}", callback_data=f"wd_note_{wid}")])
    kb = InlineKeyboardMarkup(buttons)
    if q:
        await q.answer(); await q.edit_message_text("\n".join(lines), reply_markup=kb)
    else:
        await update.message.reply_text("\n".join(lines), reply_markup=kb)


async def admin_withdrawal_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    if not is_admin(uid):
        await q.answer("غير مصرح.", show_alert=True)
        return
    _, action, wid = q.data.split("_")
    wid = int(wid)
    if action == "ok":
        cur.execute("UPDATE withdrawals SET status='paid' WHERE id=?", (wid,))
        cur.execute("SELECT user_id, amount FROM withdrawals WHERE id=?", (wid,))
        row = cur.fetchone()
        if row:
            try:
                await context.bot.send_message(chat_id=row[0], text=f"✅ تم دفع طلب السحب #{wid} بقيمة {row[1]} USDT")
            except Exception:
                pass
    elif action == "no":
        cur.execute("UPDATE withdrawals SET status='rejected' WHERE id=?", (wid,))
    elif action == "note":
        context.user_data["awaiting_withdraw_note"] = wid
        await q.answer(); await q.edit_message_text("أرسل الملاحظة الآن.")
        return
    db.commit()
    await q.answer("تم")
    await q.edit_message_text("✅ تم تحديث طلب السحب.", reply_markup=admin_kb())


async def admin_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    if not is_admin(uid):
        if q:
            await q.answer("غير مصرح.", show_alert=True)
        else:
            await update.message.reply_text("❌ هذا الأمر مخصص للإدارة فقط.")
        return
    text = (
        "🛠️ الإعدادات الحالية\n"
        f"🎁 مكافأة الإحالة: {get_setting('referral_reward', '1')}\n"
        f"👀 مكافأة المشاهدة: {get_setting('view_reward', '1')}\n"
        f"⏳ وقت الانتظار: {get_setting('wait_seconds', str(MIN_VISIT_SECONDS))}\n"
        f"📝 التسجيل: {'مفعل' if get_setting('registration_enabled', '1') == '1' else 'معطل'}\n"
        f"🛡 مستوى الحماية: {get_setting('protection_level', '2')}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("تغيير مكافأة الإحالة", callback_data="set_ref")],
        [InlineKeyboardButton("تغيير مكافأة المشاهدة", callback_data="set_view")],
        [InlineKeyboardButton("تعديل وقت الانتظار", callback_data="set_wait")],
        [InlineKeyboardButton("تفعيل/تعطيل التسجيل", callback_data="set_reg")],
        [InlineKeyboardButton("تعديل مستوى الحماية", callback_data="set_prot")],
    ])
    if q:
        await q.answer(); await q.edit_message_text(text, reply_markup=kb)
    else:
        await update.message.reply_text(text, reply_markup=kb)


async def admin_setting_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    if not is_admin(uid):
        await q.answer("غير مصرح.", show_alert=True)
        return
    action = q.data
    if action == "set_reg":
        new_val = "0" if get_setting("registration_enabled", "1") == "1" else "1"
        set_setting("registration_enabled", new_val)
        await q.answer("تم")
        await q.edit_message_text("✅ تم تحديث حالة التسجيل.", reply_markup=admin_kb())
        return
    mapping = {"set_ref": "referral_reward", "set_view": "view_reward", "set_wait": "wait_seconds", "set_prot": "protection_level"}
    if action in mapping:
        context.user_data["awaiting_setting_key"] = mapping[action]
        await q.answer(); await q.edit_message_text("أرسل القيمة الجديدة.")


async def admin_protection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    if not is_admin(uid):
        if q:
            await q.answer("غير مصرح.", show_alert=True)
        else:
            await update.message.reply_text("❌ هذا الأمر مخصص للإدارة فقط.")
        return
    cur.execute("SELECT COUNT(*) FROM spam_log")
    spam_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM users WHERE banned=1")
    banned_count = cur.fetchone()[0]
    text = (
        f"🚨 الحماية:\n"
        f"📛 سجلات السبام: {spam_count}\n"
        f"🚫 المحظورون: {banned_count}\n"
        f"مستوى الحماية: {get_setting('protection_level', '2')}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("عرض قائمة السبام", callback_data="prot_spam")],
        [InlineKeyboardButton("عرض المحظورين", callback_data="prot_banned")],
        [InlineKeyboardButton("فك حظر مستخدم", callback_data="prot_unban")],
    ])
    if q:
        await q.answer(); await q.edit_message_text(text, reply_markup=kb)
    else:
        await update.message.reply_text(text, reply_markup=kb)


async def admin_protection_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    if not is_admin(uid):
        await q.answer("غير مصرح.", show_alert=True)
        return
    if q.data == "prot_spam":
        cur.execute("SELECT user_id, reason FROM spam_log ORDER BY id DESC LIMIT 20")
        rows = cur.fetchall()
        text = "📛 آخر سجلات السبام:\n" + ("\n".join([f"{r[0]} - {r[1]}" for r in rows]) if rows else "لا يوجد")
        await q.answer(); await q.edit_message_text(text, reply_markup=admin_kb())
    elif q.data == "prot_banned":
        cur.execute("SELECT user_id FROM users WHERE banned=1 ORDER BY user_id DESC LIMIT 50")
        rows = cur.fetchall()
        text = "🚫 المحظورون:\n" + ("\n".join([str(r[0]) for r in rows]) if rows else "لا يوجد")
        await q.answer(); await q.edit_message_text(text, reply_markup=admin_kb())
    elif q.data == "prot_unban":
        context.user_data["awaiting_unban_id"] = True
        await q.answer(); await q.edit_message_text("أرسل ID المستخدم لفك الحظر.")


async def admin_broadcast_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    if not is_admin(uid):
        await q.answer("غير مصرح.", show_alert=True)
        return
    context.user_data["bc_target"] = "all"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("الكل", callback_data="bc_t_all"), InlineKeyboardButton("VIP", callback_data="bc_t_vip")],
        [InlineKeyboardButton("غير VIP", callback_data="bc_t_nonvip"), InlineKeyboardButton("المحظورون", callback_data="bc_t_banned")],
        [InlineKeyboardButton("رسالة نصية", callback_data="bc_m_text")],
        [InlineKeyboardButton("صورة + نص", callback_data="bc_m_photo")],
        [InlineKeyboardButton("زر + رابط", callback_data="bc_m_button")],
    ])
    await q.answer()
    await q.edit_message_text("📢 اختر فئة الإرسال ثم نوع البث.", reply_markup=kb)


async def admin_broadcast_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    if not is_admin(uid):
        await q.answer("غير مصرح.", show_alert=True)
        return
    if q.data.startswith("bc_t_"):
        context.user_data["bc_target"] = q.data.replace("bc_t_", "")
        await q.answer("تم اختيار الفئة")
        return
    if q.data.startswith("bc_m_"):
        context.user_data["bc_mode"] = q.data.replace("bc_m_", "")
        context.user_data["broadcast"] = True
        await q.answer()
        msg = "أرسل الرسالة الآن."
        if context.user_data["bc_mode"] == "photo":
            msg = "أرسل صورة مع تعليق (Caption)."
        elif context.user_data["bc_mode"] == "button":
            msg = "أرسل بهذا الشكل: النص | نص الزر | الرابط"
        await q.edit_message_text(msg)


async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("broadcast"):
        return

    uid = update.effective_user.id
    if uid != ADMIN_ID:
        context.user_data["broadcast"] = False
        return

    mode = context.user_data.get("bc_mode", "text")
    target = context.user_data.get("bc_target", "all")

    where = ""
    params = []
    if target == "vip":
        where = " WHERE vip=1"
    elif target == "nonvip":
        where = " WHERE vip=0"
    elif target == "banned":
        where = " WHERE banned=1"

    cur.execute("SELECT user_id FROM users" + where, params)
    users = [row[0] for row in cur.fetchall()]
    sent = 0

    for user_id in users:
        try:
            if mode == "photo" and update.message.photo:
                photo_id = update.message.photo[-1].file_id
                await context.bot.send_photo(chat_id=user_id, photo=photo_id, caption=update.message.caption or "")
            elif mode == "button":
                parts = [p.strip() for p in (update.message.text or "").split("|")]
                if len(parts) != 3:
                    continue
                msg, btn_text, btn_url = parts
                kb = InlineKeyboardMarkup([[InlineKeyboardButton(btn_text, url=btn_url)]])
                await context.bot.send_message(chat_id=user_id, text=msg, reply_markup=kb)
            else:
                await context.bot.send_message(chat_id=user_id, text=update.message.text or "")
            sent += 1
        except Exception:
            continue

    context.user_data["broadcast"] = False
    context.user_data.pop("bc_mode", None)
    await update.message.reply_text(f"تم إرسال البث إلى {sent} مستخدم.")


async def points_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    cur.execute("SELECT points, minutes, level, vip FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone() or (0, 0, 1, 0)
    points, minutes, level, vip = row
    cur.execute("SELECT id, title, points_cost, minutes_reward FROM point_shop WHERE active=1 ORDER BY id ASC")
    items = cur.fetchall()
    lines = [
        f"🪙 نقاطك: {points}",
        f"⏱ دقائقك: {minutes}",
        f"🏷 مستواك: {level}",
        f"💎 VIP: {'نعم' if vip else 'لا'}",
        "",
        "🛒 المتجر:",
    ]
    buttons = []
    for item_id, title, cost, reward in items:
        lines.append(f"{item_id}. {title} (التكلفة: {cost})")
        buttons.append([InlineKeyboardButton(f"استبدال #{item_id}", callback_data=f"shop_buy_{item_id}")])
    kb = InlineKeyboardMarkup(buttons) if buttons else main_kb()
    text = "\n".join(lines)
    if q:
        await q.answer()
        await q.edit_message_text(text, reply_markup=kb)
    else:
        await update.message.reply_text(text, reply_markup=main_reply_kb())


async def shop_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    await q.answer()
    item_id = q.data.replace("shop_buy_", "")
    if not item_id.isdigit():
        await q.edit_message_text("طلب غير صالح.", reply_markup=main_kb())
        return
    item_id = int(item_id)
    cur.execute("SELECT points_cost, minutes_reward FROM point_shop WHERE id=? AND active=1", (item_id,))
    row = cur.fetchone()
    if not row:
        await q.edit_message_text("العنصر غير متاح.", reply_markup=main_kb())
        return
    cost, reward = row
    cur.execute("SELECT points FROM users WHERE user_id=?", (uid,))
    user = cur.fetchone()
    points = user[0] if user else 0
    if points < cost:
        await q.edit_message_text("❌ نقاطك غير كافية.", reply_markup=main_kb())
        return
    cur.execute("UPDATE users SET points = points - ?, minutes = minutes + ? WHERE user_id=?", (cost, reward, uid))
    db.commit()
    await q.edit_message_text(f"✅ تم الاستبدال بنجاح (+{reward} دقيقة).", reply_markup=main_kb())


async def tasks_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    reset_daily_counters_if_needed(uid)
    cur.execute("SELECT today_refs, today_views, last_login_bonus, points FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone() or (0, 0, 0, 0)
    today_refs, today_views, last_login_bonus, points = row
    req_refs = int(get_setting("daily_task_referrals", "3"))
    req_views = int(get_setting("daily_task_views", "5"))
    reward_points = int(get_setting("daily_task_reward_points", "10"))
    text = (
        "✅ المهام اليومية:\n"
        f"👥 دعوة {req_refs}: {today_refs}/{req_refs}\n"
        f"🔗 مشاهدة {req_views}: {today_views}/{req_views}\n"
        f"🎁 مكافأة إنهاء المهام: {reward_points} نقطة\n"
        f"🪙 نقاطك الحالية: {points}"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("استلام مكافأة المهام", callback_data="tasks_claim")]])
    if q:
        await q.answer()
        await q.edit_message_text(text, reply_markup=kb)
    else:
        await update.message.reply_text(text, reply_markup=main_reply_kb())


async def tasks_claim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    await q.answer()
    reset_daily_counters_if_needed(uid)
    req_refs = int(get_setting("daily_task_referrals", "3"))
    req_views = int(get_setting("daily_task_views", "5"))
    reward_points = int(get_setting("daily_task_reward_points", "10"))
    cur.execute("SELECT today_refs, today_views, last_task_reset FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone() or (0, 0, 0)
    today_refs, today_views, last_task_reset = row
    if today_refs < req_refs or today_views < req_views:
        await q.edit_message_text("❌ لم تكمل المهام اليومية بعد.", reply_markup=main_kb())
        return
    cur.execute("UPDATE users SET points = points + ?, today_refs=0, today_views=0, last_task_reset=? WHERE user_id=?", (reward_points, now_ts(), uid))
    db.commit()
    await q.edit_message_text(f"✅ تم إضافة {reward_points} نقطة.", reply_markup=main_kb())


async def withdraw_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    if q:
        await q.answer()
    context.user_data["awaiting_withdraw_address"] = True
    msg = "أرسل عنوان USDT ثم المبلغ بهذا الشكل:\nالعنوان | المبلغ"
    if q:
        await q.edit_message_text(msg)
    else:
        await update.message.reply_text(msg, reply_markup=main_reply_kb())


async def ad_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = update.effective_user.id
    if q:
        await q.answer()
    context.user_data["awaiting_ad"] = True
    msg = "أرسل الإعلان بهذا الشكل:\nالرابط | عدد الدقائق"
    if q:
        await q.edit_message_text(msg)
    else:
        await update.message.reply_text(msg, reply_markup=main_reply_kb())


# ================= TEXT SHORTCUTS =================
async def handle_text_shortcuts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_link") or context.user_data.get("broadcast"):
        return

    text = update.message.text.strip()
    if text == "🔗 تخطي رابط":
        await skip_link(update, context)
    elif text == "➕ إضافة رابط":
        await add_link(update, context)
    elif text == "🪙 نقاطي / المتجر":
        await points_panel(update, context)
    elif text == "✅ المهام اليومية":
        await tasks_panel(update, context)
    elif text == "💸 طلب سحب USDT":
        await withdraw_request(update, context)
    elif text == "📌 إعلان مدفوع":
        await ad_buy(update, context)
    elif text == "📊 إحصائياتي":
        await stats(update, context)
    elif text == "🏆 الترتيب":
        await top(update, context)
    elif text == "🎁 الهدية اليومية":
        await daily(update, context)
    elif text == "👥 رابط الإحالة":
        await referral(update, context)
    elif text == "📊 إحصائيات البوت":
        await admin_stats(update, context)
    elif text == "📢 نظام البث":
        await update.message.reply_text("استخدم الأزرار داخل /admin لإدارة البث المتقدم.", reply_markup=admin_reply_kb())
    elif text == "📈 نظام الترتيب":
        await admin_top(update, context)
    elif text == "👤 إدارة مستخدم":
        await admin_user_manage(update, context)
    elif text == "💰 إدارة السحب":
        await admin_withdrawals(update, context)
    elif text == "🛠️ إعدادات عامة":
        await admin_settings(update, context)
    elif text == "🚨 الحماية":
        await admin_protection(update, context)
    elif text == "➕ إضافة رابط ثابت":
        await admin_add_link(update, context)
    elif text == "🗑 حذف رابط ثابت":
        await admin_delete_link(update, context)
    elif text == "📄 قائمة الروابط":
        await admin_list_links(update, context)
    elif text == "🔙 رجوع":
        await update.message.reply_text("تم الرجوع.", reply_markup=main_reply_kb())


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_banned(uid):
        await update.message.reply_text("🚫 أنت محظور من استخدام البوت.")
        return
    touch_user(uid)
    reset_daily_counters_if_needed(uid)

    cur.execute("SELECT last_login_bonus FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    last_login_bonus = row[0] if row and row[0] else 0
    now = now_ts()
    if now - last_login_bonus >= 86400:
        login_points = int(get_setting("daily_login_points", "2"))
        cur.execute("UPDATE users SET points = points + ?, last_login_bonus=? WHERE user_id=?", (login_points, now, uid))
        db.commit()
        await update.message.reply_text(f"🎁 مكافأة الدخول اليومي: +{login_points} نقطة")

    if context.user_data.get("awaiting_link"):
        await handle_link_message(update, context)
        return
    if context.user_data.get("awaiting_admin_link"):
        await handle_admin_link_message(update, context)
        return
    if context.user_data.get("awaiting_user_id"):
        context.user_data["awaiting_user_id"] = False
        if not update.message.text.isdigit():
            await update.message.reply_text("أرسل ID صحيح.")
            return
        await admin_show_user_menu(update, context, int(update.message.text))
        return
    if context.user_data.get("awaiting_add_minutes"):
        target_id = context.user_data.pop("awaiting_add_minutes")
        try:
            value = int(update.message.text.strip())
        except ValueError:
            await update.message.reply_text("القيمة يجب أن تكون رقمًا.")
            return
        cur.execute("UPDATE users SET minutes = minutes + ? WHERE user_id=?", (value, target_id))
        db.commit()
        await update.message.reply_text("✅ تم تعديل الرصيد.", reply_markup=admin_reply_kb())
        return
    if context.user_data.get("awaiting_level"):
        target_id = context.user_data.pop("awaiting_level")
        if not update.message.text.strip().isdigit():
            await update.message.reply_text("المستوى يجب أن يكون رقمًا.")
            return
        cur.execute("UPDATE users SET level=? WHERE user_id=?", (int(update.message.text.strip()), target_id))
        db.commit()
        await update.message.reply_text("✅ تم تعديل المستوى.", reply_markup=admin_reply_kb())
        return
    if context.user_data.get("awaiting_setting_key"):
        key = context.user_data.pop("awaiting_setting_key")
        set_setting(key, update.message.text.strip())
        await update.message.reply_text("✅ تم حفظ الإعداد.", reply_markup=admin_reply_kb())
        return
    if context.user_data.get("awaiting_unban_id"):
        context.user_data["awaiting_unban_id"] = False
        if not update.message.text.strip().isdigit():
            await update.message.reply_text("أرسل ID صحيح.")
            return
        cur.execute("UPDATE users SET banned=0 WHERE user_id=?", (int(update.message.text.strip()),))
        db.commit()
        await update.message.reply_text("✅ تم فك الحظر.", reply_markup=admin_reply_kb())
        return
    if context.user_data.get("awaiting_withdraw_note"):
        wid = context.user_data.pop("awaiting_withdraw_note")
        cur.execute("UPDATE withdrawals SET note=? WHERE id=?", (update.message.text.strip(), wid))
        db.commit()
        await update.message.reply_text("✅ تمت إضافة الملاحظة.", reply_markup=admin_reply_kb())
        return
    if context.user_data.get("awaiting_withdraw_address"):
        context.user_data["awaiting_withdraw_address"] = False
        if "|" not in update.message.text:
            await update.message.reply_text("صيغة غير صحيحة. استخدم: العنوان | المبلغ")
            return
        address, amount_str = [p.strip() for p in update.message.text.split("|", 1)]
        if not amount_str.isdigit():
            await update.message.reply_text("المبلغ يجب أن يكون رقمًا صحيحًا.")
            return
        amount = int(amount_str)
        cur.execute(
            "INSERT INTO withdrawals (user_id, amount, status, note, requested_at, usdt_address) VALUES (?, ?, 'pending', '', ?, ?)",
            (uid, amount, now_ts(), address),
        )
        db.commit()
        await update.message.reply_text("✅ تم إرسال طلب السحب للإدارة.", reply_markup=main_reply_kb())
        return
    if context.user_data.get("awaiting_ad"):
        context.user_data["awaiting_ad"] = False
        if "|" not in update.message.text:
            await update.message.reply_text("صيغة غير صحيحة. استخدم: الرابط | عدد الدقائق")
            return
        url, mins = [p.strip() for p in update.message.text.split("|", 1)]
        if not URL_PATTERN.match(url) or not mins.isdigit():
            await update.message.reply_text("تحقق من الرابط والمدة.")
            return
        minutes = int(mins)
        cur.execute("SELECT minutes FROM users WHERE user_id=?", (uid,))
        row = cur.fetchone()
        user_minutes = row[0] if row else 0
        if user_minutes < minutes:
            await update.message.reply_text("❌ رصيد الدقائق غير كافٍ لشراء الإعلان.")
            return
        cur.execute("UPDATE users SET minutes = minutes - ? WHERE user_id=?", (minutes, uid))
        cur.execute(
            "INSERT INTO paid_ads (owner, url, expires, priority, created_at) VALUES (?, ?, ?, 2, ?)",
            (uid, url, now_ts() + minutes * 60, now_ts()),
        )
        db.commit()
        await update.message.reply_text("✅ تم شراء إعلانك بنجاح وسيظهر بأولوية.", reply_markup=main_reply_kb())
        return
    if context.user_data.get("broadcast"):
        await handle_broadcast(update, context)
        return
    await handle_text_shortcuts(update, context)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    try:
        if hasattr(update, "effective_message") and update.effective_message:
            await update.effective_message.reply_text("⚠️ حدث خطأ غير متوقع، حاول مرة أخرى.")
    except Exception:
        pass


# ================= RUN =================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin_panel))

app.add_handler(CallbackQueryHandler(referral, "^ref$"))
app.add_handler(CallbackQueryHandler(skip_link, "^skip$"))
app.add_handler(CallbackQueryHandler(confirm_click, r"^confirm_\d+$"))
app.add_handler(CallbackQueryHandler(confirm_click, r"^confirm_admin_\d+$"))
app.add_handler(CallbackQueryHandler(confirm_click, r"^confirm_ad_\d+$"))
app.add_handler(CallbackQueryHandler(add_link, "^add$"))
app.add_handler(CallbackQueryHandler(points_panel, "^points$"))
app.add_handler(CallbackQueryHandler(tasks_panel, "^tasks$"))
app.add_handler(CallbackQueryHandler(tasks_claim, "^tasks_claim$"))
app.add_handler(CallbackQueryHandler(withdraw_request, "^withdraw$"))
app.add_handler(CallbackQueryHandler(ad_buy, "^ad_buy$"))
app.add_handler(CallbackQueryHandler(shop_buy, r"^shop_buy_\d+$"))
app.add_handler(CallbackQueryHandler(stats, "^stats$"))
app.add_handler(CallbackQueryHandler(top, "^top$"))
app.add_handler(CallbackQueryHandler(daily, "^daily$"))

app.add_handler(CallbackQueryHandler(admin_stats, "^a_stats$"))
app.add_handler(CallbackQueryHandler(admin_broadcast, "^a_bc$"))
app.add_handler(CallbackQueryHandler(admin_top, "^a_top$"))
app.add_handler(CallbackQueryHandler(admin_user_manage, "^a_user_manage$"))
app.add_handler(CallbackQueryHandler(admin_withdrawals, "^a_withdrawals$"))
app.add_handler(CallbackQueryHandler(admin_settings, "^a_settings$"))
app.add_handler(CallbackQueryHandler(admin_protection, "^a_protection$"))
app.add_handler(CallbackQueryHandler(admin_user_action, r"^u_.*_\d+$"))
app.add_handler(CallbackQueryHandler(admin_withdrawal_action, r"^wd_(ok|no|note)_\d+$"))
app.add_handler(CallbackQueryHandler(admin_setting_action, r"^set_(ref|view|wait|reg|prot)$"))
app.add_handler(CallbackQueryHandler(admin_protection_action, r"^prot_(spam|banned|unban)$"))
app.add_handler(CallbackQueryHandler(admin_broadcast_choice, r"^bc_(t|m)_.+$"))
app.add_handler(CallbackQueryHandler(admin_add_link, "^a_add_link$"))
app.add_handler(CallbackQueryHandler(admin_delete_link, "^a_del_link$"))
app.add_handler(CallbackQueryHandler(admin_list_links, "^a_list_links$"))
app.add_handler(CallbackQueryHandler(admin_delete_link_confirm, r"^admin_del_\d+$"))
app.add_handler(CallbackQueryHandler(admin_back, "^back$"))

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
app.add_error_handler(on_error)

app.run_polling()
