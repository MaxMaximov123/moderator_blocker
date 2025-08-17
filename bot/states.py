from aiogram.fsm.state import StatesGroup, State


class EditWelcome(StatesGroup):
    waiting_for_welcome_text = State()


class EditLimitMessage(StatesGroup):
    waiting_for_limit_text = State()

class EditLimit(StatesGroup):
    waiting_for_limit = State()


class EditSchedule(StatesGroup):
    waiting_for_message = State()
    waiting_for_interval = State()


# Тип A: Интервальная рассылка
class IntervalMailingState(StatesGroup):
    waiting_for_message = State()            # сообщение (текст/медиа)
    waiting_for_interval = State()           # интервал (минуты/часы/дни)
    waiting_for_repeats = State()            # количество повторений
    waiting_for_pin = State()                # закреплять?
    waiting_for_unpin_delay = State()        # через сколько открепить
    waiting_for_delete_option = State()      # удалить сообщение?
    waiting_for_delete_delay = State()       # через сколько удалить


# Тип B: По дате и времени
class TimedMailingState(StatesGroup):
    waiting_for_message = State()
    waiting_for_date = State()               # дата (выбор календаря)
    waiting_for_time = State()               # время (00:00 – 23:59)
    waiting_for_pin = State()
    waiting_for_unpin_delay = State()
    waiting_for_delete_option = State()
    waiting_for_delete_delay = State()


# Общие состояния для выбора группы или очистки
class MailingMenuState(StatesGroup):
    choosing_mailing_type = State()
    choosing_post_to_delete = State()