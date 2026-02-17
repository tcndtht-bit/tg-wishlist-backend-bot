import base64
import io
import json
import re
import os
import urllib.parse
import telebot
from telebot import types
import requests
from PIL import Image

BOT_TOKEN = os.getenv('BOT_TOKEN')
WEB_APP_URL = os.getenv('WEB_APP_URL')
LINK_SCRAPER_URL = os.getenv('LINK_SCRAPER_URL', '').rstrip('/')

if not BOT_TOKEN:
    print("ОШИБКА: BOT_TOKEN не установлен!")
    exit(1)

if not WEB_APP_URL:
    print("ОШИБКА: WEB_APP_URL не установлен!")
    exit(1)

if not WEB_APP_URL.startswith('http://') and not WEB_APP_URL.startswith('https://'):
    WEB_APP_URL = f'https://{WEB_APP_URL}'

bot = telebot.TeleBot(BOT_TOKEN)


def is_url(text):
    return bool(re.match(r'^https?://\S+$', (text or '').strip()))


def pack_start_param(payload):
    return base64.b64encode(json.dumps(payload).encode()).decode()


@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message,
        "Привет! 👋\n\n"
        "Отправь фото, ссылку или текст «хочу ...» — я помогу создать карточку желания.\n\n"
        "📸 Фото — анализ изображения\n"
        "🔗 Ссылка — анализ страницы товара\n"
        "📝 «Хочу ...» — карточка из текста")


@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    if not LINK_SCRAPER_URL:
        bot.reply_to(message, 'Сервис временно недоступен. Попробуй позже.')
        return
    try:
        photo = message.photo[-1]
        file_info = bot.get_file(photo.file_id)
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
        img_resp = requests.get(file_url, timeout=15)
        img_resp.raise_for_status()
        img_b64_raw = base64.b64encode(img_resp.content).decode()
        r = requests.post(
            f'{LINK_SCRAPER_URL}/analyze-image',
            json={'image': img_b64_raw},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        # Сжимаем изображение для передачи в start_param (ограничение длины URL)
        img_b64 = None
        try:
            img = Image.open(io.BytesIO(img_resp.content))
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=85, optimize=True)
            img_b64 = base64.b64encode(buf.getvalue()).decode()
        except Exception:
            pass
        payload = {
            'n': data.get('name') or 'N/A',
            'p': data.get('price'),
            'c': data.get('currency'),
            's': data.get('size'),
        }
        if img_b64:
            payload['i'] = img_b64
        start_param = 'img_' + pack_start_param(payload)
        app_url = f"{WEB_APP_URL}#tgWebAppStartParam={urllib.parse.quote(start_param, safe='')}"
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton(
            text='📸 Создать карточку',
            web_app=types.WebAppInfo(url=app_url),
        ))
        bot.reply_to(message, 'Нажми кнопку ниже, чтобы создать карточку 👇', reply_markup=keyboard)
    except Exception as e:
        print(f'Ошибка analyze-image: {e}')
        bot.reply_to(message, 'Не удалось проанализировать фото. Попробуй ещё раз.')


def starts_with_want(text):
    return (text or '').strip().lower().startswith('хочу')


@bot.message_handler(func=lambda m: m.content_type == 'text' and starts_with_want(m.text))
def handle_want_text(message):
    text = message.text.strip()
    if not LINK_SCRAPER_URL:
        bot.reply_to(message, 'Сервис временно недоступен. Попробуй позже.')
        return
    try:
        r = requests.post(
            f'{LINK_SCRAPER_URL}/store-wish-text',
            json={'text': text},
            timeout=10,
        )
        r.raise_for_status()
        wid = r.json().get('id')
        if not wid:
            raise ValueError('No id returned')
        r2 = requests.get(
            f'{LINK_SCRAPER_URL}/wish-text?id={wid}&analyze=1',
            timeout=20,
        )
        r2.raise_for_status()
        data = r2.json()
        payload = {
            'n': data.get('name') or 'Желание',
            'p': data.get('price'),
            'c': data.get('currency'),
            's': data.get('size'),
        }
        start_param = 'text_' + pack_start_param(payload)
        app_url = f'{WEB_APP_URL}#tgWebAppStartParam={urllib.parse.quote(start_param, safe="")}'
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton(
            text='📝 Создать карточку',
            web_app=types.WebAppInfo(url=app_url),
        ))
        bot.reply_to(message, 'Нажми кнопку ниже, чтобы создать карточку 👇', reply_markup=keyboard)
    except Exception as e:
        print(f'Ошибка wish-text: {e}')
        bot.reply_to(message, 'Не удалось проанализировать текст. Попробуй ещё раз.')


@bot.message_handler(func=lambda m: m.content_type == 'text' and is_url(m.text))
def handle_link(message):
    target_url = message.text.strip()
    if not LINK_SCRAPER_URL:
        bot.reply_to(message, 'Сервис временно недоступен. Попробуй позже.')
        return
    try:
        r = requests.get(
            f'{LINK_SCRAPER_URL}/?url={urllib.parse.quote(target_url, safe="")}',
            timeout=45,
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
        start_param = 'link_' + pack_start_param(payload)
        app_url = f"{WEB_APP_URL}#tgWebAppStartParam={urllib.parse.quote(start_param, safe='')}"
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton(
            text='🔗 Создать карточку',
            web_app=types.WebAppInfo(url=app_url),
        ))
        bot.reply_to(message, 'Нажми кнопку ниже, чтобы создать карточку 👇', reply_markup=keyboard)
    except Exception as e:
        print(f'Ошибка parse link: {e}')
        bot.reply_to(message, 'Не удалось проанализировать ссылку. Попробуй ещё раз.')


@bot.message_handler(func=lambda m: True)
def handle_all(message):
    bot.reply_to(message,
        'Отправь фото, ссылку или напиши «хочу ...» — я помогу создать карточку! 📸🔗📝')

if __name__ == '__main__':
    print("Бот запущен!")
    print(f"WEB_APP_URL: {WEB_APP_URL}")
    bot.polling(none_stop=True)
