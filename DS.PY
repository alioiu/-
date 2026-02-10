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
db.commit()


# ================= HELPERS =================
def now_ts() -> int:
    return int(time.time())


def main_kb():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔗 تخطي رابط", callback_data="skip")],
            [InlineKeyboardButton("➕ إضافة رابط", callback_data="add")],
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
            [KeyboardButton("📊 إحصائياتي"), KeyboardButton("🏆 الترتيب")],
            [KeyboardButton("🎁 الهدية اليومية"), KeyboardButton("👥 رابط الإحالة")],
        ],
        resize_keyboard=True,
    )


def admin_kb():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 إحصائيات البوت", callback_data="a_stats")],
            [InlineKeyboardButton("📢 رسالة جماعية", callback_data="a_bc")],
            [InlineKeyboardButton("🏆 ترتيب الأعضاء", callback_data="a_top")],
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
            [KeyboardButton("📢 رسالة جماعية")],
            [KeyboardButton("🏆 ترتيب الأعضاء")],
            [KeyboardButton("➕ إضافة رابط ثابت")],
            [KeyboardButton("🗑 حذف رابط ثابت")],
            [KeyboardButton("📄 قائمة الروابط")],
            [KeyboardButton("🔙 رجوع")],
        ],
        resize_keyboard=True,
    )


def ensure_user(uid: int, ref: int | None = None) -> None:
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (uid,))
    exists = cur.fetchone()
    if exists:
        return

    cur.execute(
        "INSERT INTO users (user_id, minutes, invited_by) VALUES (?, ?, ?)",
        (uid, 1 if ref else 0, ref if ref != uid else None),
    )

    if ref and ref != uid:
        cur.execute(
            "UPDATE users SET minutes = minutes + 1, referrals = referrals + 1 WHERE user_id=?",
            (ref,),
        )
    db.commit()


def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID


def get_user(uid: int):
    cur.execute(
        "SELECT minutes, total_clicks, referrals, last_daily FROM users WHERE user_id=?",
        (uid,),
    )
    return cur.fetchone()


def get_next_link(uid: int):
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
        SELECT id, url, owner
        FROM links
        WHERE owner != ?
          AND expires > ?
          AND id NOT IN (SELECT link_id FROM click_history WHERE user_id=?)
        ORDER BY id ASC
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
    ref = int(context.args[0]) if context.args and context.args[0].isdigit() else None
    ensure_user(uid, ref)

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

    if link[0] == "admin":
        _, link_id, url = link
        context.user_data["pending"] = ("admin", link_id)
        confirm_data = f"confirm_admin_{link_id}"
    else:
        _, link_id, url, owner = link
        context.user_data["pending"] = ("user", link_id)
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
    if q.data.startswith("confirm_admin_"):
        link_id = q.data.replace("confirm_admin_", "")
        if not link_id.isdigit():
            await q.edit_message_text("خطأ في الطلب.", reply_markup=main_kb())
            return
        link_id = int(link_id)
        cur.execute("SELECT url FROM admin_links WHERE id=?", (link_id,))
        row = cur.fetchone()
        if not row:
            await q.edit_message_text("هذا الرابط غير متاح.", reply_markup=main_kb())
            return
        cur.execute(
            "INSERT OR IGNORE INTO admin_click_history (user_id, link_id, clicked_at) VALUES (?, ?, ?)",
            (uid, link_id, now_ts()),
        )
        if cur.rowcount == 0:
            await q.edit_message_text("تم احتساب هذا الرابط سابقًا.", reply_markup=main_kb())
            return
        cur.execute(
            "UPDATE users SET minutes = minutes + 1, total_clicks = total_clicks + 1 WHERE user_id=?",
            (uid,),
        )
        db.commit()
        context.user_data.pop("pending", None)
        await q.edit_message_text("✅ تم احتساب دقيقة لك!", reply_markup=main_kb())
        return

    payload = q.data.split("_")
    if len(payload) != 2 or not payload[1].isdigit():
        await q.edit_message_text("خطأ في الطلب.", reply_markup=main_kb())
        return

    link_id = int(payload[1])
    cur.execute(
        "SELECT owner FROM links WHERE id=? AND expires > ?",
        (link_id, now_ts()),
    )
    row = cur.fetchone()
    if not row:
        await q.edit_message_text("هذا الرابط غير متاح.", reply_markup=main_kb())
        return
    owner_id = row[0]

    cur.execute(
        "INSERT OR IGNORE INTO click_history (user_id, link_id, clicked_at) VALUES (?, ?, ?)",
        (uid, link_id, now_ts()),
    )
    if cur.rowcount == 0:
        await q.edit_message_text("تم احتساب هذا الرابط سابقًا.", reply_markup=main_kb())
        return

    cur.execute(
        "UPDATE users SET minutes = minutes + 1, total_clicks = total_clicks + 1 WHERE user_id=?",
        (uid,),
    )
    cur.execute(
        "UPDATE users SET minutes = minutes + 1 WHERE user_id=?",
        (owner_id,),
    )
    db.commit()
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

    minutes, _, _, _ = user
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

    minutes, _, _, _ = user
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

    minutes, clicks, referrals, _ = user
    text = (
        "📊 إحصائياتك:\n"
        f"⏱ الدقائق: {minutes}\n"
        f"✅ النقرات: {clicks}\n"
        f"👥 الإحالات: {referrals}"
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
    cur.execute("SELECT COUNT(*) FROM users")
    users_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM links WHERE expires > ?", (now_ts(),))
    active_links = cur.fetchone()[0]
    text = (
        f"📊 إحصائيات البوت:\n"
        f"👥 المستخدمون: {users_count}\n"
        f"🔗 الروابط الفعالة: {active_links}"
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
    cur.execute(
        "SELECT user_id, minutes FROM users ORDER BY minutes DESC LIMIT 10"
    )
    rows = cur.fetchall()
    if not rows:
        if q:
            await q.answer()
            await q.edit_message_text("لا يوجد ترتيب بعد.", reply_markup=admin_kb())
        else:
            await update.message.reply_text(
                "لا يوجد ترتيب بعد.", reply_markup=admin_reply_kb()
            )
        return

    lines = []
    for idx, (user_id, minutes) in enumerate(rows, start=1):
        lines.append(f"{idx}. {user_id} — {minutes} دقيقة")
    text = "🏆 أفضل 10:\n" + "\n".join(lines)
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
            await q.answer()
            await q.edit_message_text("غير مصرح.", reply_markup=main_kb())
        else:
            await update.message.reply_text("غير مصرح.", reply_markup=main_reply_kb())
        return
    context.user_data["broadcast"] = True
    if q:
        await q.answer()
        await q.edit_message_text("أرسل الرسالة ليتم إرسالها للجميع.")
    else:
        await update.message.reply_text(
            "أرسل الرسالة ليتم إرسالها للجميع.", reply_markup=admin_reply_kb()
        )


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
        await update.message.reply_text(
            "أرسل الرابط المختصر لإضافته بشكل دائم.", reply_markup=admin_reply_kb()
        )


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
    buttons = [
        [InlineKeyboardButton(f"حذف {row[0]}", callback_data=f"admin_del_{row[0]}")]
        for row in rows
    ]
    if q:
        await q.answer()
        await q.edit_message_text("اختر رابطًا للحذف:", reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update.message.reply_text(
            "اختر رابطًا للحذف:", reply_markup=InlineKeyboardMarkup(buttons)
        )


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
    if not rows:
        message = "لا توجد روابط ثابتة."
    else:
        message = "📄 الروابط الثابتة:\n" + "\n".join(
            [f"{row[0]}. {row[1]}" for row in rows]
        )
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
    cur.execute(
        "INSERT INTO admin_links (url, added_at) VALUES (?, ?)",
        (url, now_ts()),
    )
    db.commit()
    context.user_data["awaiting_admin_link"] = False
    await update.message.reply_text("✅ تم إضافة الرابط الثابت.", reply_markup=admin_reply_kb())

async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("broadcast"):
        return

    uid = update.effective_user.id
    if uid != ADMIN_ID:
        context.user_data["broadcast"] = False
        return

    context.user_data["broadcast"] = False
    message = update.message.text
    cur.execute("SELECT user_id FROM users")
    users = [row[0] for row in cur.fetchall()]
    sent = 0
    for user_id in users:
        try:
            await context.bot.send_message(chat_id=user_id, text=message)
            sent += 1
        except Exception:
            continue
    await update.message.reply_text(f"تم إرسال الرسالة إلى {sent} مستخدم.")


# ================= TEXT SHORTCUTS =================
async def handle_text_shortcuts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_link") or context.user_data.get("broadcast"):
        return

    text = update.message.text.strip()
    if text == "🔗 تخطي رابط":
        await skip_link(update, context)
    elif text == "➕ إضافة رابط":
        await add_link(update, context)
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
    elif text == "📢 رسالة جماعية":
        await admin_broadcast(update, context)
    elif text == "🏆 ترتيب الأعضاء":
        await admin_top(update, context)
    elif text == "➕ إضافة رابط ثابت":
        await admin_add_link(update, context)
    elif text == "🗑 حذف رابط ثابت":
        await admin_delete_link(update, context)
    elif text == "📄 قائمة الروابط":
        await admin_list_links(update, context)
    elif text == "🔙 رجوع":
        await update.message.reply_text("تم الرجوع.", reply_markup=main_reply_kb())


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_link"):
        await handle_link_message(update, context)
        return
    if context.user_data.get("awaiting_admin_link"):
        await handle_admin_link_message(update, context)
        return
    if context.user_data.get("broadcast"):
        await handle_broadcast(update, context)
        return
    await handle_text_shortcuts(update, context)


# ================= RUN =================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin_panel))

app.add_handler(CallbackQueryHandler(referral, "^ref$"))
app.add_handler(CallbackQueryHandler(skip_link, "^skip$"))
app.add_handler(CallbackQueryHandler(confirm_click, r"^confirm_\d+$"))
app.add_handler(CallbackQueryHandler(confirm_click, r"^confirm_admin_\d+$"))
app.add_handler(CallbackQueryHandler(add_link, "^add$"))
app.add_handler(CallbackQueryHandler(stats, "^stats$"))
app.add_handler(CallbackQueryHandler(top, "^top$"))
app.add_handler(CallbackQueryHandler(daily, "^daily$"))

app.add_handler(CallbackQueryHandler(admin_stats, "^a_stats$"))
app.add_handler(CallbackQueryHandler(admin_broadcast, "^a_bc$"))
app.add_handler(CallbackQueryHandler(admin_top, "^a_top$"))
app.add_handler(CallbackQueryHandler(admin_add_link, "^a_add_link$"))
app.add_handler(CallbackQueryHandler(admin_delete_link, "^a_del_link$"))
app.add_handler(CallbackQueryHandler(admin_list_links, "^a_list_links$"))
app.add_handler(CallbackQueryHandler(admin_delete_link_confirm, r"^admin_del_\d+$"))
app.add_handler(CallbackQueryHandler(admin_back, "^back$"))

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

app.run_polling()
