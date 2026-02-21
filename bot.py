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
SCRAPER_API_KEY = os.getenv('SCRAPER_API_KEY', '')
_session = requests.Session()
_session.headers['User-Agent'] = 'WishlistBot/1.0'
if SCRAPER_API_KEY:
    _session.headers['x-api-key'] = SCRAPER_API_KEY

# ── Analytics ────────────────────────────────────────────────
SUPABASE_URL = os.getenv('SUPABASE_URL', '').rstrip('/')
SUPABASE_ANON_KEY = os.getenv('SUPABASE_ANON_KEY', '')

def track_event(user_id, event_name, properties=None):
    if not SUPABASE_URL or not SUPABASE_ANON_KEY or not user_id:
        return
    try:
        _session.post(
            f'{SUPABASE_URL}/rest/v1/analytics_events',
            json={'event_name': event_name, 'user_id': user_id, 'source': 'bot', 'properties': properties or {}},
            headers={
                'apikey': SUPABASE_ANON_KEY,
                'Authorization': f'Bearer {SUPABASE_ANON_KEY}',
                'Content-Type': 'application/json',
                'Prefer': 'return=minimal',
            },
            timeout=5,
        )
    except Exception as e:
        log.warning('track_event failed: %s', e)

def ensure_user_analytics(message):
    u = message.from_user
    if not u or not SUPABASE_URL or not SUPABASE_ANON_KEY:
        return
    user_id = f'tg_{u.id}'
    cohort_month = __import__('datetime').datetime.utcnow().strftime('%Y-%m')
    try:
        resp = _session.get(
            f'{SUPABASE_URL}/rest/v1/users?id=eq.{user_id}&select=id',
            headers={'apikey': SUPABASE_ANON_KEY, 'Authorization': f'Bearer {SUPABASE_ANON_KEY}'},
            timeout=5,
        )
        is_new = resp.ok and resp.json() == []
        if is_new:
            track_event(user_id, 'user_registered', {'cohort_month': cohort_month})
    except Exception as e:
        log.warning('ensure_user_analytics failed: %s', e)

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


def is_ru(message):
    lang = getattr(message.from_user, 'language_code', '') or ''
    return lang.startswith('ru')


def safe_reply(message, text, reply_markup=None):
    try:
        bot.reply_to(message, text, reply_markup=reply_markup)
    except Exception as e:
        log.warning("safe_reply failed: %s", e)


def reply_with_card_button(message, start_param, emoji):
    label = 'Создать карточку' if is_ru(message) else 'Create my wish'
    hint = 'Нажми кнопку ниже, чтобы создать карточку 👇' if is_ru(message) else 'Tap the button below to create a wish 👇'
    app_url = f"{WEB_APP_URL}#tgWebAppStartParam={urllib.parse.quote(start_param, safe='')}"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton(
        text=f'{emoji} {label}',
        web_app=types.WebAppInfo(url=app_url),
    ))
    safe_reply(message, hint, reply_markup=keyboard)


def send_typing(message):
    try:
        bot.send_chat_action(message.chat.id, 'typing')
    except Exception:
        pass


def starts_with_want(text):
    t = (text or '').strip().lower()
    return t.startswith('хочу') or t.startswith('i wish')


# ── Handlers ────────────────────────────────────────────────
@bot.message_handler(commands=['start'])
def start(message):
    ensure_user_analytics(message)
    if is_ru(message):
        safe_reply(message,
            "Привет! 👋\n\n"
            "Отправь фото, ссылку или текст «хочу ...» — я помогу создать карточку желания.\n\n"
            "📸 Фото — анализ изображения\n"
            "🔗 Ссылка — анализ страницы товара\n"
            "📝 «Хочу ...» — карточка из текста")
    else:
        safe_reply(message,
            "Hey! 👋\n\n"
            "Send a photo, a link, or type \"I wish ...\" — I'll help you create a wish card.\n\n"
            "📸 Photo — image analysis\n"
            "🔗 Link — product page analysis\n"
            "📝 \"I wish ...\" — card from text")


@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    if _is_rate_limited(message.chat.id):
        safe_reply(message, 'Слишком много запросов. Подожди немного ⏳' if is_ru(message) else 'Too many requests. Hold on ⏳')
        return
    if not LINK_SCRAPER_URL:
        safe_reply(message, 'Сервис временно недоступен. Попробуй позже.' if is_ru(message) else 'Service temporarily unavailable. Try later.')
        return
    try:
        send_typing(message)
        photo = message.photo[-1]
        file_info = bot.get_file(photo.file_id)
        if file_info.file_size and file_info.file_size > MAX_FILE_SIZE:
            safe_reply(message, 'Фото слишком большое (макс 5 МБ). Сожми и попробуй снова.' if is_ru(message) else 'Photo is too large (max 5 MB). Compress and try again.')
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
        track_event(f'tg_{message.from_user.id}', 'wish_created', {'method': 'image', 'source': 'bot'})
        reply_with_card_button(message, 'img_' + pack_start_param(payload), '📸')
    except Exception as e:
        log.error('analyze-image error: %s', e, exc_info=True)
        safe_reply(message, 'Не удалось проанализировать фото. Попробуй ещё раз.' if is_ru(message) else "Couldn't analyze the photo. Try again.")


@bot.message_handler(func=lambda m: m.content_type == 'text' and starts_with_want(m.text))
def handle_want_text(message):
    if _is_rate_limited(message.chat.id):
        safe_reply(message, 'Слишком много запросов. Подожди немного ⏳' if is_ru(message) else 'Too many requests. Hold on ⏳')
        return
    text = message.text.strip()
    if not LINK_SCRAPER_URL:
        safe_reply(message, 'Сервис временно недоступен. Попробуй позже.' if is_ru(message) else 'Service temporarily unavailable. Try later.')
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
        track_event(f'tg_{message.from_user.id}', 'wish_created', {'method': 'text', 'source': 'bot'})
        reply_with_card_button(message, 'text_' + pack_start_param(payload), '📝')
    except Exception as e:
        log.error('analyze-text error: %s', e, exc_info=True)
        safe_reply(message, 'Не удалось проанализировать текст. Попробуй ещё раз.' if is_ru(message) else "Couldn't analyze the text. Try again.")


@bot.message_handler(func=lambda m: m.content_type == 'text' and is_url(m.text))
def handle_link(message):
    if _is_rate_limited(message.chat.id):
        safe_reply(message, 'Слишком много запросов. Подожди немного ⏳' if is_ru(message) else 'Too many requests. Hold on ⏳')
        return
    target_url = message.text.strip()
    if not LINK_SCRAPER_URL:
        safe_reply(message, 'Сервис временно недоступен. Попробуй позже.' if is_ru(message) else 'Service temporarily unavailable. Try later.')
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
        track_event(f'tg_{message.from_user.id}', 'wish_created', {'method': 'link', 'source': 'bot'})
        reply_with_card_button(message, 'link_' + pack_start_param(payload), '🔗')
    except Exception as e:
        log.error('parse link error: %s', e, exc_info=True)
        safe_reply(message, 'Не удалось проанализировать ссылку. Попробуй ещё раз.' if is_ru(message) else "Couldn't analyze the link. Try again.")


@bot.message_handler(func=lambda m: True)
def handle_all(message):
    if is_ru(message):
        safe_reply(message, 'Отправь фото, ссылку или напиши «хочу ...» — я помогу создать карточку! 📸🔗📝')
    else:
        safe_reply(message, 'Send a photo, a link, or type "I wish ..." — I\'ll help you create a wish card! 📸🔗📝')

if __name__ == '__main__':
    log.info("Bot started | WEB_APP_URL=%s | threads=4", WEB_APP_URL)
    bot.infinity_polling(timeout=30, long_polling_timeout=25)
