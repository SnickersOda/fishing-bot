import logging
import random
import os
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, \
    BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats, LabeledPrice
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
    CallbackQueryHandler, MessageHandler, PreCheckoutQueryHandler, filters
)
import psycopg

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

# ─── РЫБЫ ────────────────────────────────────────────────────────────────────

FISH_TYPES = [
    # (название, мин, макс, редкость_шанс)  шанс 1.0 = обычная, 0.05 = 5%
    ("🐟 Карась",        3,   6,  1.0),
    ("🐠 Окунь",         4,   8,  1.0),
    ("🐡 Лещ",           5,   9,  1.0),
    ("🦈 Щука",          7,  13,  0.8),
    ("🐬 Сом",          10,  16,  0.6),
    ("🐳 Карп",         12,  18,  0.5),
    ("🦑 Судак",        14,  20,  0.4),
    # Редкие
    ("🌟 Золотая рыбка", 20,  30,  0.05),
    ("🦈 Акула-молот",   25,  40,  0.03),
    ("👾 Кракен",        40,  60,  0.01),
]

SHOP_ITEMS = [
    {"id": "rod_basic",   "name": "🎣 Обычная удочка",  "price": 0,   "bonus": 0, "desc": "Стартовая удочка",          "type": "rod"},
    {"id": "rod_medium",  "name": "🎣 Хорошая удочка",  "price": 50,  "bonus": 2, "desc": "+2 кг к улову",             "type": "rod"},
    {"id": "rod_pro",     "name": "🎣 Проф. удочка",    "price": 150, "bonus": 5, "desc": "+5 кг к улову",             "type": "rod"},
    {"id": "bait_basic",  "name": "🪱 Обычная наживка", "price": 10,  "bonus": 1, "desc": "+1 кг к улову",             "type": "bait"},
    {"id": "bait_good",   "name": "🦗 Хорошая наживка", "price": 30,  "bonus": 3, "desc": "+3 кг к улову",             "type": "bait"},
    {"id": "shield",      "name": "🛡 Защита от кражи", "price": 150, "bonus": 0, "desc": "Защита на 12 часов",        "type": "consumable"},
    {"id": "steal_extra", "name": "🗡 Доп. кража",      "price": 200, "bonus": 0, "desc": "Внеплановая попытка кражи", "type": "consumable"},
]

# Звёздные пакеты (XTR = Telegram Stars)
STAR_PACKAGES = [
    {"id": "stars_100",  "stars": 50,  "coins": 300,  "title": "Мешок монет",     "desc": "300 монет на рыбалку"},
    {"id": "stars_250",  "stars": 100, "coins": 800,  "title": "Сундук монет",    "desc": "800 монет — выгоднее!"},
    {"id": "stars_vip",  "stars": 200, "coins": 0,    "title": "⭐ VIP на 7 дней", "desc": "x2 монеты с улова 7 дней"},
]

TEXT_TRIGGERS = {
    "fish":    ["рыбалка", "рыбачить", "закинуть", "поймать рыбу"],
    "profile": ["профиль", "мой профиль", "стата", "статус"],
    "top":     ["топ", "рейтинг", "лучшие рыбаки"],
    "stats":   ["статистика", "мой улов", "моя статистика"],
    "shop":    ["магазин", "купить", "снаряжение"],
    "help":    ["помощь", "команды"],
}

STEAL_TRIGGERS  = ["спиздить", "украсть", "кража", "стырить", "свиснуть", "спереть"]
MIN_COINS       = 15
STEAL_COOLDOWN  = timedelta(hours=12)
SHIELD_DURATION = timedelta(hours=12)
VIP_DURATION    = timedelta(days=7)

# ─── ДОСТИЖЕНИЯ ──────────────────────────────────────────────────────────────

ACHIEVEMENTS = [
    {"id": "first_fish",   "name": "🐟 Первый улов",      "desc": "Поймать первую рыбу",         "check": lambda u, _: u["fish_count"] >= 1},
    {"id": "fish_10",      "name": "🎣 Рыбак",            "desc": "Поймать 10 рыб",              "check": lambda u, _: u["fish_count"] >= 10},
    {"id": "fish_50",      "name": "🏆 Мастер рыбалки",   "desc": "Поймать 50 рыб",              "check": lambda u, _: u["fish_count"] >= 50},
    {"id": "fish_100",     "name": "👑 Легенда реки",     "desc": "Поймать 100 рыб",             "check": lambda u, _: u["fish_count"] >= 100},
    {"id": "kg_100",       "name": "⚖️ Центнер",          "desc": "Поймать 100 кг суммарно",     "check": lambda u, _: u["total_kg"] >= 100},
    {"id": "kg_500",       "name": "⚖️ Полтонны",         "desc": "Поймать 500 кг суммарно",     "check": lambda u, _: u["total_kg"] >= 500},
    {"id": "rich",         "name": "💰 Богач",             "desc": "Накопить 500 монет",           "check": lambda u, _: u["coins"] >= 500},
    {"id": "thief",        "name": "🕵️ Вор",              "desc": "Украсть монеты 3 раза",       "check": lambda u, e: e.get("steals_success", 0) >= 3},
    {"id": "rare_fish",    "name": "🌟 Редкость",          "desc": "Поймать редкую рыбу",         "check": lambda u, e: e.get("rare_caught", 0) >= 1},
    {"id": "record_20",    "name": "📏 Крупняк",           "desc": "Поймать рыбу тяжелее 20 кг",  "check": lambda u, _: u["best_catch"] >= 20},
    {"id": "record_40",    "name": "🦈 Монстр",            "desc": "Поймать рыбу тяжелее 40 кг",  "check": lambda u, _: u["best_catch"] >= 40},
]

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
                extra_steals    INTEGER DEFAULT 0,
                last_daily      TEXT    DEFAULT NULL,
                daily_streak    INTEGER DEFAULT 0,
                vip_until       TEXT    DEFAULT NULL,
                steals_success  INTEGER DEFAULT 0,
                rare_caught     INTEGER DEFAULT 0
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS achievements (
                user_id BIGINT,
                ach_id  TEXT,
                earned_at TEXT,
                PRIMARY KEY (user_id, ach_id)
            )
        """)
        for col, definition in [
            ("last_steal",     "TEXT DEFAULT NULL"),
            ("shield_until",   "TEXT DEFAULT NULL"),
            ("extra_steals",   "INTEGER DEFAULT 0"),
            ("last_daily",     "TEXT DEFAULT NULL"),
            ("daily_streak",   "INTEGER DEFAULT 0"),
            ("vip_until",      "TEXT DEFAULT NULL"),
            ("steals_success", "INTEGER DEFAULT 0"),
            ("rare_caught",    "INTEGER DEFAULT 0"),
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

# ─── ACHIEVEMENTS DB ─────────────────────────────────────────────────────────

def get_user_achievements(user_id):
    with get_db() as conn:
        rows = conn.execute("SELECT ach_id FROM achievements WHERE user_id=%s", (user_id,)).fetchall()
        return {r["ach_id"] for r in rows}

def grant_achievement(user_id, ach_id):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO achievements (user_id, ach_id, earned_at) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            (user_id, ach_id, datetime.now().isoformat())
        )
        conn.commit()

async def check_achievements(user_id, update_obj):
    u = get_user(user_id, "")
    earned = get_user_achievements(user_id)
    extra = {"steals_success": u.get("steals_success") or 0, "rare_caught": u.get("rare_caught") or 0}
    new_achs = []
    for ach in ACHIEVEMENTS:
        if ach["id"] not in earned and ach["check"](u, extra):
            grant_achievement(user_id, ach["id"])
            new_achs.append(ach)
    if new_achs and update_obj:
        for ach in new_achs:
            await update_obj.reply_text(
                f"🏅 *Новое достижение: {ach['name']}!*\n_{ach['desc']}_",
                parse_mode="Markdown"
            )

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

def is_vip(user):
    vip_str = user.get("vip_until")
    if not vip_str:
        return False
    return datetime.now() < datetime.fromisoformat(str(vip_str))

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
    extra = user.get("extra_steals") or 0
    if extra > 0:
        return True, 0, True
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
    return datetime.now() < datetime.fromisoformat(str(shield_str))

def shield_remaining_str(user):
    shield_str = user.get("shield_until")
    if not shield_str:
        return ""
    remaining = datetime.fromisoformat(str(shield_str)) - datetime.now()
    h = int(remaining.total_seconds() // 3600)
    m = int((remaining.total_seconds() % 3600) // 60)
    return f"{h}ч {m}м"

def can_daily(user):
    last_str = user.get("last_daily")
    if not last_str:
        return True, 0
    last = datetime.fromisoformat(str(last_str))
    diff = datetime.now() - last
    if diff >= timedelta(hours=24):
        return True, 0
    remaining = int((timedelta(hours=24) - diff).total_seconds() / 3600)
    remaining_m = int(((timedelta(hours=24) - diff).total_seconds() % 3600) / 60)
    return False, f"{remaining}ч {remaining_m}м"

def pick_fish():
    """Выбирает рыбу с учётом редкости"""
    pool = []
    for f in FISH_TYPES:
        weight = int(f[3] * 100)
        pool.extend([f] * weight)
    return random.choice(pool)

# ─── CORE LOGIC ──────────────────────────────────────────────────────────────

async def do_fish(user, reply_fn, message_obj=None):
    u = get_user(user.id, user.first_name)
    ok, mins = can_fish(u["last_fish"])
    if not ok:
        return await reply_fn(f"⏳ Рыба ещё не клюёт! Подожди ещё *{mins} мин.*", parse_mode="Markdown")

    fish_data = pick_fish()
    fish_name, w_min, w_max, rarity = fish_data
    is_rare = rarity <= 0.05
    weight = round(random.uniform(w_min, w_max) + get_rod_bonus(u["rod"]) + get_bait_bonus(u["bait"]), 2)
    weight = min(weight, 65.0)
    coins_earned = int(weight * 2)
    if is_vip(u):
        coins_earned *= 2  # VIP бонус x2
    is_best = weight > u["best_catch"]

    updates = dict(
        total_kg   = u["total_kg"] + weight,
        fish_count = u["fish_count"] + 1,
        best_catch = max(u["best_catch"], weight),
        coins      = u["coins"] + coins_earned,
        last_fish  = datetime.now().isoformat()
    )
    if is_rare:
        updates["rare_caught"] = (u.get("rare_caught") or 0) + 1
    update_user(user.id, **updates)
    add_catch(user.id, fish_name, weight)

    best_text = "\n🏆 *Новый личный рекорд!*" if is_best else ""
    rare_text  = "\n✨ *РЕДКАЯ РЫБА!*" if is_rare else ""
    vip_text   = "\n⭐ *VIP бонус x2!*" if is_vip(u) else ""
    emoji = "🌟" if is_rare else ("🏆" if is_best else "🎣")
    await reply_fn(
        f"{emoji} *{user.first_name} поймал рыбу!*\n\n"
        f"🐟 Вид: {fish_name}\n"
        f"⚖️ Вес: *{weight} кг*\n"
        f"💰 Монет получено: +{coins_earned}"
        f"{best_text}{rare_text}{vip_text}",
        parse_mode="Markdown"
    )
    if message_obj:
        await check_achievements(user.id, message_obj)

async def do_profile(user, reply_fn):
    u = get_user(user.id, user.first_name)
    rod_name  = next((i["name"] for i in SHOP_ITEMS if i["id"] == u["rod"]), "Обычная удочка")
    bait_name = next((i["name"] for i in SHOP_ITEMS if i["id"] == u["bait"]), "Без наживки") if u["bait"] else "Без наживки"
    ok, mins = can_fish(u["last_fish"])
    fish_status = "✅ Готов к рыбалке!" if ok else f"⏳ Следующий заброс через {mins} мин."
    ok2, mins2, _ = can_steal(u)
    steal_status = "✅ Можно красть!" if ok2 else f"⏳ Следующая кража через {mins2} мин."
    extra_text = f"\n🗡 Доп. краж: *{u.get('extra_steals') or 0}*" if (u.get("extra_steals") or 0) > 0 else ""
    shield_text = f"\n🛡 Защита активна ещё *{shield_remaining_str(u)}*" if is_shielded(u) else ""
    vip_text = ""
    if is_vip(u):
        vip_until = datetime.fromisoformat(str(u["vip_until"]))
        days_left = (vip_until - datetime.now()).days
        vip_text = f"\n⭐ *VIP* — ещё {days_left} дн. (x2 монеты)"
    streak = u.get("daily_streak") or 0
    streak_text = f"\n🔥 Стрик: *{streak} дней*" if streak > 1 else ""

    earned = get_user_achievements(user.id)
    achs_text = ""
    if earned:
        icons = [a["name"].split()[0] for a in ACHIEVEMENTS if a["id"] in earned]
        achs_text = f"\n\n🏅 Достижения: {' '.join(icons)}"

    await reply_fn(
        f"👤 *Профиль: {user.first_name}*\n\n"
        f"🐟 Поймано рыб: *{u['fish_count']}*\n"
        f"⚖️ Общий улов: *{u['total_kg']:.1f} кг*\n"
        f"🏆 Рекорд: *{u['best_catch']:.1f} кг*\n"
        f"💰 Монет: *{u['coins']}*"
        f"{vip_text}\n\n"
        f"🎣 Удочка: {rod_name}\n"
        f"🪱 Наживка: {bait_name}"
        f"{extra_text}{shield_text}{streak_text}"
        f"{achs_text}\n\n"
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
    keyboard.append([InlineKeyboardButton("⭐ Купить за звёзды Telegram", callback_data="show_stars")])
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
        "📅 /daily — Ежедневный бонус\n"
        "🏅 /achievements — Все достижения\n"
        "🎁 /promo [код] — Активировать промокод\n"
        "🦹 Ответь *спиздить* на сообщение — украсть монеты (раз в 12 часов)\n\n"
        "_Лови рыбу, зарабатывай монеты, грабь соседей!_",
        parse_mode="Markdown"
    )

# ─── DAILY BONUS ─────────────────────────────────────────────────────────────

async def daily_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = get_user(user.id, user.first_name)
    ok, time_left = can_daily(u)

    if not ok:
        await update.message.reply_text(
            f"⏳ Следующий бонус через *{time_left}*",
            parse_mode="Markdown"
        )
        return

    # Стрик
    streak = u.get("daily_streak") or 0
    last_str = u.get("last_daily")
    if last_str:
        last = datetime.fromisoformat(str(last_str))
        if datetime.now() - last < timedelta(hours=48):
            streak += 1
        else:
            streak = 1
    else:
        streak = 1

    # Таблица наград по дням
    day_rewards = [
        (1,  20,  "🪙"),
        (2,  25,  "🪙"),
        (3,  30,  "🥉"),
        (4,  40,  "🥉"),
        (5,  50,  "🥈"),
        (6,  65,  "🥈"),
        (7,  100, "🥇"),
    ]
    day_index = min(streak - 1, len(day_rewards) - 1)
    _, coins_reward, _ = day_rewards[day_index]

    # Бонус за стрик 7+ дней
    if streak >= 7:
        coins_reward = 100 + (streak - 7) * 10

    update_user(user.id,
        coins       = u["coins"] + coins_reward,
        last_daily  = datetime.now().isoformat(),
        daily_streak= streak
    )

    # Строим таблицу кнопок
    keyboard = []
    row = []
    for i, (day, reward, icon) in enumerate(day_rewards):
        current = (i == day_index)
        done    = (i < day_index) or (streak > 7 and i == 6)
        if done:
            label = f"✅ День {day}"
        elif current:
            label = f"🎁 День {day}: +{reward}💰"
        else:
            label = f"🔒 День {day}"
        row.append(InlineKeyboardButton(label, callback_data="daily_noop"))
        if len(row) == 2 or i == len(day_rewards) - 1:
            keyboard.append(row)
            row = []

    if streak >= 7:
        keyboard.append([InlineKeyboardButton(f"🔥 Стрик {streak} дней: +{coins_reward}💰", callback_data="daily_noop")])

    await update.message.reply_text(
        f"📅 *Ежедневный бонус*\n\n"
        f"🔥 Стрик: *{streak} {'день' if streak == 1 else 'дней'}*\n"
        f"💰 Получено: *+{coins_reward} монет*\n\n"
        f"Заходи каждый день чтобы не потерять стрик!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    await check_achievements(user.id, update.message)

# ─── ACHIEVEMENTS CMD ────────────────────────────────────────────────────────

async def achievements_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    earned = get_user_achievements(user.id)
    lines = [f"🏅 *Достижения — {user.first_name}*\n"]
    for ach in ACHIEVEMENTS:
        icon = "✅" if ach["id"] in earned else "🔒"
        lines.append(f"{icon} {ach['name']} — _{ach['desc']}_")
    lines.append(f"\n*{len(earned)}/{len(ACHIEVEMENTS)}* получено")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ─── STEAL ───────────────────────────────────────────────────────────────────

async def do_steal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    thief = update.effective_user
    if not msg.reply_to_message:
        await msg.reply_text("🕵️ Чтобы украсть — *ответь* на сообщение жертвы и напиши *спиздить*", parse_mode="Markdown")
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
    can, mins_left, used_extra = can_steal(t)
    if not can:
        h = mins_left // 60; m = mins_left % 60
        await msg.reply_text(f"⏳ *{thief.first_name}*, следующая кража через *{h}ч {m}м*\nИли купи 🗡 Доп. кражу в /shop", parse_mode="Markdown")
        return
    if t["coins"] < MIN_COINS:
        await msg.reply_text(f"😅 *{thief.first_name}*, у тебя меньше {MIN_COINS}💰 — нищий у нищего не крадёт!", parse_mode="Markdown")
        return
    if v["coins"] < MIN_COINS:
        await msg.reply_text(f"😬 У *{victim.first_name}* всего {v['coins']}💰 — грабить нищих последнее дело!", parse_mode="Markdown")
        return
    if is_shielded(v):
        if used_extra:
            update_user(thief.id, extra_steals=max(0, (t.get("extra_steals") or 1) - 1))
        await msg.reply_text(f"🛡 *{victim.first_name}* защищён! Щит активен ещё *{shield_remaining_str(v)}*.\n{'🗡 Доп. кража потрачена!' if used_extra else ''}", parse_mode="Markdown")
        return
    if used_extra:
        update_user(thief.id, extra_steals=max(0, (t.get("extra_steals") or 1) - 1))
    else:
        update_user(thief.id, last_steal=datetime.now().isoformat())
    success = random.random() < 0.5
    if success:
        stolen = max(1, int(v["coins"] * random.uniform(0.05, 0.40)))
        update_user(thief.id, coins=t["coins"] + stolen, steals_success=(t.get("steals_success") or 0) + 1)
        update_user(victim.id, coins=v["coins"] - stolen)
        outcomes = [
            f"😈 *{thief.first_name}* мастерски обчистил карманы *{victim.first_name}*!\n\n💸 Украдено: *{stolen}💰*\n👛 Теперь у тебя: *{t['coins'] + stolen}💰*",
            f"🕵️ Операция прошла успешно!\n\n*{thief.first_name}* увёл *{stolen}💰* у *{victim.first_name}* — тот даже не заметил!",
            f"🐟 Пока *{victim.first_name}* глядел на поплавок,\n*{thief.first_name}* стащил *{stolen}💰* из его кармана! 😏",
        ]
        await msg.reply_text(random.choice(outcomes), parse_mode="Markdown")
        await check_achievements(thief.id, msg)
    else:
        fine = random.randint(1, min(20, t["coins"]))
        update_user(thief.id, coins=t["coins"] - fine)
        fails = [
            f"🚨 *{thief.first_name}* попался на горячем!\n*{victim.first_name}* заметил и вызвал рыбнадзор.\nШтраф: *{fine}💰* 😤",
            f"👮 Неудача! *{thief.first_name}* поскользнулся на рыбьей чешуе.\nШтраф: *{fine}💰*",
            f"😂 *{thief.first_name}* попытался обокрасть *{victim.first_name}*, но пойман!\nШтраф: *{fine}💰* 🤦",
        ]
        await msg.reply_text(random.choice(fails), parse_mode="Markdown")

# ─── STARS PAYMENTS ──────────────────────────────────────────────────────────

async def stars_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    for pkg in STAR_PACKAGES:
        keyboard.append([InlineKeyboardButton(
            f"⭐ {pkg['stars']} звёзд — {pkg['title']}",
            callback_data=f"stars_{pkg['id']}"
        )])
    await update.message.reply_text(
        "⭐ *Купить за звёзды Telegram*\n\n"
        "🪙 50 звёзд → *300 монет*\n"
        "💰 100 звёзд → *800 монет*\n"
        "👑 200 звёзд → *VIP на 7 дней* (x2 монеты)\n\n"
        "_Звёзды можно купить в Telegram_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def stars_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pkg_id = query.data.replace("stars_", "")
    pkg = next((p for p in STAR_PACKAGES if p["id"] == pkg_id), None)
    if not pkg:
        return
    await ctx.bot.send_invoice(
        chat_id    = query.from_user.id,
        title      = pkg["title"],
        description= pkg["desc"],
        payload    = f"stars_{pkg_id}_{query.from_user.id}",
        currency   = "XTR",
        prices     = [LabeledPrice(label=pkg["title"], amount=pkg["stars"])],
        provider_token=""
    )

async def precheckout_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    payload = payment.invoice_payload  # stars_stars_100_12345
    parts = payload.split("_")
    pkg_id = f"{parts[1]}_{parts[2]}"
    pkg = next((p for p in STAR_PACKAGES if p["id"] == pkg_id), None)
    if not pkg:
        await update.message.reply_text("✅ Оплата получена! Напиши в поддержку.")
        return
    user = update.effective_user
    u = get_user(user.id, user.first_name)
    if pkg_id == "stars_vip":
        vip_until = (datetime.now() + VIP_DURATION).isoformat()
        update_user(user.id, vip_until=vip_until)
        await update.message.reply_text(
            f"⭐ *VIP активирован на 7 дней!*\n\nТеперь ты получаешь x2 монеты с каждого улова!\n👛 Баланс: *{u['coins']}💰*",
            parse_mode="Markdown"
        )
    else:
        update_user(user.id, coins=u["coins"] + pkg["coins"])
        await update.message.reply_text(
            f"⭐ *Оплата прошла!*\n\n💰 Начислено: *+{pkg['coins']} монет*\n👛 Баланс: *{u['coins'] + pkg['coins']}💰*",
            parse_mode="Markdown"
        )

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
        f"🎉 Промокод *{code.upper()}* активирован!\n\n💰 Начислено: *+{coins} монет*\n👛 Баланс: *{u['coins'] + coins}💰*",
        parse_mode="Markdown"
    )

# ─── ADMIN DB ────────────────────────────────────────────────────────────────

def get_all_users(limit=20, offset=0):
    with get_db() as conn:
        return conn.execute(
            "SELECT user_id, username, coins, fish_count, total_kg FROM users ORDER BY total_kg DESC LIMIT %s OFFSET %s",
            (limit, offset)
        ).fetchall()

def get_all_user_ids():
    with get_db() as conn:
        return [r["user_id"] for r in conn.execute("SELECT user_id FROM users").fetchall()]

def get_bot_stats():
    with get_db() as conn:
        stats = conn.execute("""
            SELECT
                COUNT(*) as total_users,
                SUM(fish_count) as total_fish,
                SUM(total_kg) as total_kg,
                SUM(coins) as total_coins,
                MAX(best_catch) as best_catch,
                (SELECT username FROM users ORDER BY best_catch DESC LIMIT 1) as best_user
            FROM users
        """).fetchone()
        return dict(stats)

def get_user_by_username(username):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE LOWER(username)=LOWER(%s)", (username,)
        ).fetchone()
        return dict(row) if row else None

# ─── ADMIN ───────────────────────────────────────────────────────────────────

def admin_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Статистика бота",    callback_data="adm_stats")],
        [InlineKeyboardButton("👥 Список игроков",     callback_data="adm_players_0")],
        [InlineKeyboardButton("➕ Создать промокод",   callback_data="adm_create")],
        [InlineKeyboardButton("📋 Список промокодов",  callback_data="adm_list")],
        [InlineKeyboardButton("💰 Выдать монеты",      callback_data="adm_give"),
         InlineKeyboardButton("💸 Забрать монеты",     callback_data="adm_take")],
        [InlineKeyboardButton("📢 Рассылка",           callback_data="adm_broadcast")],
    ])

async def admin_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Нет доступа.")
        return
    await update.message.reply_text(
        "🔧 *Панель администратора*",
        parse_mode="Markdown",
        reply_markup=admin_main_keyboard()
    )

async def admin_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Нет доступа!", show_alert=True)
        return
    await query.answer()
    data = query.data

    # ── Статистика ──
    if data == "adm_stats":
        s = get_bot_stats()
        await query.message.reply_text(
            f"📊 *Статистика бота*\n\n"
            f"👥 Игроков: *{s['total_users']}*\n"
            f"🐟 Поймано рыб: *{s['total_fish'] or 0}*\n"
            f"⚖️ Общий улов: *{(s['total_kg'] or 0):.1f} кг*\n"
            f"💰 Монет в обороте: *{s['total_coins'] or 0}*\n"
            f"🏆 Рекорд: *{(s['best_catch'] or 0):.1f} кг* ({s['best_user'] or '?'})",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="adm_back")]])
        )

    # ── Список игроков ──
    elif data.startswith("adm_players_"):
        offset = int(data.replace("adm_players_", ""))
        players = get_all_users(limit=10, offset=offset)
        if not players:
            await query.message.reply_text("👥 Игроков пока нет.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="adm_back")]]))
            return
        lines = [f"👥 *Игроки* (#{offset+1}–{offset+len(players)})\n"]
        for p in players:
            name = p["username"] or "Неизвестный"
            lines.append(f"• *{name}* — {p['coins']}💰 | {p['fish_count']} рыб | {p['total_kg']:.1f} кг")
        nav = []
        if offset > 0:
            nav.append(InlineKeyboardButton("◀️ Назад", callback_data=f"adm_players_{offset-10}"))
        if len(players) == 10:
            nav.append(InlineKeyboardButton("▶️ Вперёд", callback_data=f"adm_players_{offset+10}"))
        keyboard = []
        if nav:
            keyboard.append(nav)
        keyboard.append([InlineKeyboardButton("🔙 В меню", callback_data="adm_back")])
        await query.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    # ── Промокоды ──
    elif data == "adm_create":
        await query.message.reply_text(
            "📝 Формат:\n\n`/newpromo КОД МОНЕТЫ АКТИВАЦИЙ`\n\nПример: `/newpromo FISH100 100 50`",
            parse_mode="Markdown"
        )
    elif data == "adm_list":
        promos = list_promos()
        if not promos:
            await query.message.reply_text("📋 Промокодов пока нет.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="adm_back")]]))
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

    # ── Выдать/забрать монеты ──
    elif data == "adm_give":
        ctx.user_data["adm_action"] = "give"
        await query.message.reply_text(
            "💰 *Выдать монеты*\n\nФормат:\n`/givecoins ИМЯ_ПОЛЬЗОВАТЕЛЯ КОЛИЧЕСТВО`\n\nПример: `/givecoins Пидиди 500`",
            parse_mode="Markdown"
        )
    elif data == "adm_take":
        ctx.user_data["adm_action"] = "take"
        await query.message.reply_text(
            "💸 *Забрать монеты*\n\nФормат:\n`/takecoins ИМЯ_ПОЛЬЗОВАТЕЛЯ КОЛИЧЕСТВО`\n\nПример: `/takecoins Пидиди 100`",
            parse_mode="Markdown"
        )

    # ── Рассылка ──
    elif data == "adm_broadcast":
        ctx.user_data["adm_action"] = "broadcast"
        await query.message.reply_text(
            "📢 *Рассылка всем игрокам*\n\nОтправь команду:\n`/broadcast ТЕКСТ СООБЩЕНИЯ`\n\nПример:\n`/broadcast 🎉 Новое обновление бота! Теперь есть редкие рыбы!`",
            parse_mode="Markdown"
        )

    elif data == "adm_back":
        await query.message.reply_text(
            "🔧 *Панель администратора*",
            parse_mode="Markdown",
            reply_markup=admin_main_keyboard()
        )

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
    await update.message.reply_text(f"✅ Промокод создан!\n\n🎁 Код: `{code}`\n💰 Монет: *{coins}*\n🔢 Активаций: *{uses}*", parse_mode="Markdown")

async def givecoins_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Нет доступа.")
        return
    args = ctx.args
    if len(args) != 2:
        await update.message.reply_text("❌ Формат: `/givecoins ИМЯ КОЛИЧЕСТВО`", parse_mode="Markdown")
        return
    try:
        username = args[0]; amount = int(args[1]); assert amount != 0
    except Exception:
        await update.message.reply_text("❌ Количество должно быть числом!")
        return
    target = get_user_by_username(username)
    if not target:
        await update.message.reply_text(f"❌ Игрок *{username}* не найден!", parse_mode="Markdown")
        return
    new_coins = max(0, target["coins"] + amount)
    update_user(target["user_id"], coins=new_coins)
    sign = "+" if amount > 0 else ""
    await update.message.reply_text(
        f"✅ *{username}*: {sign}{amount}💰\nНовый баланс: *{new_coins}💰*",
        parse_mode="Markdown"
    )

async def takecoins_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Нет доступа.")
        return
    args = ctx.args
    if len(args) != 2:
        await update.message.reply_text("❌ Формат: `/takecoins ИМЯ КОЛИЧЕСТВО`", parse_mode="Markdown")
        return
    try:
        username = args[0]; amount = int(args[1]); assert amount > 0
    except Exception:
        await update.message.reply_text("❌ Количество должно быть положительным числом!")
        return
    target = get_user_by_username(username)
    if not target:
        await update.message.reply_text(f"❌ Игрок *{username}* не найден!", parse_mode="Markdown")
        return
    new_coins = max(0, target["coins"] - amount)
    update_user(target["user_id"], coins=new_coins)
    await update.message.reply_text(
        f"✅ Забрано *{amount}💰* у *{username}*\nНовый баланс: *{new_coins}💰*",
        parse_mode="Markdown"
    )

async def broadcast_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Нет доступа.")
        return
    if not ctx.args:
        await update.message.reply_text("❌ Формат: `/broadcast ТЕКСТ`", parse_mode="Markdown")
        return
    text = " ".join(ctx.args)
    user_ids = get_all_user_ids()
    sent = 0; failed = 0
    status_msg = await update.message.reply_text(f"📢 Рассылка... 0/{len(user_ids)}")
    for i, uid in enumerate(user_ids):
        try:
            await ctx.bot.send_message(chat_id=uid, text=f"📢 *Сообщение от администратора:*\n\n{text}", parse_mode="Markdown")
            sent += 1
        except Exception:
            failed += 1
        if (i + 1) % 10 == 0:
            try:
                await status_msg.edit_text(f"📢 Рассылка... {i+1}/{len(user_ids)}")
            except Exception:
                pass
    await status_msg.edit_text(
        f"✅ *Рассылка завершена!*\n\n"
        f"📨 Отправлено: *{sent}*\n"
        f"❌ Не доставлено: *{failed}*",
        parse_mode="Markdown"
    )

# ─── COMMAND HANDLERS ────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bot_username = (await ctx.bot.get_me()).username
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎣 Рыбачить",   callback_data="cmd_fish"),
         InlineKeyboardButton("👤 Профиль",    callback_data="cmd_profile")],
        [InlineKeyboardButton("🏆 Топ",        callback_data="cmd_top"),
         InlineKeyboardButton("📊 Статистика", callback_data="cmd_stats")],
        [InlineKeyboardButton("🛒 Магазин",    callback_data="cmd_shop"),
         InlineKeyboardButton("📅 Бонус",      callback_data="cmd_daily")],
        [InlineKeyboardButton("🏅 Достижения", callback_data="cmd_achievements"),
         InlineKeyboardButton("❓ Помощь",     callback_data="cmd_help")],
        [InlineKeyboardButton("➕ Добавить бота в группу", url=f"https://t.me/{bot_username}?startgroup=true")]
    ])
    await update.message.reply_text(
        "🎣 *Добро пожаловать на Рыбалку!*\n\n"
        "Каждый час закидывай удочку /fish и лови рыбу от 3 до 60 кг! 🐟🦈👾\n\n"
        "🌟 Есть редкие рыбы — Золотая рыбка, Акула-молот и Кракен!\n"
        "🦹 В группе можно *спиздить* монеты у соседей!\n"
        "📅 Заходи каждый день за бонусом!\n"
        "👥 Добавь бота в группу — и устроим турнир!",
        parse_mode="Markdown", reply_markup=keyboard
    )

async def fish_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await do_fish(update.effective_user, update.message.reply_text, update.message)

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
    has_reply = bool(update.message.reply_to_message)
    logger.info(f"MSG: '{text}' | reply={has_reply} | user={user.first_name} | chat={update.effective_chat.type}")
    if any(t in text for t in STEAL_TRIGGERS):
        await do_steal(update, ctx)
        return
    for cmd, triggers in TEXT_TRIGGERS.items():
        if any(t in text for t in triggers):
            if cmd == "fish":       await do_fish(user, update.message.reply_text, update.message)
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
    if cmd == "fish":             await do_fish(user, reply, query.message)
    elif cmd == "profile":        await do_profile(user, reply)
    elif cmd == "top":            await do_top(reply)
    elif cmd == "stats":          await do_stats(user, reply)
    elif cmd == "shop":           await do_shop(user, reply)
    elif cmd == "help":           await do_help(reply)
    elif cmd == "daily":
        update.message = query.message
        update._effective_user = user
        await daily_cmd(update, ctx)
    elif cmd == "achievements":
        update.message = query.message
        update._effective_user = user
        await achievements_cmd(update, ctx)

async def shop_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "show_stars":
        keyboard = []
        for pkg in STAR_PACKAGES:
            keyboard.append([InlineKeyboardButton(f"⭐ {pkg['stars']} звёзд — {pkg['title']}", callback_data=f"stars_{pkg['id']}")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_shop")])
        await query.edit_message_text(
            "⭐ *Купить за звёзды Telegram*\n\n"
            "🪙 50 звёзд → *300 монет*\n"
            "💰 100 звёзд → *800 монет*\n"
            "👑 200 звёзд → *VIP на 7 дней* (x2 монеты)\n\n"
            "_Звёзды можно купить в настройках Telegram_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data.startswith("stars_"):
        pkg_id = data.replace("stars_", "")
        pkg = next((p for p in STAR_PACKAGES if p["id"] == pkg_id), None)
        if not pkg:
            return
        await ctx.bot.send_invoice(
            chat_id     = query.from_user.id,
            title       = pkg["title"],
            description = pkg["desc"],
            payload     = f"stars_{pkg_id}_{query.from_user.id}",
            currency    = "XTR",
            prices      = [LabeledPrice(label=pkg["title"], amount=pkg["stars"])],
            provider_token=""
        )
        return

    if data == "back_shop":
        user = query.from_user
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
        keyboard.append([InlineKeyboardButton("⭐ Купить за звёзды Telegram", callback_data="show_stars")])
        await query.edit_message_text(
            f"🛒 *Магазин*\n\nТвои монеты: *{u['coins']}* 💰\n\nВыбери товар:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data == "daily_noop":
        return

    item_id = data.replace("buy_", "")
    user = query.from_user
    u = get_user(user.id, user.first_name)
    item = next((i for i in SHOP_ITEMS if i["id"] == item_id), None)
    if not item or item["price"] == 0:
        await query.answer("❌ Товар не найден.", show_alert=True)
        return
    if u["coins"] < item["price"]:
        await query.answer(f"❌ Недостаточно монет! Нужно {item['price']}💰", show_alert=True)
        return
    if item["type"] == "consumable":
        if item_id == "shield":
            shield_until = (datetime.now() + SHIELD_DURATION).isoformat()
            update_user(user.id, coins=u["coins"] - item["price"], shield_until=shield_until)
            await query.edit_message_text(f"🛡 *Защита активирована на 12 часов!*\n\nНикто не сможет украсть твои монеты.\n💰 Списано: {item['price']}💰", parse_mode="Markdown")
        elif item_id == "steal_extra":
            extra = (u.get("extra_steals") or 0) + 1
            update_user(user.id, coins=u["coins"] - item["price"], extra_steals=extra)
            await query.edit_message_text(f"🗡 *Доп. кража куплена!*\n\nТеперь у тебя *{extra}* внеплановых попыток кражи.\n💰 Списано: {item['price']}💰", parse_mode="Markdown")
        return
    if u["rod"] == item_id or u["bait"] == item_id:
        await query.answer("✅ У тебя уже есть этот предмет!", show_alert=True)
        return
    if item["type"] == "rod":
        update_user(user.id, rod=item_id, coins=u["coins"] - item["price"])
    else:
        update_user(user.id, bait=item_id, coins=u["coins"] - item["price"])
    await query.edit_message_text(f"✅ *Куплено: {item['name']}!*\n{item['desc']}", parse_mode="Markdown")
    await check_achievements(user.id, None)

# ─── MAIN ─────────────────────────────────────────────────────────────────────

async def set_commands(app):
    private_cmds = [
        BotCommand("start",        "🎣 Главное меню"),
        BotCommand("fish",         "🎣 Закинуть удочку"),
        BotCommand("profile",      "👤 Мой профиль"),
        BotCommand("top",          "🏆 Топ рыбаков"),
        BotCommand("stats",        "📊 Статистика улова"),
        BotCommand("shop",         "🛒 Магазин снаряжения"),
        BotCommand("daily",        "📅 Ежедневный бонус"),
        BotCommand("achievements", "🏅 Достижения"),
        BotCommand("promo",        "🎁 Активировать промокод"),
        BotCommand("stars",        "⭐ Купить за звёзды"),
        BotCommand("help",         "❓ Все команды"),
        BotCommand("admin",        "🔧 Панель админа"),
    ]
    group_cmds = [
        BotCommand("fish",     "🎣 Закинуть удочку"),
        BotCommand("profile",  "👤 Показать профиль"),
        BotCommand("top",      "🏆 Топ рыбаков"),
        BotCommand("stats",    "📊 Статистика улова"),
        BotCommand("shop",     "🛒 Магазин с бонусами"),
        BotCommand("daily",    "📅 Ежедневный бонус"),
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

    app.add_handler(CommandHandler("start",        start))
    app.add_handler(CommandHandler("fish",         fish_cmd))
    app.add_handler(CommandHandler("profile",      profile_cmd))
    app.add_handler(CommandHandler("top",          top_cmd))
    app.add_handler(CommandHandler("stats",        stats_cmd))
    app.add_handler(CommandHandler("shop",         shop_cmd))
    app.add_handler(CommandHandler("help",         help_cmd))
    app.add_handler(CommandHandler("daily",        daily_cmd))
    app.add_handler(CommandHandler("achievements", achievements_cmd))
    app.add_handler(CommandHandler("stars",        stars_cmd))
    app.add_handler(CommandHandler("promo",        promo_cmd))
    app.add_handler(CommandHandler("admin",        admin_panel))
    app.add_handler(CommandHandler("newpromo",     newpromo_cmd))
    app.add_handler(CommandHandler("givecoins",    givecoins_cmd))
    app.add_handler(CommandHandler("takecoins",    takecoins_cmd))
    app.add_handler(CommandHandler("broadcast",    broadcast_cmd))

    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    app.add_handler(CallbackQueryHandler(shop_callback,  pattern="^(buy_|show_stars|stars_|back_shop|daily_noop)"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^adm_"))
    app.add_handler(CallbackQueryHandler(menu_callback,  pattern="^cmd_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    logger.info("🎣 Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
