import os
import sqlite3
import logging
from datetime import datetime
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv(
    "BOT_TOKEN", "7637311997:AAGpz4BB_W_CuNVKn1xH2DpMFqYO6QkVQcU"
)
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "6367495275").split(",")]
FORCE_SUB_CHANNELS = os.getenv(
    "FORCE_CHANNELS", "@Budget_Deals_Bazaar,@EarnMoneyTips_Official"
).split(",")

REFERRAL_REWARD = float(os.getenv("REFERRAL_REWARD", "100"))
MIN_WITHDRAW = float(os.getenv("MIN_WITHDRAW", "10"))

user_states = {}  # WAITING_AMOUNT / CONFIRM_X


# =====================================================
# DATABASE
# =====================================================
def init_db():
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            referrer_id INTEGER,
            balance REAL DEFAULT 0,
            total_referrals INTEGER DEFAULT 0,
            joined_date TEXT,
            upi_id TEXT,
            is_banned INTEGER DEFAULT 0
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER,
            referred_id INTEGER,
            reward_amount REAL,
            date TEXT
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            payment_method TEXT,
            payment_details TEXT,
            status TEXT DEFAULT 'pending',
            request_date TEXT,
            process_date TEXT
        )
        """
    )

    conn.commit()
    conn.close()


def get_user(uid):
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    r = c.fetchone()
    conn.close()
    return r


def add_user(uid, username, first_name, referrer=None):
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    joined = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    c.execute(
        """
        INSERT OR IGNORE INTO users (user_id, username, first_name, referrer_id, joined_date)
        VALUES (?, ?, ?, ?, ?)
        """,
        (uid, username, first_name, referrer, joined),
    )
    conn.commit()
    conn.close()


def update_balance(uid, amt):
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()

    c.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amt, uid))
    conn.commit()
    conn.close()


def set_upi(uid, upi):
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    c.execute("UPDATE users SET upi_id=? WHERE user_id=?", (upi, uid))
    conn.commit()
    conn.close()


def create_withdraw_request(uid, amt, method, details):
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    c.execute(
        """
        INSERT INTO withdrawals (user_id, amount, payment_method, payment_details, request_date)
        VALUES (?, ?, ?, ?, ?)
        """,
        (uid, amt, method, details, now),
    )

    conn.commit()
    conn.close()


def get_pending_withdrawals():
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    c.execute("SELECT * FROM withdrawals WHERE status='pending'")
    rows = c.fetchall()
    conn.close()
    return rows


def update_withdraw_status(wid, status):
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute(
        "UPDATE withdrawals SET status=?, process_date=? WHERE id=?",
        (status, now, wid),
    )
    conn.commit()
    conn.close()


# =====================================================
# SUBSCRIPTION CHECK
# =====================================================
async def check_subscription(uid, context):
    for ch in FORCE_SUB_CHANNELS:
        try:
            m = await context.bot.get_chat_member(ch, uid)
            if m.status not in ["member", "creator", "administrator"]:
                return False, ch
        except:
            return False, ch
    return True, None

async def force_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    uid = q.from_user.id
    ok, ch = await check_subscription(uid, context)

    if ok:
        await q.message.edit_text("Subscription verified. Use /start to continue.")
    else:
        await q.answer(f"Please join {ch}", show_alert=True)

def sub_keyboard():
    kb = []
    for ch in FORCE_SUB_CHANNELS:
        kb.append(
            [
                InlineKeyboardButton(
                    f"Join {ch}", url=f"https://t.me/{ch.replace('@', '')}"
                )
            ]
        )
    kb.append([InlineKeyboardButton("I Joined", callback_data="check_subscription")])
    return InlineKeyboardMarkup(kb)


menu = ReplyKeyboardMarkup(
    [["Withdraw"], ["Refer", "Stats"], ["Balance"]], resize_keyboard=True
)


# =====================================================
# COMMANDS
# =====================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id

    ref = None
    if context.args:
        try:
            r = int(context.args[0])
            if r != uid:
                ref = r
        except:
            pass

    u = get_user(uid)

    if not u:
        ok, ch = await check_subscription(uid, context)
        if not ok:
            await update.message.reply_text(
                "Join channels to continue.", reply_markup=sub_keyboard()
            )
            return

        add_user(uid, user.username or "", user.first_name, ref)

        if ref and get_user(ref):
            update_balance(ref, REFERRAL_REWARD)

        await update.message.reply_text("Registered.", reply_markup=menu)
        return

    await update.message.reply_text("Welcome back.", reply_markup=menu)


async def setup_upi(update, context):
    uid = update.effective_user.id

    if len(context.args) < 1:
        await update.message.reply_text("Use: /setup_upi yourupi@bank")
        return

    set_upi(uid, context.args[0])
    await update.message.reply_text("UPI updated.", reply_markup=menu)


async def balance(update, context):
    u = get_user(update.effective_user.id)
    await update.message.reply_text(f"Balance: {u[4]}", reply_markup=menu)


async def stats(update, context):
    u = get_user(update.effective_user.id)
    await update.message.reply_text(
        f"User: {u[1]}\nBalance: {u[4]}\nReferrals: {u[5]}\nUPI: {u[7]}",
        reply_markup=menu,
    )


async def refer(update, context):
    uid = update.effective_user.id
    link = f"https://t.me/{context.bot.username}?start={uid}"
    u = get_user(uid)

    await update.message.reply_text(
        f"Referral Link:\n{link}\nReferrals: {u[5]}\nBalance: {u[4]}",
        reply_markup=menu,
    )


# =====================================================
# ADMIN COMMANDS
# =====================================================
async def admin_panel(update, context):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await update.message.reply_text("Access Denied.")
        return

    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM users")
    users = c.fetchone()[0]

    c.execute("SELECT SUM(balance) FROM users")
    total_bal = c.fetchone()[0] or 0

    c.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'")
    pending = c.fetchone()[0]

    conn.close()

    await update.message.reply_text(
        f"Users: {users}\nPending: {pending}\nTotal Balance: {total_bal}"
    )

async def pending_w(update, context):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await update.message.reply_text("Access Denied.")
        return

    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()

    # JOIN withdrawals + users to fetch UPI
    c.execute("""
        SELECT w.id, w.user_id, w.amount, u.upi_id
        FROM withdrawals w
        JOIN users u ON w.user_id = u.user_id
        WHERE w.status='pending'
    """)

    rows = c.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("No pending withdrawals.")
        return

    msg = ""
    for wid, uid2, amt, upi in rows:
        msg += f"ID: {wid} | UID: {uid2} | Amount: {amt} | UPI: {upi}\n"

    await update.message.reply_text(msg)


async def approve(update, context):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await update.message.reply_text("Access Denied.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /approve <id>")
        return

    wid = int(context.args[0])

    # Fetch withdrawal info (user_id + amount)
    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    c.execute("SELECT user_id, amount FROM withdrawals WHERE id=?", (wid,))
    row = c.fetchone()
    conn.close()

    if not row:
        await update.message.reply_text("Invalid withdrawal ID.")
        return

    user_id, amount = row

    # Update withdrawal status
    update_withdraw_status(wid, "approved")

    # Notify admin
    await update.message.reply_text(f"Approved withdrawal #{wid} for user {user_id}.")

    # Notify user
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"Your withdrawal of {amount} has been approved and is being processed."
        )
    except:
        pass  # user might have blocked the bot or privacy settings

async def reject(update, context):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await update.message.reply_text("Access Denied.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /reject <id>")
        return

    wid = int(context.args[0])

    conn = sqlite3.connect("bot_database.db")
    c = conn.cursor()
    c.execute("SELECT user_id, amount FROM withdrawals WHERE id=?", (wid,))
    row = c.fetchone()

    if not row:
        await update.message.reply_text("Request not found.")
        return

    uid2, amt = row
    update_balance(uid2, amt)
    update_withdraw_status(wid, "rejected")

    await update.message.reply_text("Rejected and refunded.")


# =====================================================
# WITHDRAW SYSTEM
# =====================================================
async def withdraw(update, context):
    uid = update.effective_user.id
    u = get_user(uid)

    upi = u[7]

    if not upi:
        await update.message.reply_text(
            "Set UPI first using /setup_upi yourupi@bank", reply_markup=menu
        )
        return

    user_states[uid] = "WAITING_AMOUNT"

    await update.message.reply_text(
        f"Your UPI: {upi}\nBalance: {u[4]}\nMinimum: {MIN_WITHDRAW}\n\nEnter amount:"
    )


async def amount_handler(update, context):
    uid = update.effective_user.id
    msg = update.message.text.strip()

    if user_states.get(uid) != "WAITING_AMOUNT":
        return

    if not msg.replace(".", "", 1).isdigit():
        return

    amt = float(msg)
    u = get_user(uid)

    if amt < MIN_WITHDRAW or amt > u[4]:
        return

    user_states[uid] = f"CONFIRM_{amt}"

    await update.message.reply_text(
        f"Confirm withdrawal of {amt} to UPI {u[7]}?\nType YES or NO."
    )


async def confirm_handler(update, context):
    uid = update.effective_user.id
    msg = update.message.text.strip().upper()

    if uid not in user_states:
        return

    state = user_states[uid]

    if not state.startswith("CONFIRM_"):
        return

    if msg not in ["YES", "NO"]:
        return

    amt = float(state.split("_")[1])

    if msg == "NO":
        del user_states[uid]
        await update.message.reply_text("Cancelled.", reply_markup=menu)
        return

    u = get_user(uid)

    update_balance(uid, -amt)
    create_withdraw_request(uid, amt, "UPI", u[7])

    del user_states[uid]
    await update.message.reply_text(
        "Withdrawal submitted. You will receive payment within 24 hours.",
        reply_markup=menu,
    )


# =====================================================
# KEYBOARD
# =====================================================
async def keyboard_handler(update, context):
    t = update.message.text.strip()

    if t not in ["Withdraw", "Refer", "Stats", "Balance"]:
        return

    if t == "Withdraw":
        await withdraw(update, context)
    elif t == "Refer":
        await refer(update, context)
    elif t == "Stats":
        await stats(update, context)
    elif t == "Balance":
        await balance(update, context)


async def callback(update, context):
    q = update.callback_query
    await q.answer()

    uid = q.from_user.id
    ok, ch = await check_subscription(uid, context)

    if ok:
        await q.message.reply_text("Use /start", reply_markup=menu)
    else:
        await q.answer("Join required channel.", show_alert=True)


# =====================================================
# MAIN
# =====================================================
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # User commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(force_join_callback, pattern="check_subscription"))
    app.add_handler(CommandHandler("setup_upi", setup_upi))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("refer", refer))

    # Admin commands
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("pending", pending_w))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("reject", reject))

    # YES/NO only
    app.add_handler(MessageHandler(
        filters.Regex(r"^(YES|NO|yes|no)$"),
        confirm_handler
    ))

    # numeric amounts only
    app.add_handler(MessageHandler(
        filters.Regex(r"^[0-9]+(\.[0-9]+)?$"),
        amount_handler
    ))

    # keyboard buttons only
    app.add_handler(MessageHandler(
        filters.Regex(r"^(Withdraw|Refer|Stats|Balance)$"),
        keyboard_handler
    ))



    logger.info("BOT STARTED")
    app.run_polling()


if __name__ == "__main__":
    main()
    
