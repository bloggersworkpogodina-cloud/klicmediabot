import asyncio
import os
import sqlite3
from datetime import datetime
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()
}
DB_PATH = "collab_bot.db"

router = Router()


# =========================
# Database
# =========================

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            role TEXT,
            created_at TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS creators (
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            city TEXT,
            creator_type TEXT,
            niche TEXT,
            social_link TEXT,
            followers TEXT,
            reach TEXT,
            cooperation_formats TEXT,
            contact TEXT,
            status TEXT DEFAULT 'active',
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS business_profiles (
            user_id INTEGER PRIMARY KEY,
            business_name TEXT,
            city TEXT,
            niche TEXT,
            social_link TEXT,
            contact TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS requests (
            request_id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_user_id INTEGER,
            business_name TEXT,
            city TEXT,
            niche TEXT,
            task TEXT,
            creator_needed TEXT,
            cooperation_format TEXT,
            budget TEXT,
            social_link TEXT,
            contact TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT,
            FOREIGN KEY(business_user_id) REFERENCES users(user_id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS responses (
            response_id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER,
            creator_user_id INTEGER,
            business_user_id INTEGER,
            status TEXT DEFAULT 'new',
            created_at TEXT,
            UNIQUE(request_id, creator_user_id),
            FOREIGN KEY(request_id) REFERENCES requests(request_id),
            FOREIGN KEY(creator_user_id) REFERENCES users(user_id),
            FOREIGN KEY(business_user_id) REFERENCES users(user_id)
        )
        """
    )

    conn.commit()
    conn.close()


def upsert_user(message: Message, role: Optional[str] = None) -> None:
    conn = db()
    cur = conn.cursor()
    existing = cur.execute("SELECT * FROM users WHERE user_id=?", (message.from_user.id,)).fetchone()
    if existing:
        if role:
            cur.execute("UPDATE users SET role=?, username=?, full_name=? WHERE user_id=?", (
                role, message.from_user.username, message.from_user.full_name, message.from_user.id
            ))
    else:
        cur.execute(
            "INSERT INTO users(user_id, username, full_name, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (message.from_user.id, message.from_user.username, message.from_user.full_name, role, datetime.now().isoformat())
        )
    conn.commit()
    conn.close()


# =========================
# Keyboards
# =========================

def main_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="Я креатор", callback_data="role_creator_intro")
    kb.button(text="Я бизнес", callback_data="role_business_intro")
    kb.button(text="Как это работает", callback_data="how_it_works")
    kb.adjust(1)
    return kb.as_markup()


def back_to_main_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="Назад", callback_data="main_menu")
    return kb.as_markup()


def creator_intro_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="Начать регистрацию", callback_data="creator_register")
    kb.button(text="Назад", callback_data="main_menu")
    kb.adjust(1)
    return kb.as_markup()


def business_intro_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="Создать профиль", callback_data="business_register")
    kb.button(text="Назад", callback_data="main_menu")
    kb.adjust(1)
    return kb.as_markup()


def business_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="Создать заявку", callback_data="business_create_request")
    kb.button(text="Мои заявки", callback_data="business_my_requests")
    kb.button(text="Отклики креаторов", callback_data="business_responses")
    kb.button(text="Мой профиль", callback_data="business_profile")
    kb.button(text="Помощь", callback_data="help_business")
    kb.adjust(1)
    return kb.as_markup()


def creator_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="Проекты", callback_data="creator_view_requests")
    kb.button(text="Мои отклики", callback_data="creator_my_responses")
    kb.button(text="Мой профиль", callback_data="creator_profile")
    kb.button(text="Настройки", callback_data="creator_settings")
    kb.button(text="Помощь", callback_data="help_creator")
    kb.adjust(1)
    return kb.as_markup()

def request_kb(request_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="Откликнуться", callback_data=f"respond:{request_id}")
    kb.adjust(1)
    return kb.as_markup()


def business_decision_kb(response_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="Принять", callback_data=f"accept:{response_id}")
    kb.button(text="Отклонить", callback_data=f"decline:{response_id}")
    kb.adjust(2)
    return kb.as_markup()


def admin_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="Статистика", callback_data="admin_stats")
    kb.button(text="Активные заявки", callback_data="admin_requests")
    kb.adjust(1)
    return kb.as_markup()


# =========================
# States
# =========================

class CreatorForm(StatesGroup):
    name = State()
    city = State()
    creator_type = State()
    niche = State()
    social_link = State()
    followers = State()
    reach = State()
    cooperation_formats = State()
    contact = State()


class BusinessForm(StatesGroup):
    business_name = State()
    city = State()
    niche = State()
    social_link = State()
    contact = State()


class RequestForm(StatesGroup):
    creator_needed = State()
    task = State()
    cooperation_format = State()
    budget = State()


# =========================
# Formatters
# =========================

def request_card(r) -> str:
    return (
        f"<b>Заявка #{r['request_id']}</b>\n\n"
        f"Бизнес: {r['business_name']}\n"
        f"Город: {r['city']}\n"
        f"Ниша: {r['niche']}\n"
        f"Кого ищут: {r['creator_needed']}\n"
        f"Задача: {r['task']}\n"
        f"Формат: {r['cooperation_format']}\n"
        f"Бюджет / бартер: {r['budget']}\n"
        f"Соцсети: {r['social_link']}\n"
        f"Статус: {r['status']}"
    )


def creator_card(c) -> str:
    return (
        f"<b>Креатор</b>\n\n"
        f"Имя: {c['name']}\n"
        f"Город: {c['city']}\n"
        f"Тип: {c['creator_type']}\n"
        f"Ниша: {c['niche']}\n"
        f"Соцсети: {c['social_link']}\n"
        f"Подписчики: {c['followers']}\n"
        f"Охваты: {c['reach']}\n"
        f"Форматы: {c['cooperation_formats']}"
    )


def contact_line(user_id: int, contact: str) -> str:
    conn = db()
    u = conn.execute("SELECT username FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    tg = f"@{u['username']}" if u and u['username'] else "username не указан"
    return f"Telegram: {tg}\nКонтакт: {contact}"


# =========================
# Handlers
# =========================

@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    upsert_user(message)
    await message.answer(
        "<b>КЛИК | Медиа-маркет</b>\n\n"
        "Платформа, где <b>бизнес находит креаторов</b>, а <b>креаторы — проекты, рекламу и коллаборации</b>.\n\n"
        "Без бесконечных переписок и поиска по чатам.\n\n"
        "Выберите, кто вы:",
        reply_markup=main_menu_kb()
    )


@router.callback_query(F.data == "main_menu")
async def main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "<b>КЛИК | Медиа-маркет</b>\n\nВыберите, кто вы:",
        reply_markup=main_menu_kb()
    )
    await callback.answer()


@router.callback_query(F.data == "how_it_works")
async def how_it_works(callback: CallbackQuery):
    await callback.message.edit_text(
        "<b>Как работает КЛИК</b>\n\n"
        "Бизнес размещает проект и условия сотрудничества.\n\n"
        "Креаторы находят подходящие проекты и нажимают <b>«Откликнуться»</b>.\n\n"
        "Бизнес видит профиль креатора и выбирает: <b>принять</b> или <b>отклонить</b> отклик.\n\n"
        "После принятия КЛИК открывает контакты обеим сторонам.",
        reply_markup=back_to_main_kb()
    )
    await callback.answer()


# ---------- Business registration ----------

@router.callback_query(F.data == "role_business_intro")
async def role_business_intro(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "<b>Найдём вам подходящих креаторов.</b>\n\n"
        "Создайте профиль бизнеса, а затем разместите заявку: укажите город, задачу и условия сотрудничества.\n\n"
        "Креаторы смогут откликнуться, а вы — <b>принять или отклонить отклик</b>.",
        reply_markup=business_intro_kb()
    )
    await callback.answer()


@router.callback_query(F.data == "business_register")
async def role_business(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(BusinessForm.business_name)
    await callback.message.edit_text("<b>Создаём профиль бизнеса</b>\n\nВведите название бизнеса:")
    await callback.answer()


@router.message(BusinessForm.business_name)
async def business_name(message: Message, state: FSMContext):
    await state.update_data(business_name=message.text)
    await state.set_state(BusinessForm.city)
    await message.answer("Город / онлайн:")


@router.message(BusinessForm.city)
async def business_city(message: Message, state: FSMContext):
    await state.update_data(city=message.text)
    await state.set_state(BusinessForm.niche)
    await message.answer("Ниша бизнеса:")


@router.message(BusinessForm.niche)
async def business_niche(message: Message, state: FSMContext):
    await state.update_data(niche=message.text)
    await state.set_state(BusinessForm.social_link)
    await message.answer("Ссылка на соцсети бизнеса:")


@router.message(BusinessForm.social_link)
async def business_social(message: Message, state: FSMContext):
    await state.update_data(social_link=message.text)
    await state.set_state(BusinessForm.contact)
    await message.answer("Контакт для связи:")


@router.message(BusinessForm.contact)
async def business_contact(message: Message, state: FSMContext):
    data = await state.get_data()
    upsert_user(message, "business")
    conn = db()
    conn.execute(
        """
        INSERT OR REPLACE INTO business_profiles
        (user_id, business_name, city, niche, social_link, contact)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (message.from_user.id, data["business_name"], data["city"], data["niche"], data["social_link"], message.text)
    )
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer(
        "<b>Готово. Ваш профиль создан.</b>\n\n"
        "Теперь можно разместить первую заявку и начать получать отклики креаторов.",
        reply_markup=business_menu_kb()
    )


# ---------- Creator registration ----------

@router.callback_query(F.data == "role_creator_intro")
async def role_creator_intro(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "<b>Давайте создадим ваш профиль.</b>\n\n"
        "Он понадобится, чтобы бизнес видел вас в откликах, а КЛИК мог показывать подходящие проекты.\n\n"
        "Это займёт около 2 минут.",
        reply_markup=creator_intro_kb()
    )
    await callback.answer()


@router.callback_query(F.data == "creator_register")
async def role_creator(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(CreatorForm.name)
    await callback.message.edit_text("<b>Создаём профиль креатора</b>\n\nКак вас зовут?")
    await callback.answer()


@router.message(CreatorForm.name)
async def creator_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(CreatorForm.city)
    await message.answer("Ваш город / онлайн:")


@router.message(CreatorForm.city)
async def creator_city(message: Message, state: FSMContext):
    await state.update_data(city=message.text)
    await state.set_state(CreatorForm.creator_type)
    await message.answer("Кто вы? Например: блогер, UGC-креатор, фотограф, видеограф, модель, эксперт")


@router.message(CreatorForm.creator_type)
async def creator_type(message: Message, state: FSMContext):
    await state.update_data(creator_type=message.text)
    await state.set_state(CreatorForm.niche)
    await message.answer("Ваша ниша: бьюти, еда, лайфстайл, бизнес, дети, спорт, другое")


@router.message(CreatorForm.niche)
async def creator_niche(message: Message, state: FSMContext):
    await state.update_data(niche=message.text)
    await state.set_state(CreatorForm.social_link)
    await message.answer("Ссылка на ваши соцсети / портфолио:")


@router.message(CreatorForm.social_link)
async def creator_social(message: Message, state: FSMContext):
    await state.update_data(social_link=message.text)
    await state.set_state(CreatorForm.followers)
    await message.answer("Количество подписчиков:")


@router.message(CreatorForm.followers)
async def creator_followers(message: Message, state: FSMContext):
    await state.update_data(followers=message.text)
    await state.set_state(CreatorForm.reach)
    await message.answer("Средние охваты / просмотры:")


@router.message(CreatorForm.reach)
async def creator_reach(message: Message, state: FSMContext):
    await state.update_data(reach=message.text)
    await state.set_state(CreatorForm.cooperation_formats)
    await message.answer("Какие форматы рассматриваете? Оплата / бартер / процент / коллаборации")


@router.message(CreatorForm.cooperation_formats)
async def creator_formats(message: Message, state: FSMContext):
    await state.update_data(cooperation_formats=message.text)
    await state.set_state(CreatorForm.contact)
    await message.answer("Контакт для связи:")


@router.message(CreatorForm.contact)
async def creator_contact(message: Message, state: FSMContext):
    data = await state.get_data()
    upsert_user(message, "creator")
    conn = db()
    conn.execute(
        """
        INSERT OR REPLACE INTO creators
        (user_id, name, city, creator_type, niche, social_link, followers, reach, cooperation_formats, contact, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
        """,
        (
            message.from_user.id, data["name"], data["city"], data["creator_type"],
            data["niche"], data["social_link"], data["followers"], data["reach"],
            data["cooperation_formats"], message.text
        )
    )
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer(
        "<b>Готово. Ваш профиль создан.</b>\n\n"
        "Теперь можно смотреть актуальные проекты и откликаться на подходящие предложения.",
        reply_markup=creator_menu_kb()
    )


# ---------- Business request creation ----------

@router.callback_query(F.data == "business_create_request")
async def create_request_start(callback: CallbackQuery, state: FSMContext):
    conn = db()
    profile = conn.execute("SELECT * FROM business_profiles WHERE user_id=?", (callback.from_user.id,)).fetchone()
    conn.close()
    if not profile:
        await callback.message.edit_text("Сначала заполните профиль бизнеса.", reply_markup=main_menu_kb())
        await callback.answer()
        return
    await state.set_state(RequestForm.creator_needed)
    await callback.message.edit_text("Кого ищем? Например: UGC-креатор, блогер, фотограф, видеограф")
    await callback.answer()


@router.message(RequestForm.creator_needed)
async def req_creator_needed(message: Message, state: FSMContext):
    await state.update_data(creator_needed=message.text)
    await state.set_state(RequestForm.task)
    await message.answer("Что нужно сделать? Опишите задачу:")


@router.message(RequestForm.task)
async def req_task(message: Message, state: FSMContext):
    await state.update_data(task=message.text)
    await state.set_state(RequestForm.cooperation_format)
    await message.answer("Формат сотрудничества: оплата / бартер / процент / коллаборация / обсудим")


@router.message(RequestForm.cooperation_format)
async def req_format(message: Message, state: FSMContext):
    await state.update_data(cooperation_format=message.text)
    await state.set_state(RequestForm.budget)
    await message.answer("Бюджет или что даёте по бартеру:")


@router.message(RequestForm.budget)
async def req_budget(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    conn = db()
    profile = conn.execute("SELECT * FROM business_profiles WHERE user_id=?", (message.from_user.id,)).fetchone()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO requests
        (business_user_id, business_name, city, niche, task, creator_needed, cooperation_format, budget, social_link, contact, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
        """,
        (
            message.from_user.id, profile["business_name"], profile["city"], profile["niche"],
            data["task"], data["creator_needed"], data["cooperation_format"], message.text,
            profile["social_link"], profile["contact"], datetime.now().isoformat()
        )
    )
    request_id = cur.lastrowid
    request = conn.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
    creators = conn.execute("SELECT * FROM creators WHERE status='active'").fetchall()
    conn.commit()
    conn.close()

    await state.clear()
    await message.answer("Заявка создана. Креаторы смогут откликнуться.", reply_markup=business_menu_kb())

    # MVP: отправляем всем активным креаторам. Позже можно включить фильтры по городу/нише/формату.
    for creator in creators:
        try:
            await bot.send_message(
                creator["user_id"],
                "<b>Новая заявка для вас</b>\n\n" + request_card(request),
                reply_markup=request_kb(request_id)
            )
        except Exception:
            pass


# ---------- Creator views/responds ----------

@router.callback_query(F.data == "creator_view_requests")
async def creator_view_requests(callback: CallbackQuery):
    conn = db()
    requests = conn.execute("SELECT * FROM requests WHERE status='active' ORDER BY request_id DESC LIMIT 10").fetchall()
    conn.close()
    if not requests:
        await callback.message.edit_text("Сейчас активных заявок нет.", reply_markup=creator_menu_kb())
        await callback.answer()
        return
    await callback.message.edit_text("Активные заявки:")
    for r in requests:
        await callback.message.answer(request_card(r), reply_markup=request_kb(r["request_id"]))
    await callback.answer()


@router.callback_query(F.data.startswith("respond:"))
async def respond_to_request(callback: CallbackQuery, bot: Bot):
    request_id = int(callback.data.split(":")[1])
    conn = db()
    creator = conn.execute("SELECT * FROM creators WHERE user_id=?", (callback.from_user.id,)).fetchone()
    if not creator:
        conn.close()
        await callback.answer("Сначала заполните анкету креатора", show_alert=True)
        return

    request = conn.execute("SELECT * FROM requests WHERE request_id=? AND status='active'", (request_id,)).fetchone()
    if not request:
        conn.close()
        await callback.answer("Заявка уже не активна", show_alert=True)
        return

    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO responses(request_id, creator_user_id, business_user_id, status, created_at) VALUES (?, ?, ?, 'new', ?)",
            (request_id, callback.from_user.id, request["business_user_id"], datetime.now().isoformat())
        )
        response_id = cur.lastrowid
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        await callback.answer("Вы уже откликались на эту заявку", show_alert=True)
        return
    conn.close()

    await callback.answer("Отклик отправлен")
    await callback.message.answer("Ваш отклик отправлен бизнесу.")

    await bot.send_message(
        request["business_user_id"],
        "<b>Новый отклик на вашу заявку</b>\n\n"
        + request_card(request)
        + "\n\n"
        + creator_card(creator),
        reply_markup=business_decision_kb(response_id)
    )


# ---------- Business accepts/declines ----------

@router.callback_query(F.data.startswith("accept:"))
async def accept_response(callback: CallbackQuery, bot: Bot):
    response_id = int(callback.data.split(":")[1])
    conn = db()
    response = conn.execute("SELECT * FROM responses WHERE response_id=?", (response_id,)).fetchone()
    if not response or response["business_user_id"] != callback.from_user.id:
        conn.close()
        await callback.answer("Отклик не найден", show_alert=True)
        return

    conn.execute("UPDATE responses SET status='accepted' WHERE response_id=?", (response_id,))
    creator = conn.execute("SELECT * FROM creators WHERE user_id=?", (response["creator_user_id"],)).fetchone()
    request = conn.execute("SELECT * FROM requests WHERE request_id=?", (response["request_id"],)).fetchone()
    business = conn.execute("SELECT * FROM business_profiles WHERE user_id=?", (response["business_user_id"],)).fetchone()
    conn.commit()
    conn.close()

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "Отклик принят. Контакты креатора:\n\n" + contact_line(creator["user_id"], creator["contact"])
    )
    await bot.send_message(
        creator["user_id"],
        "Ваш отклик принят 🎉\n\n"
        f"Бизнес: {request['business_name']}\n"
        f"Заявка: {request['task']}\n\n"
        "Контакты бизнеса:\n"
        + contact_line(business["user_id"], business["contact"])
    )
    await callback.answer("Принято")


@router.callback_query(F.data.startswith("decline:"))
async def decline_response(callback: CallbackQuery, bot: Bot):
    response_id = int(callback.data.split(":")[1])
    conn = db()
    response = conn.execute("SELECT * FROM responses WHERE response_id=?", (response_id,)).fetchone()
    if not response or response["business_user_id"] != callback.from_user.id:
        conn.close()
        await callback.answer("Отклик не найден", show_alert=True)
        return

    conn.execute("UPDATE responses SET status='declined' WHERE response_id=?", (response_id,))
    request = conn.execute("SELECT * FROM requests WHERE request_id=?", (response["request_id"],)).fetchone()
    conn.commit()
    conn.close()

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Отклик отклонён.")
    await bot.send_message(
        response["creator_user_id"],
        f"По заявке #{request['request_id']} бизнес выбрал другого креатора. Новые предложения появятся в боте."
    )
    await callback.answer("Отклонено")


# ---------- Lists ----------

@router.callback_query(F.data == "business_my_requests")
async def business_my_requests(callback: CallbackQuery):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM requests WHERE business_user_id=? ORDER BY request_id DESC LIMIT 10",
        (callback.from_user.id,)
    ).fetchall()
    conn.close()
    if not rows:
        await callback.message.edit_text("У вас пока нет заявок.", reply_markup=business_menu_kb())
        await callback.answer()
        return
    await callback.message.edit_text("Ваши заявки:")
    for r in rows:
        await callback.message.answer(request_card(r))
    await callback.answer()


@router.callback_query(F.data == "business_responses")
async def business_responses(callback: CallbackQuery):
    conn = db()
    rows = conn.execute(
        """
        SELECT responses.*, creators.name, creators.social_link, requests.task
        FROM responses
        JOIN creators ON creators.user_id = responses.creator_user_id
        JOIN requests ON requests.request_id = responses.request_id
        WHERE responses.business_user_id=?
        ORDER BY responses.response_id DESC LIMIT 10
        """,
        (callback.from_user.id,)
    ).fetchall()
    conn.close()
    if not rows:
        await callback.message.edit_text("Откликов пока нет.", reply_markup=business_menu_kb())
        await callback.answer()
        return
    await callback.message.edit_text("Последние отклики:")
    for r in rows:
        await callback.message.answer(
            f"Заявка: {r['task']}\nКреатор: {r['name']}\nСоцсети: {r['social_link']}\nСтатус: {r['status']}",
            reply_markup=business_decision_kb(r["response_id"]) if r["status"] == "new" else None
        )
    await callback.answer()


@router.callback_query(F.data == "creator_my_responses")
async def creator_my_responses(callback: CallbackQuery):
    conn = db()
    rows = conn.execute(
        """
        SELECT responses.*, requests.business_name, requests.task
        FROM responses
        JOIN requests ON requests.request_id = responses.request_id
        WHERE responses.creator_user_id=?
        ORDER BY responses.response_id DESC LIMIT 10
        """,
        (callback.from_user.id,)
    ).fetchall()
    conn.close()
    if not rows:
        await callback.message.edit_text("У вас пока нет откликов.", reply_markup=creator_menu_kb())
        await callback.answer()
        return
    await callback.message.edit_text("Ваши отклики:")
    for r in rows:
        await callback.message.answer(
            f"Бизнес: {r['business_name']}\nЗадача: {r['task']}\nСтатус: {r['status']}"
        )
    await callback.answer()


# ---------- Profiles / help ----------

@router.callback_query(F.data == "business_profile")
async def business_profile(callback: CallbackQuery):
    conn = db()
    p = conn.execute("SELECT * FROM business_profiles WHERE user_id=?", (callback.from_user.id,)).fetchone()
    conn.close()
    if not p:
        await callback.message.edit_text("Профиль бизнеса пока не заполнен.", reply_markup=business_intro_kb())
    else:
        await callback.message.edit_text(
            "<b>Мой профиль</b>\n\n"
            f"Бизнес: {p['business_name']}\n"
            f"Город: {p['city']}\n"
            f"Ниша: {p['niche']}\n"
            f"Соцсети: {p['social_link']}\n"
            f"Контакт: {p['contact']}",
            reply_markup=business_menu_kb()
        )
    await callback.answer()


@router.callback_query(F.data == "creator_profile")
async def creator_profile(callback: CallbackQuery):
    conn = db()
    c = conn.execute("SELECT * FROM creators WHERE user_id=?", (callback.from_user.id,)).fetchone()
    conn.close()
    if not c:
        await callback.message.edit_text("Профиль креатора пока не заполнен.", reply_markup=creator_intro_kb())
    else:
        await callback.message.edit_text(creator_card(c), reply_markup=creator_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "creator_settings")
async def creator_settings(callback: CallbackQuery):
    await callback.message.edit_text(
        "<b>Настройки</b>\n\nСкоро здесь появятся настройки города, уведомлений и форматов сотрудничества.",
        reply_markup=creator_menu_kb()
    )
    await callback.answer()


@router.callback_query(F.data.in_({"help_creator", "help_business"}))
async def help_section(callback: CallbackQuery):
    is_business = callback.data == "help_business"
    await callback.message.edit_text(
        "<b>Помощь</b>\n\n"
        "КЛИК соединяет бизнес и креаторов.\n\n"
        "Если что-то не получается, напишите администратору проекта. Контакт добавим перед публичным запуском.",
        reply_markup=business_menu_kb() if is_business else creator_menu_kb()
    )
    await callback.answer()

# ---------- Admin ----------

@router.message(Command("admin"))
async def admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Нет доступа.")
        return
    await message.answer("Админ-панель:", reply_markup=admin_kb())


@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    conn = db()
    users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    creators = conn.execute("SELECT COUNT(*) AS c FROM creators").fetchone()["c"]
    businesses = conn.execute("SELECT COUNT(*) AS c FROM business_profiles").fetchone()["c"]
    requests = conn.execute("SELECT COUNT(*) AS c FROM requests").fetchone()["c"]
    responses = conn.execute("SELECT COUNT(*) AS c FROM responses").fetchone()["c"]
    conn.close()
    await callback.message.edit_text(
        f"Статистика:\n\nПользователи: {users}\nКреаторы: {creators}\nБизнесы: {businesses}\nЗаявки: {requests}\nОтклики: {responses}",
        reply_markup=admin_kb()
    )
    await callback.answer()


@router.callback_query(F.data == "admin_requests")
async def admin_requests(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    conn = db()
    rows = conn.execute("SELECT * FROM requests ORDER BY request_id DESC LIMIT 10").fetchall()
    conn.close()
    if not rows:
        await callback.message.edit_text("Заявок нет.", reply_markup=admin_kb())
        await callback.answer()
        return
    await callback.message.edit_text("Последние заявки:")
    for r in rows:
        await callback.message.answer(request_card(r))
    await callback.answer()


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Add it to .env")
    init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
