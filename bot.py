# bot.py
import os
import sqlite3
import json
import hmac
import hashlib
from datetime import date
from flask import Flask, request, jsonify, send_from_directory
from aiogram import Bot, Dispatcher, types
from aiogram.types import WebAppInfo
from aiogram.utils.executor import start_webhook

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = os.environ.get('BOT_TOKEN')
BOT_USERNAME = "diggerfest_bot"
WEBAPP_URL = 'https://muhammadaminpy.github.io/diggerfest/'

# Render выдаст свой URL автоматически
WEBHOOK_HOST = os.environ.get('RENDER_EXTERNAL_URL')  # Автоматически от Render
if not WEBHOOK_HOST:
    WEBHOOK_HOST = "https://твой-сервис.onrender.com"  # Замени на свой после первого деплоя!

WEBHOOK_PATH = '/webhook'
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

# Порт от Render
PORT = int(os.environ.get('PORT', 10000))

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
        params = dict(pair.split('=', 1) for pair in init_data.split('&') if '=' in pair)
        received_hash = params.pop('hash', None)
        if not received_hash:
            return None
        data_check_string = '\n'.join(f"{k}={v}" for k, v in sorted(params.items()))
        secret_key = hashlib.sha256(BOT_TOKEN.encode()).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        return json.loads(params.get('user', '{}')) if calculated_hash == received_hash else None
    except:
        return None

# ==================== API ЭНДПОИНТЫ ====================
@app.route('/')
def index():
    return send_from_directory(STATIC_FOLDER, 'index.html')

@app.route('/api/init', methods=['POST'])
def init_user():
    data = request.json or {}
    init_data = data.get('initData')
    user = validate_init_data(init_data)
    if not user:
        return jsonify({'error': 'Invalid initData'}), 403

    user_id = user['id']
    first_name = user.get('first_name', 'User')
    username = user.get('username')
    photo_url = user.get('photo_url')

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
        cursor.execute('SELECT 1 FROM users WHERE id = ?', (referred_by,))
        if cursor.fetchone():
            cursor.execute('UPDATE users SET ref_count = ref_count + 1 WHERE id = ?', (referred_by,))
    conn.commit()

    return jsonify({'success': True})

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
        'refEarnings': 0
    })

@app.route('/api/top')
def get_top():
    cursor.execute('SELECT id, first_name, photo_url, ref_count FROM users ORDER BY ref_count DESC LIMIT 25')
    rows = cursor.fetchall()
    return jsonify([{
        'id': r[0],
        'first_name': r[1],
        'photo_url': r[2],
        'refCount': r[3]
    } for r in rows])

@app.route('/api/unlocks/<int:user_id>')
def get_unlocks(user_id):
    auth = request.headers.get('Authorization')
    if not validate_init_data(auth):
        return jsonify({'error': 'Invalid initData'}), 403

    cursor.execute('SELECT chapter FROM unlocks WHERE user_id = ?', (user_id,))
    unlocked = [r[0] for r in cursor.fetchall()]

    today = date.today().isoformat()
    cursor.execute('SELECT COUNT(*) FROM unlocks WHERE user_id = ? AND unlock_date = ?', (user_id, today))
    daily_opened = cursor.fetchone()[0]

    return jsonify({'unlocked': unlocked, 'dailyOpened': daily_opened})

@app.route('/api/buy-chapter', methods=['POST'])
def buy_chapter():
    data = request.json or {}
    init_data = request.headers.get('Authorization')
    user = validate_init_data(init_data)
    if not user:
        return jsonify({'error': 'Invalid initData'}), 403

    user_id = user['id']
    chapter_id = data.get('chapterId')
    if chapter_id not in range(1, 12):
        return jsonify({'error': 'Invalid chapter'}), 400

    prices = {1:29, 2:29, 3:29, 4:29, 5:99, 6:99, 7:99, 8:99, 9:199, 10:199, 11:199}
    cost = prices[chapter_id]

    cursor.execute('SELECT balance FROM users WHERE id = ?', (user_id,))
    row = cursor.fetchone()
    if not row or row[0] < cost:
        return jsonify({'error': 'Not enough Stars'}), 400

    today = date.today().isoformat()
    cursor.execute('SELECT COUNT(*) FROM unlocks WHERE user_id = ? AND unlock_date = ?', (user_id, today))
    if cursor.fetchone()[0] >= 2:
        return jsonify({'error': 'Daily limit reached'}), 400

    if chapter_id > 1:
        cursor.execute('SELECT 1 FROM unlocks WHERE user_id = ? AND chapter = ?', (user_id, chapter_id - 1))
        if not cursor.fetchone():
            return jsonify({'error': 'Previous chapter not unlocked'}), 400

    cursor.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (cost, user_id))
    cursor.execute('INSERT OR REPLACE INTO unlocks (user_id, chapter, unlock_date) VALUES (?, ?, ?)',
                   (user_id, chapter_id, today))
    conn.commit()

    return jsonify({'success': True})

# ==================== START КОМАНДА ====================
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    args = message.get_args()
    web_app_url = WEBAPP_URL
    if args and args.startswith('ref'):
        web_app_url += f"?start={args}"

    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton(
        text="🎄 Открыть Просто DIGGER",
        web_app=WebAppInfo(url=web_app_url)
    ))

    await message.answer(
        "🎅 <b>Добро пожаловать в Просто DIGGER!</b>\n\n"
        "Новогодний майнинг в мире TON. Копай историю, приглашай друзей и собирай секретное послание!\n\n"
        "Нажми кнопку ниже:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

# ==================== WEBHOOK ====================
@app.route(WEBHOOK_PATH, methods=['POST'])
async def webhook():
    update = types.Update.de_json(request.get_json())
    await dp.process_update(update)
    return 'ok'

# ==================== ЗАПУСК НА RENDER ====================
if __name__ == "__main__":
    start_webhook(
        dispatcher=dp,
        webhook_path=WEBHOOK_PATH,
        on_startup=lambda _: bot.set_webhook(WEBHOOK_URL),
        on_shutdown=lambda _: bot.delete_webhook(),
        skip_updates=True,
        host='0.0.0.0',
        port=PORT
    )


