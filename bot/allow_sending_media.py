import asyncio
from aiogram import Bot
from aiogram.types import ChatPermissions
from sqlalchemy import select
from db.session import AsyncSession
from db.models import Group, UnblockedUserLimit
import os
from tqdm import tqdm

BOT_TOKEN = os.getenv("BOT_TOKEN")  # или вставьте токен напрямую

async def grant_media_permissions():
    bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
    async with AsyncSession() as session:
        groups = (await session.execute(select(Group))).scalars().all()
        for group in groups:
            group_id = group.id
            try:
                # Получаем всех пользователей из вашей базы, которые могут писать
                stmt = select(UnblockedUserLimit).where(
                    UnblockedUserLimit.group_id == group_id,
                    UnblockedUserLimit.max_messages != 0  # или ваша логика
                )
                result = await session.execute(stmt)
                user_limits = result.scalars().all()
                print('Update for group #', group_id, sep='')
                for user_limit in tqdm(user_limits):
                    user_id = user_limit.user_id
                    try:
                        member = await bot.get_chat_member(group_id, user_id)
                        if member.status in ("member", "restricted") and getattr(member, "can_send_messages", True):
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
                        print(f"[!] Ошибка для пользователя {user_id} в группе {group_id}: {e}")
                        continue
            except Exception as e:
                print(f"[!] Ошибка для группы {group_id}: {e}")
    await bot.session.close()

if __name__ == "__main__":
    asyncio.run(grant_media_permissions())