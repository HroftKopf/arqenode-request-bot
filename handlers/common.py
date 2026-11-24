"""Глобальные команды: доступны даже во время заполнения формы."""
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import config
from helpers import client_help
from keyboards import menu

router = Router(name='common')
router.message.filter(F.chat.type == 'private')


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    admin = message.from_user.id in config.ADMIN_IDS
    await message.answer(client_help(admin), reply_markup=menu(admin))


@router.message(Command('help'))
@router.message(F.text == '❓ Помощь')
async def help_command(message: Message):
    await message.answer(client_help(message.from_user.id in config.ADMIN_IDS))


@router.message(Command('id'))
async def show_id(message: Message):
    await message.answer(f'Ваш Telegram ID: {message.from_user.id}')


@router.message(Command('cancel'))
@router.message(F.text == '❌ Отмена')
async def cancel(message: Message, state: FSMContext):
    old = await state.get_state()
    await state.clear()
    await message.answer(
        '❌ Текущий ввод отменён.' if old else 'Сейчас нечего отменять.',
        reply_markup=menu(message.from_user.id in config.ADMIN_IDS),
    )
