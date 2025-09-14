#!/usr/bin/env python3
"""
Complete Gift Card Marketplace Bot - ready to launch

Features:
- Main menu layout: Deposit (single row), Listing (single row), Referral, Profile,
  Stock Updates + Support (side-by-side).
- Admin Panel button visible to admin in the main menu.
- Auto-referral $2 when new user starts with /start <inviter_id>.
- Deposit flow: choose coin (BTC/LTC/SOL) -> I Paid -> TXID AMOUNT -> stored -> admin Approve/Reject -> balance updated.
- Giftcards: admin guided upload or /upload command, BIN extraction, stored in DB (code hidden).
- Auto-post to stock channel with BIN and basic info (no code).
- Listing: show all available cards (with BIN), filter by brand, search by BIN (button + /bin), refresh buttons.
- Buy: deducts user balance, marks card sold, delivers full code privately.
- Admin Panel (inline): View pending deposits, Upload (wizard), Manage Stock (delete/mark sold/view), Users (check/adjust balance).
"""

import os
import sqlite3
import logging
from datetime import datetime
from dotenv import load_dotenv

try:
    import telebot
    from telebot import types
except Exception:
    raise SystemExit("Missing dependency 'pyTelegramBotAPI'. Install: pip install pyTelegramBotAPI")

# ---- Load config ----
load_dotenv("yvl.env")
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN missing in yvl.env")
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
except ValueError:
    ADMIN_ID = 0

POST_CHANNEL = None
if os.getenv("POST_CHANNEL"):
    try:
        POST_CHANNEL = int(os.getenv("POST_CHANNEL"))
    except ValueError:
        POST_CHANNEL = None

UPDATES_CHANNEL = os.getenv("UPDATES_CHANNEL")  # optional
SUPPORT_CHAT = os.getenv("SUPPORT_CHAT")        # optional

# Coin addresses (display only; set in yvl.env if you want)
COIN_ADDRESSES = {
    "BTC": os.getenv("BTC_ADDRESS"),
    "LTC": os.getenv("LTC_ADDRESS"),
    "SOL": os.getenv("SOL_ADDRESS")
}

# ---- Bot & logging ----
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ---- DB init ----
DB_PATH = os.getenv("DB_PATH", "shop.db")
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    balance REAL DEFAULT 0,
    referred_by INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS deposits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    coin TEXT,
    amount REAL DEFAULT 0,
    txid TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS giftcards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand TEXT,
    value REAL,
    price REAL,
    code TEXT,
    bin TEXT,
    status TEXT DEFAULT 'available',
    created_at TEXT
)
""")
conn.commit()

# ---- Helpers ----
def ensure_user_exists(user_id):
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()

def fetch_user_balance(user_id):
    cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    r = cursor.fetchone()
    return float(r[0]) if r and r[0] is not None else 0.0

def add_balance(user_id, amount):
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    conn.commit()

def credit_referral_and_create_user(new_user_id, inviter_id):
    cursor.execute("SELECT user_id FROM users WHERE user_id=?", (new_user_id,))
    if cursor.fetchone():
        return
    referred_by = None
    if inviter_id and inviter_id != new_user_id:
        cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (inviter_id,))
        cursor.execute("UPDATE users SET balance = balance + 2 WHERE user_id=?", (inviter_id,))
        conn.commit()
        try:
            bot.send_message(inviter_id, f"🎉 You earned $2 for referring <b>{new_user_id}</b>!")
        except Exception:
            logging.exception("Could not notify inviter")
        referred_by = inviter_id
    cursor.execute("INSERT INTO users (user_id, referred_by) VALUES (?, ?)", (new_user_id, referred_by))
    conn.commit()

def create_deposit_request(user_id, coin):
    created_at = datetime.utcnow().isoformat()
    cursor.execute("INSERT INTO deposits (user_id, coin, status, created_at) VALUES (?, ?, 'pending', ?)",
                   (user_id, coin, created_at))
    conn.commit()
    return cursor.lastrowid

def update_deposit_txid_amount(deposit_id, txid, amount):
    cursor.execute("UPDATE deposits SET txid=?, amount=? WHERE id=?", (txid, amount, deposit_id))
    conn.commit()

def fetch_deposit(deposit_id):
    cursor.execute("SELECT id, user_id, coin, amount, txid, status FROM deposits WHERE id=?", (deposit_id,))
    return cursor.fetchone()
# ---- Start handler (auto-referral + menu) ----
@bot.message_handler(commands=["start"])
def cmd_start(message):
    user_id = message.from_user.id
    args = message.text.strip().split()
    inviter_id = None
    if len(args) > 1 and args[1].isdigit():
        inviter_id = int(args[1])

    # Check if user exists, else create + handle referral
    cursor.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    if not cursor.fetchone():
        credit_referral_and_create_user(user_id, inviter_id)
    else:
        ensure_user_exists(user_id)

    # ✅ Always show main menu with balance and buttons
    send_main_menu(message)


      # main menu layout (final):
    # Deposit (one row)
    # Listing (one row)
    # Referral + Stock Updates + Support (one row)
    # Profile + My Orders (one row)
    kb = types.InlineKeyboardMarkup(row_width=3)

    # First row: Deposit
    kb.add(types.InlineKeyboardButton("💰 Deposit", callback_data="menu_deposit"))

    # Second row: Listing
    kb.add(types.InlineKeyboardButton("🛒 Listing", callback_data="listing"))

    # Third row: Referral + Stock Updates + Support
    row = [
        types.InlineKeyboardButton("🎉 Referral Program", callback_data="referral")
    ]
    if UPDATES_CHANNEL:
        row.append(types.InlineKeyboardButton("📢 Stock Updates", url=UPDATES_CHANNEL))
    else:
        row.append(types.InlineKeyboardButton("📢 Stock Updates", callback_data="no_updates"))

    if SUPPORT_CHAT:
        row.append(types.InlineKeyboardButton("🆘 Support", url=SUPPORT_CHAT))
    else:
        row.append(types.InlineKeyboardButton("🆘 Support", callback_data="no_support"))

    kb.add(*row)

    # Fourth row: Profile + My Orders
    kb.add(
        types.InlineKeyboardButton("👤 Profile", callback_data="profile"),
        types.InlineKeyboardButton("📦 My Orders", callback_data="my_orders")
    )

    # Admin Panel (admin only)
    if message.from_user.id == ADMIN_ID:
        kb.add(types.InlineKeyboardButton("👮 Admin Panel", callback_data="admin_panel"))

    bot.send_message(user_id,
                     f"⚡ Welcome {message.from_user.first_name} to <b>YVL WORLD</b>! ⚡\n\n"
                     "One Stop Shop For All Prepaids\n\n"
                     "🌟 Earn $2 for each friend you refer!\n"
                     "Use /ref to get your referral link",
                     reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data in ["no_updates", "no_support"])
def cb_no_link(call):
    bot.answer_callback_query(call.id, "This link wasn't configured by admin.", show_alert=True)

# ---- /ref command ----
@bot.message_handler(commands=["ref"])
def cmd_ref(message):
    uid = message.from_user.id
    try:
        username = bot.get_me().username
    except Exception:
        username = "<bot>"
    bot.send_message(uid, f"👥 Invite friends and earn $2 each!\n\nYour link:\nhttps://t.me/{username}?start={uid}")

# ---- Profile ----
@bot.callback_query_handler(func=lambda c: c.data == "profile")
def cb_profile(call):
    bot.answer_callback_query(call.id)
    cursor.execute("SELECT balance FROM users WHERE user_id=?", (call.from_user.id,))
    r = cursor.fetchone()
    balance = float(r[0]) if r and r[0] is not None else 0.0

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="back_to_menu"))

    bot.send_message(
        call.from_user.id,
        f"👤 <b>Your Profile</b>\n\n💵 Balance: ${balance:.2f}",
        parse_mode="HTML",
        reply_markup=kb
    )

# ---- Referral ----
@bot.callback_query_handler(func=lambda c: c.data == "referral")
def cb_referral(call):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    ref_link = f"https://t.me/{BOT_USERNAME}?start={uid}"

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="back_to_menu"))

    bot.send_message(
        call.from_user.id,
        f"🎉 <b>Referral Program</b>\n\nInvite friends with your link:\n{ref_link}\n\n"
        "Earn $2.00 when they join!",
        parse_mode="HTML",
        reply_markup=kb
    )


# ---- My Orders ----
@bot.callback_query_handler(func=lambda c: c.data == "my_orders")
def cb_my_orders(call):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id

    cursor.execute(
        "SELECT id, brand, value, price, created_at FROM giftcards "
        "WHERE buyer_id=? ORDER BY created_at DESC LIMIT 10",
        (uid,)
    )
    rows = cursor.fetchall()

    if not rows:
        bot.send_message(uid, "📭 You have no past orders.")
        return

    text_lines = ["📦 <b>Your Last 10 Orders</b>\n"]
    for order_id, brand, value, price, created_at in rows:
        text_lines.append(f"#{order_id} | {brand} | Value: ${value} | Price: ${price} | {created_at}")

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="back_to_menu"))

    bot.send_message(uid, "\n".join(text_lines), parse_mode="HTML", reply_markup=kb)

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="back_to_menu"))

    bot.send_message(uid, msg, parse_mode="HTML", reply_markup=kb)
        f"🎉 <b>Referral Program</b>\n\n"
        f"Invite friends and earn <b>$2</b> each!\n\n"
        f"🔗 Your referral link:\n{ref_link}",
        parse_mode="HTML",
        reply_markup=kb
    )
# ---- Deposit flow (BTC, LTC, SOL) ----
@bot.callback_query_handler(func=lambda c: c.data == "menu_deposit")
def cb_menu_deposit(call):
    bot.answer_callback_query(call.id)
    kb = types.InlineKeyboardMarkup(row_width=2)
    # two buttons per row is fine, but your main menu requested Deposit on its own row.
    # This deposit menu will show coins 2-per-row as requested earlier.
    for coin in ["BTC", "LTC", "SOL"]:
        kb.add(types.InlineKeyboardButton(coin, callback_data=f"deposit|{coin}"))
    
    # 🔙 Back button to return to main menu
    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="back_to_menu"))

    bot.send_message(call.from_user.id, "💰 Choose a coin to deposit:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("deposit|"))
def cb_deposit_coin(call):
    bot.answer_callback_query(call.id)
    coin = call.data.split("|", 1)[1]
    user_id = call.from_user.id
    addr = COIN_ADDRESSES.get(coin) or os.getenv(f"{coin}_ADDRESS") or "Address not configured"
    deposit_id = create_deposit_request(user_id, coin)
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("✅ I Paid", callback_data=f"paid|{deposit_id}|{coin}"),
           types.InlineKeyboardButton("✖ Cancel", callback_data="deposit_cancel"))
    bot.send_message(user_id,
                     f"Send {coin} to:\n<code>{addr}</code>\n\nAfter sending, press <b>I Paid</b> and then send TXID & amount in chat.",
                     reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "deposit_cancel")
def cb_deposit_cancel(call):
    bot.answer_callback_query(call.id)
    bot.send_message(call.from_user.id, "❌ Deposit cancelled.")

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("paid|"))
def cb_paid(call):
    bot.answer_callback_query(call.id)
    parts = call.data.split("|")
    if len(parts) < 3:
        bot.send_message(call.from_user.id, "⚠️ Invalid request.")
        return
    deposit_id = int(parts[1])
    coin = parts[2]
    msg = bot.send_message(call.from_user.id, f"✅ Please send your TXID & amount for {coin} deposit (format: TXID AMOUNT):")
    bot.register_next_step_handler(msg, handle_deposit_txid_amount, deposit_id, coin)

def handle_deposit_txid_amount(message, deposit_id, coin):
    user_id = message.from_user.id
    text = (message.text or "").strip().split()
    if len(text) < 2:
        m = bot.send_message(user_id, "⚠️ Please send in format: `TXID AMOUNT` (e.g. TX123abc 0.5). Try again.")
        bot.register_next_step_handler(m, handle_deposit_txid_amount, deposit_id, coin)
        return
    txid = text[0]
    amount_raw = text[1]
    try:
        amount = float(amount_raw)
    except ValueError:
        m = bot.send_message(user_id, "⚠️ Invalid amount. Please send numeric amount. Try again.")
        bot.register_next_step_handler(m, handle_deposit_txid_amount, deposit_id, coin)
        return

    update_deposit_txid_amount(deposit_id, txid, amount)

    # notify admin with Approve/Reject
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("✅ Approve", callback_data=f"approve_deposit|{deposit_id}"),
           types.InlineKeyboardButton("❌ Reject", callback_data=f"reject_deposit|{deposit_id}"))

    dep = fetch_deposit(deposit_id)
    if dep:
        _, dep_user_id, dep_coin, dep_amount, dep_txid, dep_status = dep
        admin_text = (
            f"💵 <b>Deposit Request</b>\n\n"
            f"👤 User: <a href='tg://user?id={dep_user_id}'>User</a>\n"
            f"🆔 ID: <code>{dep_user_id}</code>\n"
            f"Coin: <b>{dep_coin}</b>\n"
            f"Amount: <b>{dep_amount}</b>\n"
            f"TXID: <code>{dep_txid}</code>\n\nChoose an action:"
        )
        if ADMIN_ID:
            try:
                bot.send_message(ADMIN_ID, admin_text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                logging.exception("Failed to notify admin about deposit")

    bot.send_message(user_id, "✅ Deposit submitted. Staff will review and approve or reject it shortly.")

# Admin approve / reject handlers
@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("approve_deposit|"))
def cb_approve_deposit(call):
    bot.answer_callback_query(call.id)
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "You are not authorized.", show_alert=True)
        return
    _, deposit_id_str = call.data.split("|", 1)
    try:
        deposit_id = int(deposit_id_str)
    except ValueError:
        bot.send_message(call.message.chat.id, "⚠️ Bad deposit id.")
        return
    dep = fetch_deposit(deposit_id)
    if not dep:
        bot.send_message(call.message.chat.id, "⚠️ Deposit not found.")
        return
    _, user_id, coin, amount, txid, status = dep
    if status != "pending":
        bot.send_message(call.message.chat.id, "⚠️ Deposit already handled.")
        return
    cursor.execute("UPDATE deposits SET status='approved' WHERE id=?", (deposit_id,))
    add_balance(user_id, float(amount))
    conn.commit()
    try:
        bot.send_message(user_id, f"✅ Your deposit of {amount} {coin} has been approved! 🎉")
    except Exception:
        logging.exception("Failed to message user on approve")
    bot.send_message(call.message.chat.id, f"👍 Deposit #{deposit_id} approved.")

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("reject_deposit|"))
def cb_reject_deposit(call):
    bot.answer_callback_query(call.id)
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "You are not authorized.", show_alert=True)
        return
    _, deposit_id_str = call.data.split("|", 1)
    try:
        deposit_id = int(deposit_id_str)
    except ValueError:
        bot.send_message(call.message.chat.id, "⚠️ Bad deposit id.")
        return
    dep = fetch_deposit(deposit_id)
    if not dep:
        bot.send_message(call.message.chat.id, "⚠️ Deposit not found.")
        return
    _, user_id, coin, amount, txid, status = dep
    if status != "pending":
        bot.send_message(call.message.chat.id, "⚠️ Deposit already handled.")
        return
    cursor.execute("UPDATE deposits SET status='rejected' WHERE id=?", (deposit_id,))
    conn.commit()
    try:
        bot.send_message(user_id, f"❌ Your deposit of {amount} {coin} was rejected. Please contact support.")
    except Exception:
        logging.exception("Failed to message user on reject")
    bot.send_message(call.message.chat.id, f"⚠️ Deposit #{deposit_id} rejected.")

# ---- Admin Panel (always available button for ADMIN_ID) ----
@bot.callback_query_handler(func=lambda c: c.data == "admin_panel")
def cb_admin_panel(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "You are not authorized.", show_alert=True)
        return
    bot.answer_callback_query(call.id)
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("📥 View Deposits", callback_data="admin_deposits"),
           types.InlineKeyboardButton("➕ Upload Card", callback_data="admin_upload"))
    kb.add(types.InlineKeyboardButton("🗂 Manage Stock", callback_data="admin_stock"),
           types.InlineKeyboardButton("👥 Users", callback_data="admin_users"))
    bot.send_message(ADMIN_ID, "👮 Admin Panel:", reply_markup=kb)

# View pending deposits
@bot.callback_query_handler(func=lambda c: c.data == "admin_deposits")
def cb_admin_deposits(call):
    if call.from_user.id != ADMIN_ID:
        return
    bot.answer_callback_query(call.id)
    cursor.execute("SELECT id, user_id, coin, amount, txid, created_at FROM deposits WHERE status='pending' ORDER BY created_at DESC")
    rows = cursor.fetchall()
    if not rows:
        bot.send_message(ADMIN_ID, "No pending deposits.")
        return
    for dep in rows:
        dep_id, uid, coin, amount, txid, created_at = dep
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton("✅ Approve", callback_data=f"approve_deposit|{dep_id}"),
               types.InlineKeyboardButton("❌ Reject", callback_data=f"reject_deposit|{dep_id}"))
        bot.send_message(ADMIN_ID, f"#{dep_id} | user:{uid} | {coin} {amount}\nTXID: {txid}\nCreated: {created_at}", reply_markup=kb)

# Admin upload wizard
@bot.callback_query_handler(func=lambda c: c.data == "admin_upload")
def cb_admin_upload(call):
    if call.from_user.id != ADMIN_ID:
        return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(ADMIN_ID, "✍️ Enter card details on one line:\nFormat: Brand,Value,Price,Code")
    bot.register_next_step_handler(msg, admin_process_upload)

def admin_process_upload(message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        brand, value_str, price_str, code = [s.strip() for s in message.text.split(",", 3)]
        value = float(value_str); price = float(price_str)
    except Exception:
        bot.send_message(ADMIN_ID, "⚠️ Bad format. Use: Brand,Value,Price,Code")
        return
    bin_val = code[:6] if len(code) >= 6 else None
    created_at = datetime.utcnow().isoformat()
    cursor.execute("INSERT INTO giftcards (brand, value, price, code, bin, status, created_at) VALUES (?, ?, ?, ?, ?, 'available', ?)",
                   (brand, value, price, code, bin_val, created_at))
    conn.commit()
    card_id = cursor.lastrowid
    msg = (f"🛒 <b>New Card Added!</b>\n\n🏷 Brand: {brand}\n💳 Value: ${value}\n💵 Price: ${price}\n🔎 BIN: {bin_val or 'N/A'}\n🆔 ID: {card_id}")
    try:
        if POST_CHANNEL:
            bot.send_message(POST_CHANNEL, msg, parse_mode="HTML")
    except Exception:
        logging.exception("Failed to post to stock channel")
    bot.send_message(ADMIN_ID, f"✅ Uploaded card ID {card_id}")

# /upload fallback
@bot.message_handler(commands=["upload"])
def cmd_upload(message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        _, brand, value_str, price_str, code = message.text.split(maxsplit=4)
        value = float(value_str); price = float(price_str)
    except Exception:
        bot.send_message(ADMIN_ID, "⚠️ Usage: /upload <brand> <value> <price> <code>")
        return
    bin_val = code[:6] if len(code) >= 6 else None
    created_at = datetime.utcnow().isoformat()
    cursor.execute("INSERT INTO giftcards (brand, value, price, code, bin, status, created_at) VALUES (?, ?, ?, ?, ?, 'available', ?)",
                   (brand, value, price, code, bin_val, created_at))
    conn.commit()
    card_id = cursor.lastrowid
    msg = (f"🛒 <b>New Card Added!</b>\n\n🏷 Brand: {brand}\n💳 Value: ${value}\n💵 Price: ${price}\n🔎 BIN: {bin_val or 'N/A'}\n🆔 ID: {card_id}")
    if POST_CHANNEL:
        try:
            bot.send_message(POST_CHANNEL, msg, parse_mode="HTML")
        except Exception:
            logging.exception("Failed to post to stock channel")
    bot.send_message(ADMIN_ID, f"✅ Uploaded card ID {card_id}")

# Manage stock
@bot.callback_query_handler(func=lambda c: c.data == "admin_stock")
def cb_admin_stock(call):
    if call.from_user.id != ADMIN_ID:
        return
    bot.answer_callback_query(call.id)
    cursor.execute("SELECT id, brand, value, price, bin, status FROM giftcards ORDER BY id DESC LIMIT 40")
    rows = cursor.fetchall()
    if not rows:
        bot.send_message(ADMIN_ID, "No cards in stock.")
        return
    for card_id, brand, value, price, bin_val, status in rows:
        kb = types.InlineKeyboardMarkup(row_width=3)
        kb.add(types.InlineKeyboardButton("❌ Delete", callback_data=f"delete_card|{card_id}"),
               types.InlineKeyboardButton("Mark Sold", callback_data=f"mark_sold|{card_id}"),
               types.InlineKeyboardButton("View", callback_data=f"view_card|{card_id}"))
        bot.send_message(ADMIN_ID, f"ID:{card_id} | {brand} | ${value} | ${price} | BIN:{bin_val or 'N/A'} | {status}", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("delete_card|"))
def cb_delete_card(call):
    if call.from_user.id != ADMIN_ID:
        return
    card_id = int(call.data.split("|",1)[1])
    cursor.execute("DELETE FROM giftcards WHERE id=?", (card_id,))
    conn.commit()
    bot.answer_callback_query(call.id, f"Deleted card {card_id}")
    bot.send_message(ADMIN_ID, f"🗑 Card {card_id} deleted.")

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("mark_sold|"))
def cb_mark_sold(call):
    if call.from_user.id != ADMIN_ID:
        return
    card_id = int(call.data.split("|",1)[1])
    cursor.execute("UPDATE giftcards SET status='sold' WHERE id=?", (card_id,))
    conn.commit()
    bot.answer_callback_query(call.id, f"Marked {card_id} sold")
    bot.send_message(ADMIN_ID, f"✅ Card {card_id} marked sold.")

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("view_card|"))
def cb_view_card(call):
    if call.from_user.id != ADMIN_ID:
        return
    card_id = int(call.data.split("|",1)[1])
    cursor.execute("SELECT id, brand, value, price, code, bin, status, created_at FROM giftcards WHERE id=?", (card_id,))
    r = cursor.fetchone()
    if not r:
        bot.send_message(ADMIN_ID, "Card not found.")
        return
    cid, brand, value, price, code, bin_val, status, created_at = r
    bot.send_message(ADMIN_ID, f"ID:{cid}\nBrand:{brand}\nValue:{value}\nPrice:{price}\nBIN:{bin_val}\nStatus:{status}\nCode:{code}\nCreated:{created_at}")

# Admin users
@bot.callback_query_handler(func=lambda c: c.data == "admin_users")
def cb_admin_users(call):
    if call.from_user.id != ADMIN_ID:
        return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(ADMIN_ID, "Enter user ID to check:")
    bot.register_next_step_handler(msg, admin_process_check_user)

def admin_process_check_user(message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        uid = int(message.text.strip())
    except Exception:
        bot.send_message(ADMIN_ID, "Invalid user id.")
        return
    cursor.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
    row = cursor.fetchone()
    if not row:
        bot.send_message(ADMIN_ID, "User not found.")
        return
    bal = row[0]
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("➕ Add $10", callback_data=f"addbal|{uid}|10"),
           types.InlineKeyboardButton("➖ Remove $10", callback_data=f"addbal|{uid}|-10"))
    bot.send_message(ADMIN_ID, f"User {uid} balance: ${bal:.2f}", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("addbal|"))
def cb_addbal(call):
    if call.from_user.id != ADMIN_ID:
        return
    _, uid_str, amt_str = call.data.split("|",2)
    try:
        uid = int(uid_str); amt = float(amt_str)
    except:
        bot.answer_callback_query(call.id, "Bad data")
        return
    add_balance(uid, amt)
    bot.answer_callback_query(call.id, f"Adjusted {uid} by ${amt:+}")
    bot.send_message(ADMIN_ID, f"✅ Updated balance for {uid} by ${amt:+}")
# ---- Listings: All / Brand / BIN / Refresh / Buy ----
@bot.callback_query_handler(func=lambda c: c.data in ["listing", "refresh_all"])
def cb_listing(call):
    bot.answer_callback_query(call.id)
    cursor.execute("SELECT id, brand, value, price, bin FROM giftcards WHERE status='available' ORDER BY created_at DESC")
    rows = cursor.fetchall()
    if not rows:
        bot.send_message(call.from_user.id, "📭 No cards in stock.")
        return

    text_lines = ["🛒 <b>All Available Cards</b>\n"]
    kb = types.InlineKeyboardMarkup(row_width=1)

    for card_id, brand, value, price, bin_val in rows:
        text_lines.append(f"ID: {card_id} | {brand} | Value: ${value} | Price: ${price} | BIN: {bin_val or 'N/A'}")
        kb.add(types.InlineKeyboardButton(f"Buy {brand} ${value} for ${price}", callback_data=f"buy|{card_id}"))

    kb.add(
        types.InlineKeyboardButton("📂 Filter by Brand", callback_data="filter_menu"),
        types.InlineKeyboardButton("🔎 Search by BIN", callback_data="bin_menu")
    )
    kb.add(types.InlineKeyboardButton("🔄 Refresh", callback_data="refresh_all"))

    # 🔙 Back button
    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="back_to_menu"))

    bot.send_message(call.from_user.id, "\n".join(text_lines), parse_mode="HTML", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data == "filter_menu")
def cb_filter_menu(call):
    bot.answer_callback_query(call.id)
    cursor.execute("SELECT DISTINCT brand FROM giftcards WHERE status='available' ORDER BY brand")
    brands = [r[0] for r in cursor.fetchall()]
    if not brands:
        bot.send_message(call.from_user.id, "📭 No brands in stock.")
        return

    kb = types.InlineKeyboardMarkup(row_width=2)
    for b in brands:
        kb.add(types.InlineKeyboardButton(b, callback_data=f"brand|{b}"))
    kb.add(types.InlineKeyboardButton("🔄 Refresh all", callback_data="refresh_all"))

    # 🔙 Back button
    kb.add(types.InlineKeyboardButton("🔙 Back", callback_data="back_to_menu"))

    bot.send_message(call.from_user.id, "📂 Choose a brand:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("brand|") or c.data.startswith("refresh_brand|"))
def cb_brand_listing(call):
    bot.answer_callback_query(call.id)
    if call.data.startswith("refresh_brand|"):
        brand = call.data.split("|",1)[1]
    else:
        brand = call.data.split("|",1)[1]
    cursor.execute("SELECT id, value, price, bin FROM giftcards WHERE brand=? AND status='available' ORDER BY created_at DESC", (brand,))
    rows = cursor.fetchall()
    if not rows:
        bot.send_message(call.from_user.id, f"📭 No {brand} cards in stock.")
        return
    text_lines = [f"🛒 <b>Available {brand} Cards</b>\n"]
    kb = types.InlineKeyboardMarkup(row_width=1)
    for card_id, value, price, bin_val in rows:
        text_lines.append(f"ID: {card_id} | Value: ${value} | Price: ${price} | BIN: {bin_val or 'N/A'}")
        kb.add(types.InlineKeyboardButton(f"Buy ${value} for ${price}", callback_data=f"buy|{card_id}"))
    kb.add(types.InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_brand|{brand}"))
    kb.add(types.InlineKeyboardButton("⬅ Back to All", callback_data="listing"))
    bot.send_message(call.from_user.id, "\n".join(text_lines), parse_mode="HTML", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "bin_menu")
def cb_bin_menu(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.from_user.id, "🔎 Enter the 6-digit BIN to search:")
    bot.register_next_step_handler(msg, process_bin_search)

@bot.message_handler(commands=["bin"])
def cmd_bin(message):
    parts = message.text.strip().split()
    if len(parts) < 2:
        bot.send_message(message.chat.id, "⚠️ Usage: /bin <6-digit BIN>")
        return
    bin_code = parts[1].strip()
    process_bin_search_msg(message.chat.id, bin_code)

def process_bin_search(message):
    bin_code = (message.text or "").strip()
    process_bin_search_msg(message.chat.id, bin_code)

def process_bin_search_msg(chat_id, bin_code):
    if not bin_code.isdigit() or len(bin_code) < 6:
        bot.send_message(chat_id, "⚠️ Please enter a valid 6-digit BIN.")
        return
    cursor.execute("SELECT id, brand, value, price, bin FROM giftcards WHERE bin=? AND status='available' ORDER BY created_at DESC", (bin_code,))
    rows = cursor.fetchall()
    if not rows:
        bot.send_message(chat_id, f"📭 No cards found for BIN <b>{bin_code}</b>.", parse_mode="HTML")
        return
    text_lines = [f"🔎 Cards matching BIN {bin_code}:\n"]
    kb = types.InlineKeyboardMarkup(row_width=1)
    for card_id, brand, value, price, bin_val in rows:
        text_lines.append(f"ID: {card_id} | {brand} | Value: ${value} | Price: ${price} | BIN: {bin_val or 'N/A'}")
        kb.add(types.InlineKeyboardButton(f"Buy {brand} ${value} for ${price}", callback_data=f"buy|{card_id}"))
    kb.add(types.InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_bin|{bin_code}"))
    bot.send_message(chat_id, "\n".join(text_lines), parse_mode="HTML", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("refresh_bin|"))
def cb_refresh_bin(call):
    bot.answer_callback_query(call.id)
    bin_code = call.data.split("|",1)[1]
    cursor.execute("SELECT id, brand, value, price, bin FROM giftcards WHERE bin=? AND status='available' ORDER BY created_at DESC", (bin_code,))
    rows = cursor.fetchall()
    if not rows:
        bot.send_message(call.from_user.id, f"📭 No cards found for BIN <b>{bin_code}</b>.", parse_mode="HTML")
        return
    text_lines = [f"🔎 Refreshed BIN {bin_code} Results:\n"]
    kb = types.InlineKeyboardMarkup(row_width=1)
    for card_id, brand, value, price, bin_val in rows:
        text_lines.append(f"ID: {card_id} | {brand} | Value: ${value} | Price: ${price} | BIN: {bin_val or 'N/A'}")
        kb.add(types.InlineKeyboardButton(f"Buy {brand} ${value} for ${price}", callback_data=f"buy|{card_id}"))
    kb.add(types.InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_bin|{bin_code}"))
    bot.send_message(call.from_user.id, "\n".join(text_lines), parse_mode="HTML", reply_markup=kb)

# ---- Buying ----
@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("buy|"))
def cb_buy(call):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    try:
        card_id = int(call.data.split("|",1)[1])
    except:
        bot.send_message(uid, "⚠️ Bad card ID.")
        return
    ensure_user_exists(uid)
    cursor.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
    r = cursor.fetchone()
    balance = float(r[0]) if r and r[0] is not None else 0.0
    cursor.execute("SELECT brand, value, price, code, status FROM giftcards WHERE id=?", (card_id,))
    card = cursor.fetchone()
    if not card:
        bot.send_message(uid, "❌ Card not found.")
        return
    brand, value, price, code, status = card
    if status != "available":
        bot.send_message(uid, "❌ This card is no longer available.")
        return
    if balance < price:
        bot.send_message(uid, "❌ Insufficient balance. Please deposit more.")
        return
    
    # Deduct balance and mark card as sold
    cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (price, uid))
    cursor.execute("UPDATE giftcards SET status='sold' WHERE id=?", (card_id,))
    conn.commit()

    # ✅ Log the order in orders table
    cursor.execute(
        "INSERT INTO orders (user_id, item, price) VALUES (?, ?, ?)",
        (uid, f"{brand} {value}", price)
    )
    conn.commit()

    # Send card details to user
    try:
        bot.send_message(
            uid,
            f"✅ Purchase successful!\n\n🏷 Brand: {brand}\n💳 Value: ${value}\n💵 Price: ${price}\n\n🔑 Your Code:\n<code>{code}</code>",
            parse_mode="HTML"
        )
    except Exception:
        pass

        logging.exception("Failed to send code to user")
    try:
        if ADMIN_ID:
            bot.send_message(ADMIN_ID, f"🛒 Card ID {card_id} sold to user {uid} for ${price}")
    except Exception:
        logging.exception("Failed to notify admin of sale")

# Admin helper: list deposits (command)
@bot.message_handler(commands=["list_deposits"])
def cmd_list_deposits(message):
    if message.from_user.id != ADMIN_ID:
        return
    cursor.execute("SELECT id, user_id, coin, amount, status, created_at FROM deposits ORDER BY created_at DESC LIMIT 50")
    rows = cursor.fetchall()
    if not rows:
        bot.send_message(ADMIN_ID, "No deposits found.")
        return
    text_lines = ["Recent deposits:\n"]
    for r in rows:
        text_lines.append(f"#{r[0]} | user:{r[1]} | {r[2]} {r[3]} | {r[4]} | {r[5]}")
    bot.send_message(ADMIN_ID, "\n".join(text_lines))

# ---- Run bot ----
if __name__ == "__main__":
    logging.info("Starting bot...")
    try:
        bot.infinity_polling(skip_pending=True)
    except Exception:
        logging.exception("Bot stopped unexpectedly")

