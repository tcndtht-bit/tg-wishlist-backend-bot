import telebot
from telebot import types
import urllib.parse
import os

BOT_TOKEN = os.getenv('BOT_TOKEN')
WEB_APP_URL = os.getenv('WEB_APP_URL')

if not BOT_TOKEN:
    print("ОШИБКА: BOT_TOKEN не установлен!")
    exit(1)

if not WEB_APP_URL:
    print("ОШИБКА: WEB_APP_URL не установлен!")
    exit(1)

# Убеждаемся, что URL начинается с https://
if not WEB_APP_URL.startswith('http://') and not WEB_APP_URL.startswith('https://'):
    WEB_APP_URL = f'https://{WEB_APP_URL}'

bot = telebot.TeleBot(BOT_TOKEN)

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, 
        "Привет! 👋\n\n"
        "Отправь мне фотографию товара, и я помогу создать карточку желания.\n\n"
        "Просто отправь фото — я открою мини-приложение для анализа.")

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    try:
        photo = message.photo[-1]
        file_id = photo.file_id
        
        file_info = bot.get_file(file_id)
        file_path = file_info.file_path
        
        file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        
        encoded_url = urllib.parse.quote(file_url, safe='')
        start_param = f"img_url_{encoded_url}"
        
        keyboard = types.InlineKeyboardMarkup()
        button = types.InlineKeyboardButton(
            text="📸 Анализировать изображение",
            web_app=types.WebAppInfo(url=f"{WEB_APP_URL}?start_param={start_param}")
        )
        keyboard.add(button)
        
        bot.reply_to(message, 
            "Открываю мини-приложение для анализа изображения...\n\n"
            "Нажми кнопку ниже 👇",
            reply_markup=keyboard
        )
    except Exception as e:
        error_msg = str(e)
        print(f"Ошибка при обработке фото: {error_msg}")
        bot.reply_to(message, f"Произошла ошибка: {error_msg}. Попробуй отправить фото еще раз.")

@bot.message_handler(func=lambda message: True)
def handle_all(message):
    bot.reply_to(message, 
        "Отправь мне фотографию товара, и я помогу создать карточку желания! 📸"
    )

if __name__ == '__main__':
    print("Бот запущен!")
    print(f"WEB_APP_URL: {WEB_APP_URL}")
    bot.polling(none_stop=True)
