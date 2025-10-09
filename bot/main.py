# bot/main.py
import asyncio
import logging
import os
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
from db.session import engine, AsyncSession
from db.models import Base, Admin
from sqlalchemy import select
from bot.handlers import group_events, forwarding, admin_panel, limits
from bot.scheduler import start_scheduler
from aiogram import Bot, Dispatcher
from aiogram.enums.parse_mode import ParseMode
from aiogram.client.default import DefaultBotProperties
from bot.scheduler import restore_scheduled_tasks
from aiogram.client.session.aiohttp import AiohttpSession


load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
RAW_ADMIN_IDS = os.getenv("ADMIN_IDS", "").split()

dp = Dispatcher(storage=MemoryStorage())

bot = Bot(token=BOT_TOKEN, request_timeout=30, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


async def on_startup():
    # 1. Создание таблиц
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 2. Добавление админов в БД
    async with AsyncSession() as session:
        for admin_id in RAW_ADMIN_IDS:
            try:
                admin_id_int = int(admin_id)
            except ValueError:
                continue

            # Если уже есть — пропускаем
            exists = await session.get(Admin, admin_id_int)
            if not exists:
                session.add(Admin(id=admin_id_int, username=""))
        await session.commit()

    # 3. Регистрация роутеров
    dp.include_routers(
        admin_panel.router,
        group_events.router,
        limits.router,
        forwarding.router,
    )

    # 4. Планировщик
    await start_scheduler(bot)
    await restore_scheduled_tasks(bot)


async def main():
    await on_startup()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())