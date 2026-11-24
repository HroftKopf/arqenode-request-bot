"""Административный раздел: поиск, фильтры и смена статусов."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

import config
from database import get_request, list_admin_requests, set_status
from helpers import (
    notify_admins,
    parse_history_args,
    redraw,
    request_text,
    safe_send,
)
from keyboards import admin_detail, admin_filters, admin_list
from models import MAX_PAGE, STATUS_LABELS, TERMINAL, status_label
from rate_limit import check_limit, format_retry
from states import ReplyForm

router = Router(name='admin')
router.message.filter(F.chat.type == 'private')


async def show_panel(message: Message):
    await message.answer(
        '🛠 Панель администратора Arqenode\n\n'
        'Выберите фильтр ниже и откройте нужную заявку.\n'
        'В карточке доступны ответ клиенту, история и изменение статуса.\n\n'
        'Команды: /admin — панель, /requests — список, /help — инструкция, /cancel — отменить ввод.',
        reply_markup=admin_filters(),
    )


@router.message(Command('admin'))
@router.message(StateFilter(None), F.text == '🛠 Админ-панель')
async def admin_start(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer('⛔ Доступ закрыт.')
        return
    await state.clear()
    await show_panel(message)


@router.message(Command('requests'))
async def admin_requests(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        await message.answer('⛔ Доступ закрыт.')
        return
    await state.clear()
    items, more = await list_admin_requests('all', 0)
    await message.answer(
        '📋 Все заявки. Нажмите на нужную:' if items else '📭 Заявок пока нет.',
        reply_markup=admin_list(items, 'all', 0, more),
    )


@router.callback_query(F.data.startswith('a:'))
async def admin_callbacks(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer('⛔ Доступ запрещён.', show_alert=True)
        return
    parts = (callback.data or '').split(':')
    action = parts[1] if len(parts) > 1 else ''
    if action != 'reply':
        await state.clear()
    if action == 'menu':
        await redraw(callback, '🛠 Панель администратора\nВыберите фильтр:', admin_filters())
        await callback.answer()
        return
    if action == 'list':
        if len(parts) != 4 or (parts[2] != 'all' and parts[2] not in STATUS_LABELS) or not parts[3].isdigit():
            await callback.answer('Некорректный фильтр.', show_alert=True)
            return
        code, page = parts[2], min(int(parts[3]), MAX_PAGE)
        items, more = await list_admin_requests(code, page)
        label = 'Все' if code == 'all' else status_label(code)
        await redraw(
            callback,
            f'📋 {label} — страница {page + 1}.\nВыберите заявку:' if items else 'Заявок по фильтру нет.',
            admin_list(items, code, page, more),
        )
        await callback.answer()
        return
    if len(parts) < 3 or not parts[2].isdigit():
        await callback.answer('Некорректная заявка.', show_alert=True)
        return
    rid = int(parts[2])
    item = await get_request(rid)
    if not item:
        await callback.answer('Заявка не найдена.', show_alert=True)
        return
    if action == 'open':
        await redraw(callback, request_text(item, admin=True), admin_detail(item))
    elif action == 'status':
        if len(parts) != 4 or parts[3] not in STATUS_LABELS:
            await callback.answer('Некорректный статус.', show_alert=True)
            return
        wait = check_limit(callback.from_user.id, 'status')
        if wait is not None:
            await callback.answer(format_retry(wait, 'status'), show_alert=True)
            return
        new_status = parts[3]
        outcome, updated = await set_status(rid, new_status, admin_id=callback.from_user.id)
        if outcome == 'updated' and updated is not None:
            delivered = await safe_send(
                bot,
                updated['user_id'],
                f'🔔 Статус вашей заявки #{rid} изменён: {status_label(new_status)}.\n'
                'Откройте «📋 Мои заявки», чтобы посмотреть детали.',
            )
            if not delivered:
                await safe_send(
                    bot,
                    callback.from_user.id,
                    '⚠️ Статус изменён, но клиенту не удалось доставить уведомление.',
                )
            await notify_admins(
                bot,
                f'📋 Администратор обновил статус заявки #{rid}: {status_label(new_status)}.',
                updated,
                exclude=callback.from_user.id,
            )
            await redraw(callback, request_text(updated, admin=True), admin_detail(updated))
        elif outcome == 'same':
            await callback.answer('Этот статус уже установлен.')
            return
        elif outcome == 'invalid':
            current = status_label(item['status'])
            target = status_label(new_status)
            await callback.answer(f'Недопустимый переход: {current} → {target}.', show_alert=True)
            return
        elif outcome == 'closed':
            await callback.answer('Заявка уже закрыта. Статус изменить нельзя.', show_alert=True)
            return
        else:
            await callback.answer('Заявка не найдена.', show_alert=True)
            return
    elif action == 'reply':
        if item['status'] in TERMINAL:
            await callback.answer('Заявка закрыта.', show_alert=True)
            return
        await state.clear()
        await state.update_data(request_id=rid, role='admin')
        await state.set_state(ReplyForm.text)
        await bot.send_message(
            callback.from_user.id,
            f'💬 Ответ клиенту по заявке #{rid}.\n'
            'Введите текст до 1500 символов.\nОтмена: /cancel',
            reply_markup=ReplyKeyboardRemove(),
        )
    elif action == 'history':
        from handlers.dialogs import show_history
        page, max_id = parse_history_args(parts)
        await show_history(callback, item, 'admin', page, max_id)
    else:
        await callback.answer('Неизвестная кнопка.', show_alert=True)
        return
    await callback.answer()
