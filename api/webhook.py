import asyncio
import hmac
import os
import smtplib
import time
import traceback
from email.header import Header
from email.mime.text import MIMEText

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import Update
from fastapi import FastAPI, Request, Response

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
CHANNEL_ID = int(os.environ["CHANNEL_ID"])
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
# BOTDB_REDIS_URL is set by the Vercel Upstash integration; REDIS_URL is the legacy manual value.
REDIS_URL = os.environ.get("BOTDB_REDIS_URL") or os.environ["REDIS_URL"]

storage = RedisStorage.from_url(REDIS_URL)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)
app = FastAPI()

SERVICE_LABELS = {
    "curator": "Куратор студентов",
    "moderator": "Модератор вебинаров",
    "both": "Куратор студентов + Модератор вебинаров",
}

WELCOME_TEXT = (
    "Добрый день!\n\n"
    "Меня зовут Алла. Я куратор студентов и модератор обучающих вебинаров с большим опытом.\n\n"
    "Если вам нужен специалист — оставьте заявку или напишите мне напрямую."
)


class OrderForm(StatesGroup):
    waiting_for_name = State()
    waiting_for_goal = State()
    waiting_for_contact = State()


class FreeForm(StatesGroup):
    waiting_for_message = State()
    waiting_for_contact = State()


def main_keyboard() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="1. Нужен куратор студентов", callback_data="service_curator")
    builder.button(text="2. Нужен модератор вебинаров", callback_data="service_moderator")
    builder.button(text="3. Нужны оба", callback_data="service_both")
    builder.button(text="4. Написать в ЛС", callback_data="service_free")
    builder.adjust(1)
    return builder.as_markup()


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def send_email(subject: str, body: str) -> None:
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = GMAIL_ADDRESS
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, GMAIL_ADDRESS, msg.as_string())
        print("Email sent OK")
    except Exception as e:
        print(f"Email error: {type(e).__name__}: {e}")


async def send_notification(text_lines: str, user: types.User, subject: str) -> None:
    prefix = text_lines + "🔗 Telegram: "
    if user.username:
        mention_str = f"@{user.username}"
        full_text = prefix + mention_str
        entity = types.MessageEntity(
            type="mention",
            offset=utf16_len(prefix),
            length=utf16_len(mention_str),
        )
    else:
        mention_str = user.full_name or str(user.id)
        full_text = prefix + mention_str
        entity = types.MessageEntity(
            type="text_mention",
            offset=utf16_len(prefix),
            length=utf16_len(mention_str),
            user=user,
        )
    try:
        await bot.send_message(chat_id=CHANNEL_ID, text=full_text, entities=[entity])
    except Exception:
        await bot.send_message(chat_id=CHANNEL_ID, text=full_text)

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        send_email,
        subject,
        full_text.replace("🔗 Telegram: ", "Telegram: "),
    )


@dp.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(WELCOME_TEXT, reply_markup=main_keyboard())


@dp.callback_query(F.data.in_({"service_curator", "service_moderator", "service_both"}))
async def service_handler(callback: types.CallbackQuery, state: FSMContext) -> None:
    service_key = callback.data.replace("service_", "")
    await state.update_data(service=SERVICE_LABELS[service_key])
    await state.set_state(OrderForm.waiting_for_name)
    await callback.message.answer("Добрый день, как к вам обращаться?")
    await callback.answer()


@dp.callback_query(F.data == "service_free")
async def free_handler(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.set_state(FreeForm.waiting_for_message)
    await callback.message.answer("Напишите ваше сообщение в свободной форме:")
    await callback.answer()


@dp.message(OrderForm.waiting_for_name)
async def name_handler(message: types.Message, state: FSMContext) -> None:
    await state.update_data(name=message.text)
    await state.set_state(OrderForm.waiting_for_goal)
    await message.answer(
        "Напишите, пожалуйста, для каких целей ищете специалиста?\n"
        "(для онлайн-школы, для курса в мессенджере, для частных созвонов/вебинаров и т.д.)"
    )


@dp.message(OrderForm.waiting_for_goal)
async def goal_handler(message: types.Message, state: FSMContext) -> None:
    await state.update_data(goal=message.text)
    await state.set_state(OrderForm.waiting_for_contact)
    await message.answer("Сообщите, пожалуйста, ваш мейл или мессенджер.")


@dp.message(OrderForm.waiting_for_contact)
async def contact_handler(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    text_lines = (
        f"📬 Новая заявка!\n\n"
        f"🎯 Услуга: {data.get('service', '—')}\n"
        f"👤 Имя / должность: {data.get('name', '—')}\n"
        f"📋 Цель: {data.get('goal', '—')}\n"
        f"📩 Контакт: {message.text}\n"
    )
    await send_notification(text_lines, message.from_user, subject="📬 Новая заявка от клиента")
    await message.answer("Спасибо, ваш запрос получен, я обязательно отвечу вам в ближайшее время.")


@dp.message(FreeForm.waiting_for_message)
async def free_message_handler(message: types.Message, state: FSMContext) -> None:
    await state.update_data(free_message=message.text)
    await state.set_state(FreeForm.waiting_for_contact)
    await message.answer("Укажите, пожалуйста, ваш мессенджер или мейл для связи:")


@dp.message(FreeForm.waiting_for_contact)
async def free_contact_handler(message: types.Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    text_lines = (
        f"💬 Сообщение в ЛС!\n\n"
        f"📝 Текст: {data.get('free_message', '—')}\n"
        f"📩 Контакт: {message.text}\n"
    )
    await send_notification(text_lines, message.from_user, subject="💬 Новое сообщение от клиента")
    await message.answer("Спасибо, ваш запрос получен, я обязательно отвечу вам в ближайшее время.")


@dp.message()
async def fallback_handler(message: types.Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer(WELCOME_TEXT, reply_markup=main_keyboard())


KEEPALIVE_KEY = "keepalive:last_ping"
KEEPALIVE_MIN_INTERVAL_S = 24 * 60 * 60
CRON_SCHEDULE_HEADER = "x-vercel-cron-schedule"


@app.get("/api/keepalive")
async def keepalive(request: Request) -> Response:
    """Touch Redis so the free Upstash database is never archived for inactivity.

    Triggered by the Vercel cron in vercel.json. Writes at most once per day,
    so repeated or spoofed calls cost a single read.
    """
    cron_secret = os.environ.get("CRON_SECRET")
    if cron_secret:
        auth = request.headers.get("authorization", "")
        if not hmac.compare_digest(auth, f"Bearer {cron_secret}"):
            return Response(status_code=401)
    elif CRON_SCHEDULE_HEADER not in request.headers:
        return Response(status_code=403)

    redis = storage.redis
    now = int(time.time())
    last = await redis.get(KEEPALIVE_KEY)
    if last is not None and now - int(last) < KEEPALIVE_MIN_INTERVAL_S:
        return Response(content="skipped", status_code=200)
    await redis.set(KEEPALIVE_KEY, str(now))
    print(f"Keepalive OK at {now}")
    return Response(content="ok", status_code=200)


@app.post("/api/webhook")
async def handle_webhook(request: Request) -> Response:
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(token, WEBHOOK_SECRET):
        return Response(status_code=403)
    try:
        data = await request.json()
    except Exception:
        return Response(status_code=400)
    update = Update.model_validate(data)
    await dp.feed_update(bot=bot, update=update)
    return Response(status_code=200)
