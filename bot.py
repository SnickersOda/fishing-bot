import logging
import random
import sqlite3
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
    CallbackQueryHandler, MessageHandler, filters
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = "fishing.db"

FISH_TYPES = [
    ("🐟 Карась",   3,  6),
    ("🐠 Окунь",    4,  8),
    ("🐡 Лещ",      5,  9),
    ("🦈 Щука",     7,  13),
    ("🐬 Сом",      10, 16),
    ("🐳 Карп",     12, 18),
    ("🦑 Судак",    14, 20),
]

SHOP_ITEMS = [
    {"id": "rod_basic",  "name": "🎣 Обычная удочка",  "price": 0,   "bonus": 0, "desc": "Стартовая удочка"},
    {"id": "rod_medium", "name": "🎣 Хорошая удочка",  "price": 50,  "bonus": 2, "desc": "+2 кг к улову"},
    {"id": "rod_pro",    "name": "🎣 Проф. удочка",    "price": 150, "bonus": 5, "desc": "+5 кг к улову"},
    {"id": "bait_basic", "name": "🪱 Обычная наживка", "price": 10,  "bonus": 1, "desc": "+1 кг к улову"},
    {"id": "bait_good",  "name": "🦗 Хорошая наживка", "price": 30,  "bonus": 3, "desc": "+3 кг к улову"},
]

TEXT_TRIGGERS = {
    "fish":    ["рыбалка", "рыбачить", "закинуть", "поймать рыбу", "рыбу"],
    "profile": ["профиль", "мой профиль", "стата", "статус"],
    "top":     ["топ", "рейтинг", "лучшие рыбаки"],
    "stats":   ["статистика", "мой улов", "моя статистика"],
    "shop":    ["магазин", "купить", "снаряжение"],
    "help":    ["помощь", "команды"],
}

# ─── DATABASE ────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                total_kg    REAL    DEFAULT 0,
                fish_count  INTEGER DEFAULT 0,
                best_catch  REAL    DEFAULT 0,
                coins       INTEGER DEFAULT 0,
                rod         TEXT    DEFAULT 'rod_basic',
                bait        TEXT    DEFAULT NULL,
                last_fish   TEXT    DEFAULT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS catches (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER,
                fish_name   TEXT,
                weight      REAL,
                caught_at   TEXT
            )
        """)
        db.commit()

def get_user(user_id, username):
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            db.execute("INSERT INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
            db.commit()
            row = db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return dict(row)

def update_user(user_id, **kwargs):
    fields = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values()) + [user_id]
    with get_db() as db:
        db.execute(f"UPDATE users SET {fields} WHERE user_id=?", values)
        db.commit()

def add_catch(user_id, fish_name, weight):
    with get_db() as db:
        db.execute(
            "INSERT INTO catches (user_id, fish_name, weight, caught_at) VALUES (?,?,?,?)",
            (user_id, fish_name, weight, datetime.now().isoformat())
        )
        db.commit()

def get_top(limit=10):
    with get_db() as db:
        return db.execute(
            "SELECT username, total_kg, fish_count, best_catch FROM users ORDER BY total_kg DESC LIMIT ?",
            (limit,)
        ).fetchall()

def get_stats(user_id):
    with get_db() as db:
        return db.execute(
            """SELECT fish_name, COUNT(*) as cnt, SUM(weight) as total, MAX(weight) as best
               FROM catches WHERE user_id=? GROUP BY fish_name ORDER BY total DESC""",
            (user_id,)
        ).fetchall()

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def get_rod_bonus(rod_id):
    for item in SHOP_ITEMS:
        if item["id"] == rod_id:
            return item["bonus"]
    return 0

def get_bait_bonus(bait_id):
    if not bait_id:
        return 0
    for item in SHOP_ITEMS:
        if item["id"] == bait_id:
            return item["bonus"]
    return 0

def can_fish(last_fish_str):
    if not last_fish_str:
        return True, 0
    last = datetime.fromisoformat(last_fish_str)
    diff = datetime.now() - last
    cooldown = timedelta(hours=1)
    if diff >= cooldown:
        return True, 0
    remaining = int((cooldown - diff).total_seconds() / 60)
    return False, remaining

# ─── CORE LOGIC ──────────────────────────────────────────────────────────────

async def do_fish(user, reply_fn):
    u = get_user(user.id, user.first_name)
    ok, mins = can_fish(u["last_fish"])
    if not ok:
        return await reply_fn(
            f"⏳ Рыба ещё не клюёт! Подожди ещё *{mins} мин.* перед следующим забросом.",
            parse_mode="Markdown"
        )
    fish_name, w_min, w_max = random.choice(FISH_TYPES)
    weight = round(random.uniform(w_min, w_max) + get_rod_bonus(u["rod"]) + get_bait_bonus(u["bait"]), 2)
    weight = min(weight, 25.0)
    coins_earned = int(weight * 2)
    is_best = weight > u["best_catch"]
    update_user(
        user.id,
        total_kg   = u["total_kg"] + weight,
        fish_count = u["fish_count"] + 1,
        best_catch = max(u["best_catch"], weight),
        coins      = u["coins"] + coins_earned,
        last_fish  = datetime.now().isoformat()
    )
    add_catch(user.id, fish_name, weight)
    best_text = "\n🏆 *Новый личный рекорд!*" if is_best else ""
    emoji = "🏆" if is_best else "🎣"
    await reply_fn(
        f"{emoji} *{user.first_name} поймал рыбу!*\n\n"
        f"🐟 Вид: {fish_name}\n"
        f"⚖️ Вес: *{weight} кг*\n"
        f"💰 Монет получено: +{coins_earned}"
        f"{best_text}",
        parse_mode="Markdown"
    )

async def do_profile(user, reply_fn):
    u = get_user(user.id, user.first_name)
    rod_name  = next((i["name"] for i in SHOP_ITEMS if i["id"] == u["rod"]), "Обычная удочка")
    bait_name = next((i["name"] for i in SHOP_ITEMS if i["id"] == u["bait"]), "Без наживки") if u["bait"] else "Без наживки"
    ok, mins = can_fish(u["last_fish"])
    status = "✅ Готов к рыбалке!" if ok else f"⏳ Следующий заброс через {mins} мин."
    await reply_fn(
        f"👤 *Профиль: {user.first_name}*\n\n"
        f"🐟 Поймано рыб: *{u['fish_count']}*\n"
        f"⚖️ Общий улов: *{u['total_kg']:.1f} кг*\n"
        f"🏆 Рекорд: *{u['best_catch']:.1f} кг*\n"
        f"💰 Монет: *{u['coins']}*\n\n"
        f"🎣 Удочка: {rod_name}\n"
        f"🪱 Наживка: {bait_name}\n\n"
        f"{status}",
        parse_mode="Markdown"
    )

async def do_top(reply_fn):
    rows = get_top()
    if not rows:
        return await reply_fn("📊 Пока никто не рыбачил!")
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 *Топ рыбаков*\n"]
    for i, row in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{medal} *{row['username']}* — {row['total_kg']:.1f} кг ({row['fish_count']} рыб, рекорд: {row['best_catch']:.1f} кг)")
    await reply_fn("\n".join(lines), parse_mode="Markdown")

async def do_stats(user, reply_fn):
    rows = get_stats(user.id)
    if not rows:
        return await reply_fn("📊 Ты ещё ничего не поймал! Напиши *рыбалка* или /fish", parse_mode="Markdown")
    lines = [f"📊 *Статистика улова — {user.first_name}*\n"]
    for row in rows:
        lines.append(f"{row['fish_name']}: {row['cnt']} шт. | {row['total']:.1f} кг всего | рекорд {row['best']:.1f} кг")
    await reply_fn("\n".join(lines), parse_mode="Markdown")

async def do_shop(user, reply_fn):
    u = get_user(user.id, user.first_name)
    keyboard = []
    for item in SHOP_ITEMS:
        if item["price"] == 0:
            continue
        owned = (u["rod"] == item["id"] or u["bait"] == item["id"])
        label = f"{'✅ ' if owned else ''}{item['name']} — {item['price']}💰 ({item['desc']})"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"buy_{item['id']}")])
    await reply_fn(
        f"🛒 *Магазин*\n\nТвои монеты: *{u['coins']}* 💰\n\nВыбери товар:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def do_help(reply_fn):
    await reply_fn(
        "💡 *Все команды:*\n\n"
        "🎣 /fish или напиши *рыбалка*\n"
        "👤 /profile или *профиль*\n"
        "🏆 /top или *топ*\n"
        "📊 /stats или *статистика*\n"
        "🛒 /shop или *магазин*\n"
        "❓ /help или *помощь*\n\n"
        "_Лови рыбу, зарабатывай монеты и покупай лучшее снаряжение!_",
        parse_mode="Markdown"
    )

# ─── COMMAND HANDLERS ────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bot_username = (await ctx.bot.get_me()).username
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎣 Рыбачить",   callback_data="cmd_fish"),
            InlineKeyboardButton("👤 Профиль",    callback_data="cmd_profile"),
        ],
        [
            InlineKeyboardButton("🏆 Топ",        callback_data="cmd_top"),
            InlineKeyboardButton("📊 Статистика", callback_data="cmd_stats"),
        ],
        [
            InlineKeyboardButton("🛒 Магазин",    callback_data="cmd_shop"),
            InlineKeyboardButton("❓ Помощь",     callback_data="cmd_help"),
        ],
        [
            InlineKeyboardButton("➕ Добавить бота в группу", url=f"https://t.me/{bot_username}?startgroup=true")
        ]
    ])
    await update.message.reply_text(
        "🎣 *Добро пожаловать на Рыбалку!*\n\n"
        "Каждый час ты можешь закидывать удочку командой /fish\n"
        "и ловить рыбу весом от 3 до 20 кг! 🐟🐠🦈\n\n"
        "👥 Добавь бота в группу — и устроим рыболовный турнир!",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def fish(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await do_fish(update.effective_user, update.message.reply_text)

async def profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await do_profile(update.effective_user, update.message.reply_text)

async def top(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await do_top(update.message.reply_text)

async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await do_stats(update.effective_user, update.message.reply_text)

async def shop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await do_shop(update.effective_user, update.message.reply_text)

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await do_help(update.message.reply_text)

# ─── TEXT TRIGGER HANDLER ────────────────────────────────────────────────────

async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.lower().strip()
    user = update.effective_user
    for cmd, triggers in TEXT_TRIGGERS.items():
        if any(t in text for t in triggers):
            if cmd == "fish":       await do_fish(user, update.message.reply_text)
            elif cmd == "profile":  await do_profile(user, update.message.reply_text)
            elif cmd == "top":      await do_top(update.message.reply_text)
            elif cmd == "stats":    await do_stats(user, update.message.reply_text)
            elif cmd == "shop":     await do_shop(user, update.message.reply_text)
            elif cmd == "help":     await do_help(update.message.reply_text)
            return

# ─── CALLBACK HANDLERS ───────────────────────────────────────────────────────

async def menu_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    async def reply(text, **kwargs):
        await query.message.reply_text(text, **kwargs)
    cmd = query.data.replace("cmd_", "")
    if cmd == "fish":       await do_fish(user, reply)
    elif cmd == "profile":  await do_profile(user, reply)
    elif cmd == "top":      await do_top(reply)
    elif cmd == "stats":    await do_stats(user, reply)
    elif cmd == "shop":     await do_shop(user, reply)
    elif cmd == "help":     await do_help(reply)

async def shop_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    item_id = query.data.replace("buy_", "")
    u = get_user(user.id, user.first_name)
    item = next((i for i in SHOP_ITEMS if i["id"] == item_id), None)
    if not item:
        await query.edit_message_text("❌ Товар не найден.")
        return
    if u["coins"] < item["price"]:
        await query.answer(f"❌ Недостаточно монет! Нужно {item['price']}💰", show_alert=True)
        return
    if u["rod"] == item_id or u["bait"] == item_id:
        await query.answer("✅ У тебя уже есть этот предмет!", show_alert=True)
        return
    if item_id.startswith("rod_"):
        update_user(user.id, rod=item_id, coins=u["coins"] - item["price"])
    else:
        update_user(user.id, bait=item_id, coins=u["coins"] - item["price"])
    await query.edit_message_text(
        f"✅ *Куплено: {item['name']}!*\n{item['desc']}",
        parse_mode="Markdown"
    )

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    import os
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN не задан!")
    init_db()
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("fish",    fish))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("top",     top))
    app.add_handler(CommandHandler("stats",   stats))
    app.add_handler(CommandHandler("shop",    shop))
    app.add_handler(CommandHandler("help",    help_cmd))
    app.add_handler(CallbackQueryHandler(shop_callback, pattern="^buy_"))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^cmd_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    logger.info("🎣 Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
