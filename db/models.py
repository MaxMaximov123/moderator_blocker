from sqlalchemy import (
    Column, BigInteger, Integer, Text, ForeignKey,
    Enum, Boolean, DateTime, Interval, String
)
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import declarative_base
import enum
import datetime
from sqlalchemy.dialects.postgresql import JSON

Base = declarative_base()


class Admin(Base, AsyncAttrs):
    __tablename__ = "admins"

    id = Column(BigInteger, primary_key=True)
    username = Column(Text)


class Group(Base, AsyncAttrs):
    __tablename__ = "groups"

    id = Column(BigInteger, primary_key=True)
    title = Column(Text)
    description = Column(Text)
    welcome_template = Column(Text)
    limit_exceeded_template = Column(Text)
    limit_msg = Column(Integer)
    admin_username = Column(Text)


class MessageSchedule(Base, AsyncAttrs):  # можно удалить в будущем (всё заменяет ScheduledPost)
    __tablename__ = "messages_schedule"

    id = Column(Integer, primary_key=True)
    group_id = Column(BigInteger, ForeignKey("groups.id"))
    message = Column(Text)
    interval_minutes = Column(Integer)


class UnblockedUserLimit(Base, AsyncAttrs):
    __tablename__ = "unblocked_limits"

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger)
    group_id = Column(BigInteger, ForeignKey("groups.id"))
    max_messages = Column(Integer)  # 0 = без ограничений
    used_messages = Column(Integer, default=0)
    delete_after_minutes = Column(Integer, nullable=True)
    username = Column(Text, nullable=True)


class PostType(str, enum.Enum):
    interval = "interval"
    datetime = "datetime"


class ScheduledPost(Base, AsyncAttrs):
    __tablename__ = "scheduled_posts"

    id = Column(Integer, primary_key=True)
    group_id = Column(BigInteger, ForeignKey("groups.id"))
    type = Column(Enum(PostType))

    # Общее содержимое
    content = Column(Text)  # текст или подпись
    media_file_id = Column(Text, nullable=True)  # file_id, если есть

    # Тип A (по интервалу)
    interval_minutes = Column(Integer, nullable=True)
    repeat_count = Column(Integer, nullable=True)

    # Тип B (по дате/времени)
    scheduled_datetime = Column(DateTime, nullable=True)

    # Общие флаги
    pin = Column(Boolean, default=False)
    unpin_after_minutes = Column(Integer, nullable=True)
    delete_type = Column(Text, default="none")  # none / immediately / after / after_unpin
    delete_after_minutes = Column(Integer, nullable=True)


class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, nullable=False)
    message_id = Column(BigInteger, nullable=False)
    run_at = Column(DateTime, nullable=False)