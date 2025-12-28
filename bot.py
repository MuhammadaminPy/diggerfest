import os
import json
import hmac
import hashlib
import asyncio
import logging
import base64
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs

import aiohttp
import aiosqlite
from aiohttp import web

from aiogram import Bot, Dispatcher, types, Router, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import (
    WebAppInfo,
    InlineKeyboardButton,
    LabeledPrice,
    PreCheckoutQuery,
    ChatMemberAdministrator,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8132623719:AAGVPkFir8MKEEyT8Pz0oXoLk39kaB5ydjA")
WEBAPP_URL = "https://muhammadaminpy.github.io/diggerfest/"
WEBHOOK_HOST = "https://quinsied-undeliberatively-kerry.ngrok-free.dev"
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

PORT = int(os.environ.get("PORT", 9000))
ADMIN_ID = 1027715401
RECEIVER_ADDRESS = "UQBIYfPSgJN7n3eoYpkRqR1jnpQhLnwojPLKywyvr2sTE2HZ"
DB_NAME = "digger.db"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# --- РАБОТА С БД ---

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                first_name TEXT,
                username TEXT,
                photo_url TEXT,
                balance INTEGER DEFAULT 0,
                ref_count INTEGER DEFAULT 0,
                ref_earnings INTEGER DEFAULT 0,
                referred_by INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS unlocks (
                user_id INTEGER,
                chapter INTEGER,
                unlock_date TEXT,
                cost_stars INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, chapter)
            )
        """)
        await db.execute("CREATE TABLE IF NOT EXISTS processed_txs (tx_hash TEXT PRIMARY KEY)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chapters (
                id INTEGER PRIMARY KEY,
                title TEXT,
                price INTEGER,
                ton_price REAL,
                content TEXT,
                easter_word TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT,
                title TEXT,
                link TEXT,
                reward INTEGER,
                max_activations INTEGER,
                channel_id INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS completed_tasks (
                user_id INTEGER,
                task_id INTEGER,
                PRIMARY KEY (user_id, task_id)
            )
        """)
        
        # Проверка и инициализация глав
        cursor = await db.execute("SELECT COUNT(*) FROM chapters")
        count = (await cursor.fetchone())[0]
        if count == 0:
            chapters_data = [
                (1, "Начало пути", 29, 0.3, "В далёком 2017 году...", "КОПАЙ"),
                (2, "Встреча с Дуровым", 29, 0.3, "Прошло несколько месяцев...", "ГЛУБЖЕ"),
                (3, "Тёмные времена", 29, 0.3, "2020 год принёс удар...", "И"),
                (4, "Возрождение", 29, 0.3, "2021 год. Слухи о TON...", "ТЫ"),
                (5, "Первый миллион", 99, 0.0, "К концу 2022 года...", "НАЙДЁШЬ"),
                (6, "Telegram Mini Apps", 99, 1.05, "2023 год стал переломным...", "СВОЙ"),
                (7, "Враги в тени", 99, 0.0, "Успех привлёк не только друзей...", "ПУТЬ"),
                (8, "Глобальное признание", 99, 1.05, "2024 год. Сингапур...", "К"),
                (9, "Новый вызов", 199, 3.0, "Глобальный кризис 2024...", "ЗВЁЗДАМ"),
                (10, "Философия копателя", 199, 3.0, "Копай не золото, а знания...", "В"),
                (11, "Наследие", 199, 3.0, "Сегодня, в 2025 году...", "TON"),
            ]
            await db.executemany("INSERT INTO chapters VALUES (?, ?, ?, ?, ?, ?)", chapters_data)
        await db.commit()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def validate_init_data(init_data: str):
    if not init_data: return None
    try:
        params = {k: v[0] for k, v in parse_qs(init_data).items()}
        received_hash = params.pop("hash", None)
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if calculated_hash == received_hash:
            return json.loads(params.get("user", "{}"))
    except: pass
    return None

async def get_user_from_req(request):
    auth = request.headers.get("Authorization")
    if not auth:
        try:
            data = await request.json()
            auth = data.get("initData")
        except: pass
    return validate_init_data(auth)

# --- ХЕНДЛЕРЫ ТЕЛЕГРАМ ---

@router.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject):
    ref_id = None
    if command.args and command.args.startswith("ref"):
        try:
            ref_id = int(command.args[3:])
            if ref_id == message.from_user.id: ref_id = None
        except: pass
    
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT * FROM users WHERE id = ?", (message.from_user.id,))
        if not await cursor.fetchone():
            await db.execute(
                "INSERT INTO users (id, first_name, username, referred_by) VALUES (?, ?, ?, ?)",
                (message.from_user.id, message.from_user.first_name, message.from_user.username, ref_id)
            )
            if ref_id:
                await db.execute(
                    "UPDATE users SET ref_count = ref_count + 1, ref_earnings = ref_earnings + 10, balance = balance + 10 WHERE id = ?",
                    (ref_id,)
                )
            await db.commit()
    
    kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="Открыть Просто DIGGER", web_app=WebAppInfo(url=WEBAPP_URL)))
    await message.answer("<b>Добро пожаловать в Просто DIGGER!</b>\nНовогодний майнинг в мире TON", reply_markup=kb.as_markup(), parse_mode="HTML")

@router.pre_checkout_query()
async def pre_checkout_handler(query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)

@router.message(F.successful_payment)
async def success_pay(message: types.Message):
    payload = message.successful_payment.invoice_payload
    if payload.startswith('topup:'):
        amount = int(payload.split(':')[1])
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, message.from_user.id))
            await db.commit()
        await message.answer(f"Баланс пополнен на {amount} ⭐")

# --- API ---

async def get_user_handler(request):
    user = await get_user_from_req(request)
    uid = int(request.match_info['user_id'])
    if not user or user['id'] != uid: return web.json_response({"error": "403"}, status=403)
    
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE id = ?", (uid,))
        data = await cursor.fetchone()
        return web.json_response(dict(data) if data else {"error": "not_found"})

async def get_chapters_handler(request):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM chapters ORDER BY id")
        rows = await cursor.fetchall()
        return web.json_response([dict(r) for r in rows])

async def buy_chapter_handler(request):
    user = await get_user_from_req(request)
    if not user: return web.json_response({"error": "403"}, status=403)
    data = await request.json()
    ch_id = data.get('chapterId')
    
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        u_row = await (await db.execute("SELECT balance FROM users WHERE id = ?", (user['id'],))).fetchone()
        c_row = await (await db.execute("SELECT price FROM chapters WHERE id = ?", (ch_id,))).fetchone()
        
        if u_row['balance'] < c_row['price']:
            return web.json_response({"error": "Недостаточно Stars"}, status=400)
        
        today = date.today().isoformat()
        await db.execute("INSERT OR IGNORE INTO unlocks (user_id, chapter, unlock_date, cost_stars) VALUES (?, ?, ?, ?)", 
                         (user['id'], ch_id, today, c_row['price']))
        await db.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (c_row['price'], user['id']))
        await db.commit()
        return web.json_response({"success": True})

async def get_unlocks_handler(request):
    user = await get_user_from_req(request)
    uid = int(request.match_info['user_id'])
    if not user or user['id'] != uid: return web.json_response({"error": "403"}, status=403)
    
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT chapter FROM unlocks WHERE user_id = ?", (uid,))
        unlocked = [r[0] for r in await cursor.fetchall()]
        today = date.today().isoformat()
        cursor = await db.execute("SELECT COUNT(*) FROM unlocks WHERE user_id = ? AND unlock_date = ?", (uid, today))
        daily = (await cursor.fetchone())[0]
        return web.json_response({"unlocked": unlocked, "dailyOpened": daily})

async def get_admin_stats_handler(request):
    user = await get_user_from_req(request)
    if not user or user['id'] != ADMIN_ID: return web.json_response({"error": "403"}, status=403)
    
    async with aiosqlite.connect(DB_NAME) as db:
        today = date.today().isoformat()
        cursor = await db.execute("SELECT COUNT(*) FROM unlocks WHERE unlock_date = ?", (today,))
        daily_opens = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT SUM(cost_stars) FROM unlocks WHERE unlock_date = ?", (today,))
        earn_24h = (await cursor.fetchone())[0] or 0
        
        return web.json_response({
            "dailyOpens": daily_opens,
            "earn24h": earn_24h,
            "earn7d": 0, "earn10d": 0, "firstCompleter": "Никто"
        })

# --- ЗАПУСК ---

async def on_startup(app):
    await init_db()
    await bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook set to {WEBHOOK_URL}")

async def on_shutdown(app):
    await bot.delete_webhook()
    await bot.session.close()

def main():
    app = web.Application()
    
    # Регистрация роутов API
    app.router.add_get("/api/user/{user_id}", get_user_handler)
    app.router.add_get("/api/chapters", get_chapters_handler)
    app.router.add_post("/api/buy-chapter", buy_chapter_handler)
    app.router.add_get("/api/unlocks/{user_id}", get_unlocks_handler)
    app.router.add_get("/api/admin/stats", get_admin_stats_handler)
    
    # Настройка webhook aiogram
    handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    handler.register(app, path=WEBHOOK_PATH)
    
    # Важно: setup_application вызывается один раз
    setup_application(app, dp, bot=bot)
    
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    # CORS middleware
    @web.middleware
    async def cors_middleware(request, handler):
        if request.method == "OPTIONS":
            resp = web.Response()
        else:
            resp = await handler(request)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "*"
        return resp
    
    app.middlewares.append(cors_middleware)

    logger.info(f"Starting server on port {PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass