import logging
import random
import sqlite3
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = "fishing.db"

FISH_TYPES = [
    ("🐟 Карась",        3,  6),
    ("🐠 Окунь",         4,  8),
    ("🐡 Лещ",           5,  9),
    ("🦈 Щука",          7,  13),
    ("🐬 Сом",           10, 16),
    ("🐳 Карп",          12, 18),
    ("🦑 Судак",         14, 20),
]

SHOP_ITEMS = [
    {"id": "rod_basic",   "name": "🎣 Обычная удочка",   "price": 0,   "bonus": 0,  "desc": "Стартовая удочка"},
    {"id": "rod_medium",  "name": "🎣 Хорошая удочка",   "price": 50,  "bonus": 2,  "desc": "+2 кг к улову"},
    {"id": "rod_pro",     "name": "🎣 Проф. удочка",     "price": 150, "bonus": 5,  "desc": "+5 кг к улову"},
    {"id": "bait_basic",  "name": "🪱 Обычная наживка",  "price": 10,  "bonus": 1,  "desc": "+1 кг к улову"},
    {"id": "bait_good",   "name": "🦗 Хорошая наживка",  "price": 30,  "bonus": 3,  "desc": "+3 кг к улову"},
]

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

def get_user(user_id: int, username: str):
    with get_db() as db:
        row = db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            db.execute(
                "INSERT INTO users (user_id, username) VALUES (?, ?)",
                (user_id, username)
            )
            db.commit()
            row = db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return dict(row)

def update_user(user_id: int, **kwargs):
    fields = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values()) + [user_id]
    with get_db() as db:
        db.execute(f"UPDATE users SET {fields} WHERE user_id=?", values)
        db.commit()

def add_catch(user_id: int, fish_name: str, weight: float):
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

def get_stats(user_id: int):
    with get_db() as db:
        return db.execute(
            """SELECT fish_name, COUNT(*) as cnt, SUM(weight) as total, MAX(weight) as best
               FROM catches WHERE user_id=? GROUP BY fish_name ORDER BY total DESC""",
            (user_id,)
        ).fetchall()

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def get_rod_bonus(rod_id: str) -> float:
    for item in SHOP_ITEMS:
        if item["id"] == rod_id:
            return item["bonus"]
    return 0

def get_bait_bonus(bait_id: str) -> float:
    if not bait_id:
        return 0
    for item in SHOP_ITEMS:
        if item["id"] == bait_id:
            return item["bonus"]
    return 0

def can_fish(last_fish_str: str) -> tuple[bool, int]:
    if not last_fish_str:
        return True, 0
    last = datetime.fromisoformat(last_fish_str)
    diff = datetime.now() - last
    cooldown = timedelta(hours=1)
    if diff >= cooldown:
        return True, 0
    remaining = int((cooldown - diff).total_seconds() / 60)
    return False, remaining

# ─── HANDLERS ────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "🎣 *Добро пожаловать на Рыбалку!*\n\n"
        "Каждый час ты можешь закидывать удочку командой /fish\n"
        "и ловить рыбу весом от 3 до 20 кг! 🐟🐠🦈\n\n"
        "👥 Добавь бота в группу — и устроим рыболовный турнир!"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def fish(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = get_user(user.id, user.first_name)

    ok, mins = can_fish(u["last_fish"])
    if not ok:
        await update.message.reply_text(
            f"⏳ Рыба ещё не клюёт! Подожди ещё *{mins} мин.* перед следующим забросом.",
            parse_mode="Markdown"
        )
        return

    fish_name, w_min, w_max = random.choice(FISH_TYPES)
    rod_bonus  = get_rod_bonus(u["rod"])
    bait_bonus = get_bait_bonus(u["bait"])

    weight = round(random.uniform(w_min, w_max) + rod_bonus + bait_bonus, 2)
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

    emoji = "🏆" if is_best else "🎣"
    best_text = "\n🏆 *Новый личный рекорд!*" if is_best else ""

    text = (
        f"{emoji} *{user.first_name} поймал рыбу!*\n\n"
        f"🐟 Вид: {fish_name}\n"
        f"⚖️ Вес: *{weight} кг*\n"
        f"💰 Монет получено: +{coins_earned}\n"
        f"{best_text}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = get_user(user.id, user.first_name)

    rod_name  = next((i["name"] for i in SHOP_ITEMS if i["id"] == u["rod"]),  "Обычная удочка")
    bait_name = next((i["name"] for i in SHOP_ITEMS if i["id"] == u["bait"]), "Без наживки") if u["bait"] else "Без наживки"

    ok, mins = can_fish(u["last_fish"])
    status = "✅ Готов к рыбалке!" if ok else f"⏳ Следующий заброс через {mins} мин."

    text = (
        f"👤 *Профиль: {user.first_name}*\n\n"
        f"🐟 Поймано рыб: *{u['fish_count']}*\n"
        f"⚖️ Общий улов: *{u['total_kg']:.1f} кг*\n"
        f"🏆 Рекорд: *{u['best_catch']:.1f} кг*\n"
        f"💰 Монет: *{u['coins']}*\n\n"
        f"🎣 Удочка: {rod_name}\n"
        f"🪱 Наживка: {bait_name}\n\n"
        f"{status}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def top(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = get_top()
    if not rows:
        await update.message.reply_text("📊 Пока никто не рыбачил!")
        return

    lines = ["🏆 *Топ рыбаков*\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, row in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        lines.append(
            f"{medal} *{row['username']}* — {row['total_kg']:.1f} кг "
            f"({row['fish_count']} рыб, рекорд: {row['best_catch']:.1f} кг)"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    rows = get_stats(user.id)
    if not rows:
        await update.message.reply_text("📊 Ты ещё ничего не поймал! Попробуй /fish")
        return

    lines = [f"📊 *Статистика улова — {user.first_name}*\n"]
    for row in rows:
        lines.append(
            f"{row['fish_name']}: {row['cnt']} шт. | "
            f"{row['total']:.1f} кг всего | рекорд {row['best']:.1f} кг"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def shop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = get_user(user.id, user.first_name)

    keyboard = []
    for item in SHOP_ITEMS:
        if item["price"] == 0:
            continue
        owned = (u["rod"] == item["id"] or u["bait"] == item["id"])
        label = f"{'✅ ' if owned else ''}{item['name']} — {item['price']}💰 ({item['desc']})"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"buy_{item['id']}")])

    text = f"🛒 *Магазин*\n\nТвои монеты: *{u['coins']}* 💰\n\nВыбери товар:"
    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

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

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "💡 *Все команды:*\n\n"
        "🎣 /fish — Закинуть удочку (1 раз в час)\n"
        "👤 /profile — Твой профиль\n"
        "🏆 /top — Топ рыбаков\n"
        "📊 /stats — Статистика улова\n"
        "🛒 /shop — Магазин удочек и наживок\n"
        "❓ /help — Все команды\n\n"
        "_Лови рыбу, зарабатывай монеты и покупай лучшее снаряжение!_"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

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

    logger.info("🎣 Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
