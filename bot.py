import logging
import sqlite3
import os
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

BOT_TOKEN = os.getenv("BOT_TOKEN")
USER_IDS = list(filter(None, [os.getenv("USER_ID"), os.getenv("USER_ID_2")]))
USER_IDS = [int(x) for x in USER_IDS]

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

DB = "badminton.db"

# --- DB ---
def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        balance REAL DEFAULT 0,
        created_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS trainings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        price REAL DEFAULT 16,
        created_at TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS training_players (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        training_id INTEGER NOT NULL,
        player_id INTEGER NOT NULL,
        status TEXT DEFAULT 'registered',
        paid REAL DEFAULT 0,
        note TEXT,
        FOREIGN KEY (training_id) REFERENCES trainings(id),
        FOREIGN KEY (player_id) REFERENCES players(id)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS shuttlecocks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        change INTEGER NOT NULL,
        reason TEXT,
        created_at TEXT NOT NULL
    )""")
    conn.commit()
    conn.close()

def db():
    return sqlite3.connect(DB)

def get_players():
    with db() as conn:
        return conn.execute("SELECT * FROM players ORDER BY name").fetchall()

def get_player(player_id):
    with db() as conn:
        return conn.execute("SELECT * FROM players WHERE id=?", (player_id,)).fetchone()

def add_player(name):
    with db() as conn:
        conn.execute("INSERT INTO players (name, created_at) VALUES (?, ?)",
                     (name, datetime.now().isoformat()))
        conn.commit()

def delete_player(player_id):
    with db() as conn:
        conn.execute("DELETE FROM players WHERE id=?", (player_id,))
        conn.execute("DELETE FROM training_players WHERE player_id=?", (player_id,))
        conn.commit()

def update_player_balance(player_id, delta):
    with db() as conn:
        conn.execute("UPDATE players SET balance = balance + ? WHERE id=?", (delta, player_id))
        conn.commit()

def get_trainings():
    with db() as conn:
        return conn.execute("SELECT * FROM trainings ORDER BY date DESC").fetchall()

def get_training(training_id):
    with db() as conn:
        return conn.execute("SELECT * FROM trainings WHERE id=?", (training_id,)).fetchone()

def add_training(date, price=16):
    with db() as conn:
        conn.execute("INSERT INTO trainings (date, price, created_at) VALUES (?, ?, ?)",
                     (date, price, datetime.now().isoformat()))
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

def delete_training(training_id):
    with db() as conn:
        conn.execute("DELETE FROM trainings WHERE id=?", (training_id,))
        conn.execute("DELETE FROM training_players WHERE training_id=?", (training_id,))
        conn.commit()

def get_training_players(training_id):
    with db() as conn:
        return conn.execute("""
            SELECT tp.*, p.name FROM training_players tp
            JOIN players p ON tp.player_id = p.id
            WHERE tp.training_id = ?
            ORDER BY p.name
        """, (training_id,)).fetchall()

def add_player_to_training(training_id, player_id):
    with db() as conn:
        existing = conn.execute(
            "SELECT id FROM training_players WHERE training_id=? AND player_id=?",
            (training_id, player_id)).fetchone()
        if not existing:
            conn.execute("INSERT INTO training_players (training_id, player_id) VALUES (?, ?)",
                         (training_id, player_id))
            conn.commit()
            return True
        return False

def remove_player_from_training(training_id, player_id):
    with db() as conn:
        conn.execute("DELETE FROM training_players WHERE training_id=? AND player_id=?",
                     (training_id, player_id))
        conn.commit()

def set_player_status(training_id, player_id, status, note=None):
    with db() as conn:
        conn.execute("UPDATE training_players SET status=?, note=? WHERE training_id=? AND player_id=?",
                     (status, note, training_id, player_id))
        conn.commit()

def set_player_paid(training_id, player_id, amount):
    with db() as conn:
        old = conn.execute("SELECT paid FROM training_players WHERE training_id=? AND player_id=?",
                           (training_id, player_id)).fetchone()
        old_paid = old[0] if old else 0
        conn.execute("UPDATE training_players SET paid=? WHERE training_id=? AND player_id=?",
                     (amount, training_id, player_id))
        conn.commit()
        # Update player balance
        delta = amount - old_paid
        conn.execute("UPDATE players SET balance = balance + ? WHERE id=?", (delta, player_id))
        conn.commit()

def get_shuttlecock_balance():
    with db() as conn:
        row = conn.execute("SELECT SUM(change) FROM shuttlecocks").fetchone()
        return row[0] or 0

def change_shuttlecocks(amount, reason):
    with db() as conn:
        conn.execute("INSERT INTO shuttlecocks (change, reason, created_at) VALUES (?, ?, ?)",
                     (amount, reason, datetime.now().isoformat()))
        conn.commit()

# --- Auth ---
def is_authorized(user_id):
    return user_id in USER_IDS

# --- States ---
class AddPlayer(StatesGroup):
    waiting_name = State()

class AddTraining(StatesGroup):
    waiting_date = State()

class EditPaid(StatesGroup):
    waiting_amount = State()

class CancelPlayer(StatesGroup):
    waiting_note = State()

class Shuttlecocks(StatesGroup):
    waiting_add = State()
    waiting_remove = State()

# --- Keyboards ---
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Тренировки", callback_data="trainings"),
         InlineKeyboardButton(text="👥 Участники", callback_data="players")],
        [InlineKeyboardButton(text="💰 Финансы", callback_data="finances"),
         InlineKeyboardButton(text="🏸 Воланы", callback_data="shuttlecocks")],
    ])

def back_button(to="main"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"back:{to}")]
    ])

def back_kb(to="main"):
    return [InlineKeyboardButton(text="◀️ Назад", callback_data=f"back:{to}")]

# --- Handlers ---
async def check_auth(callback_or_message):
    if isinstance(callback_or_message, CallbackQuery):
        uid = callback_or_message.from_user.id
    else:
        uid = callback_or_message.from_user.id
    return is_authorized(uid)

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    if not is_authorized(message.from_user.id):
        await message.answer("⛔️ Нет доступа.")
        return
    await state.clear()
    await message.answer("👋 Привет! Бот для управления тренировками.", reply_markup=main_menu())

@dp.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    if not is_authorized(message.from_user.id):
        return
    await state.clear()
    await message.answer("Главное меню:", reply_markup=main_menu())

@dp.callback_query(F.data.startswith("back:"))
async def cb_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    dest = callback.data.split(":", 1)[1]
    if dest == "main":
        await callback.message.edit_text("Главное меню:", reply_markup=main_menu())
    elif dest == "trainings":
        await show_trainings(callback.message, edit=True)
    elif dest == "players":
        await show_players(callback.message, edit=True)
    elif dest == "shuttlecocks":
        await show_shuttlecocks(callback.message, edit=True)

# ==================== PLAYERS ====================
async def show_players(msg, edit=False):
    players = get_players()
    if not players:
        text = "👥 Участников пока нет."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить", callback_data="player_add")],
            back_kb()
        ])
    else:
        text = "👥 <b>Участники:</b>\n"
        buttons = []
        for p in players:
            balance = p[2]
            bal_text = f"+{balance:.0f}€" if balance > 0 else f"{balance:.0f}€"
            buttons.append([InlineKeyboardButton(
                text=f"{p[1]}  {bal_text}",
                callback_data=f"player_view:{p[0]}"
            )])
        buttons.append([InlineKeyboardButton(text="➕ Добавить", callback_data="player_add")])
        buttons.append(back_kb())
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    if edit:
        await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await msg.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "players")
async def cb_players(callback: CallbackQuery):
    await show_players(callback.message, edit=True)

@dp.callback_query(F.data == "player_add")
async def cb_player_add(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddPlayer.waiting_name)
    await callback.message.edit_text("Введи имя участника:", reply_markup=back_button("players"))

@dp.message(AddPlayer.waiting_name)
async def process_player_name(message: Message, state: FSMContext):
    name = message.text.strip()
    try:
        add_player(name)
        await state.clear()
        sent = await message.answer(f"✅ Участник {name} добавлен!", reply_markup=main_menu())
    except:
        sent = await message.answer(f"❌ Участник с таким именем уже есть.", reply_markup=main_menu())

@dp.callback_query(F.data.startswith("player_view:"))
async def cb_player_view(callback: CallbackQuery):
    player_id = int(callback.data.split(":")[1])
    p = get_player(player_id)
    if not p:
        await callback.message.edit_text("❌ Участник не найден.", reply_markup=main_menu())
        return
    balance = p[2]
    bal_emoji = "✅" if balance >= 0 else "⚠️"
    text = (f"👤 <b>{p[1]}</b>\n\n"
            f"{bal_emoji} Баланс: {'+' if balance > 0 else ''}{balance:.2f} €")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить участника", callback_data=f"player_delete:{player_id}")],
        back_kb("players")
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("player_delete:"))
async def cb_player_delete(callback: CallbackQuery):
    player_id = int(callback.data.split(":")[1])
    p = get_player(player_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"player_delete_confirm:{player_id}"),
         InlineKeyboardButton(text="◀️ Отмена", callback_data=f"player_view:{player_id}")]
    ])
    await callback.message.edit_text(f"Удалить участника <b>{p[1]}</b>?", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("player_delete_confirm:"))
async def cb_player_delete_confirm(callback: CallbackQuery):
    player_id = int(callback.data.split(":")[1])
    delete_player(player_id)
    await show_players(callback.message, edit=True)

# ==================== TRAININGS ====================
async def show_trainings(msg, edit=False):
    trainings = get_trainings()
    if not trainings:
        text = "📅 Тренировок пока нет."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать", callback_data="training_add")],
            back_kb()
        ])
    else:
        text = "📅 <b>Тренировки:</b>\n"
        buttons = []
        for t in trainings:
            tp = get_training_players(t[0])
            paid_count = sum(1 for p in tp if p[5] > 0)
            buttons.append([InlineKeyboardButton(
                text=f"{t[1]}  👥{len(tp)}  💰{paid_count}",
                callback_data=f"training_view:{t[0]}"
            )])
        buttons.append([InlineKeyboardButton(text="➕ Создать", callback_data="training_add")])
        buttons.append(back_kb())
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    if edit:
        await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await msg.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "trainings")
async def cb_trainings(callback: CallbackQuery):
    await show_trainings(callback.message, edit=True)

@dp.callback_query(F.data == "training_add")
async def cb_training_add(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddTraining.waiting_date)
    today = datetime.now().strftime("%d.%m.%Y")
    await callback.message.edit_text(
        f"Введи дату тренировки (например: {today}):",
        reply_markup=back_button("trainings"))

@dp.message(AddTraining.waiting_date)
async def process_training_date(message: Message, state: FSMContext):
    await state.clear()
    training_id = add_training(message.text.strip())
    await message.answer(f"✅ Тренировка {message.text.strip()} создана!", reply_markup=main_menu())

@dp.callback_query(F.data.startswith("training_view:"))
async def cb_training_view(callback: CallbackQuery):
    training_id = int(callback.data.split(":")[1])
    t = get_training(training_id)
    tp = get_training_players(training_id)

    total_paid = sum(p[5] for p in tp)
    expected = len(tp) * t[2]

    text = (f"📅 <b>Тренировка {t[1]}</b>\n"
            f"Цена: {t[2]:.0f} €  |  Участников: {len(tp)}\n"
            f"Собрано: {total_paid:.0f} € / {expected:.0f} €\n\n")

    if tp:
        for p in tp:
            status_emoji = {"registered": "⬜️", "paid": "✅", "cancelled": "❌", "no_show": "🚫"}.get(p[4], "⬜️")
            paid_text = f" {p[5]:.0f}€" if p[5] > 0 else ""
            note_text = f" ({p[6]})" if p[6] else ""
            text += f"{status_emoji} {p[7]}{paid_text}{note_text}\n"

    buttons = []
    buttons.append([InlineKeyboardButton(text="➕ Добавить участника", callback_data=f"training_add_player:{training_id}")])
    if tp:
        buttons.append([InlineKeyboardButton(text="💰 Отметить оплату", callback_data=f"training_pay:{training_id}")])
        buttons.append([InlineKeyboardButton(text="❌ Отмена участника", callback_data=f"training_cancel:{training_id}")])
    buttons.append([InlineKeyboardButton(text="🗑 Удалить тренировку", callback_data=f"training_delete:{training_id}")])
    buttons.append(back_kb("trainings"))

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("training_add_player:"))
async def cb_training_add_player(callback: CallbackQuery):
    training_id = int(callback.data.split(":")[1])
    t = get_training(training_id)
    players = get_players()
    tp = get_training_players(training_id)
    already_ids = {p[2] for p in tp}

    available = [p for p in players if p[0] not in already_ids]
    if not available:
        await callback.answer("Все участники уже добавлены!", show_alert=True)
        return

    buttons = []
    for p in available:
        buttons.append([InlineKeyboardButton(
            text=p[1], callback_data=f"training_add_player_confirm:{training_id}:{p[0]}"
        )])
    buttons.append(back_kb(f"training_view:{training_id}"))
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(f"Выбери участника для тренировки {t[1]}:", reply_markup=kb)

@dp.callback_query(F.data.startswith("training_add_player_confirm:"))
async def cb_training_add_player_confirm(callback: CallbackQuery):
    _, training_id, player_id = callback.data.split(":")
    training_id, player_id = int(training_id), int(player_id)
    add_player_to_training(training_id, player_id)
    # Reload training view
    t = get_training(training_id)
    tp = get_training_players(training_id)
    p = get_player(player_id)
    await callback.answer(f"✅ {p[1]} добавлен!")
    # Refresh
    fake = callback
    fake.data = f"training_view:{training_id}"
    await cb_training_view(callback)

@dp.callback_query(F.data.startswith("training_pay:"))
async def cb_training_pay(callback: CallbackQuery):
    training_id = int(callback.data.split(":")[1])
    tp = get_training_players(training_id)
    unpaid = [p for p in tp if p[5] == 0 and p[4] != "cancelled"]

    if not unpaid:
        await callback.answer("Все уже оплатили!", show_alert=True)
        return

    buttons = []
    for p in unpaid:
        buttons.append([InlineKeyboardButton(
            text=p[7], callback_data=f"training_pay_confirm:{training_id}:{p[2]}"
        )])
    buttons.append(back_kb(f"training_view:{training_id}"))
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("Кто оплатил?", reply_markup=kb)

@dp.callback_query(F.data.startswith("training_pay_confirm:"))
async def cb_training_pay_confirm(callback: CallbackQuery, state: FSMContext):
    _, training_id, player_id = callback.data.split(":")
    training_id, player_id = int(training_id), int(player_id)
    t = get_training(training_id)
    await state.update_data(training_id=training_id, player_id=player_id)
    await state.set_state(EditPaid.waiting_amount)
    p = get_player(player_id)
    await callback.message.edit_text(
        f"Сколько заплатил {p[1]}? (стандартно {t[2]:.0f} €)",
        reply_markup=back_button(f"training_pay:{training_id}"))

@dp.message(EditPaid.waiting_amount)
async def process_paid_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("❌ Введи число")
        return
    data = await state.get_data()
    set_player_paid(data["training_id"], data["player_id"], amount)
    set_player_status(data["training_id"], data["player_id"], "paid")
    await state.clear()
    p = get_player(data["player_id"])
    await message.answer(f"✅ {p[1]} оплатил {amount:.0f} €", reply_markup=main_menu())

@dp.callback_query(F.data.startswith("training_cancel:"))
async def cb_training_cancel(callback: CallbackQuery):
    training_id = int(callback.data.split(":")[1])
    tp = get_training_players(training_id)
    active = [p for p in tp if p[4] != "cancelled"]

    buttons = []
    for p in active:
        buttons.append([InlineKeyboardButton(
            text=p[7], callback_data=f"training_cancel_confirm:{training_id}:{p[2]}"
        )])
    buttons.append(back_kb(f"training_view:{training_id}"))
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("Кто отменяет?", reply_markup=kb)

@dp.callback_query(F.data.startswith("training_cancel_confirm:"))
async def cb_training_cancel_confirm(callback: CallbackQuery, state: FSMContext):
    _, training_id, player_id = callback.data.split(":")
    training_id, player_id = int(training_id), int(player_id)
    p = get_player(player_id)
    await state.update_data(training_id=training_id, player_id=player_id)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Возврат денег", callback_data=f"training_cancel_refund:{training_id}:{player_id}"),
         InlineKeyboardButton(text="➡️ Перенос", callback_data=f"training_cancel_transfer:{training_id}:{player_id}")],
        back_kb(f"training_cancel:{training_id}")
    ])
    await callback.message.edit_text(
        f"Отмена для <b>{p[1]}</b>. Что делаем с оплатой?",
        reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("training_cancel_refund:"))
async def cb_cancel_refund(callback: CallbackQuery):
    _, training_id, player_id = callback.data.split(":")
    training_id, player_id = int(training_id), int(player_id)
    tp_row = next((p for p in get_training_players(training_id) if p[2] == player_id), None)
    if tp_row and tp_row[5] > 0:
        set_player_paid(training_id, player_id, 0)
    set_player_status(training_id, player_id, "cancelled", "возврат")
    p = get_player(player_id)
    await callback.answer(f"✅ Отмена + возврат для {p[1]}")
    callback.data = f"training_view:{training_id}"
    await cb_training_view(callback)

@dp.callback_query(F.data.startswith("training_cancel_transfer:"))
async def cb_cancel_transfer(callback: CallbackQuery):
    _, training_id, player_id = callback.data.split(":")
    training_id, player_id = int(training_id), int(player_id)
    set_player_status(training_id, player_id, "cancelled", "перенос")
    p = get_player(player_id)
    await callback.answer(f"✅ Отмена + перенос для {p[1]}")
    callback.data = f"training_view:{training_id}"
    await cb_training_view(callback)

@dp.callback_query(F.data.startswith("training_delete:"))
async def cb_training_delete(callback: CallbackQuery):
    training_id = int(callback.data.split(":")[1])
    t = get_training(training_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"training_delete_confirm:{training_id}"),
         InlineKeyboardButton(text="◀️ Отмена", callback_data=f"training_view:{training_id}")]
    ])
    await callback.message.edit_text(f"Удалить тренировку <b>{t[1]}</b>?", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("training_delete_confirm:"))
async def cb_training_delete_confirm(callback: CallbackQuery):
    training_id = int(callback.data.split(":")[1])
    delete_training(training_id)
    await show_trainings(callback.message, edit=True)

# ==================== FINANCES ====================
@dp.callback_query(F.data == "finances")
async def cb_finances(callback: CallbackQuery):
    players = get_players()
    trainings = get_trainings()

    total_collected = 0
    for t in trainings:
        tp = get_training_players(t[0])
        total_collected += sum(p[5] for p in tp)

    debtors = [p for p in players if p[2] < 0]
    creditors = [p for p in players if p[2] > 0]

    text = f"💰 <b>Финансы</b>\n\nВсего собрано: <b>{total_collected:.0f} €</b>\n\n"

    if debtors:
        text += "⚠️ <b>Должники:</b>\n"
        for p in debtors:
            text += f"  {p[1]}: {p[2]:.0f} €\n"
        text += "\n"

    if creditors:
        text += "✅ <b>Переплата:</b>\n"
        for p in creditors:
            text += f"  {p[1]}: +{p[2]:.0f} €\n"

    if not debtors and not creditors:
        text += "✅ Все расчёты в порядке"

    await callback.message.edit_text(text, reply_markup=back_button(), parse_mode="HTML")

# ==================== SHUTTLECOCKS ====================
async def show_shuttlecocks(msg, edit=False):
    balance = get_shuttlecock_balance()
    warning = "\n\n⚠️ <b>Пора покупать!</b>" if balance <= 5 else ""
    text = f"🏸 <b>Воланы</b>\n\nОстаток: <b>{balance} коробок</b>{warning}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить", callback_data="shuttle_add"),
         InlineKeyboardButton(text="➖ Списать", callback_data="shuttle_remove")],
        back_kb()
    ])
    if edit:
        await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await msg.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "shuttlecocks")
async def cb_shuttlecocks(callback: CallbackQuery):
    await show_shuttlecocks(callback.message, edit=True)

@dp.callback_query(F.data == "shuttle_add")
async def cb_shuttle_add(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Shuttlecocks.waiting_add)
    await callback.message.edit_text("Сколько коробок добавить?", reply_markup=back_button("shuttlecocks"))

@dp.message(Shuttlecocks.waiting_add)
async def process_shuttle_add(message: Message, state: FSMContext):
    try:
        amount = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введи целое число")
        return
    change_shuttlecocks(amount, "покупка")
    balance = get_shuttlecock_balance()
    await state.clear()
    await message.answer(f"✅ Добавлено {amount} коробок. Остаток: {balance}", reply_markup=main_menu())

@dp.callback_query(F.data == "shuttle_remove")
async def cb_shuttle_remove(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Shuttlecocks.waiting_remove)
    await callback.message.edit_text("Сколько коробок списать?", reply_markup=back_button("shuttlecocks"))

@dp.message(Shuttlecocks.waiting_remove)
async def process_shuttle_remove(message: Message, state: FSMContext):
    try:
        amount = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введи целое число")
        return
    change_shuttlecocks(-amount, "использование")
    balance = get_shuttlecock_balance()
    await state.clear()
    warning = "\n⚠️ Пора покупать!" if balance <= 5 else ""
    await message.answer(f"✅ Списано {amount} коробок. Остаток: {balance}{warning}", reply_markup=main_menu())

# --- Setup commands ---
async def set_commands():
    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="menu", description="Главное меню"),
    ])

async def main():
    init_db()
    await set_commands()
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
