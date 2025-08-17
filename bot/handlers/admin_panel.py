from pprint import pprint

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import any_state
from sqlalchemy import select
from db.session import AsyncSession
from db.models import Group, Admin, ScheduledPost
from bot.keyboards.panel import groups_keyboard, group_panel_keyboard
from bot.states import EditWelcome, IntervalMailingState, TimedMailingState, MailingMenuState, EditLimitMessage, EditLimit
from bot.scheduler import add_post_to_schedule
from aiogram import Bot
import datetime
from db.models import UnblockedUserLimit

router = Router()


@router.message(F.text == "/cancel", StateFilter("*"))
async def cancel_state(msg: Message, state: FSMContext):
    data = await state.get_data()
    group_id = data.get("group_id")

    await state.clear()

    if group_id:
        await msg.answer(
            f"❌ Отменено. Настройки группы <code>{group_id}</code>",
            reply_markup=group_panel_keyboard(group_id)
        )
    else:
        await msg.answer("❌ Действие отменено.")


@router.message(F.text == "Мои группы", StateFilter("*"))
async def admin_panel(msg: Message, state: FSMContext):
    async with AsyncSession() as session:
        await state.clear()
        admin = await session.get(Admin, msg.from_user.id)
        if not admin:
            return

        groups = await get_admin_groups(session, admin.username)
        if not groups:
            await msg.answer("У вас нет групп.")
            return

        await msg.answer("Ваши группы:", reply_markup=groups_keyboard(groups, page=0))


@router.message(CommandStart())
async def start(msg: Message):
    async with AsyncSession() as session:
        admin = await session.get(Admin, msg.from_user.id)

        if not admin:
            await msg.answer("У вас нет прав для управления ботом.")
            return

        reply_kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Мои группы")]],
            resize_keyboard=True
        )

        await msg.answer(
            "Добро пожаловать, модератор!\n\nНажмите на кнопку ниже, чтобы перейти к управлению группами.",
            reply_markup=reply_kb
        )


@router.callback_query(F.data.startswith("groups_page_"))
async def paginate_groups(cb: CallbackQuery):
    page = int(cb.data.split("_")[-1])

    async with AsyncSession() as session:
        admin = await session.get(Admin, cb.from_user.id)
        if not admin:
            return

        groups = await get_admin_groups(session, admin.username)
        await cb.message.edit_text("Ваши группы:", reply_markup=groups_keyboard(groups, page=page))


@router.callback_query(F.data.startswith("group_settings_"))
async def group_settings(cb: CallbackQuery):
    group_id = cb.data.split("_")[-1]
    await cb.message.edit_text(
        f"Настройки группы ID <code>{group_id}</code>",
        reply_markup=group_panel_keyboard(group_id)
    )


@router.callback_query(F.data.startswith("edit_welcome_"))
async def edit_welcome(cb: CallbackQuery, state: FSMContext):
    group_id = int(cb.data.split("_")[-1])
    await state.set_state(EditWelcome.waiting_for_welcome_text)
    await state.update_data(group_id=group_id)

    await cb.message.edit_text(
        f"Введите новый текст приветствия для группы <code>{group_id}</code>.\n"
        "Вы можете использовать: <code>{user}</code>, <code>{title}</code>, <code>{description}</code>, <code>{admin}</code>"
    )


@router.message(EditWelcome.waiting_for_welcome_text)
async def save_new_welcome_text(msg: Message, state: FSMContext):
    data = await state.get_data()
    group_id = data.get("group_id")

    async with AsyncSession() as session:
        group = await session.get(Group, group_id)
        if not group:
            await msg.answer("⚠️ Группа не найдена.")
            return

        group.welcome_template = msg.html_text
        await session.commit()

    await state.clear()
    await msg.answer(
        f"✅ Приветствие обновлено для группы <code>{group_id}</code>.",
        reply_markup=group_panel_keyboard(group_id)
    )


# ------------------------------------------------------------
@router.callback_query(F.data.startswith("edit_limit_message_"))
async def edit_limit_msg(cb: CallbackQuery, state: FSMContext):
    group_id = int(cb.data.split("_")[-1])
    await state.set_state(EditLimitMessage.waiting_for_limit_text)
    await state.update_data(group_id=group_id)

    await cb.message.edit_text(
        f"Введите новый текст при превышении лимита для группы <code>{group_id}</code>.\n"
        "Вы должны использовать: <code>{user}</code>"
    )


@router.message(EditLimitMessage.waiting_for_limit_text)
async def save_new_limit_text(msg: Message, state: FSMContext):
    data = await state.get_data()
    group_id = data.get("group_id")

    async with AsyncSession() as session:
        group = await session.get(Group, group_id)
        if not group:
            await msg.answer("⚠️ Группа не найдена.")
            return

        group.limit_exceeded_template = msg.html_text
        await session.commit()

    await state.clear()
    await msg.answer(
        f"✅ Предупреждение о лимитах обновлено для группы <code>{group_id}</code>.",
        reply_markup=group_panel_keyboard(group_id)
    )



# ------------------------------------------------------------

@router.callback_query(F.data.startswith("edit_limit_"))
async def edit_limit(cb: CallbackQuery, state: FSMContext):
    group_id = int(cb.data.split("_")[-1])
    await state.set_state(EditLimit.waiting_for_limit)
    await state.update_data(group_id=group_id)

    await cb.message.edit_text(
        f"Введите лимит сообщений для группы <code>{group_id}</code>.\n"
        "Варианты ответа: 0=restrict, N - целое число, * - без ограничений"
    )


@router.message(EditLimit.waiting_for_limit)
async def save_new_limit(msg: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    group_id = data.get("group_id")

    async with AsyncSession() as session:
        group = await session.get(Group, group_id)
        if not group:
            await msg.answer("⚠️ Группа не найдена.")
            return

        raw_limit = msg.text.strip()
        if raw_limit == '*':
            base_limit = None
        else:
            try:
                base_limit = int(raw_limit)
            except ValueError:
                await msg.answer("Неверный формат. Примеры: *, 0, 5")
                return

        # Обновляем лимит в таблице групп
        group.limit_msg = base_limit
        await session.commit()

        # Обновляем лимиты всех пользователей
        stmt = select(UnblockedUserLimit).where(UnblockedUserLimit.group_id == group_id)
        result = await session.execute(stmt)
        limits = result.scalars().all()

        for user_limit in limits:
            user_limit.max_messages = base_limit

            # Restrict/Unrestrict по лимиту
            try:
                if base_limit == 0:
                    await bot.restrict_chat_member(
                        chat_id=group_id,
                        user_id=user_limit.user_id,
                        permissions=ChatPermissions(can_send_messages=False)
                    )
                else:
                    await bot.restrict_chat_member(
                        chat_id=group_id,
                        user_id=user_limit.user_id,
                        permissions=ChatPermissions(can_send_messages=True)
                    )
            except Exception as e:
                print(f"[!] Ошибка рестрикта user_id={user_limit.user_id}: {e}")

        await session.commit()

    await state.clear()
    await msg.answer(
        f"✅ Базовый лимит обновлён и применён ко всем пользователям группы <code>{group_id}</code>.",
        reply_markup=group_panel_keyboard(group_id)
    )
# ------------------------------------------------------------

@router.callback_query(F.data.startswith("mailing_menu_"))
async def mailing_menu(cb: CallbackQuery, state: FSMContext):
    group_id = int(cb.data.split("_")[-1])
    await state.set_state(MailingMenuState.choosing_mailing_type)
    await state.update_data(group_id=group_id)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ По интервалу", callback_data=f"add_interval_{group_id}")
        ],
        [
            InlineKeyboardButton(text="🕒 По дате и времени", callback_data=f"add_timed_{group_id}")
        ]
    ])

    await cb.message.edit_text("Выберите тип новой рассылки:", reply_markup=kb)


@router.callback_query(F.data.startswith("add_interval_"))
async def interval_start(cb: CallbackQuery, state: FSMContext):
    group_id = int(cb.data.split("_")[-1])
    await state.set_state(IntervalMailingState.waiting_for_message)
    await state.update_data(group_id=group_id)
    await cb.message.edit_text("Введите текст рассылки (можно медиа с подписью):")


# === interval mailing step-by-step handlers ===
@router.message(IntervalMailingState.waiting_for_message)
async def interval_get_message(msg: Message, state: FSMContext):
    content_type = msg.content_type
    file_id = None
    caption = None

    if content_type in ["photo", "video", "document", "animation", "audio", "voice", "sticker"]:
        file_id = getattr(msg, content_type).file_id if content_type != "photo" else msg.photo[-1].file_id
        caption = msg.html_text if hasattr(msg, "caption") else ""
    elif content_type == "text":
        caption = msg.html_text
    else:
        await msg.answer("Отправьте текст или медиа (фото, видео, гифка, документ, стикер, аудио, голосовое).")
        return

    await state.update_data(
        media_file_id=f"{content_type.value}+++{file_id}",
        message=caption,
    )
    await state.set_state(IntervalMailingState.waiting_for_interval)
    await msg.answer("Укажи интервал в минутах/часах/днях (например, 30, 2h, 1d):")

@router.message(IntervalMailingState.waiting_for_interval)
async def interval_get_interval(msg: Message, state: FSMContext):
    raw = msg.text.lower().strip()
    multiplier = 1
    if raw.endswith("h"):
        multiplier = 60
        raw = raw[:-1]
    elif raw.endswith("d"):
        multiplier = 60 * 24
        raw = raw[:-1]
    try:
        interval = int(raw) * multiplier
        if interval <= 0:
            raise ValueError
    except ValueError:
        await msg.answer("Неверный формат. Пример: 60, 2h, 1d")
        return

    await state.update_data(interval=interval)
    await state.set_state(IntervalMailingState.waiting_for_repeats)
    await msg.answer("Сколько раз повторять рассылку? (0 = бесконечно)")


@router.message(IntervalMailingState.waiting_for_repeats)
async def interval_get_repeats(msg: Message, state: FSMContext):
    try:
        repeats = int(msg.text.strip())
        if repeats < 0:
            raise ValueError
    except ValueError:
        await msg.answer("Введите целое число 0 или больше.")
        return
    await state.update_data(repeats=repeats)
    await state.set_state(IntervalMailingState.waiting_for_pin)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Да", callback_data=f"pin")
        ],
        [
            InlineKeyboardButton(text="Нет", callback_data=f"not_pin")
        ]
    ])
    await msg.answer("Закреплять сообщение?", reply_markup=kb)


@router.callback_query(IntervalMailingState.waiting_for_pin)
async def interval_get_pin(cb: CallbackQuery, state: FSMContext):
    answer = cb.data
    if answer not in ["pin", "not_pin"]:
        await cb.message.answer("Ответь \"да\" или \"нет\"")
        return
    await state.update_data(pin=(answer == "pin"))
    if answer == "pin":
        await state.set_state(IntervalMailingState.waiting_for_unpin_delay)
        await cb.message.answer("Через сколько минут открепить сообщение? (0 = не откреплять)")
    else:
        await state.set_state(IntervalMailingState.waiting_for_delete_option)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Нет", callback_data=f"нет")],
            [InlineKeyboardButton(text="Сразу", callback_data=f"сразу")],
            [InlineKeyboardButton(text="Через N минут", callback_data=f"через N минут")]
        ]
        + ([[InlineKeyboardButton(text="После открепа", callback_data=f"после открепа")]] if (await state.get_data())['pin'] else [])
        )

        await cb.message.answer("Удалять сообщение?", reply_markup=kb)


@router.message(IntervalMailingState.waiting_for_unpin_delay)
async def interval_get_unpin_delay(msg: Message, state: FSMContext):
    try:
        delay = int(msg.text.strip())
        if delay < 0:
            raise ValueError
    except ValueError:
        await msg.answer("Введите число минут (0 = не откреплять)")
        return
    await state.update_data(unpin_after=delay)
    await state.set_state(IntervalMailingState.waiting_for_delete_option)

    kb = InlineKeyboardMarkup(inline_keyboard=[
                                                  [InlineKeyboardButton(text="Нет", callback_data=f"нет")],
                                                  [InlineKeyboardButton(text="Сразу", callback_data=f"сразу")],
                                                  [InlineKeyboardButton(text="Через N минут",
                                                                        callback_data=f"через N минут")]]
                                              + ([[InlineKeyboardButton(text="После открепа",
                                                                      callback_data=f"после открепа")]] if (await
    state.get_data())['pin'] else [])
                              )
    await msg.answer("Удалять сообщение?", reply_markup=kb)


@router.message(IntervalMailingState.waiting_for_delete_option)
async def interval_get_delete_time(msg: Message, state: FSMContext):
    delete_type = "after"
    try:
        delete_delay = int(msg.text.strip())
        if delete_delay < 0:
            raise ValueError
    except ValueError:
        await msg.answer("Введите число минут, через сколько удалить")
        return

    await state.update_data(delete_type=delete_type, delete_delay=delete_delay)

    data = await state.get_data()
    await add_scheduled_post(data, msg.bot)

    await state.clear()
    await msg.answer("✅ Рассылка по интервалу сохранена.")


@router.callback_query(IntervalMailingState.waiting_for_delete_option)
async def interval_get_delete_option(cb: CallbackQuery, state: FSMContext):
    text = cb.data
    delete_type = "none"
    delete_delay = None

    if text == "нет":
        delete_type = "none"
    elif text == "сразу":
        delete_type = "immediately"
    elif text == "после открепа":
        delete_type = "after_unpin"

    elif text == 'через N минут':
        await state.set_state(IntervalMailingState.waiting_for_delete_option)
        await cb.message.answer('Введите число минут, через сколько удалить')
        return

    await state.update_data(delete_type=delete_type, delete_delay=delete_delay)

    data = await state.get_data()
    await add_scheduled_post(data, cb.bot)

    await state.clear()
    await cb.message.answer("✅ Рассылка по интервалу сохранена.")


@router.callback_query(F.data.startswith("add_timed_"))
async def timed_start(cb: CallbackQuery, state: FSMContext):
    group_id = int(cb.data.split("_")[-1])
    await state.set_state(TimedMailingState.waiting_for_message)
    await state.update_data(group_id=group_id)
    await cb.message.edit_text("Введите текст рассылки (можно медиа с подписью):")


@router.message(TimedMailingState.waiting_for_message)
async def timed_get_message(msg: Message, state: FSMContext):
    content_type = msg.content_type
    file_id = None
    caption = None

    if content_type in ["photo", "video", "document", "animation", "audio", "voice", "sticker"]:
        file_id = getattr(msg, content_type).file_id if content_type != "photo" else msg.photo[-1].file_id
        caption = msg.html_text if hasattr(msg, "caption") else ""
    elif content_type == "text":
        caption = msg.html_text
    else:
        await msg.answer("Отправьте текст или медиа (фото, видео, гифка, документ, стикер, аудио, голосовое).")
        return

    await state.update_data(
        media_file_id=f"{content_type.value}+++{file_id}",
        message=caption,
    )

    await state.set_state(TimedMailingState.waiting_for_date)
    await msg.answer("Введите дату рассылки в формате ДД.ММ.ГГГГ:")


@router.message(TimedMailingState.waiting_for_date)
async def timed_get_date(msg: Message, state: FSMContext):
    print(msg.text)
    try:
        import pytz
        moscow_tz = pytz.timezone("Europe/Moscow")

        date = datetime.datetime.strptime(msg.text.strip(), "%d.%m.%Y").date()
        await state.update_data(date=date, tzinfo=moscow_tz.zone)
        await state.set_state(TimedMailingState.waiting_for_time)
        await msg.answer("Введите время в формате ЧЧ:ММ (24 часа):")
    except Exception as e:
        print(e)
        await msg.answer("Неверный формат. Пример: 01.08.2025")


@router.message(TimedMailingState.waiting_for_time)
async def timed_get_time(msg: Message, state: FSMContext):
    try:
        import pytz

        time = datetime.datetime.strptime(msg.text.strip(), "%H:%M").time()
        data = await state.get_data()

        moscow_tz = pytz.timezone("Europe/Moscow")
        local_dt = moscow_tz.localize(datetime.datetime.combine(data["date"], time))
        utc_dt = local_dt.astimezone(pytz.utc)

        await state.update_data(scheduled_datetime=utc_dt.replace(tzinfo=None))
        await state.set_state(TimedMailingState.waiting_for_pin)

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Да", callback_data="pin")],
            [InlineKeyboardButton(text="Нет", callback_data="not_pin")]
        ])
        await msg.answer("Закреплять сообщение?", reply_markup=kb)
    except Exception:
        await msg.answer("Неверный формат. Пример: 14:00")


@router.callback_query(TimedMailingState.waiting_for_pin)
async def timed_get_pin(cb: CallbackQuery, state: FSMContext):
    answer = cb.data
    await state.update_data(pin=(answer == "pin"))
    if answer == "pin":
        await state.set_state(TimedMailingState.waiting_for_unpin_delay)
        await cb.message.answer("Через сколько минут открепить сообщение? (0 = не откреплять)")
    else:
        await state.set_state(TimedMailingState.waiting_for_delete_option)
        kb = InlineKeyboardMarkup(inline_keyboard=[
                                                      [InlineKeyboardButton(text="Нет", callback_data=f"нет")],
                                                      [InlineKeyboardButton(text="Сразу", callback_data=f"сразу")],
                                                      [InlineKeyboardButton(text="Через N минут",
                                                                            callback_data=f"через N минут")]]
                                                  + ([[InlineKeyboardButton(text="После открепа",
                                                                          callback_data=f"после открепа")]] if (await state.get_data())['pin'] else [])
                                  )
        await cb.message.answer("Удалять сообщение?", reply_markup=kb)


@router.message(TimedMailingState.waiting_for_unpin_delay)
async def timed_get_unpin_delay(msg: Message, state: FSMContext):
    try:
        delay = int(msg.text.strip())
        if delay < 0:
            raise ValueError
    except ValueError:
        await msg.answer("Введите число минут (0 = не откреплять)")
        return
    await state.update_data(unpin_after=delay)
    await state.set_state(TimedMailingState.waiting_for_delete_option)

    kb = InlineKeyboardMarkup(inline_keyboard=[
                                                  [InlineKeyboardButton(text="Нет", callback_data=f"нет")],
                                                  [InlineKeyboardButton(text="Сразу", callback_data=f"сразу")],
                                                  [InlineKeyboardButton(text="Через N минут",
                                                                        callback_data=f"через N минут")]]
                                              + ([[InlineKeyboardButton(text="После открепа",
                                                                      callback_data=f"после открепа")]] if (await
    state.get_data())['pin'] else [])
                              )
    await msg.answer("Удалять сообщение?", reply_markup=kb)


@router.callback_query(TimedMailingState.waiting_for_delete_option)
async def timed_get_delete_option(cb: CallbackQuery, state: FSMContext):
    text = cb.data
    delete_type = "none"
    delete_delay = None

    if text == "нет":
        delete_type = "none"
    elif text == "сразу":
        delete_type = "immediately"
    elif text == "после открепа":
        delete_type = "after_unpin"
    elif text == "через N минут":
        await state.set_state(TimedMailingState.waiting_for_delete_delay)
        await cb.message.answer("Введите число минут, через сколько удалить")
        return

    await state.update_data(delete_type=delete_type, delete_delay=delete_delay)
    data = await state.get_data()
    await add_timed_post(data, cb.bot)
    await state.clear()
    await cb.message.answer("✅ Отложенная рассылка сохранена.")


@router.message(TimedMailingState.waiting_for_delete_delay)
async def timed_get_delete_delay(msg: Message, state: FSMContext):
    try:
        delay = int(msg.text.strip())
        if delay < 0:
            raise ValueError
    except ValueError:
        await msg.answer("Введите число минут, через сколько удалить")
        return

    await state.update_data(delete_type="after", delete_delay=delay)
    data = await state.get_data()
    await add_timed_post(data,  msg.bot)

    await state.clear()
    await msg.answer("✅ Отложенная рассылка сохранена.")


@router.callback_query(F.data.startswith("planned_posts_"))
async def planned_posts_list(cb: CallbackQuery, bot: Bot):
    group_id = int(cb.data.split("_")[-1])
    await cb.message.delete()

    async with AsyncSession() as session:
        stmt = select(ScheduledPost).where(ScheduledPost.group_id == group_id)
        result = await session.execute(stmt)
        posts = result.scalars().all()

    if not posts:
        await bot.send_message(cb.from_user.id, "📭 У вас нет запланированных постов")
        return

    for post in posts:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(text=f"🗑 Удалить ID {post.id}", callback_data=f"delete_post_{post.id}")
            ]]
        )

        # Формируем описание параметров рассылки
        details = [f"📌 ID: {post.id}", f"Тип: {'По интервалу' if post.type == 'interval' else 'По дате'}"]
        if post.type == "interval":
            details.append(f"Интервал: {post.interval_minutes} мин")
            details.append(f"Повторов: {post.repeat_count if post.repeat_count is not None else '∞'}")
        else:
            details.append(f"Дата и время: {post.scheduled_datetime.strftime('%d.%m.%Y %H:%M')}")

        details.append(f"Закреплять: {'Да' if post.pin else 'Нет'}")
        if post.unpin_after_minutes:
            details.append(f"Открепить через: {post.unpin_after_minutes} мин")

        if post.delete_type == "immediately":
            details.append("Удалить: сразу")
        elif post.delete_type == "after" and post.delete_after_minutes:
            details.append(f"Удалить через: {post.delete_after_minutes} мин")
        elif post.delete_type == "after_unpin":
            details.append("Удалить после открепления")
        else:
            details.append("Удалить: нет")

        params_text = "\n".join(details)

        try:
            ct = "text"
            media_id = ""
            if post.media_file_id and "+++" in post.media_file_id:
                ct, media_id = post.media_file_id.split("+++")

            if ct == "text" or not media_id:
                await bot.send_message(
                    chat_id=cb.from_user.id,
                    text=f"{post.content or '(пусто)'}\n\n{params_text}",
                    reply_markup=kb
                )
            elif ct == "photo":
                await bot.send_photo(chat_id=cb.from_user.id, photo=media_id, caption=f"{post.content or ''}\n\n{params_text}", reply_markup=kb)
            elif ct == "video":
                await bot.send_video(chat_id=cb.from_user.id, video=media_id, caption=f"{post.content or ''}\n\n{params_text}", reply_markup=kb)
            elif ct == "document":
                await bot.send_document(chat_id=cb.from_user.id, document=media_id, caption=f"{post.content or ''}\n\n{params_text}", reply_markup=kb)
            elif ct == "audio":
                await bot.send_audio(chat_id=cb.from_user.id, audio=media_id, caption=f"{post.content or ''}\n\n{params_text}", reply_markup=kb)
            elif ct == "voice":
                await bot.send_voice(chat_id=cb.from_user.id, voice=media_id, reply_markup=kb)
                await bot.send_message(chat_id=cb.from_user.id, text=params_text, reply_markup=kb)
            elif ct == "animation":
                await bot.send_animation(chat_id=cb.from_user.id, animation=media_id, caption=f"{post.content or ''}\n\n{params_text}", reply_markup=kb)
            elif ct == "sticker":
                await bot.send_sticker(chat_id=cb.from_user.id, sticker=media_id, reply_markup=kb)
                await bot.send_message(chat_id=cb.from_user.id, text=params_text, reply_markup=kb)
            else:
                await bot.send_message(chat_id=cb.from_user.id, text=f"[ID {post.id}] ❓ Неизвестный тип контента", reply_markup=kb)

        except Exception as e:
            print(f"[!] Ошибка отображения запланированного поста {post.id}: {e}")
            await bot.send_message(chat_id=cb.from_user.id, text=f"[ID {post.id}] ⚠️ Ошибка отображения", reply_markup=kb)

@router.callback_query(F.data.startswith("delete_post_"))
async def delete_post_handler(cb: CallbackQuery, bot: Bot):
    post_id = int(cb.data.split("_")[-1])

    async with AsyncSession() as session:
        post = await session.get(ScheduledPost, post_id)
        if post:
            await session.delete(post)
            await session.commit()

            from bot.scheduler import scheduler
            job = scheduler.get_job(f"{post.type}_{post_id}")
            if job:
                scheduler.remove_job(job.id)

            # Пытаемся отредактировать, если нельзя — удалим и отправим новое
            try:
                await cb.message.edit_text(f"✅ Рассылка ID {post_id} удалена.")
            except Exception:
                await cb.message.delete()
                await bot.send_message(cb.from_user.id, f"✅ Рассылка ID {post_id} удалена.")
        else:
            await cb.answer("Рассылка уже удалена или не найдена", show_alert=True)


async def get_admin_groups(session, username):
    stmt = select(Group).where(Group.admin_username == username)
    result = await session.execute(stmt)
    return result.scalars().all()


async def add_scheduled_post(data, bot: Bot):
    pprint(data)
    async with AsyncSession() as session:
        post = ScheduledPost(
            group_id=data["group_id"],
            type="interval",
            content=data["message"],
            interval_minutes=data["interval"],
            repeat_count=data["repeats"],
            pin=data.get("pin", False),
            unpin_after_minutes=data.get("unpin_after", None),
            delete_type=data["delete_type"],
            delete_after_minutes=data["delete_delay"],
            media_file_id=data["media_file_id"]
        )
        session.add(post)
        await session.flush()

        await add_post_to_schedule(bot, post)
        await session.commit()


async def add_timed_post(data, bot: Bot):
    async with AsyncSession() as session:
        post = ScheduledPost(
            group_id=data["group_id"],
            type="datetime",
            content=data["message"],
            scheduled_datetime=data["scheduled_datetime"],
            pin=data.get("pin", False),
            unpin_after_minutes=data.get("unpin_after", None),
            delete_type=data["delete_type"],
            delete_after_minutes=data["delete_delay"],
            media_file_id=data["media_file_id"]
        )
        session.add(post)
        await session.flush()

        await add_post_to_schedule(bot, post)
        await session.commit()