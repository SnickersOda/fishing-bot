import logging
import random
import os
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
    CallbackQueryHandler, MessageHandler, filters
)
import psycopg

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

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
    {"id": "rod_basic",   "name": "🎣 Обычная удочка",    "price": 0,   "bonus": 0, "desc": "Стартовая удочка",              "type": "rod"},
    {"id": "rod_medium",  "name": "🎣 Хорошая удочка",    "price": 50,  "bonus": 2, "desc": "+2 кг к улову",                 "type": "rod"},
    {"id": "rod_pro",     "name": "🎣 Проф. удочка",      "price": 150, "bonus": 5, "desc": "+5 кг к улову",                 "type": "rod"},
    {"id": "bait_basic",  "name": "🪱 Обычная наживка",   "price": 10,  "bonus": 1, "desc": "+1 кг к улову",                 "type": "bait"},
    {"id": "bait_good",   "name": "🦗 Хорошая наживка",   "price": 30,  "bonus": 3, "desc": "+3 кг к улову",                 "type": "bait"},
    {"id": "shield",      "name": "🛡 Защита от кражи",   "price": 150, "bonus": 0, "desc": "Защита на 12 часов",            "type": "consumable"},
    {"id": "steal_extra", "name": "🗡 Доп. кража",        "price": 200, "bonus": 0, "desc": "Внеплановая попытка кражи",     "type": "consumable"},
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
MIN_COINS       = 15
STEAL_COOLDOWN  = timedelta(hours=12)
SHIELD_DURATION = timedelta(hours=12)

# ─── DATABASE ────────────────────────────────────────────────────────────────

def get_db():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=psycopg.rows.dict_row)

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id         BIGINT PRIMARY KEY,
                username        TEXT,
                total_kg        REAL    DEFAULT 0,
                fish_count      INTEGER DEFAULT 0,
                best_catch      REAL    DEFAULT 0,
                coins           INTEGER DEFAULT 0,
                rod             TEXT    DEFAULT 'rod_basic',
                bait            TEXT    DEFAULT NULL,
                last_fish       TEXT    DEFAULT NULL,
                last_steal      TEXT    DEFAULT NULL,
                shield_until    TEXT    DEFAULT NULL,
                extra_steals    INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS catches (
                id         SERIAL PRIMARY KEY,
                user_id    BIGINT,
                fish_name  TEXT,
                weight     REAL,
                caught_at  TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS promocodes (
                code       TEXT PRIMARY KEY,
                coins      INTEGER NOT NULL,
                uses_left  INTEGER NOT NULL,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS promo_used (
                user_id BIGINT,
                code    TEXT,
                PRIMARY KEY (user_id, code)
            )
        """)
        for col, definition in [
            ("last_steal",   "TEXT DEFAULT NULL"),
            ("shield_until", "TEXT DEFAULT NULL"),
            ("extra_steals", "INTEGER DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {definition}")
            except Exception:
                conn.rollback()
        conn.commit()

def get_user(user_id, username):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id=%s", (user_id,)).fetchone()
        if not row:
            conn.execute("INSERT INTO users (user_id, username) VALUES (%s, %s)", (user_id, username))
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE user_id=%s", (user_id,)).fetchone()
        else:
            if row["username"] != username:
                conn.execute("UPDATE users SET username=%s WHERE user_id=%s", (username, user_id))
                conn.commit()
                row["username"] = username
        return dict(row)

def update_user(user_id, **kwargs):
    fields = ", ".join(f"{k}=%s" for k in kwargs)
    values = list(kwargs.values()) + [user_id]
    with get_db() as conn:
        conn.execute(f"UPDATE users SET {fields} WHERE user_id=%s", values)
        conn.commit()

def add_catch(user_id, fish_name, weight):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO catches (user_id, fish_name, weight, caught_at) VALUES (%s,%s,%s,%s)",
            (user_id, fish_name, weight, datetime.now().isoformat())
        )
        conn.commit()

def get_top(limit=10):
    with get_db() as conn:
        return conn.execute(
            "SELECT username, total_kg, fish_count, best_catch FROM users ORDER BY total_kg DESC LIMIT %s",
            (limit,)
        ).fetchall()

def get_stats(user_id):
    with get_db() as conn:
        return conn.execute(
            """SELECT fish_name, COUNT(*) as cnt, SUM(weight) as total, MAX(weight) as best
               FROM catches WHERE user_id=%s GROUP BY fish_name ORDER BY total DESC""",
            (user_id,)
        ).fetchall()

# ─── PROMO ───────────────────────────────────────────────────────────────────

def create_promo(code, coins, uses):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO promocodes (code, coins, uses_left, created_at) VALUES (%s,%s,%s,%s)
               ON CONFLICT (code) DO UPDATE SET coins=%s, uses_left=%s""",
            (code.upper(), coins, uses, datetime.now().isoformat(), coins, uses)
        )
        conn.commit()

def use_promo(user_id, code):
    with get_db() as conn:
        promo = conn.execute("SELECT * FROM promocodes WHERE code=%s", (code.upper(),)).fetchone()
        if not promo:
            return False, "❌ Промокод не найден!"
        if promo["uses_left"] <= 0:
            return False, "❌ Промокод уже полностью использован!"
        already = conn.execute("SELECT 1 FROM promo_used WHERE user_id=%s AND code=%s", (user_id, code.upper())).fetchone()
        if already:
            return False, "❌ Ты уже использовал этот промокод!"
        conn.execute("UPDATE promocodes SET uses_left=uses_left-1 WHERE code=%s", (code.upper(),))
        conn.execute("INSERT INTO promo_used (user_id, code) VALUES (%s,%s)", (user_id, code.upper()))
        conn.commit()
        return True, promo["coins"]

def list_promos():
    with get_db() as conn:
        return conn.execute("SELECT * FROM promocodes ORDER BY created_at DESC").fetchall()

def delete_promo(code):
    with get_db() as conn:
        conn.execute("DELETE FROM promocodes WHERE code=%s", (code.upper(),))
        conn.execute("DELETE FROM promo_used WHERE code=%s", (code.upper(),))
        conn.commit()

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
    last = datetime.fromisoformat(str(last_fish_str))
    diff = datetime.now() - last
    if diff >= timedelta(hours=1):
        return True, 0
    remaining = int((timedelta(hours=1) - diff).total_seconds() / 60)
    return False, remaining

def can_steal(user):
    """Проверяет кулдаун кражи. Доп. кражи игнорируют кулдаун."""
    extra = user.get("extra_steals") or 0
    if extra > 0:
        return True, 0, True   # (can, mins_left, used_extra)
    last_str = user.get("last_steal")
    if not last_str:
        return True, 0, False
    last = datetime.fromisoformat(str(last_str))
    diff = datetime.now() - last
    if diff >= STEAL_COOLDOWN:
        return True, 0, False
    remaining = int((STEAL_COOLDOWN - diff).total_seconds() / 60)
    return False, remaining, False

def is_shielded(user):
    shield_str = user.get("shield_until")
    if not shield_str:
        return False
    shield_until = datetime.fromisoformat(str(shield_str))
    return datetime.now() < shield_until

def shield_remaining_str(user):
    shield_str = user.get("shield_until")
    if not shield_str:
        return ""
    shield_until = datetime.fromisoformat(str(shield_str))
    remaining = shield_until - datetime.now()
    hours = int(remaining.total_seconds() // 3600)
    mins  = int((remaining.total_seconds() % 3600) // 60)
    return f"{hours}ч {mins}м"

# ─── CORE LOGIC ──────────────────────────────────────────────────────────────

async def do_fish(user, reply_fn):
    u = get_user(user.id, user.first_name)
    ok, mins = can_fish(u["last_fish"])
    if not ok:
        return await reply_fn(
            f"⏳ Рыба ещё не клюёт! Подожди ещё *{mins} мин.*",
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
    fish_status = "✅ Готов к рыбалке!" if ok else f"⏳ Следующий заброс через {mins} мин."

    ok2, mins2, _ = can_steal(u)
    steal_status = "✅ Можно красть!" if ok2 else f"⏳ Следующая кража через {mins2} мин."

    extra = u.get("extra_steals") or 0
    extra_text = f"\n🗡 Доп. краж: *{extra}*" if extra > 0 else ""

    shield_text = ""
    if is_shielded(u):
        shield_text = f"\n🛡 Защита активна ещё *{shield_remaining_str(u)}*"

    await reply_fn(
        f"👤 *Профиль: {user.first_name}*\n\n"
        f"🐟 Поймано рыб: *{u['fish_count']}*\n"
        f"⚖️ Общий улов: *{u['total_kg']:.1f} кг*\n"
        f"🏆 Рекорд: *{u['best_catch']:.1f} кг*\n"
        f"💰 Монет: *{u['coins']}*\n\n"
        f"🎣 Удочка: {rod_name}\n"
        f"🪱 Наживка: {bait_name}"
        f"{extra_text}"
        f"{shield_text}\n\n"
        f"{fish_status}\n"
        f"🦹 {steal_status}",
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
        name = row["username"] or "Неизвестный"
        lines.append(f"{medal} *{name}* — {row['total_kg']:.1f} кг ({row['fish_count']} рыб, рекорд: {row['best_catch']:.1f} кг)")
    await reply_fn("\n".join(lines), parse_mode="Markdown")

async def do_stats(user, reply_fn):
    rows = get_stats(user.id)
    if not rows:
        return await reply_fn("📊 Ты ещё ничего не поймал! Попробуй /fish", parse_mode="Markdown")
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
        if item["type"] == "rod":
            owned = u["rod"] == item["id"]
            label = f"{'✅ ' if owned else ''}{item['name']} — {item['price']}💰 ({item['desc']})"
        elif item["type"] == "bait":
            owned = u["bait"] == item["id"]
            label = f"{'✅ ' if owned else ''}{item['name']} — {item['price']}💰 ({item['desc']})"
        else:
            label = f"{item['name']} — {item['price']}💰 ({item['desc']})"
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
        "🦹 Ответь *спиздить* на сообщение — украсть монеты (раз в 12 часов)\n\n"
        "_Лови рыбу, зарабатывай монеты, грабь соседей!_",
        parse_mode="Markdown"
    )

# ─── STEAL ───────────────────────────────────────────────────────────────────

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
        await msg.reply_text("🤖 У бота нечего красть!")
        return

    t = get_user(thief.id, thief.first_name)
    v = get_user(victim.id, victim.first_name)

    # Проверка кулдауна
    can, mins_left, used_extra = can_steal(t)
    if not can:
        hours_left = mins_left // 60
        mins_only  = mins_left % 60
        await msg.reply_text(
            f"⏳ *{thief.first_name}*, следующая кража через *{hours_left}ч {mins_only}м*\n"
            f"Или купи 🗡 Доп. кражу в /shop",
            parse_mode="Markdown"
        )
        return

    # Проверка монет
    if t["coins"] < MIN_COINS:
        await msg.reply_text(
            f"😅 *{thief.first_name}*, у тебя меньше {MIN_COINS}💰 — нищий у нищего не крадёт!",
            parse_mode="Markdown"
        )
        return
    if v["coins"] < MIN_COINS:
        await msg.reply_text(
            f"😬 У *{victim.first_name}* всего {v['coins']}💰 — грабить нищих последнее дело!",
            parse_mode="Markdown"
        )
        return

    # Проверка щита у жертвы
    if is_shielded(v):
        shield_str = shield_remaining_str(v)
        # Если потратили доп. кражу — всё равно списываем
        if used_extra:
            update_user(thief.id, extra_steals=max(0, (t.get("extra_steals") or 1) - 1))
        await msg.reply_text(
            f"🛡 *{victim.first_name}* защищён! Щит активен ещё *{shield_str}*.\n"
            f"{'🗡 Твоя доп. кража потрачена впустую!' if used_extra else ''}",
            parse_mode="Markdown"
        )
        return

    # Списываем доп. кражу или обновляем кулдаун
    if used_extra:
        update_user(thief.id, extra_steals=max(0, (t.get("extra_steals") or 1) - 1))
    else:
        update_user(thief.id, last_steal=datetime.now().isoformat())

    # Результат
    success = random.random() < 0.5
    if success:
        stolen = max(1, int(v["coins"] * random.uniform(0.05, 0.40)))
        update_user(thief.id, coins=t["coins"] + stolen)
        update_user(victim.id, coins=v["coins"] - stolen)
        outcomes = [
            f"😈 *{thief.first_name}* мастерски обчистил карманы *{victim.first_name}*!\n\n💸 Украдено: *{stolen}💰*\n👛 Теперь у тебя: *{t['coins'] + stolen}💰*",
            f"🕵️ Операция прошла успешно!\n\n*{thief.first_name}* увёл *{stolen}💰* у *{victim.first_name}* — тот даже не заметил!",
            f"🐟 Пока *{victim.first_name}* глядел на поплавок,\n*{thief.first_name}* стащил *{stolen}💰* из его кармана! 😏",
        ]
        await msg.reply_text(random.choice(outcomes), parse_mode="Markdown")
    else:
        fine = random.randint(1, min(20, t["coins"]))
        update_user(thief.id, coins=t["coins"] - fine)
        fails = [
            f"🚨 *{thief.first_name}* попался на горячем!\n\n*{victim.first_name}* заметил кражу и вызвал рыбнадзор.\nШтраф: *{fine}💰* 😤",
            f"👮 Неудача! *{thief.first_name}* поскользнулся на рыбьей чешуе.\nШтраф: *{fine}💰*",
            f"😂 *{thief.first_name}* попытался обокрасть *{victim.first_name}*, но был пойман!\nШтраф: *{fine}💰* 🤦",
        ]
        await msg.reply_text(random.choice(fails), parse_mode="Markdown")

# ─── PROMO CMD ───────────────────────────────────────────────────────────────

async def promo_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = ctx.args
    if not args:
        await update.message.reply_text("🎁 Введи промокод: `/promo КОД`", parse_mode="Markdown")
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
        f"🎉 Промокод *{code.upper()}* активирован!\n\n💰 Начислено: *+{coins} монет*\n👛 Твой баланс: *{u['coins'] + coins}💰*",
        parse_mode="Markdown"
    )

# ─── ADMIN ───────────────────────────────────────────────────────────────────

async def admin_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Нет доступа.")
        return
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Создать промокод", callback_data="adm_create")],
        [InlineKeyboardButton("📋 Список промокодов", callback_data="adm_list")],
    ])
    await update.message.reply_text("🔧 *Панель администратора*", parse_mode="Markdown", reply_markup=keyboard)

async def admin_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Нет доступа!", show_alert=True)
        return
    await query.answer()
    data = query.data
    if data == "adm_create":
        await query.message.reply_text(
            "📝 Формат:\n\n`/newpromo КОД МОНЕТЫ АКТИВАЦИЙ`\n\nПример: `/newpromo FISH100 100 50`",
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
            lines.append(f"🎁 `{p['code']}` — {p['coins']}💰 | активаций: *{p['uses_left']}*")
            keyboard.append([InlineKeyboardButton(f"❌ Удалить {p['code']}", callback_data=f"adm_del_{p['code']}")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="adm_back")])
        await query.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("adm_del_"):
        code = data.replace("adm_del_", "")
        delete_promo(code)
        await query.message.reply_text(f"🗑 Промокод `{code}` удалён.", parse_mode="Markdown")
    elif data == "adm_back":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Создать промокод", callback_data="adm_create")],
            [InlineKeyboardButton("📋 Список промокодов", callback_data="adm_list")],
        ])
        await query.message.reply_text("🔧 *Панель администратора*", parse_mode="Markdown", reply_markup=keyboard)

async def newpromo_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Нет доступа.")
        return
    args = ctx.args
    if len(args) != 3:
        await update.message.reply_text("❌ Формат: `/newpromo КОД МОНЕТЫ АКТИВАЦИЙ`", parse_mode="Markdown")
        return
    try:
        code = args[0].upper(); coins = int(args[1]); uses = int(args[2])
        assert coins > 0 and uses > 0
    except Exception:
        await update.message.reply_text("❌ Монеты и активации должны быть положительными числами!")
        return
    create_promo(code, coins, uses)
    await update.message.reply_text(
        f"✅ Промокод создан!\n\n🎁 Код: `{code}`\n💰 Монет: *{coins}*\n🔢 Активаций: *{uses}*",
        parse_mode="Markdown"
    )

# ─── COMMAND HANDLERS ────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bot_username = (await ctx.bot.get_me()).username
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎣 Рыбачить", callback_data="cmd_fish"),
         InlineKeyboardButton("👤 Профиль",  callback_data="cmd_profile")],
        [InlineKeyboardButton("🏆 Топ",      callback_data="cmd_top"),
         InlineKeyboardButton("📊 Статистика", callback_data="cmd_stats")],
        [InlineKeyboardButton("🛒 Магазин",  callback_data="cmd_shop"),
         InlineKeyboardButton("❓ Помощь",   callback_data="cmd_help")],
        [InlineKeyboardButton("➕ Добавить бота в группу", url=f"https://t.me/{bot_username}?startgroup=true")]
    ])
    await update.message.reply_text(
        "🎣 *Добро пожаловать на Рыбалку!*\n\n"
        "Каждый час закидывай удочку /fish и лови рыбу от 3 до 20 кг! 🐟🦈\n\n"
        "🦹 В группе можно *спиздить* монеты у соседей (раз в 12 часов)!\n"
        "🛡 Купи защиту в /shop чтобы не обокрали!\n"
        "👥 Добавь бота в группу — и устроим турнир!",
        parse_mode="Markdown", reply_markup=keyboard
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

# ─── CALLBACKS ───────────────────────────────────────────────────────────────

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
    if not item or item["price"] == 0:
        await query.answer("❌ Товар не найден.", show_alert=True)
        return
    if u["coins"] < item["price"]:
        await query.answer(f"❌ Недостаточно монет! Нужно {item['price']}💰", show_alert=True)
        return

    # Расходники — не проверяем "уже куплено"
    if item["type"] == "consumable":
        if item_id == "shield":
            shield_until = (datetime.now() + SHIELD_DURATION).isoformat()
            update_user(user.id, coins=u["coins"] - item["price"], shield_until=shield_until)
            await query.edit_message_text(
                f"🛡 *Защита активирована на 12 часов!*\n\nНикто не сможет украсть твои монеты.\n💰 Списано: {item['price']}💰",
                parse_mode="Markdown"
            )
        elif item_id == "steal_extra":
            extra = (u.get("extra_steals") or 0) + 1
            update_user(user.id, coins=u["coins"] - item["price"], extra_steals=extra)
            await query.edit_message_text(
                f"🗡 *Доп. кража куплена!*\n\nТеперь у тебя *{extra}* внеплановых попыток кражи.\n💰 Списано: {item['price']}💰",
                parse_mode="Markdown"
            )
        return

    # Обычные предметы
    if u["rod"] == item_id or u["bait"] == item_id:
        await query.answer("✅ У тебя уже есть этот предмет!", show_alert=True)
        return
    if item["type"] == "rod":
        update_user(user.id, rod=item_id, coins=u["coins"] - item["price"])
    else:
        update_user(user.id, bait=item_id, coins=u["coins"] - item["price"])
    await query.edit_message_text(
        f"✅ *Куплено: {item['name']}!*\n{item['desc']}",
        parse_mode="Markdown"
    )

# ─── MAIN ─────────────────────────────────────────────────────────────────────

async def set_commands(app):
    private_cmds = [
        BotCommand("start",    "🎣 Главное меню"),
        BotCommand("fish",     "🎣 Закинуть удочку"),
        BotCommand("profile",  "👤 Мой профиль"),
        BotCommand("top",      "🏆 Топ рыбаков"),
        BotCommand("stats",    "📊 Статистика улова"),
        BotCommand("shop",     "🛒 Магазин снаряжения"),
        BotCommand("promo",    "🎁 Активировать промокод"),
        BotCommand("help",     "❓ Все команды"),
        BotCommand("admin",    "🔧 Панель админа"),
    ]
    group_cmds = [
        BotCommand("fish",     "🎣 Закинуть удочку"),
        BotCommand("profile",  "👤 Показать профиль"),
        BotCommand("top",      "🏆 Топ рыбаков"),
        BotCommand("stats",    "📊 Статистика улова"),
        BotCommand("shop",     "🛒 Магазин с бонусами"),
        BotCommand("help",     "💡 Все команды"),
    ]
    await app.bot.set_my_commands(private_cmds, scope=BotCommandScopeAllPrivateChats())
    await app.bot.set_my_commands(group_cmds,   scope=BotCommandScopeAllGroupChats())

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
