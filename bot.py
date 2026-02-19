import base64
import json
import re
import os
import time
import logging
import traceback
import urllib.parse
import telebot
from telebot import types
import requests

# ── Logging ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
log = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────
BOT_TOKEN = os.getenv('BOT_TOKEN')
WEB_APP_URL = os.getenv('WEB_APP_URL')
LINK_SCRAPER_URL = os.getenv('LINK_SCRAPER_URL', '').rstrip('/')

if not BOT_TOKEN:
    log.critical("BOT_TOKEN not set")
    exit(1)

if not WEB_APP_URL:
    log.critical("WEB_APP_URL not set")
    exit(1)

if not WEB_APP_URL.startswith('http://') and not WEB_APP_URL.startswith('https://'):
    WEB_APP_URL = f'https://{WEB_APP_URL}'

bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=4)

TIMEOUT_FAST = int(os.getenv('TIMEOUT_FAST', '15'))
TIMEOUT_ANALYZE = int(os.getenv('TIMEOUT_ANALYZE', '30'))
TIMEOUT_SCRAPE = int(os.getenv('TIMEOUT_SCRAPE', '45'))
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB

# ── Reusable session for connection pooling ─────────────────
_session = requests.Session()
_session.headers['User-Agent'] = 'WishlistBot/1.0'

# ── Per-user rate limiter ───────────────────────────────────
_rate = {}
RATE_LIMIT = 12      # max messages
RATE_WINDOW = 60     # per N seconds

def _is_rate_limited(chat_id):
    now = time.time()
    bucket = _rate.setdefault(chat_id, [])
    # prune old entries
    _rate[chat_id] = [t for t in bucket if now - t < RATE_WINDOW]
    if len(_rate[chat_id]) >= RATE_LIMIT:
        return True
    _rate[chat_id].append(now)
    return False


# ── Helpers ─────────────────────────────────────────────────
def is_url(text):
    return bool(re.match(r'^https?://\S+$', (text or '').strip()))


def pack_start_param(payload):
    return base64.b64encode(json.dumps(payload).encode()).decode()


def safe_reply(message, text, reply_markup=None):
    try:
        bot.reply_to(message, text, reply_markup=reply_markup)
    except Exception as e:
        log.warning("safe_reply failed: %s", e)


def reply_with_card_button(message, start_param, emoji, label):
    app_url = f"{WEB_APP_URL}#tgWebAppStartParam={urllib.parse.quote(start_param, safe='')}"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton(
        text=f'{emoji} {label}',
        web_app=types.WebAppInfo(url=app_url),
    ))
    safe_reply(message, 'Нажми кнопку ниже, чтобы создать карточку 👇', reply_markup=keyboard)


def send_typing(message):
    try:
        bot.send_chat_action(message.chat.id, 'typing')
    except Exception:
        pass


def starts_with_want(text):
    return (text or '').strip().lower().startswith('хочу')


# ── Handlers ────────────────────────────────────────────────
@bot.message_handler(commands=['start'])
def start(message):
    safe_reply(message,
        "Привет! 👋\n\n"
        "Отправь фото, ссылку или текст «хочу ...» — я помогу создать карточку желания.\n\n"
        "📸 Фото — анализ изображения\n"
        "🔗 Ссылка — анализ страницы товара\n"
        "📝 «Хочу ...» — карточка из текста")


@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    if _is_rate_limited(message.chat.id):
        safe_reply(message, 'Слишком много запросов. Подожди немного ⏳')
        return
    if not LINK_SCRAPER_URL:
        safe_reply(message, 'Сервис временно недоступен. Попробуй позже.')
        return
    try:
        send_typing(message)
        photo = message.photo[-1]
        file_info = bot.get_file(photo.file_id)
        if file_info.file_size and file_info.file_size > MAX_FILE_SIZE:
            safe_reply(message, 'Фото слишком большое (макс 5 МБ). Сожми и попробуй снова.')
            return
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
        img_resp = _session.get(file_url, timeout=TIMEOUT_FAST)
        img_resp.raise_for_status()
        img_b64_raw = base64.b64encode(img_resp.content).decode()

        send_typing(message)
        r = _session.post(
            f'{LINK_SCRAPER_URL}/analyze-image',
            json={'image': img_b64_raw},
            timeout=TIMEOUT_ANALYZE,
        )
        r.raise_for_status()
        data = r.json()
        payload = {
            'n': data.get('name') or 'N/A',
            'p': data.get('price'),
            'c': data.get('currency'),
            's': data.get('size'),
        }
        if data.get('image'):
            payload['i'] = data.get('image')[:2000]
        reply_with_card_button(message, 'img_' + pack_start_param(payload), '📸', 'Создать карточку')
    except Exception as e:
        log.error('analyze-image error: %s', e, exc_info=True)
        safe_reply(message, 'Не удалось проанализировать фото. Попробуй ещё раз.')


@bot.message_handler(func=lambda m: m.content_type == 'text' and starts_with_want(m.text))
def handle_want_text(message):
    if _is_rate_limited(message.chat.id):
        safe_reply(message, 'Слишком много запросов. Подожди немного ⏳')
        return
    text = message.text.strip()
    if not LINK_SCRAPER_URL:
        safe_reply(message, 'Сервис временно недоступен. Попробуй позже.')
        return
    try:
        send_typing(message)
        r = _session.post(
            f'{LINK_SCRAPER_URL}/analyze-text',
            json={'text': text},
            timeout=TIMEOUT_ANALYZE,
        )
        r.raise_for_status()
        data = r.json()
        payload = {
            'n': data.get('name') or 'Желание',
            'p': data.get('price'),
            'c': data.get('currency'),
            's': data.get('size'),
        }
        reply_with_card_button(message, 'text_' + pack_start_param(payload), '📝', 'Создать карточку')
    except Exception as e:
        log.error('analyze-text error: %s', e, exc_info=True)
        safe_reply(message, 'Не удалось проанализировать текст. Попробуй ещё раз.')


@bot.message_handler(func=lambda m: m.content_type == 'text' and is_url(m.text))
def handle_link(message):
    if _is_rate_limited(message.chat.id):
        safe_reply(message, 'Слишком много запросов. Подожди немного ⏳')
        return
    target_url = message.text.strip()
    if not LINK_SCRAPER_URL:
        safe_reply(message, 'Сервис временно недоступен. Попробуй позже.')
        return
    try:
        send_typing(message)
        r = _session.get(
            f'{LINK_SCRAPER_URL}/?url={urllib.parse.quote(target_url, safe="")}',
            timeout=TIMEOUT_SCRAPE,
        )
        r.raise_for_status()
        data = r.json()
        payload = {
            'n': (data.get('name') or 'N/A')[:80],
            'p': data.get('price'),
            'c': data.get('currency'),
            's': data.get('size'),
            'l': target_url[:500],
        }
        if data.get('image'):
            payload['i'] = data.get('image')[:2000]
        reply_with_card_button(message, 'link_' + pack_start_param(payload), '🔗', 'Создать карточку')
    except Exception as e:
        log.error('parse link error: %s', e, exc_info=True)
        safe_reply(message, 'Не удалось проанализировать ссылку. Попробуй ещё раз.')


@bot.message_handler(func=lambda m: True)
def handle_all(message):
    safe_reply(message,
        'Отправь фото, ссылку или напиши «хочу ...» — я помогу создать карточку! 📸🔗📝')

if __name__ == '__main__':
    log.info("Bot started | WEB_APP_URL=%s | threads=4", WEB_APP_URL)
    bot.infinity_polling(timeout=30, long_polling_timeout=25)
