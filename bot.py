import os
import json
import hmac
import hashlib
import asyncio
import logging
import base64
from datetime import date, datetime
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

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8132623719:AAERdjWUP6rSW1yRYsnoJtntLVkPB9vZ_ZU")
BOT_USERNAME = "diggerfest_bot"
WEBAPP_URL = "https://muhammadaminpy.github.io/diggerfest/"

WEBHOOK_HOST = "https://quinsied-undeliberately-kerry.ngrok-free.app"  # ← обновляйте при смене ngrok
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

PORT = int(os.environ.get("PORT", 9000))
ADMIN_ID = 1027715401

RECEIVER_ADDRESS = "UQBIYfPSgJN7n3eoYpkRqR1jnpQhLnwojPLKywyvr2sTE2HZ"

STARS_PER_TON = 100
REF_REWARD = 10

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

# ==================== БАЗА ДАННЫХ ====================
async def get_db():
    db = await aiosqlite.connect("digger.db")
    
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
            PRIMARY KEY (user_id, chapter)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS processed_txs (
            tx_hash TEXT PRIMARY KEY
        )
    """)
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
            type TEXT,               -- 'channel' or 'bot'
            title TEXT,
            link TEXT,
            reward INTEGER,
            max_activations INTEGER,
            channel_id INTEGER       -- NULL for bot
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS completed_tasks (
            user_id INTEGER,
            task_id INTEGER,
            PRIMARY KEY (user_id, task_id)
        )
    """)
    await db.commit()
    return db


async def init_chapters(db):
    cursor = await db.execute("SELECT COUNT(*) FROM chapters")
    if (await cursor.fetchone())[0] == 0:
        chapters_data = [
            (1, "Начало пути", 29, 0.3, "Текст главы 1...", "КОПАЙ"),
            (2, "Встреча с Дуровым", 29, 0.3, "Текст главы 2...", "ГЛУБЖЕ"),
            (3, "Тёмные времена", 29, 0.3, "Текст главы 3...", "И"),
            (4, "Возрождение", 29, 0.3, "Текст главы 4...", "ТЫ"),
            (5, "Первый миллион", 99, 0.0, "Текст главы 5...", "НАЙДЁШЬ"),
            (6, "Telegram Mini Apps", 99, 1.05, "Текст главы 6...", "СВОЙ"),
            (7, "Враги в тени", 99, 0.0, "Текст главы 7...", "ПУТЬ"),
            (8, "Глобальное признание", 99, 1.05, "Текст главы 8...", "К"),
            (9, "Новый вызов", 199, 3.0, "Текст главы 9...", "ЗВЁЗДАМ"),
            (10, "Философия копателя", 199, 3.0, "Текст главы 10...", "В"),
            (11, "Наследие", 199, 3.0, "Текст главы 11...", "TON"),
        ]
        await db.executemany(
            "INSERT OR IGNORE INTO chapters VALUES (?, ?, ?, ?, ?, ?)",
            chapters_data
        )
        await db.commit()


# ==================== ВАЛИДАЦИЯ initData ====================
def validate_init_data(init_data: str) -> dict | None:
    if not init_data:
        return None
    try:
        params = {k: v[0] for k, v in parse_qs(init_data).items()}
        received_hash = params.pop("hash", None)
        if not received_hash:
            return None

        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if calculated_hash == received_hash:
            return json.loads(params.get("user", "{}"))
        return None
    except Exception as e:
        logger.error(f"Validation error: {e}")
        return None


async def validate_request(request: web.Request) -> dict | None:
    try:
        if request.content_type == "application/json":
            data = await request.json()
            init_data = data.get("initData")
        else:
            init_data = None

        if not init_data:
            init_data = request.headers.get("Authorization")

        if not init_data:
            return None

        return validate_init_data(init_data)
    except Exception as e:
        logger.error(f"Request validation error: {e}")
        return None


# ==================== /start ====================
@router.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject):
    args = (command.args or "").strip()
    web_app_url = WEBAPP_URL
    ref_id = None

    if args.startswith("ref"):
        try:
            ref_id = int(args[3:])
            if ref_id == message.from_user.id:
                logger.warning(f"Самореферал от {message.from_user.id}")
                ref_id = None
        except ValueError:
            ref_id = None

    db = await get_db()
    ref_added = False

    async with db:
        cursor = await db.execute(
            "SELECT referred_by FROM users WHERE id = ?",
            (message.from_user.id,)
        )
        row = await cursor.fetchone()

        if row:
            if row[0] is None and ref_id is not None:
                await db.execute(
                    "UPDATE users SET referred_by = ? WHERE id = ?",
                    (ref_id, message.from_user.id)
                )
                await db.execute(
                    "UPDATE users SET ref_count = ref_count + 1, "
                    "ref_earnings = ref_earnings + ?, "
                    "balance = balance + ? "
                    "WHERE id = ?",
                    (REF_REWARD, REF_REWARD, ref_id)
                )
                ref_added = True
        else:
            await db.execute(
                """
                INSERT INTO users 
                (id, first_name, username, referred_by)
                VALUES (?, ?, ?, ?)
                """,
                (
                    message.from_user.id,
                    message.from_user.first_name,
                    message.from_user.username,
                    ref_id
                )
            )
            if ref_id is not None:
                await db.execute(
                    "UPDATE users SET ref_count = ref_count + 1, "
                    "ref_earnings = ref_earnings + ?, "
                    "balance = balance + ? "
                    "WHERE id = ?",
                    (REF_REWARD, REF_REWARD, ref_id)
                )
                ref_added = True

        if ref_added:
            logger.info(f"Реферал засчитан: {ref_id} → {message.from_user.id} (+{REF_REWARD}⭐)")

        await db.commit()

    if args:
        web_app_url += f"?start={args}"

    kb = InlineKeyboardBuilder().row(
        InlineKeyboardButton(
            text="🎄 Открыть Просто DIGGER",
            web_app=WebAppInfo(url=web_app_url)
        )
    )

    await message.answer(
        "🎅 <b>Добро пожаловать в Просто DIGGER!</b>\n\n"
        "Новогодний майнинг в мире TON 🎄\n\n"
        "Нажми кнопку ниже:",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )


# ==================== ПЛАТЕЖИ STARS ====================
@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@router.message(F.successful_payment)
async def successful_payment_handler(message: types.Message):
    payload = message.successful_payment.invoice_payload
    if not payload.startswith('topup:'):
        return

    try:
        _, amount_str, user_id_str = payload.split(':')
        amount = int(amount_str)
        user_id = int(user_id_str)
    except:
        return

    if message.from_user.id != user_id:
        return

    db = await get_db()
    async with db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, user_id))
        await db.commit()

    await message.answer(f"Баланс пополнен на {amount} ⭐ через Telegram Stars!")


# ==================== API ЭНДПОИНТЫ ====================

async def create_invoice_handler(request):
    user = await validate_request(request)
    if not user:
        return web.json_response({"error": "Invalid initData"}, status=403)

    try:
        data = await request.json()
        amount = int(data["amount"])
        if amount < 29:
            return web.json_response({"error": "Minimum 29 Stars"}, status=400)

        await bot.send_invoice(
            chat_id=user["id"],
            title="Пополнение баланса",
            description=f"Пополнение на {amount} ⭐",
            payload=f"topup:{amount}:{user['id']}",
            currency="XTR",
            prices=[LabeledPrice(label="Stars", amount=amount)]
        )
        return web.json_response({"success": True})
    except Exception as e:
        logger.error(f"Invoice error: {e}")
        return web.json_response({"error": "Failed to create invoice"}, status=500)


async def get_user_handler(request):
    user = await validate_request(request)
    if not user:
        return web.json_response({"error": "Invalid"}, status=403)

    req_user_id = int(request.match_info['user_id'])
    if user['id'] != req_user_id:
        return web.json_response({"error": "Access denied"}, status=403)

    db = await get_db()
    async with db:
        cursor = await db.execute('SELECT * FROM users WHERE id = ?', (req_user_id,))
        row = await cursor.fetchone()

    if row:
        keys = ['id', 'first_name', 'username', 'photo_url', 'balance', 'ref_count', 'ref_earnings', 'referred_by']
        return web.json_response(dict(zip(keys, row)))
    return web.json_response({"error": "User not found"}, status=404)


async def get_top_handler(request):
    user = await validate_request(request)
    if not user:
        return web.json_response({"error": "Invalid"}, status=403)

    db = await get_db()
    async with db:
        cursor = await db.execute(
            "SELECT id, first_name, photo_url, ref_count "
            "FROM users ORDER BY ref_count DESC LIMIT 25"
        )
        rows = await cursor.fetchall()

    top_list = [
        {"id": r[0], "first_name": r[1], "photo_url": r[2], "refCount": r[3]}
        for r in rows
    ]
    return web.json_response(top_list)


async def get_unlocks_handler(request):
    user = await validate_request(request)
    if not user:
        return web.json_response({"error": "Invalid"}, status=403)

    req_user_id = int(request.match_info['user_id'])
    if user['id'] != req_user_id:
        return web.json_response({"error": "Access denied"}, status=403)

    today = date.today().isoformat()
    db = await get_db()
    async with db:
        cursor = await db.execute(
            "SELECT chapter FROM unlocks WHERE user_id = ?",
            (req_user_id,)
        )
        unlocked = [row[0] async for row in cursor]

        cursor = await db.execute(
            "SELECT COUNT(*) FROM unlocks WHERE user_id = ? AND unlock_date = ?",
            (req_user_id, today)
        )
        daily_opened = (await cursor.fetchone())[0]

    return web.json_response({"unlocked": unlocked, "dailyOpened": daily_opened})


async def buy_chapter_handler(request):
    user = await validate_request(request)
    if not user:
        return web.json_response({"error": "Invalid"}, status=403)

    data = await request.json()
    chapter_id = data.get('chapterId')
    user_id = user['id']

    db = await get_db()
    async with db:
        cursor = await db.execute("SELECT balance FROM users WHERE id = ?", (user_id,))
        balance = (await cursor.fetchone())[0]

        cursor = await db.execute("SELECT price FROM chapters WHERE id = ?", (chapter_id,))
        price_row = await cursor.fetchone()
        if not price_row:
            return web.json_response({"error": "Chapter not found"}, status=404)
        price = price_row[0]

        if balance < price:
            return web.json_response({"error": "Insufficient balance"}, status=400)

        today = date.today().isoformat()
        cursor = await db.execute(
            "SELECT COUNT(*) FROM unlocks WHERE user_id = ? AND unlock_date = ?",
            (user_id, today)
        )
        if (await cursor.fetchone())[0] >= 2:
            return web.json_response({"error": "Daily limit reached"}, status=400)

        if chapter_id > 1:
            cursor = await db.execute(
                "SELECT 1 FROM unlocks WHERE user_id = ? AND chapter = ?",
                (user_id, chapter_id - 1)
            )
            if not await cursor.fetchone():
                return web.json_response({"error": "Previous chapter not unlocked"}, status=400)

        await db.execute(
            "INSERT OR IGNORE INTO unlocks (user_id, chapter, unlock_date) VALUES (?, ?, ?)",
            (user_id, chapter_id, today)
        )
        await db.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (price, user_id))
        await db.commit()

    return web.json_response({"success": True})


async def check_purchase_handler(request):
    user = await validate_request(request)
    if not user:
        return web.json_response({"error": "Invalid"}, status=403)

    user_id = int(request.match_info['user_id'])
    chapter_id = int(request.match_info['chapter_id'])

    if user['id'] != user_id:
        return web.json_response({"error": "Access denied"}, status=403)

    db = await get_db()
    async with db:
        cursor = await db.execute(
            "SELECT 1 FROM unlocks WHERE user_id = ? AND chapter = ?",
            (user_id, chapter_id)
        )
        purchased = await cursor.fetchone() is not None

    return web.json_response({"purchased": purchased})


async def get_admin_stats_handler(request):
    user = await validate_request(request)
    if not user or user['id'] != ADMIN_ID:
        return web.json_response({"error": "Access denied"}, status=403)

    today = date.today().isoformat()
    db = await get_db()
    async with db:
        cursor = await db.execute("SELECT COUNT(*) FROM unlocks WHERE unlock_date = ?", (today,))
        daily_opens = (await cursor.fetchone())[0]

        # Пример расчёта заработка (можно доработать)
        earn_24h = 0  # ← реализуйте логику, если нужно
        earn_7d = 0
        earn_10d = 0

        cursor = await db.execute(
            """
            SELECT first_name FROM users u
            JOIN (SELECT user_id, COUNT(*) cnt FROM unlocks GROUP BY user_id HAVING cnt = 11) ul
            ON u.id = ul.user_id
            ORDER BY (SELECT MIN(unlock_date) FROM unlocks WHERE user_id = u.id) ASC
            LIMIT 1
            """
        )
        row = await cursor.fetchone()
        first_completer = row[0] if row else 'Никто'

    return web.json_response({
        "dailyOpens": daily_opens,
        "earn24h": earn_24h,
        "earn7d": earn_7d,
        "earn10d": earn_10d,
        "firstCompleter": first_completer
    })


async def get_chapters_handler(request):
    db = await get_db()
    async with db:
        cursor = await db.execute("SELECT * FROM chapters ORDER BY id")
        rows = await cursor.fetchall()

    chapters = [
        {
            "id": r[0],
            "title": r[1],
            "price": r[2],
            "tonPrice": r[3],
            "content": r[4],
            "easterWord": r[5],
        }
        for r in rows
    ]
    return web.json_response(chapters)


async def update_chapter_handler(request):
    user = await validate_request(request)
    if not user or user["id"] != ADMIN_ID:
        return web.json_response({"error": "Access denied"}, status=403)

    try:
        data = await request.json()
        chapter_id = data["id"]
        price = data.get("price")
        ton_price = data.get("ton_price")
        content = data.get("content")
        easter_word = data.get("easter_word")

        db = await get_db()
        async with db:
            updates = []
            params = []
            if price is not None:
                updates.append("price = ?")
                params.append(price)
            if ton_price is not None:
                updates.append("ton_price = ?")
                params.append(ton_price)
            if content is not None:
                updates.append("content = ?")
                params.append(content)
            if easter_word is not None:
                updates.append("easter_word = ?")
                params.append(easter_word)

            if updates:
                query = f"UPDATE chapters SET {', '.join(updates)} WHERE id = ?"
                params.append(chapter_id)
                await db.execute(query, params)
                await db.commit()

        return web.json_response({"success": True})
    except Exception as e:
        logger.error(f"Update chapter error: {e}")
        return web.json_response({"error": str(e)}, status=500)


# ==================== TON MONITORING ====================
async def process_incoming_txs():
    async with aiohttp.ClientSession() as session:
        url = f"https://toncenter.com/api/v2/getTransactions?address={RECEIVER_ADDRESS}&limit=30&archival=true"
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return
                data = await resp.json()
        except Exception as e:
            logger.error(f"Toncenter request error: {e}")
            return

        txs = data.get("result", [])
        db = await get_db()

        async with db:
            for tx in txs:
                tx_hash = tx.get("hash")
                if not tx_hash:
                    continue

                if await db.execute_fetchone("SELECT 1 FROM processed_txs WHERE tx_hash = ?", (tx_hash,)):
                    continue

                in_msg = tx.get("in_msg", {})
                value_str = in_msg.get("value", "0")
                if value_str == "0":
                    continue

                message_b64 = in_msg.get("message", "")
                if not message_b64:
                    continue

                try:
                    payload_bytes = base64.b64decode(message_b64)
                    payload = json.loads(payload_bytes.decode("utf-8"))
                except Exception:
                    continue

                payload_type = payload.get("type")
                if payload_type not in ["topup", "buy_chapter"]:
                    continue

                init_data = payload.get("initData")
                validated = validate_init_data(init_data)
                if not validated or validated["id"] != payload.get("userId"):
                    continue

                user_id = validated["id"]
                received_nano = int(value_str)
                received_ton = received_nano / 1_000_000_000

                if payload_type == "topup":
                    expected = payload.get("amount", 0)
                    if abs(received_ton - expected) > 0.02:
                        continue
                    stars = int(expected * STARS_PER_TON)
                    await db.execute(
                        "UPDATE users SET balance = balance + ? WHERE id = ?",
                        (stars, user_id),
                    )

                elif payload_type == "buy_chapter":
                    chapter_id = payload.get("chapterId")
                    cursor = await db.execute(
                        "SELECT ton_price FROM chapters WHERE id = ?",
                        (chapter_id,)
                    )
                    row = await cursor.fetchone()
                    required = row[0] if row else 0
                    if required == 0 or received_ton < required - 0.02:
                        continue

                    today = date.today().isoformat()
                    cursor = await db.execute(
                        "SELECT COUNT(*) FROM unlocks WHERE user_id = ? AND unlock_date = ?",
                        (user_id, today),
                    )
                    if (await cursor.fetchone())[0] >= 2:
                        continue

                    if chapter_id > 1:
                        cursor = await db.execute(
                            "SELECT 1 FROM unlocks WHERE user_id = ? AND chapter = ?",
                            (user_id, chapter_id - 1),
                        )
                        if not await cursor.fetchone():
                            continue

                    await db.execute(
                        "INSERT OR IGNORE INTO unlocks (user_id, chapter, unlock_date) VALUES (?, ?, ?)",
                        (user_id, chapter_id, today),
                    )

                await db.execute("INSERT INTO processed_txs (tx_hash) VALUES (?)", (tx_hash,))
            await db.commit()


async def monitor_ton():
    while True:
        await process_incoming_txs()
        await asyncio.sleep(15)


# ==================== CORS & ЗАПУСК ====================
@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        response = web.Response()
    else:
        response = await handler(request)

    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


async def index_handler(request):
    return web.FileResponse("index.html")


async def on_startup(app):
    db = await get_db()
    await init_chapters(db)
    await bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook установлен: {WEBHOOK_URL}")
    asyncio.create_task(monitor_ton())


def main():
    dp.include_router(router)

    app = web.Application(middlewares=[cors_middleware])
    app.on_startup.append(on_startup)

    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    app.add_routes([
        web.post("/api/create_invoice", create_invoice_handler),
        web.get(r"/api/user/{user_id:\d+}", get_user_handler),
        web.get("/api/top", get_top_handler),
        web.get(r"/api/unlocks/{user_id:\d+}", get_unlocks_handler),
        web.post("/api/buy-chapter", buy_chapter_handler),
        web.get(r"/api/check-purchase/{user_id:\d+}/{chapter_id:\d+}", check_purchase_handler),
        web.get("/api/admin/stats", get_admin_stats_handler),
        web.get("/api/chapters", get_chapters_handler),
        web.post("/api/update_chapter", update_chapter_handler),
        web.get("/", index_handler),
    ])

    app.router.add_static("/static/", path=".", name="static")

    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
