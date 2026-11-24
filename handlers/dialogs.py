"""Диалоги клиента и администратора внутри карточки заявки."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import config
from database import add_reply, history
from helpers import history_text, notify_admins, redraw, safe_send
from keyboards import client_detail, history_keyboard, menu
from models import MAX_REPLY
from rate_limit import check_limit, format_retry
from states import ReplyForm

router = Router(name='dialogs')
router.message.filter(F.chat.type == 'private')


async def show_history(
    callback: CallbackQuery,
    item: dict,
    role: str,
    page: int = 0,
    max_id: int | None = None,
):
    rid = item['id']
    rows, more, effective_max_id = await history(rid, page, max_id)
    text = history_text(rid, rows, page, more)
    await redraw(callback, text, history_keyboard(role, rid, page, more, effective_max_id))


@router.message(ReplyForm.text, F.text)
async def record_reply(message: Message, state: FSMContext, bot: Bot):
    content = (message.text or '').strip()
    if not 1 <= len(content) <= MAX_REPLY:
        await message.answer(f'Отправьте сообщение длиной 1–{MAX_REPLY} символов или /cancel.')
        return
    data = await state.get_data()
    role, rid = data.get('role'), data.get('request_id')
    if not isinstance(rid, int) or role not in ('client', 'admin'):
        await state.clear()
        await message.answer('Сессия устарела, повторите действие через карточку заявки.')
        return
    if role == 'admin' and message.from_user.id not in config.ADMIN_IDS:
        await state.clear()
        await message.answer('⛔ Недостаточно прав.')
        return
    wait = check_limit(message.from_user.id, 'reply')
    if wait is not None:
        await message.answer(format_retry(wait, 'reply'))
        return
    outcome, item = await add_reply(rid, message.from_user.id, role, content)
    await state.clear()
    if outcome == 'missing':
        await message.answer('Заявка не найдена.', reply_markup=menu(config.is_admin(message.from_user.id)))
        return
    if outcome == 'closed':
        await message.answer('Заявка уже закрыта.', reply_markup=menu(config.is_admin(message.from_user.id)))
        return
    if item is None:
        await message.answer('Не удалось отправить сообщение.', reply_markup=menu(config.is_admin(message.from_user.id)))
        return
    await message.answer(
        f'✅ Сообщение сохранено в переписке заявки #{rid}.',
        reply_markup=menu(config.is_admin(message.from_user.id)),
    )
    if role == 'admin':
        success = await safe_send(
            bot,
            item['user_id'],
            f'💬 Ответ по заявке #{rid} от администратора:\n\n{content}\n\n'
            'Откройте «📋 Мои заявки» для ответа.',
            reply_markup=client_detail(item),
        )
        await notify_admins(
            bot,
            f'💬 Коллега ответил клиенту по заявке #{rid}:\n\n{content}',
            item,
            exclude=message.from_user.id,
        )
        if not success:
            await message.answer(
                '⚠️ Клиенту не удалось доставить уведомление. Сообщение сохранено в истории.'
            )
    else:
        await notify_admins(bot, f'💬 Ответ клиента по заявке #{rid}:\n\n{content}', item)


@router.message(ReplyForm.text)
async def invalid_reply(message: Message):
    await message.answer('Сейчас принимается только текст. Отправьте сообщение или /cancel.')
