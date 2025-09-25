from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, ChatPermissions
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import StateFilter
from sqlalchemy import select
from db.session import AsyncSession
from db.models import Admin, Group, UnblockedUserLimit

router = Router()


class UnlockState(StatesGroup):
    waiting_for_group_selection = State()
    waiting_for_limit = State()
    waiting_for_delete_delay = State()


@router.message(F.contact)
async def handle_user_contact(msg: Message, state: FSMContext):
    contact = msg.contact
    if not contact.user_id:
        await msg.answer("⚠️ У контакта нет user_id. Поделитесь именно своим контактом через Telegram.")
        return

    await state.update_data(request_user_id=contact.user_id, request_phone=contact.phone_number)

    # Достаём всех админов
    async with AsyncSession() as session:
        result = await session.execute(select(Admin))
        admins = result.scalars().all()

    if not admins:
        await msg.answer("❌ Нет доступных админов.")
        return

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"👤 {admin.username}", callback_data=f"req_admin_{admin.id}")]
        for admin in admins
    ])
    await msg.answer("Кому из админов отправить запрос?", reply_markup=kb)


@router.callback_query(F.data.startswith("req_admin_"))
async def process_admin_choice(cb: CallbackQuery, state: FSMContext, bot: Bot):
    admin_id = int(cb.data.split("_")[-1])
    data = await state.get_data()
    user_id = data.get("request_user_id")
    phone = data.get("request_phone")

    # Сообщаем админу
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принять", callback_data=f"approve_{user_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{user_id}")
        ]
    ])
    await bot.send_message(
        chat_id=admin_id,
        text=f"Запрос на доступ от пользователя {user_id}\n📱 Телефон: {phone}",
        reply_markup=kb
    )
    await cb.message.edit_text("Запрос отправлен админу ✅")
    await state.clear()


@router.callback_query(F.data.startswith("approve_"))
async def process_admin_approve(cb: CallbackQuery, state: FSMContext):
    target_user_id = int(cb.data.split("_")[-1])
    admin_id = cb.from_user.id

    # Сохраняем данные в состоянии
    await state.update_data(admin_id=admin_id, target_user_id=target_user_id)

    async with AsyncSession() as session:
        admin = await session.get(Admin, admin_id)
        if not admin:
            await cb.message.answer("⛔️ Вы не админ.")
            return

        stmt = select(Group).where(Group.admin_username == admin.username)
        result = await session.execute(stmt)
        groups = result.scalars().all()

        if not groups:
            await cb.message.answer("У вас нет групп.")
            return

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=group.title, callback_data=f"unlock_{group.id}")]
            for group in groups
        ])

        await cb.message.answer(
            f"✅ Запрос на {target_user_id} одобрен.\nТеперь выберите группу для разблокировки:",
            reply_markup=kb
        )
        await state.set_state(UnlockState.waiting_for_group_selection)


@router.callback_query(F.data.startswith("reject_"))
async def process_admin_reject(cb: CallbackQuery, bot: Bot):
    target_user_id = int(cb.data.split("_")[-1])
    await cb.message.edit_text("❌ Запрос отклонён.")
    try:
        await bot.send_message(chat_id=target_user_id, text="⛔️ Ваш запрос был отклонён.")
    except Exception:
        pass
    
@router.callback_query(StateFilter(UnlockState.waiting_for_group_selection), F.data.startswith("unlock_"))
async def process_group_select(cb: CallbackQuery, state: FSMContext):
    group_id = int(cb.data.split("_")[1])
    await state.update_data(group_id=group_id)
    async with AsyncSession() as session:
        data = await state.get_data()
        target_user_id = data.get("target_user_id")
        stmt = select(UnblockedUserLimit).where(
            UnblockedUserLimit.user_id == target_user_id,
            UnblockedUserLimit.group_id == group_id
        )
        result = await session.execute(stmt)
        limit = result.scalar_one_or_none()
        if limit and limit.max_messages is None:
            await cb.message.edit_text("У пользователя безлимит на сообщения.")
            await state.clear()
            return
        remaining = limit.max_messages - limit.used_messages if limit else 0

    await cb.message.edit_text(f"Пользователю сейчас доступно {remaining} сообщений.\nСколько вы хотите ему добавить?")
    await state.set_state(UnlockState.waiting_for_limit)


@router.message(StateFilter(UnlockState.waiting_for_limit))
async def process_limit_input(msg: Message, state: FSMContext, bot: Bot):
    try:
        max_messages = int(msg.text.strip())
        if max_messages < 0:
            raise ValueError
    except ValueError:
        await msg.answer("Введите число от 0 и выше.")
        return

    await state.update_data(max_messages=max_messages)
    await state.set_state(UnlockState.waiting_for_delete_delay)
    await msg.answer("Через сколько минут удалять сообщения пользователя? (0 = не удалять)")


@router.message(StateFilter(UnlockState.waiting_for_delete_delay))
async def process_delete_delay(msg: Message, state: FSMContext, bot: Bot):
    try:
        delay = int(msg.text.strip())
        if delay < 0:
            raise ValueError
    except ValueError:
        await msg.answer("Введите число минут (0 = не удалять).")
        return

    data = await state.get_data()
    group_id = data["group_id"]
    target_user_id = data["target_user_id"]
    max_messages = data["max_messages"]

    async with AsyncSession() as session:
        stmt = select(UnblockedUserLimit).where(
            UnblockedUserLimit.user_id == target_user_id,
            UnblockedUserLimit.group_id == group_id
        )
        result = await session.execute(stmt)
        existing_limit = result.scalar_one_or_none()

        if existing_limit:
            remaining = existing_limit.max_messages - existing_limit.used_messages
            existing_limit.max_messages += max_messages
            existing_limit.delete_after_minutes = delay or None
            await session.commit()
            new_total = existing_limit.max_messages - existing_limit.used_messages
            await grant_permissions(bot, group_id, target_user_id)
            await msg.answer(
                f"✅ Обновлено. Было доступно: {remaining}. Добавлено: {max_messages}. Теперь: {new_total}.\n"
                f"🗑 Удаление сообщений: {'не удаляются' if delay == 0 else f'через {delay} мин.'}"
            )
        else:
            limit = UnblockedUserLimit(
                user_id=target_user_id,
                group_id=group_id,
                max_messages=max_messages,
                used_messages=0,
                delete_after_minutes=delay or None
            )
            session.add(limit)
            await session.commit()
            await grant_permissions(bot, group_id, target_user_id)
            await msg.answer(
                f"✅ Пользователь добавлен. Доступно: {max_messages} сообщений.\n"
                f"🗑 Удаление сообщений: {'не удаляются' if delay == 0 else f'через {delay} мин.'}"
            )

    await state.clear()


async def grant_permissions(bot: Bot, group_id: int, user_id: int):
    try:
        await bot.restrict_chat_member(
            chat_id=group_id,
            user_id=user_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True
            )
        )
    except Exception as e:
        print(f"[Ошибка выдачи доступа]: {e}")