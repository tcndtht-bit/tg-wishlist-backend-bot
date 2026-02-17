import re
import os
import urllib.parse
import telebot
from telebot import types
import requests

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
    try:
        photo = message.photo[-1]
        file_id = photo.file_id
        
        file_info = bot.get_file(file_id)
        file_path = file_info.file_path
        
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        
        # Кодируем URL для передачи в start_param
        encoded_url = urllib.parse.quote(file_url, safe='')
        start_param = f"img_url_{encoded_url}"
        
        # Формируем URL с start_param в hash (Telegram передает через initData)
        # Также добавляем в query на случай, если initData не работает
        web_app_url = f"{WEB_APP_URL}#start_param={urllib.parse.quote(start_param)}"
        
        keyboard = types.InlineKeyboardMarkup()
        button = types.InlineKeyboardButton(
            text="📸 Анализировать изображение",
            web_app=types.WebAppInfo(url=web_app_url)
        )
        keyboard.add(button)
        
        # Логируем для отладки
        print(f"Start param: {start_param}")
        print(f"Web app URL: {web_app_url}")
        
        bot.reply_to(message, 
            "Открываю мини-приложение для анализа изображения...\n\n"
            "Нажми кнопку ниже 👇",
            reply_markup=keyboard
        )
    except Exception as e:
        error_msg = str(e)
        print(f"Ошибка при обработке фото: {error_msg}")
        bot.reply_to(message, f"Произошла ошибка: {error_msg}. Попробуй отправить фото еще раз.")


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
        data = r.json()
        wid = data.get('id')
        if not wid:
            raise ValueError('No id returned')
        start_param = f'text_{wid}'
        app_url = f'{WEB_APP_URL}#tgWebAppStartParam={urllib.parse.quote(start_param, safe="")}'
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton(
            text='📝 Создать карточку',
            web_app=types.WebAppInfo(url=app_url),
        ))
        bot.reply_to(message,
            'Нажми кнопку ниже, чтобы создать карточку желания.',
            reply_markup=keyboard,
        )
    except Exception as e:
        print(f'Ошибка store-wish-text: {e}')
        bot.reply_to(message, 'Не удалось сохранить текст. Попробуй ещё раз.')


@bot.message_handler(func=lambda m: m.content_type == 'text' and is_url(m.text))
def handle_link(message):
    text = message.text.strip()
    encoded_url = urllib.parse.quote(text, safe='')
    start_param = f"link_{encoded_url}"
    web_app_url = f"{WEB_APP_URL}#tgWebAppStartParam={urllib.parse.quote(start_param, safe='')}"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton(
        text="🔗 Анализировать ссылку",
        web_app=types.WebAppInfo(url=web_app_url)
    ))
    bot.reply_to(message,
        "Нажми кнопку ниже, чтобы открыть мини-приложение и создать карточку желания по ссылке.",
        reply_markup=keyboard)


@bot.message_handler(func=lambda m: True)
def handle_all(message):
    bot.reply_to(message,
        'Отправь фото, ссылку или напиши «хочу ...» — я помогу создать карточку! 📸🔗📝')

if __name__ == '__main__':
    print("Бот запущен!")
    print(f"WEB_APP_URL: {WEB_APP_URL}")
    bot.polling(none_stop=True)
