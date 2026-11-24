from aiogram.fsm.state import State, StatesGroup


class NewRequest(StatesGroup):
    category = State()
    description = State()
    confirm = State()


class EditRequest(StatesGroup):
    category = State()
    description = State()


class ReplyForm(StatesGroup):
    text = State()
