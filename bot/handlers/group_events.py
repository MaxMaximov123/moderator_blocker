# bot/handlers/group_events.py
from aiogram import Router, Bot
from aiogram.types import ChatMemberUpdated, ChatPermissions
from aiogram.filters.chat_member_updated import ChatMemberUpdatedFilter, JOIN_TRANSITION
from asyncio import sleep
from db.session import AsyncSession
from db.models import Group, Admin
from aiogram.types import ChatMemberUpdated, ChatPermissions
from aiogram.enums.chat_member_status import ChatMemberStatus
from sqlalchemy import select
from aiogram import Router, Bot, F
from aiogram.types import Message, ChatPermissions
import asyncio
from db.models import UnblockedUserLimit

router = Router()


@router.chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def on_user_join(event: ChatMemberUpdated, bot: Bot):
    group_id = event.chat.id
    user = event.new_chat_member.user
    user_id = user.id

    async with AsyncSession() as session:
        group = await session.get(Group, group_id)
        if not group:
            return

        # Определяем лимит
        base_limit = group.limit_msg

        # Добавляем запись в таблицу лимитов
        stmt = select(UnblockedUserLimit).where(
            UnblockedUserLimit.group_id == group_id,
            UnblockedUserLimit.user_id == user_id
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.max_messages = base_limit
            used_messages = existing.used_messages
        else:
            session.add(UnblockedUserLimit(
                group_id=group_id,
                user_id=user_id,
                max_messages=base_limit,
                used_messages=0
            ))

            used_messages = 0

        await session.commit()

        # Блокировка по лимиту
        if base_limit == 0:
            await bot.restrict_chat_member(
                chat_id=group_id,
                user_id=user_id,
                permissions=ChatPermissions(can_send_messages=False)
            )
        else:
            await bot.restrict_chat_member(
                chat_id=group_id,
                user_id=user_id,
                permissions=ChatPermissions(can_send_messages=True)
            )

        # Получаем админа
        result = await session.execute(select(Admin))
        admin = result.scalars().first()
        admin_username = f"@{admin.username}" if admin and admin.username else "админ"

    user_nickname = f"@{user.username}" if user.username else f"{user.full_name}"
    limit_text = "без ограничений" if base_limit is None else str(base_limit - used_messages)

    msg = await bot.send_message(
        chat_id=group_id,
        text=group.welcome_template.format(
            title=group.title,
            description=group.description,
            admin=admin_username,
            user=user_nickname
        ) # + f"\n\n💬 Доступно сообщений: {limit_text}"
    )

    await asyncio.sleep(30)
    await msg.delete()

@router.my_chat_member()
async def on_bot_added(event: ChatMemberUpdated, bot: Bot):
    group_id = event.chat.id
    adder_id = event.from_user.id
    adder_username = event.from_user.username or "admin"

    async with AsyncSession() as session:
        admin = await session.get(Admin, adder_id)
        if not admin:
            await bot.leave_chat(group_id)
            return

        # Обновляем username админа при необходимости
        if not admin.username and event.from_user.username:
            admin.username = adder_username

        # Получаем данные о чате
        chat = await bot.get_chat(group_id)

        group = await session.get(Group, group_id)
        if group:
            group.title = chat.title
            group.description = chat.description or "-"
            group.admin_username = admin.username  # обновим, если поменяли
        else:
            group = Group(
                id=group_id,
                title=chat.title,
                description=chat.description or "-",
                admin_username=admin.username,
                welcome_template="Привет, {user}! Добро пожаловать в чат {title}. Это крупнейший паблик по {description}. Чтобы разместить ваше предложение напишите админу чата {admin}"
            )
            session.add(group)

        await session.commit()