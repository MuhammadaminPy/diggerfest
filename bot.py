# bot.py
import os
import sqlite3
import json
import hmac
import hashlib
import time
from datetime import date
from flask import Flask, request, jsonify, send_from_directory
from aiogram import Bot, Dispatcher, types
from aiogram.types import LabeledPrice, PreCheckoutQuery, WebAppInfo
from aiogram.utils import executor
import asyncio

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "8132623719:AAFUYKob-jgWnLincWgJCC9_OyuMcR55PMM"  # Замените на токен вашего бота
BOT_USERNAME = "diggerfest_bot"  # Замените на username вашего бота (без @)
WEBHOOK_URL = "https://muhammadaminpy.github.io/diggerfest/"  # Ваш домен (например, https://digger.example.com)
WEBAPP_URL = f"{WEBHOOK_URL}/index.html"  # URL вашего index.html

# Для локального тестирования используйте polling
USE_WEBHOOK = True  # Поставьте False для polling (локальный запуск)

# Папка со статическими файлами (index.html должен лежать здесь)
STATIC_FOLDER = "."

# ===================================================

app = Flask(__name__, static_folder=STATIC_FOLDER, static_url_path='')
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# ==================== БАЗА ДАННЫХ ====================
conn = sqlite3.connect('digger.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    first_name TEXT,
    username TEXT,
    photo_url TEXT,
    balance INTEGER DEFAULT 0,
    ref_count INTEGER DEFAULT 0,
    referred_by INTEGER
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS unlocks (
    user_id INTEGER,
    chapter INTEGER,
    unlock_date TEXT,
    PRIMARY KEY (user_id, chapter)
)
''')

conn.commit()

# ==================== ВАЛИДАЦИЯ initData ====================
def validate_init_data(init_data: str) -> dict | None:
    if not init_data:
        return None
    try:
        params = {k: v for k, v in [pair.split('=', 1) for pair in init_data.split('&')]}
        received_hash = params.pop('hash', None)
        if not received_hash:
            return None
        data_check_string = '\n'.join(f"{k}={v}" for k, v in sorted(params.items()))
        secret_key = hashlib.sha256(BOT_TOKEN.encode()).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if calculated_hash == received_hash:
            return json.loads(params.get('user', '{}'))
    except:
        return None
    return None

# ==================== ОСНОВНЫЕ ЭНДПОИНТЫ ====================
@app.route('/')
def index():
    return send_from_directory(STATIC_FOLDER, 'index.html')

# Инициализация пользователя (регистрация + реферал)
@app.route('/api/init', methods=['POST'])
def init_user():
    data = request.json
    init_data = data.get('initData')
    user = validate_init_data(init_data)
    if not user:
        return jsonify({'error': 'Invalid initData'}), 403

    user_id = user['id']
    first_name = user.get('first_name', 'User')
    username = user.get('username')
    photo_url = user.get('photo_url')

    # Обработка реферала из query_parameters
    query_params = data.get('query_params', {})
    referred_by = None
    if 'start' in query_params and query_params['start'].startswith('ref'):
        try:
            referred_by = int(query_params['start'][3:])
        except:
            pass

    cursor.execute('INSERT OR IGNORE INTO users (id, first_name, username, photo_url) VALUES (?, ?, ?, ?)',
                   (user_id, first_name, username, photo_url))
    if referred_by and referred_by != user_id:
        cursor.execute('SELECT id FROM users WHERE id = ?', (referred_by,))
        if cursor.fetchone():
            cursor.execute('UPDATE users SET ref_count = ref_count + 1 WHERE id = ?', (referred_by,))
    conn.commit()

    return jsonify({'success': True})

# Получение данных пользователя
@app.route('/api/user/<int:user_id>')
def get_user(user_id):
    auth = request.headers.get('Authorization')
    if not validate_init_data(auth):
        return jsonify({'error': 'Invalid initData'}), 403

    cursor.execute('SELECT balance, ref_count FROM users WHERE id = ?', (user_id,))
    row = cursor.fetchone()
    if not row:
        return jsonify({'error': 'User not found'}), 404

    balance, ref_count = row
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref{user_id}"
    return jsonify({
        'balance': balance,
        'refLink': ref_link,
        'refCount': ref_count,
        'refEarnings': 0  # Можно добавить логику начисления за рефералов
    })

# ТОП-25 по рефералам
@app.route('/api/top')
def get_top():
    cursor.execute('''
        SELECT id, first_name, photo_url, ref_count 
        FROM users 
        ORDER BY ref_count DESC 
        LIMIT 25
    ''')
    rows = cursor.fetchall()
    top = []
    for row in rows:
        top.append({
            'id': row[0],
            'first_name': row[1],
            'photo_url': row[3],
            'refCount': row[3]
        })
    return jsonify(top)

# Разблокированные главы и счётчик за день
@app.route('/api/unlocks/<int:user_id>')
def get_unlocks(user_id):
    auth = request.headers.get('Authorization')
    if not validate_init_data(auth):
        return jsonify({'error': 'Invalid initData'}), 403

    cursor.execute('SELECT chapter FROM unlocks WHERE user_id = ?', (user_id,))
    unlocked = [row[0] for row in cursor.fetchall()]

    today = date.today().isoformat()
    cursor.execute('SELECT COUNT(*) FROM unlocks WHERE user_id = ? AND unlock_date = ?', (user_id, today))
    daily_opened = cursor.fetchone()[0]

    return jsonify({
        'unlocked': unlocked,
        'dailyOpened': daily_opened
    })

# Покупка главы (списание Stars с баланса)
@app.route('/api/buy-chapter', methods=['POST'])
def buy_chapter():
    data = request.json
    init_data = request.headers.get('Authorization')
    user = validate_init_data(init_data)
    if not user:
        return jsonify({'error': 'Invalid initData'}), 403

    user_id = user['id']
    chapter_id = data.get('chapterId')
    if not chapter_id or not 1 <= chapter_id <= 11:
        return jsonify({'error': 'Invalid chapter'}), 400

    # Цены
    prices = {1:29, 2:29, 3:29, 4:29, 5:99, 6:99, 7:99, 8:99, 9:199, 10:199, 11:199}
    cost = prices[chapter_id]

    # Проверка баланса
    cursor.execute('SELECT balance FROM users WHERE id = ?', (user_id,))
    balance = cursor.fetchone()[0]
    if balance < cost:
        return jsonify({'error': 'Not enough Stars'}), 400

    # Проверка последовательности и лимита 2 в день
    today = date.today().isoformat()
    cursor.execute('SELECT COUNT(*) FROM unlocks WHERE user_id = ? AND unlock_date = ?', (user_id, today))
    daily_count = cursor.fetchone()[0]
    if daily_count >= 2:
        return jsonify({'error': 'Daily limit reached'}), 400

    # Проверка, что предыдущая глава открыта
    if chapter_id > 1:
        cursor.execute('SELECT 1 FROM unlocks WHERE user_id = ? AND chapter = ?', (user_id, chapter_id - 1))
        if not cursor.fetchone():
            return jsonify({'error': 'Previous chapter not unlocked'}), 400

    # Списание и разблокировка
    cursor.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (cost, user_id))
    cursor.execute('INSERT OR REPLACE INTO unlocks (user_id, chapter, unlock_date) VALUES (?, ?, ?)',
                   (user_id, chapter_id, today))
    conn.commit()

    return jsonify({'success': True})

# ==================== ОБРАБОТЧИКИ БОТА ====================
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    args = message.get_args()
    keyboard = types.InlineKeyboardMarkup()
    web_app_url = WEBAPP_URL
    if args and args.startswith('ref'):
        web_app_url += f"?start={args}"
    keyboard.add(types.InlineKeyboardButton(
        text="🎄 Открыть Просто DIGGER",
        web_app=WebAppInfo(url=web_app_url)
    ))
    await message.answer(
        "🎅 <b>Добро пожаловать в Просто DIGGER!</b>\n\n"
        "Новогодний майнинг в мире TON. Копай историю, приглашай друзей и собирай секретное послание!\n\n"
        "Нажми кнопку ниже, чтобы начать:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

# ==================== WEBHOOK ====================
if USE_WEBHOOK:
    @app.route('/webhook', methods=['POST'])
    async def webhook():
        update = types.Update(**request.get_json())
        await dp.process_update(update)
        return 'ok'

    async def on_startup(_):
        await bot.set_webhook(WEBHOOK_URL + '/webhook')
        print(f"Webhook установлен: {WEBHOOK_URL}/webhook")

    async def on_shutdown(_):
        await bot.delete_webhook()

# ==================== ЗАПУСК ====================
if __name__ == '__main__':
    if USE_WEBHOOK:
        # Для production — настройте HTTPS (ngrok, Cloudflare Tunnel и т.д.)
        executor.start_webhook(
            dispatcher=dp,
            webhook_path='/webhook',
            on_startup=on_startup,
            on_shutdown=on_shutdown,
            skip_updates=True,
            host='0.0.0.0',
            port=int(os.environ.get('PORT', 5000))
        )
    else:
        # Для локального тестирования
        executor.start_polling(dp, skip_updates=True)
        app.run(host='127.0.0.1', port=8000)  # Доступ к index.html: http://127.0.0.1:8000