from aiogram import Router, Bot, F
from aiogram.types import Message
from sqlalchemy import select, update, UniqueConstraint
from db.session import AsyncSession
from db.models import UnblockedUserLimit, Group
import asyncio
from apscheduler.triggers.date import DateTrigger
import datetime
from aiogram.types import Message, ChatPermissions

router = Router()


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def limit_checker(msg: Message, bot: Bot):
    user_id = msg.from_user.id
    group_id = msg.chat.id

    if msg.content_type in {"new_chat_members", "left_chat_member", "pinned_message"}:
        return

    async with AsyncSession() as session:
        stmt = select(UnblockedUserLimit).where(
            UnblockedUserLimit.user_id == user_id,
            UnblockedUserLimit.group_id == group_id
        )
        result = await session.execute(stmt)
        record = result.scalars().first()

        group = await session.get(Group, group_id)
        if not group:
            return

        # Определяем лимит
        base_limit = group.limit_msg

        # Нет записи — создаем новую
        if not record:
            record = UnblockedUserLimit(
                group_id=group_id,
                user_id=user_id,
                max_messages=base_limit,
                used_messages=1
            )
            session.add(record)
            await session.commit()

        # Обработка по лимиту
        if record.max_messages is not None and record.used_messages >= record.max_messages:
            try:
                await msg.delete()
            except Exception:
                pass

            username = f"@{msg.from_user.username}" if msg.from_user.username else msg.from_user.full_name
            stmt = select(Group).where(Group.id == group_id)
            group_result = await session.execute(stmt)
            group = group_result.scalars().first()

            if group and group.limit_exceeded_template:
                text = group.limit_exceeded_template.format(user=username)
            else:
                text = f"{username}, вы исчерпали лимит сообщений."

            warn_msg = await bot.send_message(chat_id=group_id, text=text)
            await asyncio.sleep(30)
            try:
                await warn_msg.delete()
            except:
                pass
            return

        if record.max_messages is not None:
            # Увеличиваем used_messages
            stmt = update(UnblockedUserLimit).where(
                UnblockedUserLimit.id == record.id
            ).values(
                used_messages=record.used_messages + 1
            )
            await session.execute(stmt)
            await session.commit()

        # Планируем автоудаление (если включено)
        if record.delete_after_minutes:
            from bot.scheduler import scheduler  # импортируй при необходимости

            run_at = datetime.datetime.now() + datetime.timedelta(minutes=record.delete_after_minutes)
            scheduler.add_job(
                bot.delete_message,
                trigger=DateTrigger(run_date=run_at),
                kwargs={"chat_id": msg.chat.id, "message_id": msg.message_id},
                id=f"autodel_{msg.chat.id}_{msg.message_id}",
                replace_existing=True
            )