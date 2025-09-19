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
    waiting_for_manual_user = State()
    waiting_for_group_selection = State()
    waiting_for_limit = State()
    waiting_for_delete_delay = State()


@router.message(F.forward_from)
async def handle_forwarded_message(msg: Message, state: FSMContext):
    sender_id = msg.from_user.id

    if msg.forward_from is None:
        await msg.answer("⚠️ Невозможно определить пользователя, он скрыт.\nВведите @username или user_id пользователя:")
        await state.set_state(UnlockState.waiting_for_manual_user)
        await state.update_data(admin_id=sender_id)
        return

    forwarded_user_id = msg.forward_from.id

    await state.update_data(admin_id=sender_id, target_user_id=forwarded_user_id)

    async with AsyncSession() as session:
        admin = await session.get(Admin, sender_id)
        if not admin:
            await msg.answer("⛔️ Вы не админ.")
            return

        stmt = select(Group).where(Group.admin_username == admin.username)
        result = await session.execute(stmt)
        groups = result.scalars().all()

        if not groups:
            await msg.answer("У вас нет групп.")
            return

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=group.title, callback_data=f"unlock_{group.id}")]
            for group in groups
        ])
        await msg.answer("Выберите, где разблокировать пользователя:", reply_markup=kb)
        await state.set_state(UnlockState.waiting_for_group_selection)


@router.message(StateFilter(UnlockState.waiting_for_manual_user))
async def process_manual_user_input(msg: Message, state: FSMContext):
    text = msg.text.strip()
    sender_id = msg.from_user.id

    if text.startswith("@"):
        await state.update_data(target_username=text)
    else:
        try:
            user_id = int(text)
            await state.update_data(target_user_id=user_id)
        except ValueError:
            await msg.answer("Введите корректный @username или user_id пользователя.")
            return

    data = await state.get_data()
    admin_id = data.get("admin_id", sender_id)

    async with AsyncSession() as session:
        admin = await session.get(Admin, admin_id)
        if not admin:
            await msg.answer("⛔️ Вы не админ.")
            return

        stmt = select(Group).where(Group.admin_username == admin.username)
        result = await session.execute(stmt)
        groups = result.scalars().all()

        if not groups:
            await msg.answer("У вас нет групп.")
            return

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=group.title, callback_data=f"unlock_{group.id}")]
            for group in groups
        ])
        await msg.answer("Выберите, где разблокировать пользователя:", reply_markup=kb)
        await state.set_state(UnlockState.waiting_for_group_selection)


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
    target_user_id = data.get("target_user_id")
    target_username = data.get("target_username")
    max_messages = data["max_messages"]

    # If target_user_id is not known but target_username is, we might want to resolve username to user_id here.
    # But since the original logic does not include that, we proceed only if user_id is known.
    if target_user_id is None:
        await msg.answer("Не удалось определить user_id пользователя для разблокировки.")
        await state.clear()
        return

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


@router.message(F.forward_sender_name)
async def handle_hidden_forwarded_message(msg: Message, state: FSMContext):
    sender_id = msg.from_user.id
    await msg.answer("⚠️ Невозможно определить пользователя, он скрыт.\nВведите @username или user_id пользователя:")
    await state.set_state(UnlockState.waiting_for_manual_user)
    await state.update_data(admin_id=sender_id)