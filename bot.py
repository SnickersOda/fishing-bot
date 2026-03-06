import logging
import random
import sqlite3
import os
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
    CallbackQueryHandler, MessageHandler, filters
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = "fishing.db"
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))  # твой Telegram ID

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
    "fish":    ["рыбалка", "рыбачить", "закинуть", "поймать рыбу"],
    "profile": ["профиль", "мой профиль", "стата", "статус"],
    "top":     ["топ", "рейтинг", "лучшие рыбаки"],
    "stats":   ["статистика", "мой улов", "моя статистика"],
    "shop":    ["магазин", "купить", "снаряжение"],
    "help":    ["помощь", "команды"],
}

STEAL_TRIGGERS = ["спиздить", "украсть", "кража", "стырить", "свиснуть", "спереть"]
MIN_COINS = 15

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
        db.execute("""
            CREATE TABLE IF NOT EXISTS promocodes (
                code        TEXT PRIMARY KEY,
                coins       INTEGER NOT NULL,
                uses_left   INTEGER NOT NULL,
                created_at  TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS promo_used (
                user_id     INTEGER,
                code        TEXT,
                PRIMARY KEY (user_id, code)
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

# ─── PROMO DB ─────────────────────────────────────────────────────────────────

def create_promo(code, coins, uses):
    with get_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO promocodes (code, coins, uses_left, created_at) VALUES (?,?,?,?)",
            (code.upper(), coins, uses, datetime.now().isoformat())
        )
        db.commit()

def get_promo(code):
    with get_db() as db:
        row = db.execute("SELECT * FROM promocodes WHERE code=?", (code.upper(),)).fetchone()
        return dict(row) if row else None

def use_promo(user_id, code):
    """Returns (success, message)"""
    with get_db() as db:
        promo = db.execute("SELECT * FROM promocodes WHERE code=?", (code.upper(),)).fetchone()
        if not promo:
            return False, "❌ Промокод не найден!"
        if promo["uses_left"] <= 0:
            return False, "❌ Промокод уже использован максимальное количество раз!"
        already = db.execute(
            "SELECT 1 FROM promo_used WHERE user_id=? AND code=?", (user_id, code.upper())
        ).fetchone()
        if already:
            return False, "❌ Ты уже использовал этот промокод!"
        db.execute("UPDATE promocodes SET uses_left=uses_left-1 WHERE code=?", (code.upper(),))
        db.execute("INSERT INTO promo_used (user_id, code) VALUES (?,?)", (user_id, code.upper()))
        db.commit()
        return True, promo["coins"]

def list_promos():
    with get_db() as db:
        return db.execute("SELECT * FROM promocodes ORDER BY created_at DESC").fetchall()

def delete_promo(code):
    with get_db() as db:
        db.execute("DELETE FROM promocodes WHERE code=?", (code.upper(),))
        db.execute("DELETE FROM promo_used WHERE code=?", (code.upper(),))
        db.commit()

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
        "🎣 /fish — Закинуть удочку (1 раз в час)\n"
        "👤 /profile — Твой профиль\n"
        "🏆 /top — Топ рыбаков\n"
        "📊 /stats — Статистика улова\n"
        "🛒 /shop — Магазин снаряжения\n"
        "🎁 /promo [код] — Активировать промокод\n"
        "🦹 Ответь *спиздить* на чьё-то сообщение — украсть монеты\n\n"
        "_Лови рыбу, зарабатывай монеты, грабь соседей!_",
        parse_mode="Markdown"
    )

# ─── STEAL LOGIC ─────────────────────────────────────────────────────────────

async def do_steal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    thief = update.effective_user

    if not msg.reply_to_message:
        await msg.reply_text(
            "🕵️ Чтобы украсть — *ответь* на сообщение жертвы и напиши *спиздить*",
            parse_mode="Markdown"
        )
        return

    victim = msg.reply_to_message.from_user

    if victim.id == thief.id:
        await msg.reply_text("🤡 Сам у себя красть? Ну ты даёшь...")
        return

    if victim.is_bot:
        await msg.reply_text("🤖 У бота нечего красть — он не рыбачит!")
        return

    t = get_user(thief.id, thief.first_name)
    v = get_user(victim.id, victim.first_name)

    if t["coins"] < MIN_COINS:
        await msg.reply_text(
            f"😅 *{thief.first_name}*, у тебя меньше {MIN_COINS}💰 —\n"
            f"нищий у нищего не крадёт!",
            parse_mode="Markdown"
        )
        return

    if v["coins"] < MIN_COINS:
        await msg.reply_text(
            f"😬 У *{victim.first_name}* всего {v['coins']}💰 —\n"
            f"грабить нищих последнее дело!",
            parse_mode="Markdown"
        )
        return

    success = random.random() < 0.5

    if success:
        percent = random.uniform(0.05, 0.40)
        stolen = max(1, int(v["coins"] * percent))
        update_user(thief.id, coins=t["coins"] + stolen)
        update_user(victim.id, coins=v["coins"] - stolen)

        outcomes = [
            f"😈 *{thief.first_name}* мастерски обчистил карманы *{victim.first_name}*!\n\n"
            f"💸 Украдено: *{stolen}💰*\n"
            f"👛 Теперь у тебя: *{t['coins'] + stolen}💰*",

            f"🕵️ Операция прошла успешно!\n\n"
            f"*{thief.first_name}* увёл *{stolen}💰* у *{victim.first_name}* — "
            f"тот даже не заметил!",

            f"🐟 Пока *{victim.first_name}* глядел на поплавок,\n"
            f"*{thief.first_name}* стащил *{stolen}💰* из его кармана! 😏",
        ]
        await msg.reply_text(random.choice(outcomes), parse_mode="Markdown")

    else:
        fine = random.randint(1, min(20, t["coins"]))
        update_user(thief.id, coins=t["coins"] - fine)

        fails = [
            f"🚨 *{thief.first_name}* попался на горячем!\n\n"
            f"*{victim.first_name}* заметил кражу и вызвал рыбнадзор.\n"
            f"Штраф: *{fine}💰* 😤",

            f"👮 Неудача! *{thief.first_name}* поскользнулся на рыбьей чешуе\n"
            f"и привлёк внимание всей группы. Штраф: *{fine}💰*",

            f"😂 *{thief.first_name}* попытался обокрасть *{victim.first_name}*,\n"
            f"но был пойман за руку! Штраф: *{fine}💰* 🤦",
        ]
        await msg.reply_text(random.choice(fails), parse_mode="Markdown")

# ─── PROMO LOGIC ─────────────────────────────────────────────────────────────

async def promo_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = ctx.args

    if not args:
        await update.message.reply_text(
            "🎁 Введи промокод: `/promo КОД`",
            parse_mode="Markdown"
        )
        return

    code = args[0].strip()
    ok, result = use_promo(user.id, code)

    if not ok:
        await update.message.reply_text(result)
        return

    coins = result
    u = get_user(user.id, user.first_name)
    update_user(user.id, coins=u["coins"] + coins)

    await update.message.reply_text(
        f"🎉 Промокод *{code.upper()}* активирован!\n\n"
        f"💰 Начислено: *+{coins} монет*\n"
        f"👛 Твой баланс: *{u['coins'] + coins}💰*",
        parse_mode="Markdown"
    )

# ─── ADMIN PROMO PANEL ───────────────────────────────────────────────────────

async def admin_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Нет доступа.")
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Создать промокод", callback_data="adm_create")],
        [InlineKeyboardButton("📋 Список промокодов", callback_data="adm_list")],
    ])
    await update.message.reply_text(
        "🔧 *Панель администратора*\n\nУправление промокодами:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def admin_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    if user.id != ADMIN_ID:
        await query.answer("⛔ Нет доступа!", show_alert=True)
        return

    await query.answer()
    data = query.data

    if data == "adm_create":
        await query.message.reply_text(
            "📝 Отправь команду в формате:\n\n"
            "`/newpromo КОД МОНЕТЫ АКТИВАЦИЙ`\n\n"
            "Пример: `/newpromo FISHING100 100 50`\n"
            "_Создаст промокод FISHING100 на 100 монет, 50 активаций_",
            parse_mode="Markdown"
        )

    elif data == "adm_list":
        promos = list_promos()
        if not promos:
            await query.message.reply_text("📋 Промокодов пока нет.")
            return

        lines = ["📋 *Активные промокоды:*\n"]
        keyboard = []
        for p in promos:
            lines.append(
                f"🎁 `{p['code']}` — {p['coins']}💰 | осталось активаций: *{p['uses_left']}*"
            )
            keyboard.append([InlineKeyboardButton(f"❌ Удалить {p['code']}", callback_data=f"adm_del_{p['code']}")])

        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="adm_back")])
        await query.message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("adm_del_"):
        code = data.replace("adm_del_", "")
        delete_promo(code)
        await query.message.reply_text(f"🗑 Промокод `{code}` удалён.", parse_mode="Markdown")

    elif data == "adm_back":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Создать промокод", callback_data="adm_create")],
            [InlineKeyboardButton("📋 Список промокодов", callback_data="adm_list")],
        ])
        await query.message.reply_text(
            "🔧 *Панель администратора*",
            parse_mode="Markdown",
            reply_markup=keyboard
        )

async def newpromo_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Нет доступа.")
        return

    args = ctx.args
    if len(args) != 3:
        await update.message.reply_text(
            "❌ Формат: `/newpromo КОД МОНЕТЫ АКТИВАЦИЙ`\nПример: `/newpromo FISH50 50 10`",
            parse_mode="Markdown"
        )
        return

    try:
        code  = args[0].upper()
        coins = int(args[1])
        uses  = int(args[2])
        if coins <= 0 or uses <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Монеты и активации должны быть положительными числами!")
        return

    create_promo(code, coins, uses)
    await update.message.reply_text(
        f"✅ Промокод создан!\n\n"
        f"🎁 Код: `{code}`\n"
        f"💰 Монет: *{coins}*\n"
        f"🔢 Активаций: *{uses}*",
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
        "Каждый час закидывай удочку командой /fish\n"
        "и лови рыбу весом от 3 до 20 кг! 🐟🐠🦈\n\n"
        "🦹 В группе можно *спиздить* монеты у соседей!\n"
        "👥 Добавь бота в группу — и устроим турнир!",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def fish_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await do_fish(update.effective_user, update.message.reply_text)

async def profile_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await do_profile(update.effective_user, update.message.reply_text)

async def top_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await do_top(update.message.reply_text)

async def stats_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await do_stats(update.effective_user, update.message.reply_text)

async def shop_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await do_shop(update.effective_user, update.message.reply_text)

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await do_help(update.message.reply_text)

# ─── TEXT HANDLER ────────────────────────────────────────────────────────────

async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.lower().strip()
    user = update.effective_user

    # Проверяем кражу первой (реплай + триггер)
    if any(t in text for t in STEAL_TRIGGERS):
        await do_steal(update, ctx)
        return

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

async def set_commands(app):
    """Устанавливает меню команд для лички и групп"""
    private_commands = [
        BotCommand("start",   "🎣 Главное меню"),
        BotCommand("fish",    "🎣 Закинуть удочку"),
        BotCommand("profile", "👤 Мой профиль"),
        BotCommand("top",     "🏆 Топ рыбаков"),
        BotCommand("stats",   "📊 Статистика улова"),
        BotCommand("shop",    "🛒 Магазин снаряжения"),
        BotCommand("promo",   "🎁 Активировать промокод"),
        BotCommand("help",    "❓ Все команды"),
        BotCommand("admin",   "🔧 Панель админа"),
    ]
    group_commands = [
        BotCommand("fish",    "🎣 Закинуть удочку"),
        BotCommand("profile", "👤 Показать профиль"),
        BotCommand("top",     "🏆 Топ рыбаков"),
        BotCommand("stats",   "📊 Статистика улова"),
        BotCommand("shop",    "🛒 Магазин с бонусами"),
        BotCommand("help",    "💡 Все команды"),
    ]
    from telegram.constants import BotCommandScopeType
    from telegram import BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats
    await app.bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())
    await app.bot.set_my_commands(group_commands,   scope=BotCommandScopeAllGroupChats())

def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN не задан!")

    init_db()
    app = Application.builder().token(token).post_init(set_commands).build()

    app.add_handler(CommandHandler("start",    start))
    app.add_handler(CommandHandler("fish",     fish_cmd))
    app.add_handler(CommandHandler("profile",  profile_cmd))
    app.add_handler(CommandHandler("top",      top_cmd))
    app.add_handler(CommandHandler("stats",    stats_cmd))
    app.add_handler(CommandHandler("shop",     shop_cmd))
    app.add_handler(CommandHandler("help",     help_cmd))
    app.add_handler(CommandHandler("promo",    promo_cmd))
    app.add_handler(CommandHandler("admin",    admin_panel))
    app.add_handler(CommandHandler("newpromo", newpromo_cmd))

    app.add_handler(CallbackQueryHandler(shop_callback,  pattern="^buy_"))
    app.add_handler(CallbackQueryHandler(menu_callback,  pattern="^cmd_"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^adm_"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    logger.info("🎣 Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
