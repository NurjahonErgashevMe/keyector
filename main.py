import os
import asyncio
import logging
import re
from telethon import TelegramClient, events
from telethon.tl.types import Channel, User
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder
import html  # Для экранирования HTML
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Конфигурация
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
BOT_TOKEN = os.getenv('BOT_TOKEN')
SESSION_NAME = 'userbot_session'

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация клиентов
userbot = TelegramClient(SESSION_NAME, API_ID, API_HASH)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Глобальное хранилище данных
tracked_data = {
    "chats": {},  # {chat_id: {"title": "", "username": ""}}
    "keywords": {},  # {chat_id: set(["keyword1", "keyword2"])}
}

def escape_markdown_v2(text: str) -> str:
    """Экранирование специальных символов для MarkdownV2"""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

def normalize_chat_id(chat_id: int) -> int:
    """Нормализация ID чата для сравнения"""
    if str(chat_id).startswith('-100'):
        # Убираем префикс '-100' из chat_id
        return int(str(chat_id)[4:])
    return chat_id


async def format_message_link(chat_id: int, message_id: int) -> str:
    """Форматирование ссылки на сообщение"""
    chat_info = tracked_data["chats"].get(chat_id, {})
    username = chat_info.get("username", "")
    
    if username:
        return f"https://t.me/{username}/{message_id}"
    else:
        # Для приватных чатов без username
        return f"https://t.me/c/{abs(chat_id)}/{message_id}" if chat_id < 0 else f"https://t.me/c/{chat_id}/{message_id}"

def format_user_link(user) -> str:
    """Форматирование ссылки на пользователя в MarkdownV2"""
    user_id = user.id
    first_name = getattr(user, 'first_name', '') or getattr(user, 'title', '') or "Unknown"
    return f"[{escape_markdown_v2(first_name)}](tg://user?id={user_id})"

def format_user_html(user) -> str:
    """Форматирование ссылки на пользователя в HTML"""
    user_id = user.id
    first_name = getattr(user, 'first_name', '') or getattr(user, 'title', '') or "Unknown"
    return f'<a href="tg://user?id={user_id}">{html.escape(first_name)}</a>'

# ====================== AIogram Handlers (Бот) ====================== #
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
        
    welcome_text = (
        "🔍 *Бот для отслеживания ключевых слов в Telegram*\n\n"
        "Доступные команды:\n"
        "/add\\_chat \\- Добавить чат для мониторинга\n"
        "/remove\\_chat \\- Удалить чат\n"
        "/add\\_keywords \\- Добавить ключевые слова\n"
        "/remove\\_keywords \\- Удалить ключевые слова\n"
        "/list \\- Показать отслеживаемые чаты\n"
        "/help \\- Показать справку"
    )
    await message.answer(welcome_text, parse_mode=ParseMode.MARKDOWN_V2)

@dp.message(Command("add_chat"))
async def cmd_add_chat(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Укажите ссылку на чат: /add_chat @username", parse_mode=ParseMode.MARKDOWN_V2)
        return

    try:
        ref = args[1].strip()
        entity = await userbot.get_entity(ref)
        
        if not isinstance(entity, Channel):
            await message.answer("Это не чат/канал!")
            return
            
        chat_id = entity.id
        title = entity.title
        username = getattr(entity, 'username', None) or ""
        
        # Нормализуем ID чата
        chat_id = normalize_chat_id(chat_id)
        
        # Добавляем чат в отслеживаемые
        tracked_data["chats"][chat_id] = {
            "title": title,
            "username": username
        }
        
        # Инициализируем множество для ключевых слов
        if chat_id not in tracked_data["keywords"]:
            tracked_data["keywords"][chat_id] = set()
        
        await message.answer(
            f"✅ Чат *{escape_markdown_v2(title)}* добавлен для отслеживания\.\n"
            f"ID: `{chat_id}`\n"
            f"Username: @{username if username else 'N/A'}",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        logger.error(f"Ошибка добавления чата: {e}")
        await message.answer("❌ Ошибка: проверьте ссылку или права доступа")

@dp.message(Command("remove_chat"))
async def cmd_remove_chat(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Укажите ID чата: /remove_chat 123456789", parse_mode=ParseMode.MARKDOWN_V2)
        return

    try:
        chat_id = int(args[1])
        # Нормализуем ID чата
        chat_id = normalize_chat_id(chat_id)
        
        if chat_id in tracked_data["chats"]:
            title = tracked_data["chats"][chat_id]["title"]
            del tracked_data["chats"][chat_id]
            if chat_id in tracked_data["keywords"]:
                del tracked_data["keywords"][chat_id]
            await message.answer(f"❌ Чат *{escape_markdown_v2(title)}* удален", 
                               parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await message.answer("Чат не найден в списке отслеживаемых")
    except ValueError:
        await message.answer("Неверный формат ID чата")

@dp.message(Command("add_keywords"))
async def cmd_add_keywords(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        # Экранируем специальные символы
        usage_text = escape_markdown_v2("Использование: /add_keywords <chat_id> <ключевые слова через запятую>")
        await message.answer(usage_text, parse_mode=ParseMode.MARKDOWN_V2)
        return

    try:
        chat_id = int(args[1])
        # Нормализуем ID чата
        chat_id = normalize_chat_id(chat_id)
        keywords = [k.strip().lower() for k in args[2].split(",") if k.strip()]
        
        if chat_id not in tracked_data["keywords"]:
            await message.answer("Сначала добавьте чат с помощью /add_chat")
            return
            
        tracked_data["keywords"][chat_id].update(keywords)
        count = len(tracked_data["keywords"][chat_id])
        
        await message.answer(
            f"✅ Добавлено {len(keywords)} ключевых слов\n"
            f"Всего слов для чата: {count}",
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except ValueError:
        await message.answer("Неверный формат ID чата")

@dp.message(Command("remove_keywords"))
async def cmd_remove_keywords(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        # Экранируем специальные символы
        usage_text = escape_markdown_v2("Использование: /remove_keywords <chat_id> <ключевые слова через запятую>")
        await message.answer(usage_text, parse_mode=ParseMode.MARKDOWN_V2)
        return

    try:
        chat_id = int(args[1])
        # Нормализуем ID чата
        chat_id = normalize_chat_id(chat_id)
        keywords = [k.strip().lower() for k in args[2].split(",") if k.strip()]
        
        if chat_id not in tracked_data["keywords"]:
            await message.answer("Чат не найден")
            return
            
        removed = 0
        for kw in keywords:
            if kw in tracked_data["keywords"][chat_id]:
                tracked_data["keywords"][chat_id].remove(kw)
                removed += 1
                
        await message.answer(f"Удалено ключевых слов: {removed}")
    except ValueError:
        await message.answer("Неверный формат ID чата")

@dp.message(Command("list"))
async def cmd_list(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    if not tracked_data["chats"]:
        await message.answer("Нет отслеживаемых чатов")
        return

    response = "📋 *Отслеживаемые чаты:*\n\n"
    for chat_id, chat_info in tracked_data["chats"].items():
        keywords = tracked_data["keywords"].get(chat_id, set())
        response += (
            f"• *{escape_markdown_v2(chat_info['title'])}* \n"
            f"ID: `{chat_id}` \n"
            f"Ключевые слова: {', '.join(keywords) if keywords else 'нет'}\n\n"
        )
    
    await message.answer(response, parse_mode=ParseMode.MARKDOWN_V2)

# ====================== Telethon Handler (Userbot) ====================== #
@userbot.on(events.NewMessage)
async def handle_new_message(event):
    """Обработка новых сообщений в отслеживаемых чатах"""
    original_chat_id = event.chat_id
    chat_id = normalize_chat_id(original_chat_id)
    logger.info(f"Новое сообщение. Оригинальный ID: {original_chat_id}, Нормализованный ID: {chat_id}")
    
    # Пропускаем сообщения из неотслеживаемых чатов
    if chat_id not in tracked_data["chats"]:
        logger.info(f"Чат {chat_id} не отслеживается")
        return
    
    logger.info(f"Чат {chat_id} отслеживается")
    
    # Пропускаем сообщения без текста
    if not event.message.text:
        logger.info("Сообщение без текста, пропускаем")
        return
    
    # Проверяем наличие ключевых слов
    keywords = tracked_data["keywords"].get(chat_id, set())
    text_lower = event.message.text.lower()
    
    logger.info(f"Ключевые слова для чата {chat_id}: {keywords}")
    logger.info(f"Текст сообщения: {text_lower[:100]}{'...' if len(text_lower) > 100 else ''}")
    
    # Проверяем каждое ключевое слово
    found_keywords = []
    for keyword in keywords:
        if keyword in text_lower:
            found_keywords.append(keyword)
    
    if not found_keywords:
        logger.info("Ключевые слова не найдены в сообщении")
        return
    
    logger.info(f"Найдены ключевые слова: {found_keywords}")
    
    try:
        # Получаем информацию о чате
        chat_info = tracked_data["chats"][chat_id]
        chat_title = chat_info["title"]
        chat_username = chat_info.get("username", "")
        
        # Формируем ссылки
        message_link = await format_message_link(chat_id, event.message.id)
        chat_link = f"https://t.me/{chat_username}" if chat_username else ""
        
        # Формируем информацию об авторе
        sender = await event.get_sender()
        sender_name = getattr(sender, 'first_name', '') or getattr(sender, 'title', '') or "Unknown"
        
        # Всегда используем tg://user?id ссылку
        author_md = f"[{escape_markdown_v2(sender_name)}](tg://user?id={sender.id})"
        author_html = f'<a href="tg://user?id={sender.id}">{html.escape(sender_name)}</a>'
        
        # Создаем клавиатуру с кнопкой
        builder = InlineKeyboardBuilder()
        builder.button(text="Перейти к сообщению", url=message_link)
        
        # Формируем сообщение в MarkdownV2 и HTML
        notification_md = (
            f"🔔 *Обнаружено ключевое слово\!*\n\n"
            f"*Чат:* [{escape_markdown_v2(chat_title)}]({chat_link if chat_link else '#'})\n"
            f"*Автор:* {author_md}\n"
            f"*Ключевые слова:* {', '.join(found_keywords)}\n\n"
            f"*Сообщение:*\n`{escape_markdown_v2(event.message.text[:800])}`"
        )
        
        notification_html = (
            f"<b>🔔 Обнаружено ключевое слово!</b>\n\n"
            f"<b>Чат:</b> <a href='{chat_link}'>{html.escape(chat_title)}</a>\n"
            f"<b>Автор:</b> {author_html}\n"
            f"<b>Ключевые слова:</b> {', '.join(found_keywords)}\n\n"
            f"<b>Сообщение:</b>\n<code>{html.escape(event.message.text[:800])}</code>"
        )
        
        # Пытаемся отправить в MarkdownV2, если не получится - отправляем HTML
        try:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=notification_md,
                parse_mode=ParseMode.MARKDOWN_V2,
                reply_markup=builder.as_markup(),
                disable_web_page_preview=True
            )
            logger.info("Уведомление успешно отправлено в MarkdownV2")
        except Exception as md_error:
            logger.warning(f"Ошибка MarkdownV2: {md_error}. Отправляем в HTML")
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=notification_html,
                parse_mode=ParseMode.HTML,
                reply_markup=builder.as_markup(),
                disable_web_page_preview=True
            )
            logger.info("Уведомление успешно отправлено в HTML")
            
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления: {e}", exc_info=True)

# ====================== Main Function ====================== #
async def main():
    """Основная функция запуска"""
    await userbot.start()
    logger.info("Userbot успешно запущен")
    
    await dp.start_polling(bot)
    logger.info("Бот остановлен")

if __name__ == "__main__":
    asyncio.run(main())