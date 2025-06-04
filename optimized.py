import os
import asyncio
import logging
import re
import aiosqlite
from telethon import TelegramClient, events
from telethon.tl.types import Channel
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder
import html
from dotenv import load_dotenv

# Загрузка переменных окружения

load_dotenv()



# Конфигурация

API_ID = int(os.getenv('API_ID'))

API_HASH = os.getenv('API_HASH')

ADMIN_ID = int(os.getenv('ADMIN_ID'))

BOT_TOKEN = os.getenv('BOT_TOKEN')

SESSION_NAME = 'userbot_session'

DB_NAME = 'tracker.db'

BATCH_SIZE = 100  # Размер пакета для групповой обработки



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



# Глобальное хранилище для кэширования

tracked_chats = {}  # {chat_id: {"title": "", "username": "", "pattern": regex}}

pending_messages = []  # Буфер для пакетной обработки сообщений



def normalize_chat_id(chat_id: int) -> int:

    """Нормализация ID чата для сравнения"""

    if str(chat_id).startswith('-100'):

        # Убираем префикс '-100' из chat_id

        return int(str(chat_id)[4:])

    return chat_id



async def init_db():

    """Инициализация базы данных"""

    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute('''

            CREATE TABLE IF NOT EXISTS chats (

                id INTEGER PRIMARY KEY,

                title TEXT NOT NULL,

                username TEXT

            )

        ''')

        await db.execute('''

            CREATE TABLE IF NOT EXISTS keywords (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                chat_id INTEGER NOT NULL,

                keyword TEXT NOT NULL,

                FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE

            )

        ''')

        await db.execute('CREATE INDEX IF NOT EXISTS idx_chat_id ON keywords(chat_id)')

        await db.commit()



async def load_tracked_data():

    """Загрузка данных из базы в память"""

    async with aiosqlite.connect(DB_NAME) as db:

        # Загрузка чатов

        cursor = await db.execute('SELECT id, title, username FROM chats')

        chat_rows = await cursor.fetchall()

        for row in chat_rows:

            chat_id, title, username = row

            tracked_chats[chat_id] = {

                "title": title,

                "username": username,

                "pattern": None

            }

        

        # Загрузка ключевых слов и компиляция regex

        cursor = await db.execute('SELECT chat_id, GROUP_CONCAT(keyword) FROM keywords GROUP BY chat_id')

        keyword_rows = await cursor.fetchall()

        for chat_id, keywords_str in keyword_rows:

            if chat_id in tracked_chats and keywords_str:

                try:

                    # Экранирование и компиляция regex

                    escaped_keywords = [re.escape(kw.strip().lower()) for kw in keywords_str.split(',')]

                    pattern_str = r'\b(' + '|'.join(escaped_keywords) + r')\b'

                    tracked_chats[chat_id]["pattern"] = re.compile(pattern_str, re.IGNORECASE)

                    logger.info(f"Скомпилирован regex для чата {chat_id}: {pattern_str}")

                except Exception as e:

                    logger.error(f"Ошибка компиляции regex для чата {chat_id}: {e}")



async def add_chat(chat_id: int, title: str, username: str = ""):

    """Добавление чата в базу"""

    normalized_id = normalize_chat_id(chat_id)

    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute(

            'INSERT OR IGNORE INTO chats (id, title, username) VALUES (?, ?, ?)',

            (normalized_id, title, username)

        )

        await db.commit()

    # Обновление кэша

    tracked_chats[normalized_id] = {"title": title, "username": username, "pattern": None}



async def remove_chat(chat_id: int):

    """Удаление чата из базы"""

    normalized_id = normalize_chat_id(chat_id)

    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute('DELETE FROM chats WHERE id = ?', (normalized_id,))

        await db.commit()

    # Обновление кэша

    if normalized_id in tracked_chats:

        del tracked_chats[normalized_id]



async def add_keywords(chat_id: int, keywords: list):

    """Добавление ключевых слов"""

    normalized_id = normalize_chat_id(chat_id)

    async with aiosqlite.connect(DB_NAME) as db:

        # Пакетная вставка

        data = [(normalized_id, kw.strip().lower()) for kw in keywords]

        await db.executemany(

            'INSERT OR IGNORE INTO keywords (chat_id, keyword) VALUES (?, ?)',

            data

        )

        await db.commit()

    # Перезагрузка regex

    await reload_regex(normalized_id)



async def remove_keywords(chat_id: int, keywords: list):

    """Удаление ключевых слов"""

    normalized_id = normalize_chat_id(chat_id)

    async with aiosqlite.connect(DB_NAME) as db:

        placeholders = ','.join(['?'] * len(keywords))

        await db.execute(

            f'DELETE FROM keywords WHERE chat_id = ? AND keyword IN ({placeholders})',

            (normalized_id, *[kw.strip().lower() for kw in keywords])

        )

        await db.commit()
    # Перезагрузка regex
    await reload_regex(normalized_id)

async def reload_regex(chat_id: int):
    """Перезагрузка regex для чата"""
    normalized_id = normalize_chat_id(chat_id)
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            'SELECT GROUP_CONCAT(keyword) FROM keywords WHERE chat_id = ?',
            (normalized_id,)
        )
        result = await cursor.fetchone()
        keywords_str = result[0] if result else None
        
        if keywords_str and normalized_id in tracked_chats:
            try:
                escaped_keywords = [re.escape(kw.strip().lower()) for kw in keywords_str.split(',')]
                pattern_str = r'\b(' + '|'.join(escaped_keywords) + r')\b'
                tracked_chats[normalized_id]["pattern"] = re.compile(pattern_str, re.IGNORECASE)
                logger.info(f"Обновлен regex для чата {normalized_id}")
            except Exception as e:
                logger.error(f"Ошибка обновления regex: {e}")
        elif normalized_id in tracked_chats:
            tracked_chats[normalized_id]["pattern"] = None

def escape_markdown_v2(text: str) -> str:
    """Экранирование специальных символов для MarkdownV2"""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

async def format_message_link(chat_id: int, message_id: int) -> str:
    """Форматирование ссылки на сообщение"""
    normalized_id = normalize_chat_id(chat_id)
    chat_info = tracked_chats.get(normalized_id, {})
    username = chat_info.get("username", "")
    
    if username:
        return f"https://t.me/{username}/{message_id}"

    else:
        # Для приватных чатов
        return f"https://t.me/c/{abs(normalized_id)}/{message_id}" if normalized_id < 0 else f"https://t.me/c/{normalized_id}/{message_id}"

def format_user_html(user) -> str:
    """Форматирование имени пользователя в HTML"""
    first_name = (
        getattr(user, 'first_name', '')
        or getattr(user, 'title', '')
        or "Unknown"
    )
    username = getattr(user, 'username', None)
    escaped_name = html.escape(first_name)

    if username:
        return f'<a href="https://t.me/{html.escape(username)}">{escaped_name}</a>'

    return escaped_name


# ====================== Обработчики команд ====================== #

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return     
    welcome_text = (
        "🔍 <b>Бот для отслеживания ключевых слов в Telegram</b>\n\n"
        "<b>Доступные команды:</b>\n"
        "/add_chat - Добавить чат для мониторинга\n"
        "/remove_chat - Удалить чат\n"
        "/add_keywords - Добавить ключевые слова\n"
        "/remove_keywords - Удалить ключевые слова\n"
        "/list - Показать отслеживаемые чаты\n"
        "/help - Показать справку"

    )
    await message.answer(welcome_text, parse_mode=ParseMode.HTML)

@dp.message(Command("add_chat"))
async def cmd_add_chat(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Укажите ссылку на чат: /add_chat @username")
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

        await add_chat(chat_id, title, username)
        normalized_id = normalize_chat_id(chat_id)

        await message.answer(
            f"✅ Чат <b>{html.escape(title)}</b> добавлен\n"
            f"Исходный ID: <code>{chat_id}</code>\n"
            f"Нормализованный ID: <code>{normalized_id}</code>\n"
            f"Username: @{username if username else 'N/A'}",
            parse_mode=ParseMode.HTML

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
        await message.answer("Укажите ID чата: /remove_chat 123456789")
        return

    try:
        chat_id = int(args[1])
        normalized_id = normalize_chat_id(chat_id)

        if normalized_id in tracked_chats:
            title = tracked_chats[normalized_id]["title"]
            await remove_chat(chat_id)
            await message.answer(f"❌ Чат <b>{html.escape(title)}</b> удален", 
                               parse_mode=ParseMode.HTML)
        else:
            await message.answer("Чат не найден")

    except ValueError:
        await message.answer("Неверный формат ID чата")

@dp.message(Command("add_keywords"))
async def cmd_add_keywords(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("Использование: /add_keywords <chat_id> <ключевые слова через запятую>")
        return

    try:
        chat_id = int(args[1])
        keywords = [k.strip() for k in args[2].split(",") if k.strip()]
        normalized_id = normalize_chat_id(chat_id)

        if normalized_id not in tracked_chats:
            await message.answer("Сначала добавьте чат с помощью /add_chat")
            return

        await add_keywords(chat_id, keywords)
        await message.answer(
            f"✅ Добавлено {len(keywords)} ключевых слов в чат ID: <code>{normalized_id}</code>",
            parse_mode=ParseMode.HTML
        )

    except ValueError:
        await message.answer("Неверный формат ID чата")

@dp.message(Command("remove_keywords"))
async def cmd_remove_keywords(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    args = message.text.split(maxsplit=2)
    
    if len(args) < 3:
        await message.answer("Использование: /remove_keywords <chat_id> <ключевые слова через запятую>")
        return

    try:
        chat_id = int(args[1])
        keywords = [k.strip() for k in args[2].split(",") if k.strip()]
        normalized_id = normalize_chat_id(chat_id)
        
        if normalized_id not in tracked_chats:
            await message.answer("Чат не найден")
            return

        await remove_keywords(chat_id, keywords)
        await message.answer(f"Удалено ключевых слов: {len(keywords)} из чата ID: <code>{normalized_id}</code>", parse_mode=ParseMode.HTML)

    except ValueError:
        await message.answer("Неверный формат ID чата")



@dp.message(Command("list"))
async def cmd_list(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    if not tracked_chats:
        await message.answer("Нет отслеживаемых чатов")
        return

    response = ["📋 <b>Отслеживаемые чаты:</b>"]

    for chat_id, chat_info in tracked_chats.items():
        keywords = []
        if chat_info["pattern"]:
            # Извлекаем ключевые слова из regex
            pattern_str = chat_info["pattern"].pattern
            keywords = [

                kw.strip('()\\b').replace(r'\\', '') for kw in pattern_str.split('|')

            ] if pattern_str else []

        response.append(
            f"\n• <b>{html.escape(chat_info['title'])}</b>\n"
            f"ID: <code>{chat_id}</code>\n"
            f"Username: @{chat_info.get('username', '')}\n"
            f"Ключевые слова:\n" + "\n".join(f"- {kw}" for kw in keywords) if keywords else "- нет"
        )

    await message.answer('\n'.join(response), parse_mode=ParseMode.HTML)

# ====================== Обработчик сообщений ====================== #

@userbot.on(events.NewMessage)
async def handle_new_message(event):
    """Буферизация сообщений для пакетной обработки"""
    # Нормализуем ID чата перед обработкой
    normalized_chat_id = normalize_chat_id(event.chat_id)
    # Проверяем, отслеживается ли этот чат
    if normalized_chat_id in tracked_chats:
        pending_messages.append((normalized_chat_id, event))

    # Обрабатываем пакет при достижении размера
    if len(pending_messages) >= BATCH_SIZE:
        await process_message_batch()

async def process_message_batch():
    """Пакетная обработка сообщений"""
    global pending_messages
    if not pending_messages:
        return
    # Создаем задачи для обработки
    tasks = []
    for normalized_chat_id, event in pending_messages[:BATCH_SIZE]:
        tasks.append(process_message(normalized_chat_id, event))

    # Параллельная обработка
    await asyncio.gather(*tasks)
    # Удаляем обработанные сообщения
    pending_messages = pending_messages[BATCH_SIZE:]


async def process_message(normalized_chat_id: int, event):
    """Обработка отдельного сообщения"""
    chat_info = tracked_chats.get(normalized_chat_id)

    if not chat_info or not chat_info["pattern"] or not event.message.text:
        return
    
    # Поиск ключевых слов через regex
    text = event.message.text.lower()
    matches = chat_info["pattern"].findall(text)

    if not matches:
        return

    found_keywords = set(matches)
    logger.info(f"Найдены ключевые слова в чате {normalized_chat_id}: {found_keywords}")

    try:
        # Формирование ссылок
        message_link = await format_message_link(normalized_chat_id, event.message.id)
        chat_link = f"https://t.me/{chat_info['username']}" if chat_info.get("username") else ""

        # Формирование информации об авторе
        sender = await event.get_sender()
        author_html = format_user_html(sender)

        # Создание клавиатуры
        builder = InlineKeyboardBuilder()
        builder.button(text="Перейти к сообщению", url=message_link)

        # Формирование уведомления
        notification_html = (
            f"<b>🔔 Обнаружено ключевое слово!</b>\n\n"
            f"<b>Чат:</b> <a href='{chat_link}'>{html.escape(chat_info['title'])}</a>\n"
            f"<b>ID:</b> <code>{normalized_chat_id}</code>\n"
            f"<b>Автор:</b> {author_html}\n"
            f"<b>Ключевые слова:</b> {', '.join(found_keywords)}\n\n"
            f"<b>Сообщение:</b>\n<code>{html.escape(event.message.text[:800])}</code>"
        )

        await bot.send_message(
            chat_id=ADMIN_ID,
            text=notification_html,
            parse_mode=ParseMode.HTML,
            reply_markup=builder.as_markup(),
            disable_web_page_preview=True
        )

    except Exception as e:
        logger.error(f"Ошибка отправки уведомления: {e}", exc_info=True)


# ====================== Основная функция ====================== #

async def main():
    """Основная функция запуска"""
    # Инициализация базы данных
    await init_db()
    await load_tracked_data()

    # Запуск компонентов
    await userbot.start()
    logger.info("Userbot успешно запущен")

    # Периодическая обработка оставшихся сообщений

    async def process_pending():
        while True:
            if pending_messages:
                await process_message_batch()
            await asyncio.sleep(1)
            
    asyncio.create_task(process_pending())
    await dp.start_polling(bot)
    logger.info("Бот остановлен")


if __name__ == "__main__":
    asyncio.run(main())