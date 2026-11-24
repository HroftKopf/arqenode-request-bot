"""Создание, список, редактирование и отмена заявок клиентом."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

import config
from database import (
    cancel_request,
    create_request,
    edit_request,
    get_request,
    list_user_requests,
)
from helpers import notify_admins, parse_history_args, redraw, request_text
from keyboards import (
    categories,
    client_detail,
    client_list,
    confirmation,
    inline,
    menu,
)
from models import (
    CATEGORIES,
    EDITABLE,
    MAX_DESCRIPTION,
    MAX_PAGE,
    MIN_DESCRIPTION,
    TERMINAL,
)
from rate_limit import check_limit, format_retry
from states import EditRequest, NewRequest, ReplyForm

router = Router(name='client')
router.message.filter(F.chat.type == 'private')


async def list_view(user_id: int, page: int):
    page = max(0, min(page, MAX_PAGE))
    items, more = await list_user_requests(user_id, page)
    text = f'📋 Ваши заявки (страница {page + 1}). Выберите заявку:' if items else '📭 Заявок пока нет.'
    return text, client_list(items, page, more)


@router.message(StateFilter(None), F.text == '📝 Создать заявку')
async def create_form(message: Message, state: FSMContext):
    await state.set_state(NewRequest.category)
    await message.answer('📂 Шаг 1 из 3. Выберите категорию задачи:', reply_markup=categories())


@router.message(NewRequest.category, F.text.in_(CATEGORIES))
async def category_selected(message: Message, state: FSMContext):
    await state.update_data(category=message.text)
    await state.set_state(NewRequest.description)
    await message.answer(
        f'📝 Шаг 2 из 3. Опишите задачу ({MIN_DESCRIPTION}–{MAX_DESCRIPTION} символов).\n'
        'Пример: «Нужно настроить Nginx и HTTPS на Ubuntu VPS».\nОтмена: /cancel',
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(NewRequest.category)
async def category_invalid(message: Message):
    await message.answer('Выберите категорию кнопкой. Чтобы выйти, отправьте /cancel.')


@router.message(NewRequest.description, F.text)
async def description_received(message: Message, state: FSMContext):
    value = (message.text or '').strip()
    if not MIN_DESCRIPTION <= len(value) <= MAX_DESCRIPTION:
        await message.answer(
            f'Описание должно содержать {MIN_DESCRIPTION}–{MAX_DESCRIPTION} символов. Попробуйте ещё раз.'
        )
        return
    await state.update_data(description=value)
    await state.set_state(NewRequest.confirm)
    data = await state.get_data()
    await message.answer(
        f"✅ Шаг 3 из 3. Проверьте данные:\n\nКатегория: {data['category']}\n"
        f'Описание: {value}\n\nОтправить заявку?',
        reply_markup=confirmation(),
    )


@router.message(NewRequest.description)
async def description_invalid(message: Message):
    await message.answer(
        f'Пожалуйста, отправьте текстовое описание ({MIN_DESCRIPTION}–{MAX_DESCRIPTION} символов).'
    )


@router.message(NewRequest.confirm, F.text == '✅ Отправить')
async def send_new(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    category = data.get('category')
    description = data.get('description')
    if not category or not description:
        await state.clear()
        await message.answer('Сессия устарела. Создайте заявку заново.', reply_markup=menu(config.is_admin(message.from_user.id)))
        return
    wait = check_limit(message.from_user.id, 'create')
    if wait is not None:
        await message.answer(format_retry(wait, 'create'))
        return
    req_id = await create_request(message.from_user.id, category, description)
    await state.clear()
    item = await get_request(req_id)
    await message.answer(
        f'🎉 Заявка #{req_id} создана!\nСтатус: Новая.\n\n'
        'Для просмотра и переписки нажмите «📋 Мои заявки».',
        reply_markup=menu(config.is_admin(message.from_user.id)),
    )
    if item is not None:
        await notify_admins(bot, '🔔 Новая заявка!\n\n' + request_text(item, admin=True), item)


@router.message(NewRequest.confirm)
async def confirm_invalid(message: Message):
    await message.answer('Нажмите «✅ Отправить» или «❌ Отмена».')


@router.message(StateFilter(None), F.text == '📋 Мои заявки')
async def my_requests(message: Message):
    text, markup = await list_view(message.from_user.id, 0)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith('u:'))
async def client_callbacks(callback: CallbackQuery, state: FSMContext, bot: Bot):
    parts = (callback.data or '').split(':')
    uid = callback.from_user.id
    if len(parts) < 3:
        await callback.answer('Устаревшая кнопка.', show_alert=True)
        return
    action = parts[1]
    if action not in ('cat', 'desc', 'reply'):
        await state.clear()
    if action == 'list':
        if not parts[2].isdigit():
            await callback.answer('Некорректная страница.', show_alert=True)
            return
        page = min(int(parts[2]), MAX_PAGE)
        text, markup = await list_view(uid, page)
        await redraw(callback, text, markup)
        await callback.answer()
        return
    if not parts[2].isdigit():
        await callback.answer('Некорректная заявка.', show_alert=True)
        return
    rid = int(parts[2])
    item = await get_request(rid)
    if not item or item['user_id'] != uid:
        await callback.answer('Заявка недоступна.', show_alert=True)
        return
    if action == 'open':
        await redraw(callback, request_text(item), client_detail(item))
    elif action == 'edit':
        if item['status'] not in EDITABLE:
            await callback.answer('Заявку уже нельзя изменить.', show_alert=True)
            return
        await redraw(callback, 'Что хотите изменить?', inline([
            [('📂 Категория', f'u:cat:{rid}')],
            [('📝 Описание', f'u:desc:{rid}')],
            [('◀️ К заявке', f'u:open:{rid}')],
        ]))
    elif action in ('cat', 'desc'):
        if item['status'] not in EDITABLE:
            await callback.answer('Редактирование недоступно.', show_alert=True)
            return
        await state.clear()
        await state.update_data(request_id=rid)
        if action == 'cat':
            await state.set_state(EditRequest.category)
            await bot.send_message(uid, f'Заявка #{rid}: выберите новую категорию.', reply_markup=categories())
        else:
            await state.set_state(EditRequest.description)
            await bot.send_message(
                uid,
                f'Заявка #{rid}: отправьте новое описание '
                f'({MIN_DESCRIPTION}–{MAX_DESCRIPTION} символов).\nОтмена: /cancel',
                reply_markup=ReplyKeyboardRemove(),
            )
    elif action == 'cancel':
        if item['status'] in TERMINAL:
            await callback.answer('Заявка уже закрыта.', show_alert=True)
            return
        await redraw(callback, f'Точно отменить заявку #{rid}?', inline([
            [('🚫 Да, отменить', f'u:confirmcancel:{rid}')],
            [('◀️ Нет, вернуться', f'u:open:{rid}')],
        ]))
    elif action == 'confirmcancel':
        wait = check_limit(uid, 'cancel')
        if wait is not None:
            await callback.answer(format_retry(wait, 'cancel'), show_alert=True)
            return
        outcome, updated = await cancel_request(rid, uid)
        if outcome == 'updated' and updated is not None:
            await notify_admins(bot, f'🚫 Клиент отменил заявку #{rid}.', updated)
            await redraw(callback, 'Заявка отменена.\n\n' + request_text(updated), client_detail(updated))
        elif outcome == 'closed':
            await redraw(callback, 'Заявка уже закрыта.', client_detail(updated) if updated else None)
        else:
            await redraw(callback, 'Заявка не найдена.', None)
    elif action == 'reply':
        if item['status'] in TERMINAL:
            await callback.answer('Заявка закрыта.', show_alert=True)
            return
        await state.clear()
        await state.update_data(request_id=rid, role='client')
        await state.set_state(ReplyForm.text)
        await bot.send_message(
            uid,
            f'💬 Ответ по заявке #{rid}.\n'
            'Отправьте текст (до 1500 символов).\nДля отмены: /cancel',
            reply_markup=ReplyKeyboardRemove(),
        )
    elif action == 'history':
        from handlers.dialogs import show_history
        page, max_id = parse_history_args(parts)
        await show_history(callback, item, 'client', page, max_id)
    else:
        await callback.answer('Неизвестная кнопка.', show_alert=True)
        return
    await callback.answer()


@router.message(EditRequest.category, F.text.in_(CATEGORIES))
async def save_category(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    rid = data.get('request_id')
    if not isinstance(rid, int):
        await state.clear()
        await message.answer('Сессия устарела, повторите действие через карточку заявки.', reply_markup=menu(config.is_admin(message.from_user.id)))
        return
    wait = check_limit(message.from_user.id, 'edit')
    if wait is not None:
        await message.answer(format_retry(wait, 'edit'))
        return
    ok, item = await edit_request(rid, message.from_user.id, 'category', message.text or '')
    await state.clear()
    if ok and item is not None:
        await notify_admins(bot, f'✏️ Клиент изменил категорию заявки #{rid}.\nНовая категория: {message.text}', item)
    await message.answer('✅ Категория изменена.' if ok else 'Изменить заявку уже нельзя.', reply_markup=menu(config.is_admin(message.from_user.id)))


@router.message(EditRequest.category)
async def invalid_edit_category(message: Message):
    await message.answer('Выберите категорию кнопкой или отмените ввод: /cancel.')


@router.message(EditRequest.description, F.text)
async def save_description(message: Message, state: FSMContext, bot: Bot):
    value = (message.text or '').strip()
    if not MIN_DESCRIPTION <= len(value) <= MAX_DESCRIPTION:
        await message.answer(
            f'Новое описание должно содержать {MIN_DESCRIPTION}–{MAX_DESCRIPTION} символов.'
        )
        return
    data = await state.get_data()
    rid = data.get('request_id')
    if not isinstance(rid, int):
        await state.clear()
        await message.answer('Сессия устарела, повторите действие через карточку заявки.', reply_markup=menu(config.is_admin(message.from_user.id)))
        return
    wait = check_limit(message.from_user.id, 'edit')
    if wait is not None:
        await message.answer(format_retry(wait, 'edit'))
        return
    ok, item = await edit_request(rid, message.from_user.id, 'description', value)
    await state.clear()
    if ok and item is not None:
        await notify_admins(bot, f'✏️ Клиент обновил описание заявки #{rid}.\n\n{value}', item)
    await message.answer('✅ Описание изменено.' if ok else 'Изменить заявку уже нельзя.', reply_markup=menu(config.is_admin(message.from_user.id)))


@router.message(EditRequest.description)
async def invalid_edit_description(message: Message):
    await message.answer('Отправьте текстовое описание либо /cancel.')


@router.message(StateFilter(None))
async def unknown_command(message: Message):
    await message.answer(
        'Не понял сообщение. Используйте кнопки меню или /help — там есть подсказки.',
        reply_markup=menu(config.is_admin(message.from_user.id)),
    )
