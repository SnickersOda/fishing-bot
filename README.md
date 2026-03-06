# 🎣 Fishing Bot — Telegram

Бот для рыбалки в Telegram-группах. Аналог PivomerGame, но про рыбалку!

## Команды

| Команда | Описание |
|---------|----------|
| /fish | Закинуть удочку (1 раз в час) |
| /profile | Профиль игрока |
| /top | Топ рыбаков |
| /stats | Статистика улова по видам рыб |
| /shop | Магазин удочек и наживок |
| /help | Все команды |

## Механика

- Рыба ловится **1 раз в час**
- Вес улова: от **3 до 20 кг** (зависит от удочки и наживки)
- За каждый кг начисляются **монеты** (2 монеты за 1 кг)
- На монеты можно купить лучшее снаряжение в /shop

## Виды рыб

🐟 Карась, 🐠 Окунь, 🐡 Лещ, 🦈 Щука, 🐬 Сом, 🐳 Карп, 🦑 Судак

## Деплой на Render

1. Создай бота через [@BotFather](https://t.me/BotFather) → получи `BOT_TOKEN`
2. Загрузи проект на GitHub
3. Зайди на [render.com](https://render.com) → New → Web Service → подключи репозиторий
4. В разделе **Environment Variables** добавь:
   ```
   BOT_TOKEN = твой_токен_от_botfather
   ```
5. Build Command: `pip install -r requirements.txt`
6. Start Command: `python bot.py`
7. Нажми **Deploy** — бот запустится!

## Локальный запуск

```bash
pip install -r requirements.txt
BOT_TOKEN=твой_токен python bot.py
```
