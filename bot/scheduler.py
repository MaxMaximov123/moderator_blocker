# bot/scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from db.session import AsyncSession
from db.models import MessageSchedule, Group, ScheduledPost
from sqlalchemy import select
from aiogram import Bot
import datetime

scheduler = AsyncIOScheduler()


async def send_group_message(bot: Bot, group_id: int, text: str):
    try:
        await bot.send_message(chat_id=group_id, text=text)
    except Exception as e:
        print(f"[!] Ошибка отправки в группу {group_id}: {e}")


from aiogram import Bot
from db.models import ScheduledPost
from sqlalchemy import delete, update

async def send_scheduled_message(bot: Bot, post_id: int):
    async with AsyncSession() as session:
        post = await session.get(ScheduledPost, post_id)
        if not post:
            print("Post didn't found")
            return

        try:
            ct, media_file_id = post.media_file_id.split('+++')
            sent = None
            if ct == "text" or not media_file_id:
                sent = await bot.send_message(chat_id=post.group_id, text=post.content or "")
            elif ct == "photo":
                sent = await bot.send_photo(chat_id=post.group_id, photo=media_file_id, caption=post.content or "")
            elif ct == "video":
                sent = await bot.send_video(chat_id=post.group_id, video=media_file_id, caption=post.content or "")
            elif ct == "document":
                sent = await bot.send_document(chat_id=post.group_id, document=media_file_id,
                                               caption=post.content or "")
            elif ct == "audio":
                sent = await bot.send_audio(chat_id=post.group_id, audio=media_file_id, caption=post.content or "")
            elif ct == "voice":
                sent = await bot.send_voice(chat_id=post.group_id, voice=media_file_id)
            elif ct == "animation":
                sent = await bot.send_animation(chat_id=post.group_id, animation=media_file_id,
                                                caption=post.content or "")
            elif ct == "sticker":
                sent = await bot.send_sticker(chat_id=post.group_id, sticker=media_file_id)
            else:
                print(f"[!] Неизвестный content_type: {ct}")
                return
        except Exception as e:
            print(f"[!] Ошибка отправки поста {post_id}: {e}")
            return

        message_id = sent.message_id

        # Pin
        if post.pin:
            try:
                await bot.pin_chat_message(chat_id=post.group_id, message_id=message_id, disable_notification=True)
            except Exception as e:
                print(f"[!] Ошибка закрепления: {e}")

        # Unpin
        if post.unpin_after_minutes:
            scheduler.add_job(
                bot.unpin_chat_message,
                DateTrigger(run_date=datetime.datetime.now() + datetime.timedelta(minutes=post.unpin_after_minutes)),
                kwargs={"chat_id": post.group_id, "message_id": message_id}
            )

        # Delete
        if post.delete_type == "immediately":
            scheduler.add_job(
                bot.delete_message,
                DateTrigger(run_date=datetime.datetime.now() + datetime.timedelta(seconds=1)),
                kwargs={"chat_id": post.group_id, "message_id": message_id}
            )
        elif post.delete_type == "after" and post.delete_after_minutes:
            scheduler.add_job(
                bot.delete_message,
                DateTrigger(run_date=datetime.datetime.now() + datetime.timedelta(minutes=post.delete_after_minutes)),
                kwargs={"chat_id": post.group_id, "message_id": message_id}
            )
        elif post.delete_type == "after_unpin" and post.unpin_after_minutes:
            scheduler.add_job(
                bot.delete_message,
                DateTrigger(run_date=datetime.datetime.now() + datetime.timedelta(minutes=post.unpin_after_minutes + 1)),
                kwargs={"chat_id": post.group_id, "message_id": message_id}
            )

        # Уменьшить счётчик, если интервальная
        if post.type == "interval":
            if post.repeat_count is not None and post.repeat_count > 0:
                post.repeat_count -= 1
                await session.commit()
                if post.repeat_count == 0:
                    await session.delete(post)
                    await session.commit()
                    # try:
                    #     await bot.send_message(chat_id=post.group_id, text=f"✅ Рассылка {post.id} завершена и удалена.")
                    # except:
                    #     pass
                    job_id = f"interval_{post.id}"
                    job = scheduler.get_job(job_id)
                    if job:
                        scheduler.remove_job(job_id)

        # Если по дате — удаляем после отправки
        elif post.type == "datetime":
            await session.delete(post)
            await session.commit()
            # try:
            #     await bot.send_message(chat_id=post.group_id, text=f"✅ Одноразовая рассылка {post.id} выполнена и удалена.")
            # except:
            #     pass
            job_id = f"datetime_{post.id}"
            job = scheduler.get_job(job_id)
            if job:
                scheduler.remove_job(job_id)


async def load_schedules(bot: Bot):
    async with AsyncSession() as session:
        stmt = select(ScheduledPost)
        result = await session.execute(stmt)
        posts = result.scalars().all()

        for post in posts:
            await add_post_to_schedule(bot, post)


async def start_scheduler(bot: Bot):
    await load_schedules(bot)
    scheduler.start()

async def add_post_to_schedule(bot: Bot, post: ScheduledPost):
    from bot.scheduler import send_scheduled_message
    if post.type == "interval":
        if post.repeat_count == 0 or post.repeat_count > 0:
            scheduler.add_job(
                send_scheduled_message,
                IntervalTrigger(minutes=post.interval_minutes),
                args=[bot, post.id],
                id=f"interval_{post.id}",
                replace_existing=True
            )
    elif post.type == "datetime":
        if post.scheduled_datetime and post.scheduled_datetime > datetime.datetime.now():
            scheduler.add_job(
                send_scheduled_message,
                DateTrigger(run_date=post.scheduled_datetime),
                args=[bot, post.id],
                id=f"datetime_{post.id}",
                replace_existing=True
            )