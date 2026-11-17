import asyncio
import html
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone


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
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

# ====================== НАСТРОЙКИ ======================
BOT_TOKEN = (
    os.getenv("BOT_TOKEN")
    or os.getenv("TOKEN")
    or os.getenv("TELEGRAM_BOT_TOKEN")
    or ""
)

ADMIN_IDS = [7869659514, 6413320766]

PLAY_URL = "https://t.me/minitimx"
SITE_URL = "https://site.minitim.fun"

# Файл, где хранятся обращения (чтобы не пропадали при перезапуске бота).
DATA_FILE = os.getenv("TICKETS_FILE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "tickets.json"
)

# Часовой пояс для времени в обращениях (МСК = +3)
TZ = timezone(timedelta(hours=3))

ADMIN_BTN = "📋 Обращения"

WELCOME_TEXT = (
    "<b>Добро пожаловать в официальную техподдержку MiniTim SMP.</b>\n\n"
    "Здесь вы можете задать интересующий вас вопрос или обратиться к администрации "
    "проекта по любым возникающим ситуациям.\n\n"
    "Опишите вашу проблему или вопрос одним сообщением. Администрация рассмотрит "
    "обращение и ответит вам в ближайшее время."
)
# =======================================================

logging.basicConfig(level=logging.INFO)

router = Router()
admin_router = Router()

# ---------------- Хранилище обращений ----------------
tickets: dict[int, dict] = {}
counter = 0
# admin_id -> id обращения, с которым админ сейчас ведёт диалог
admin_dialog: dict[int, int] = {}


def load_data():
    global counter
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
        counter = int(data.get("counter", 0))
        for k, v in data.get("tickets", {}).items():
            tickets[int(k)] = v
        logging.info("Загружено обращений: %s", len(tickets))
    except FileNotFoundError:
        pass
    except Exception as e:
        logging.warning("Не удалось прочитать %s: %s", DATA_FILE, e)


def save_data():
    tmp = DATA_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"counter": counter, "tickets": tickets}, f, ensure_ascii=False)
        os.replace(tmp, DATA_FILE)
    except Exception as e:
        logging.warning("Не удалось сохранить %s: %s", DATA_FILE, e)


def fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%d.%m %H:%M")


def new_ticket(user) -> dict:
    global counter
    counter += 1
    now = time.time()
    t = {
        "id": counter,
        "user_id": user.id,
        "name": user.full_name,
        "username": user.username or "",
        "status": "open",
        "created": now,
        "updated": now,
        "messages": [],
    }
    tickets[counter] = t
    return t


def add_msg(t: dict, who: str, text: str, name: str = ""):
    now = time.time()
    t["messages"].append({"who": who, "text": text, "ts": now, "name": name})
    t["messages"] = t["messages"][-60:]
    t["updated"] = now


def find_open(user_id: int):
    found = [t for t in tickets.values() if t["user_id"] == user_id and t["status"] == "open"]
    return max(found, key=lambda t: t["updated"]) if found else None


def drop_dialogs(ticket_id: int):
    for admin_id, tid in list(admin_dialog.items()):
        if tid == ticket_id:
            del admin_dialog[admin_id]


def describe(m: Message) -> str:
    text = m.text or m.caption or ""
    if m.animation:
        tag = "[гиф]"
    elif m.photo:
        tag = "[фото]"
    elif m.video:
        tag = "[видео]"
    elif m.video_note:
        tag = "[кружок]"
    elif m.voice:
        tag = "[голосовое]"
    elif m.audio:
        tag = "[аудио]"
    elif m.document:
        tag = "[файл]"
    elif m.sticker:
        tag = "[стикер]"
    else:
        tag = ""
    return f"{tag} {text}".strip() or "[сообщение]"


def user_label(t: dict) -> str:
    uname = f"@{t['username']}" if t["username"] else "нет username"
    return f"{html.escape(t['name'])} ({html.escape(uname)})"


# ---------------- Клавиатуры ----------------
def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎮 Играть", url=PLAY_URL)],
            [InlineKeyboardButton(text="⌨ Сайт", url=SITE_URL)],
            [InlineKeyboardButton(text="🆘 Поддержка", callback_data="support")],
        ]
    )


def back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")]]
    )


def admin_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=ADMIN_BTN)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def dialog_kb(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏹ Выйти из диалога", callback_data="adm:exit"),
                InlineKeyboardButton(text="✅ Закрыть", callback_data=f"adm:close:{ticket_id}"),
            ]
        ]
    )


def ticket_notify_kb(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💬 Ответить", callback_data=f"adm:reply:{ticket_id}"),
                InlineKeyboardButton(text="📂 Открыть", callback_data=f"adm:view:{ticket_id}"),
            ]
        ]
    )


# ---------------- Вьюхи для админов ----------------
PAGE_SIZE = 8


def list_view(status: str, page: int):
    items = sorted(
        (t for t in tickets.values() if t["status"] == status),
        key=lambda t: t["updated"],
        reverse=True,
    )
    total = len(items)
    pages = max(1, -(-total // PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    chunk = items[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    title = "Открытые обращения" if status == "open" else "Закрытые обращения"
    text = f"📋 <b>{title}</b> — {total}"
    if not total:
        text += "\n\nПока пусто."
    elif status == "open":
        text += "\n\n🔴 ждёт ответа · 🟢 вы ответили"

    rows = []
    for t in chunk:
        if status == "closed":
            icon = "⚪"
        else:
            last = t["messages"][-1]["who"] if t["messages"] else "user"
            icon = "🔴" if last == "user" else "🟢"
        label = f"{icon} #{t['id']} · {t['name'][:20]} · {fmt(t['updated'])}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"adm:view:{t['id']}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"adm:list:{status}:{page - 1}"))
    if pages > 1:
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="adm:noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"adm:list:{status}:{page + 1}"))
    if nav:
        rows.append(nav)

    if status == "open":
        rows.append([InlineKeyboardButton(text="🗂 Закрытые", callback_data="adm:list:closed:0")])
    else:
        rows.append([InlineKeyboardButton(text="📂 Открытые", callback_data="adm:list:open:0")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def card_view(t: dict):
    state = "🟢 открыто" if t["status"] == "open" else "⚪ закрыто"
    lines = [
        f"🎫 <b>Обращение #{t['id']}</b> — {state}",
        f"👤 {user_label(t)}",
        f"🆔 <code>{t['user_id']}</code>",
        f"🕒 Создано: {fmt(t['created'])}",
        "",
        "<b>Переписка:</b>",
    ]
    for m in t["messages"][-8:]:
        if m["who"] == "user":
            who = "👤"
        else:
            who = f"👨‍💼 {html.escape(m.get('name') or 'Админ')}"
        txt = html.escape(m["text"][:200])
        lines.append(f"{who} <i>{fmt(m['ts'])}</i>: {txt}")

    back = f"adm:list:{t['status']}:0"
    if t["status"] == "open":
        rows = [
            [
                InlineKeyboardButton(text="💬 Ответить", callback_data=f"adm:reply:{t['id']}"),
                InlineKeyboardButton(text="✅ Закрыть", callback_data=f"adm:close:{t['id']}"),
            ]
        ]
    else:
        rows = [[InlineKeyboardButton(text="🔄 Открыть снова", callback_data=f"adm:reopen:{t['id']}")]]
    rows.append([InlineKeyboardButton(text="⬅️ К списку", callback_data=back)])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def show(call: CallbackQuery, text: str, kb: InlineKeyboardMarkup):
    """Редактирует сообщение с кнопкой, а если нельзя — отправляет новое."""
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" in str(e):
            return
        await call.message.answer(text, reply_markup=kb)


# ---------------- Уведомления ----------------
async def notify_admins(bot: Bot, t: dict, message: Message, is_new: bool):
    label = user_label(t)
    if is_new:
        head = f"📩 <b>Новое обращение #{t['id']}</b>\n👤 {label}\n🆔 <code>{t['user_id']}</code>"
    else:
        head = f"💬 <b>Обращение #{t['id']}</b> — сообщение от {label}"

    for admin_id in ADMIN_IDS:
        try:
            if admin_dialog.get(admin_id) == t["id"]:
                # админ уже в диалоге с этим игроком — шлём без лишних кнопок
                if message.text:
                    await bot.send_message(
                        admin_id, f"👤 <b>{html.escape(t['name'])}:</b> {message.html_text}"
                    )
                else:
                    await bot.send_message(admin_id, f"👤 <b>{html.escape(t['name'])}:</b>")
                    await bot.copy_message(admin_id, message.chat.id, message.message_id)
            else:
                await bot.send_message(admin_id, head, reply_markup=ticket_notify_kb(t["id"]))
                await bot.copy_message(admin_id, message.chat.id, message.message_id)
        except Exception as e:
            logging.warning("Не удалось отправить админу %s: %s", admin_id, e)


async def send_to_user(bot: Bot, t: dict, message: Message):
    head = f"💬 <b>Ответ администрации</b> · обращение #{t['id']}"
    if message.text:
        await bot.send_message(t["user_id"], f"{head}\n\n{message.html_text}")
    else:
        await bot.send_message(t["user_id"], head)
        await bot.copy_message(t["user_id"], message.chat.id, message.message_id)


# ====================== АДМИНЫ ======================
@admin_router.message(Command("tickets"), F.chat.type == "private", F.from_user.id.in_(ADMIN_IDS))
@admin_router.message(F.chat.type == "private", F.from_user.id.in_(ADMIN_IDS), F.text == ADMIN_BTN)
async def admin_open_list(message: Message):
    admin_dialog.pop(message.from_user.id, None)
    text, kb = list_view("open", 0)
    await message.answer(text, reply_markup=kb)


async def in_dialog(message: Message) -> bool:
    if message.chat.type != "private" or message.from_user is None:
        return False
    if message.from_user.id not in admin_dialog:
        return False
    text = message.text or ""
    return not text.startswith("/") and text != ADMIN_BTN


@admin_router.message(in_dialog)
async def admin_dialog_message(message: Message, bot: Bot):
    admin_id = message.from_user.id
    tid = admin_dialog.get(admin_id)
    t = tickets.get(tid)
    if not t or t["status"] != "open":
        admin_dialog.pop(admin_id, None)
        await message.answer("⚠️ Это обращение уже закрыто. Откройте другое через «📋 Обращения».")
        return

    try:
        await send_to_user(bot, t, message)
    except Exception as e:
        logging.warning("Не удалось отправить ответ игроку %s: %s", t["user_id"], e)
        await message.answer("❌ Не удалось отправить (игрок мог заблокировать бота).")
        return

    add_msg(t, "admin", describe(message), message.from_user.full_name)
    save_data()
    await message.answer(f"✅ Отправлено игроку (#{tid})", reply_markup=dialog_kb(tid))


@admin_router.callback_query(F.data.startswith("adm:"), F.from_user.id.in_(ADMIN_IDS))
async def admin_callbacks(call: CallbackQuery, bot: Bot):
    parts = call.data.split(":")
    action = parts[1]
    admin_id = call.from_user.id

    if action == "noop":
        await call.answer()
        return

    if action == "list":
        text, kb = list_view(parts[2], int(parts[3]))
        await show(call, text, kb)
        await call.answer()
        return

    if action == "exit":
        admin_dialog.pop(admin_id, None)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="📋 К списку", callback_data="adm:list:open:0")]]
        )
        await show(call, "⏹ Вы вышли из диалога.", kb)
        await call.answer()
        return

    t = tickets.get(int(parts[2])) if len(parts) > 2 else None
    if not t:
        await call.answer("Обращение не найдено", show_alert=True)
        return

    if action == "view":
        text, kb = card_view(t)
        await show(call, text, kb)
        await call.answer()

    elif action == "reply":
        if t["status"] != "open":
            await call.answer("Обращение закрыто. Откройте его снова.", show_alert=True)
            return
        admin_dialog[admin_id] = t["id"]
        await call.message.answer(
            f"💬 <b>Диалог по обращению #{t['id']}</b> с {user_label(t)}\n\n"
            "Всё, что вы напишете сейчас, будет отправлено игроку.",
            reply_markup=dialog_kb(t["id"]),
        )
        await call.answer()

    elif action == "close":
        if t["status"] == "open":
            t["status"] = "closed"
            t["updated"] = time.time()
            save_data()
            drop_dialogs(t["id"])
            try:
                await bot.send_message(
                    t["user_id"],
                    f"✅ Ваше обращение <b>#{t['id']}</b> закрыто администрацией.\n"
                    "Если остались вопросы — создайте новое обращение.",
                    reply_markup=back_menu(),
                )
            except Exception as e:
                logging.warning("Не удалось уведомить игрока %s: %s", t["user_id"], e)
        text, kb = card_view(t)
        await show(call, text, kb)
        await call.answer("Обращение закрыто")

    elif action == "reopen":
        t["status"] = "open"
        t["updated"] = time.time()
        save_data()
        text, kb = card_view(t)
        await show(call, text, kb)
        await call.answer("Обращение открыто снова")


# ====================== ИГРОКИ ======================
class Support(StatesGroup):
    creating = State()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    if user_id in ADMIN_IDS:
        admin_dialog.pop(user_id, None)
        open_count = sum(1 for t in tickets.values() if t["status"] == "open")
        await message.answer(
            "🛠 <b>Панель администратора</b>\n"
            f"Открытых обращений: <b>{open_count}</b>\n"
            "Кнопка «📋 Обращения» находится под клавиатурой.",
            reply_markup=admin_kb(),
        )
    await message.answer(WELCOME_TEXT, reply_markup=main_menu())


@router.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(call, WELCOME_TEXT, main_menu())
    await call.answer()


@router.callback_query(F.data == "support")
async def cb_support(call: CallbackQuery, state: FSMContext):
    await state.clear()
    t = find_open(call.from_user.id)
    if t:
        text = (
            "🆘 <b>Поддержка</b>\n\n"
            f"У вас есть открытое обращение <b>#{t['id']}</b>.\n"
            "Просто отправьте сообщение — оно будет добавлено в обращение, "
            "а ответ администрации придёт сюда."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Закрыть обращение", callback_data="close_own")],
                [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")],
            ]
        )
    else:
        text = "🆘 <b>Поддержка</b>\n\nНажмите кнопку ниже, чтобы создать обращение."
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✉️ Создать обращение", callback_data="create")],
                [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")],
            ]
        )
    await show(call, text, kb)
    await call.answer()


@router.callback_query(F.data == "create")
async def cb_create(call: CallbackQuery, state: FSMContext):
    if find_open(call.from_user.id):
        await cb_support(call, state)
        return
    await state.set_state(Support.creating)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="menu")]]
    )
    await show(
        call,
        "✉️ <b>Новое обращение</b>\n\n"
        "Опишите вашу проблему или вопрос одним сообщением. "
        "Если это баг — прикрепите скриншот.",
        kb,
    )
    await call.answer()


@router.callback_query(F.data == "close_own")
async def cb_close_own(call: CallbackQuery, bot: Bot):
    t = find_open(call.from_user.id)
    if not t:
        await show(call, WELCOME_TEXT, main_menu())
        await call.answer("Открытых обращений нет")
        return
    t["status"] = "closed"
    t["updated"] = time.time()
    save_data()
    drop_dialogs(t["id"])
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🔒 Игрок {user_label(t)} закрыл обращение <b>#{t['id']}</b>.",
            )
        except Exception as e:
            logging.warning("Не удалось уведомить админа %s: %s", admin_id, e)
    await show(call, f"✅ Обращение <b>#{t['id']}</b> закрыто. Спасибо!", back_menu())
    await call.answer()


@router.message(Support.creating, F.chat.type == "private")
async def create_ticket(message: Message, bot: Bot, state: FSMContext):
    t = new_ticket(message.from_user)
    add_msg(t, "user", describe(message), message.from_user.full_name)
    save_data()
    await state.clear()
    await notify_admins(bot, t, message, is_new=True)
    await message.answer(
        f"✅ Обращение <b>#{t['id']}</b> создано.\n"
        "Администрация ответит вам в ближайшее время — ответ придёт в этот чат.\n\n"
        "Если нужно что-то добавить, просто напишите ещё сообщение.",
        reply_markup=back_menu(),
    )


@router.message(F.chat.type == "private")
async def fallback(message: Message, bot: Bot):
    t = find_open(message.from_user.id)
    if t:
        add_msg(t, "user", describe(message), message.from_user.full_name)
        save_data()
        await notify_admins(bot, t, message, is_new=False)
        await message.answer(f"✅ Добавлено в обращение #{t['id']}.")
    else:
        await message.answer(
            "Нажмите «🆘 Поддержка», чтобы создать обращение.",
            reply_markup=main_menu(),
        )


async def main():
    token = BOT_TOKEN
    if not token:
        raise SystemExit("Не задан токен: укажи BOT_TOKEN в переменных окружения или в main.py")
    load_data()
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(admin_router)  # сначала админы
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
