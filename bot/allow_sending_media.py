import asyncio
from aiogram import Bot
from aiogram.types import ChatPermissions
from sqlalchemy import select
from db.session import AsyncSession
from db.models import Group
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")  # или вставьте токен напрямую

async def grant_media_permissions():
    bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
    async with AsyncSession() as session:
        groups = (await session.execute(select(Group))).scalars().all()
        for group in groups:
            group_id = group.id
            try:
                admins = await bot.get_chat_administrators(group_id)
                members = await bot.get_chat_members_count(group_id)
                # Получаем список участников (только для супергрупп, иначе Telegram API не даст)
                # Здесь пример для 10_000 пользователей, для больших чатов используйте свой обход
                for user_id in range(1, members + 1):
                    try:
                        member = await bot.get_chat_member(group_id, user_id)
                        if member.status in ("member", "restricted") and member.can_send_messages:
                            await bot.restrict_chat_member(
                                chat_id=group_id,
                                user_id=user_id,
                                permissions=ChatPermissions(
                                    can_send_messages=True,
                                    can_send_media_messages=True,
                                    can_send_polls=True,
                                    can_send_other_messages=True,
                                    can_add_web_page_previews=True
                                )
                            )
                            print(f"✅ Media разрешения выданы для user {user_id} в группе {group_id}")
                    except Exception as e:
                        # Игнорируем ошибки для несуществующих/удалённых пользователей
                        continue
            except Exception as e:
                print(f"[!] Ошибка для группы {group_id}: {e}")
    await bot.session.close()

if __name__ == "__main__":
    asyncio.run(grant_media_permissions())