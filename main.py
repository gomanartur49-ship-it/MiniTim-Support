import asyncio
import html
import logging
import os
import re
import subprocess
import sys


def ensure_deps():
    """Автоустановка нужных библиотек, если их нет."""
    try:
        from aiogram.client.default import DefaultBotProperties  # noqa: F401
        return
    except ImportError:
        pass

    print("📦 Устанавливаю aiogram, подожди минуту...")
    cmd = [sys.executable, "-m", "pip", "install", "-q", "-U", "aiogram>=3.13,<4"]
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError:
        try:
            subprocess.check_call([sys.executable, "-m", "ensurepip", "--upgrade"])
        except Exception:
            pass
        try:
            subprocess.check_call(cmd)
        except subprocess.CalledProcessError:
            subprocess.check_call(cmd + ["--break-system-packages"])
    print("✅ Всё установлено.")


ensure_deps()

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# ====================== НАСТРОЙКИ ======================
# Токен берётся из переменной окружения (Bothost передаёт его из формы).
# Если хостишь без переменных — впиши токен в кавычки ниже (но не выкладывай его в публичный репозиторий!).
BOT_TOKEN = (
    os.getenv("BOT_TOKEN")
    or os.getenv("TOKEN")
    or os.getenv("TELEGRAM_BOT_TOKEN")
    or ""
)

ADMIN_IDS = [7869659514, 6413320766]

# Ссылка на ТГК (или ссылка-подключение). Для IP используй текст в PLAY_TEXT ниже.
PLAY_URL = "https://t.me/your_channel"  # <-- замени на свой ТГК

RULES_TEXT = (
    "📜 <b>Правила MiniTim</b>\n\n"
    "1. Уважайте других игроков.\n"
    "2. Запрещены читы и использование багов.\n"
    "3. Запрещён гриферство и воровство.\n"
    "4. Запрещена реклама и спам.\n"
    "5. Слушайте администрацию.\n\n"
    "<i>Отредактируй этот текст в RULES_TEXT.</i>"
)

WELCOME_TEXT = (
    "👋 Добро пожаловать в службу поддержки <b>MiniTim</b>!\n\n"
    "Опишите вашу проблему. Если это баг — прикрепите скриншот.\n"
    "Администрация ответит в ближайшее время."
)
# =======================================================

logging.basicConfig(level=logging.INFO)

router = Router()
admin_router = Router()

# (chat_id админа, message_id) -> user_id игрока
reply_map: dict[tuple[int, int], int] = {}


class Support(StatesGroup):
    waiting = State()


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎮 Играть", url=PLAY_URL)],
            [InlineKeyboardButton(text="📜 Правила", callback_data="rules")],
            [InlineKeyboardButton(text="🆘 Поддержка", callback_data="support")],
        ]
    )


def back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")]
        ]
    )


# ---------------- Ответы админов игрокам ----------------
@admin_router.message(
    F.chat.type == "private",
    F.from_user.id.in_(ADMIN_IDS),
    F.reply_to_message,
    ~F.text.startswith("/"),
)
async def admin_reply(message: Message, bot: Bot):
    replied = message.reply_to_message
    user_id = reply_map.get((message.chat.id, replied.message_id))

    if user_id is None:
        # запасной вариант: достаём ID из шапки сообщения
        source = replied.text or replied.caption or ""
        match = re.search(r"ID:\s*(\d+)", source)
        if match:
            user_id = int(match.group(1))

    if user_id is None:
        await message.answer("⚠️ Не нашёл, какому игроку ответить. Отвечай реплаем на сообщение игрока.")
        return

    try:
        await bot.send_message(user_id, "💬 <b>Ответ от администрации:</b>")
        await bot.copy_message(user_id, message.chat.id, message.message_id)
        await message.answer("✅ Ответ отправлен игроку.")
    except Exception as e:
        logging.warning("Не удалось отправить ответ игроку %s: %s", user_id, e)
        await message.answer("❌ Не удалось отправить (игрок мог заблокировать бота).")


# ---------------- Меню ----------------
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(WELCOME_TEXT, reply_markup=main_menu())


@router.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.answer(WELCOME_TEXT, reply_markup=main_menu())
    await call.answer()


@router.message(Command("rules"))
async def cmd_rules(message: Message):
    await message.answer(RULES_TEXT, reply_markup=back_menu())


@router.callback_query(F.data == "rules")
async def cb_rules(call: CallbackQuery):
    await call.message.answer(RULES_TEXT, reply_markup=back_menu())
    await call.answer()


# ---------------- Режим поддержки ----------------
@router.callback_query(F.data == "support")
async def cb_support(call: CallbackQuery, state: FSMContext):
    await state.set_state(Support.waiting)
    await call.message.answer(
        "🆘 <b>Режим поддержки</b>\n\n"
        "Напишите ваш вопрос одним или несколькими сообщениями. "
        "Если это баг — прикрепите скриншот.",
        reply_markup=back_menu(),
    )
    await call.answer()


@router.message(Support.waiting, F.chat.type == "private")
async def forward_to_admins(message: Message, bot: Bot, state: FSMContext):
    user = message.from_user
    username = f"@{user.username}" if user.username else "нет username"
    header = (
        "📩 <b>Новое обращение</b>\n"
        f"👤 {html.escape(user.full_name)} ({html.escape(username)})\n"
        f"ID: {user.id}\n\n"
        "<i>Чтобы ответить — сделай реплай на это сообщение.</i>"
    )

    delivered = False
    for admin_id in ADMIN_IDS:
        try:
            head = await bot.send_message(admin_id, header)
            copy = await bot.copy_message(
                admin_id, message.chat.id, message.message_id
            )
            reply_map[(admin_id, head.message_id)] = user.id
            reply_map[(admin_id, copy.message_id)] = user.id
            delivered = True
        except Exception as e:
            logging.warning("Не удалось отправить админу %s: %s", admin_id, e)

    if delivered:
        await message.answer(
            "✅ Сообщение отправлено администрации. Ожидайте ответа.\n"
            "Можете дописать ещё или вернуться в меню.",
            reply_markup=back_menu(),
        )
    else:
        await message.answer("❌ Не удалось связаться с администрацией. Попробуйте позже.")


@router.message(F.chat.type == "private")
async def fallback(message: Message):
    await message.answer(
        "Нажмите «🆘 Поддержка», чтобы отправить вопрос администрации.",
        reply_markup=main_menu(),
    )


async def main():
    token = BOT_TOKEN
    if not token:
        raise SystemExit("Не задан токен: укажи BOT_TOKEN в переменных окружения или в main.py")
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(admin_router)  # сначала ответы админов
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
