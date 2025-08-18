from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from math import ceil

PAGE_SIZE = 5


def groups_keyboard(groups, page=0):
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    page_groups = groups[start:end]

    buttons = [
        [InlineKeyboardButton(text=group.title, callback_data=f"group_settings_{group.id}")]
        for group in page_groups
    ]

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️", callback_data=f"groups_page_{page - 1}"))
    if end < len(groups):
        nav_buttons.append(InlineKeyboardButton(text="➡️", callback_data=f"groups_page_{page + 1}"))
    if nav_buttons:
        buttons.append(nav_buttons)

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def group_panel_keyboard(group_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👋️ Приветствие", callback_data=f"edit_welcome_{group_id}")],
        [InlineKeyboardButton(text="📊️ Лимит сообщений", callback_data=f"edit_limit_{group_id}")],
        [InlineKeyboardButton(text="💬 Превышение лимита", callback_data=f"edit_limit_message_{group_id}")],
        [InlineKeyboardButton(text="📤 Настройки рассылки", callback_data=f"mailing_menu_{group_id}")],
        [InlineKeyboardButton(text="📅 Запланированные посты", callback_data=f"planned_posts_{group_id}")],
        [InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete_{group_id}")]
    ])