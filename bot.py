import logging
import sqlite3
import os
import json
import time
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

DB = "/data/badminton.db"
PRICE = 16
_last_clear_time = 0

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
        FOREIGN KEY (training_id) REFERENCES trainings(id),
        FOREIGN KEY (player_id) REFERENCES players(id)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        payment_type TEXT DEFAULT 'cash',
        note TEXT,
        created_at TEXT NOT NULL,
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

def get_db():
    return sqlite3.connect(DB)

def get_players():
    with get_db() as conn:
        return conn.execute("SELECT * FROM players ORDER BY name").fetchall()

def get_player(player_id):
    with get_db() as conn:
        return conn.execute("SELECT * FROM players WHERE id=?", (player_id,)).fetchone()

def add_player(name):
    with get_db() as conn:
        conn.execute("INSERT INTO players (name, balance, created_at) VALUES (?, 0, ?)",
                     (name, datetime.now().isoformat()))
        conn.commit()

def delete_player(player_id):
    with get_db() as conn:
        conn.execute("DELETE FROM players WHERE id=?", (player_id,))
        conn.execute("DELETE FROM training_players WHERE player_id=?", (player_id,))
        conn.execute("DELETE FROM payments WHERE player_id=?", (player_id,))
        conn.commit()

def add_payment(player_id, amount, payment_type, note=None):
    with get_db() as conn:
        conn.execute("INSERT INTO payments (player_id, amount, payment_type, note, created_at) VALUES (?, ?, ?, ?, ?)",
                     (player_id, amount, payment_type, note, datetime.now().isoformat()))
        conn.execute("UPDATE players SET balance = balance + ? WHERE id=?", (amount, player_id))
        conn.commit()

def get_player_payments(player_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM payments WHERE player_id=? ORDER BY created_at DESC LIMIT 10",
            (player_id,)).fetchall()

def get_player_stats(player_id):
    with get_db() as conn:
        total_trainings = conn.execute(
            "SELECT COUNT(*) FROM training_players WHERE player_id=? AND status='registered'",
            (player_id,)).fetchone()[0]
        total_cash = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM payments WHERE player_id=? AND payment_type='cash' AND amount > 0",
            (player_id,)).fetchone()[0]
        total_card = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM payments WHERE player_id=? AND payment_type='card' AND amount > 0",
            (player_id,)).fetchone()[0]
        return total_trainings, total_cash, total_card

def get_trainings():
    with get_db() as conn:
        return conn.execute("SELECT * FROM trainings ORDER BY date DESC").fetchall()

def get_training(training_id):
    with get_db() as conn:
        return conn.execute("SELECT * FROM trainings WHERE id=?", (training_id,)).fetchone()

def add_training(date, price=PRICE):
    with get_db() as conn:
        conn.execute("INSERT INTO trainings (date, price, created_at) VALUES (?, ?, ?)",
                     (date, price, datetime.now().isoformat()))
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

def duplicate_training(training_id, new_date):
    t = get_training(training_id)
    new_id = add_training(new_date, t[2])
    tp = get_training_players(training_id)
    for p in tp:
        add_player_to_training(new_id, p[0])
    return new_id

def delete_training(training_id):
    with get_db() as conn:
        conn.execute("DELETE FROM trainings WHERE id=?", (training_id,))
        conn.execute("DELETE FROM training_players WHERE training_id=?", (training_id,))
        conn.commit()

def get_training_players(training_id):
    with get_db() as conn:
        return conn.execute("""
            SELECT p.id, p.name, p.balance, tp.status
            FROM training_players tp
            JOIN players p ON tp.player_id = p.id
            WHERE tp.training_id = ?
            ORDER BY p.name
        """, (training_id,)).fetchall()

def add_player_to_training(training_id, player_id):
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM training_players WHERE training_id=? AND player_id=?",
            (training_id, player_id)).fetchone()
        if not existing:
            conn.execute("INSERT INTO training_players (training_id, player_id, status) VALUES (?, ?, 'registered')",
                         (training_id, player_id))
            conn.commit()
            t = get_training(training_id)
            add_payment(player_id, -t[2], "deduct", "тренировка " + t[1])
            return True
        return False

def remove_player_from_training(training_id, player_id):
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM training_players WHERE training_id=? AND player_id=?",
            (training_id, player_id)).fetchone()
        if existing:
            conn.execute("DELETE FROM training_players WHERE training_id=? AND player_id=?",
                         (training_id, player_id))
            conn.commit()
            t = get_training(training_id)
            add_payment(player_id, t[2], "refund", "возврат тренировка " + t[1])
            return True
        return False

def get_month_finances():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT strftime('%m.%Y', created_at) as month, payment_type, SUM(amount)
            FROM payments WHERE amount > 0
            GROUP BY month, payment_type
            ORDER BY month DESC
        """).fetchall()
    months = {}
    for month, ptype, total in rows:
        if month not in months:
            months[month] = {"cash": 0, "card": 0}
        months[month][ptype] = months[month].get(ptype, 0) + total
    return months

def get_shuttlecock_balance():
    with get_db() as conn:
        return conn.execute("SELECT COALESCE(SUM(change),0) FROM shuttlecocks").fetchone()[0]

def change_shuttlecocks(amount, reason):
    with get_db() as conn:
        conn.execute("INSERT INTO shuttlecocks (change, reason, created_at) VALUES (?, ?, ?)",
                     (amount, reason, datetime.now().isoformat()))
        conn.commit()

def is_authorized(user_id):
    return user_id in USER_IDS

# --- States ---
class AddPlayer(StatesGroup):
    waiting_name = State()

class AddTraining(StatesGroup):
    waiting_date = State()
    waiting_price = State()

class DuplicateTraining(StatesGroup):
    waiting_date = State()

class AddPayment(StatesGroup):
    waiting_amount = State()

class ShuttlecocksState(StatesGroup):
    waiting_add = State()
    waiting_remove = State()

# --- Keyboards ---
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Тренировки", callback_data="trainings"),
         InlineKeyboardButton(text="👥 Участники", callback_data="players")],
        [InlineKeyboardButton(text="💰 Финансы", callback_data="finances"),
         InlineKeyboardButton(text="🏸 Воланы", callback_data="shuttlecocks")],
        [InlineKeyboardButton(text="🧹 Очистить чат", callback_data="clear_chat")],
    ])

def back_kb(to="main"):
    return [InlineKeyboardButton(text="◀️ Назад", callback_data="back:" + to)]

def back_button(to="main"):
    return InlineKeyboardMarkup(inline_keyboard=[back_kb(to)])

# --- Handlers ---
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    global _last_clear_time
    if not is_authorized(message.from_user.id):
        await message.answer("⛔️ Нет доступа.")
        return
    try:
        await message.delete()
    except:
        pass
    # Only show menu if it's a genuine first start (not after clear)
    if time.time() - _last_clear_time > 300:
        await state.clear()
        _last_clear_time = time.time()
        await bot.send_message(message.chat.id, "Главное меню:", reply_markup=main_menu())

@dp.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()

@dp.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    if not is_authorized(message.from_user.id):
        return
    await state.clear()
    await message.answer("Главное меню:", reply_markup=main_menu())

@dp.callback_query(F.data == "clear_chat")
async def cb_clear_chat(callback: CallbackQuery, state: FSMContext):
    global _last_clear_time
    _last_clear_time = time.time()
    try:
        await callback.answer()
    except:
        pass
    await state.clear()
    chat_id = callback.message.chat.id
    current_id = callback.message.message_id
    import asyncio
    tasks = [bot.delete_message(chat_id, msg_id) 
             for msg_id in range(current_id, max(current_id - 15, 0), -1)]
    await asyncio.gather(*tasks, return_exceptions=True)
    _last_clear_time = time.time()
    await bot.send_message(chat_id, "Главное меню:", reply_markup=main_menu())

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
    elif dest.startswith("training_view:"):
        tid = int(dest.split(":")[1])
        await show_training_view(callback.message, tid, edit=True)
    elif dest.startswith("player_view:"):
        pid = int(dest.split(":")[1])
        await show_player_view(callback.message, pid, edit=True)

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
            bal = p[2]
            emoji = "✅" if bal >= 0 else "⚠️"
            bal_str = ("+" if bal > 0 else "") + str(int(bal)) + "€"
            buttons.append([InlineKeyboardButton(
                text=emoji + " " + p[1] + "  " + bal_str,
                callback_data="player_view:" + str(p[0]))])
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
        await message.answer("✅ " + name + " добавлен!", reply_markup=main_menu())
    except:
        await message.answer("❌ Участник с таким именем уже есть.", reply_markup=main_menu())

async def show_player_view(msg, player_id, edit=False):
    p = get_player(player_id)
    if not p:
        await msg.edit_text("❌ Не найден.", reply_markup=main_menu())
        return
    total_t, total_cash, total_card = get_player_stats(player_id)
    bal = p[2]
    bal_emoji = "✅" if bal >= 0 else "⚠️"
    bal_str = ("+" if bal > 0 else "") + str(round(bal, 2)) + " €"

    text = ("<b>" + p[1] + "</b>\n\n" +
            bal_emoji + " Баланс: <b>" + bal_str + "</b>\n\n" +
            "📊 <b>Статистика:</b>\n" +
            "Записан на тренировок: " + str(total_t) + "\n" +
            "💵 Нал: " + str(int(total_cash)) + " €\n" +
            "💳 Безнал: " + str(int(total_card)) + " €\n" +
            "Итого: " + str(int(total_cash + total_card)) + " €")

    payments = get_player_payments(player_id)
    if payments:
        text += "\n\n📋 <b>Последние операции:</b>\n"
        for pay in payments[:5]:
            amt = pay[2]
            ptype = pay[3]
            note = pay[4] or ""
            date = pay[5][:10] if pay[5] else ""
            if ptype == "deduct":
                text += "➖ " + str(abs(int(amt))) + "€ " + note + " " + date + "\n"
            elif ptype == "refund":
                text += "↩️ +" + str(int(amt)) + "€ " + note + " " + date + "\n"
            else:
                emoji = "💵" if ptype == "cash" else "💳"
                text += "➕ " + str(int(amt)) + "€ " + emoji + " " + date + "\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💵 Пополнить нал", callback_data="pay_add:" + str(player_id) + ":cash"),
         InlineKeyboardButton(text="💳 Пополнить безнал", callback_data="pay_add:" + str(player_id) + ":card")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data="player_delete:" + str(player_id))],
        back_kb("players")
    ])
    if edit:
        await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await msg.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("player_view:"))
async def cb_player_view(callback: CallbackQuery):
    pid = int(callback.data.split(":")[1])
    await show_player_view(callback.message, pid, edit=True)

@dp.callback_query(F.data.startswith("pay_add:"))
async def cb_pay_add(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    pid = int(parts[1])
    ptype = parts[2]
    p = get_player(pid)
    await state.update_data(player_id=pid, payment_type=ptype)
    await state.set_state(AddPayment.waiting_amount)
    type_text = "💵 наличными" if ptype == "cash" else "💳 безналом"
    await callback.message.edit_text(
        "Сколько пополнить для <b>" + p[1] + "</b> " + type_text + "?",
        reply_markup=back_button("player_view:" + str(pid)), parse_mode="HTML")

@dp.message(AddPayment.waiting_amount)
async def process_payment(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введи сумму, например: 64")
        return
    data = await state.get_data()
    pid = data["player_id"]
    ptype = data["payment_type"]
    add_payment(pid, amount, ptype)
    await state.clear()
    p = get_player(pid)
    emoji = "💵" if ptype == "cash" else "💳"
    await message.answer(
        "✅ Пополнено " + str(int(amount)) + " € " + emoji + " для " + p[1] + "\n" +
        "Баланс: " + str(round(p[2], 2)) + " €",
        reply_markup=main_menu())

@dp.callback_query(F.data.startswith("player_delete:"))
async def cb_player_delete(callback: CallbackQuery):
    pid = int(callback.data.split(":")[1])
    p = get_player(pid)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data="player_delete_confirm:" + str(pid)),
         InlineKeyboardButton(text="◀️ Отмена", callback_data="player_view:" + str(pid))]
    ])
    await callback.message.edit_text("Удалить <b>" + p[1] + "</b>?", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("player_delete_confirm:"))
async def cb_player_delete_confirm(callback: CallbackQuery):
    pid = int(callback.data.split(":")[1])
    delete_player(pid)
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
            buttons.append([InlineKeyboardButton(
                text=t[1] + "  👥" + str(len(tp)) + "  " + str(int(t[2])) + "€",
                callback_data="training_view:" + str(t[0]))])
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
        "Введи дату тренировки (например: " + today + "):",
        reply_markup=back_button("trainings"))

@dp.message(AddTraining.waiting_date)
async def process_training_date(message: Message, state: FSMContext):
    date = message.text.strip()
    await state.update_data(date=date)
    await state.set_state(AddTraining.waiting_price)
    await message.answer("Тренировка " + date + ". Сколько стоит? (стандартно 16 €):", reply_markup=back_button("trainings"))

@dp.message(AddTraining.waiting_price)
async def process_training_price(message: Message, state: FSMContext):
    try:
        price = float(message.text.replace(",", "."))
        if price <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введи сумму, например: 16")
        return
    data = await state.get_data()
    await state.clear()
    add_training(data["date"], price)
    await message.answer("✅ Тренировка " + data["date"] + " создана! Цена: " + str(int(price)) + " €", reply_markup=main_menu())

async def show_training_view(msg, training_id, edit=False):
    t = get_training(training_id)
    tp = get_training_players(training_id)
    collected = len(tp) * t[2]

    text = ("📅 <b>Тренировка " + t[1] + "</b>\n" +
            "Цена: " + str(int(t[2])) + " €  |  Участников: " + str(len(tp)) + "\n" +
            "Собрано: " + str(int(collected)) + " €\n\n")

    for p in tp:
        bal = p[2]
        bal_str = ("+" if bal > 0 else "") + str(int(bal)) + "€"
        bal_emoji = "✅" if bal >= 0 else "⚠️"
        text += bal_emoji + " " + p[1] + " (" + bal_str + ")\n"

    buttons = [
        [InlineKeyboardButton(text="➕ Записать участника", callback_data="training_add_player:" + str(training_id))],
        [InlineKeyboardButton(text="➖ Убрать участника", callback_data="training_remove_player:" + str(training_id))],
        [InlineKeyboardButton(text="📋 Дублировать", callback_data="training_duplicate:" + str(training_id)),
         InlineKeyboardButton(text="🗑 Удалить", callback_data="training_delete:" + str(training_id))],
        back_kb("trainings")
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    if edit:
        await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await msg.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("training_view:"))
async def cb_training_view(callback: CallbackQuery):
    tid = int(callback.data.split(":")[1])
    await show_training_view(callback.message, tid, edit=True)

@dp.callback_query(F.data.startswith("training_duplicate:"))
async def cb_training_duplicate(callback: CallbackQuery, state: FSMContext):
    tid = int(callback.data.split(":")[1])
    t = get_training(tid)
    await state.update_data(training_id=tid)
    await state.set_state(DuplicateTraining.waiting_date)
    await callback.message.edit_text(
        "Дублируем тренировку " + t[1] + ". Введи дату новой тренировки:",
        reply_markup=back_button("training_view:" + str(tid)))

@dp.message(DuplicateTraining.waiting_date)
async def process_duplicate_date(message: Message, state: FSMContext):
    data = await state.get_data()
    duplicate_training(data["training_id"], message.text.strip())
    await state.clear()
    await message.answer(
        "✅ Тренировка скопирована на " + message.text.strip() + "! Участники записаны, деньги списаны.",
        reply_markup=main_menu())

@dp.callback_query(F.data.startswith("training_add_player:"))
async def cb_training_add_player(callback: CallbackQuery):
    tid = int(callback.data.split(":")[1])
    t = get_training(tid)
    players = get_players()
    tp = get_training_players(tid)
    already_ids = {p[0] for p in tp}
    available = [p for p in players if p[0] not in already_ids]
    if not available:
        await callback.answer("Все участники уже записаны!", show_alert=True)
        return
    buttons = []
    for p in available:
        bal = p[2]
        bal_str = ("+" if bal > 0 else "") + str(int(bal)) + "€"
        buttons.append([InlineKeyboardButton(
            text=p[1] + " (" + bal_str + ")",
            callback_data="training_add_confirm:" + str(tid) + ":" + str(p[0]))])
    buttons.append(back_kb("training_view:" + str(tid)))
    await callback.message.edit_text(
        "Выбери участника для " + t[1] + " (-" + str(int(t[2])) + "€ с баланса):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("training_add_confirm:"))
async def cb_training_add_confirm(callback: CallbackQuery):
    _, tid, pid = callback.data.split(":")
    tid, pid = int(tid), int(pid)
    add_player_to_training(tid, pid)
    p = get_player(pid)
    t = get_training(tid)
    new_bal = p[2] - t[2]
    await callback.answer("✅ " + p[1] + " записан. Баланс: " + str(round(new_bal, 0)) + "€")
    await show_training_view(callback.message, tid, edit=True)

@dp.callback_query(F.data.startswith("training_remove_player:"))
async def cb_training_remove_player(callback: CallbackQuery):
    tid = int(callback.data.split(":")[1])
    tp = get_training_players(tid)
    if not tp:
        await callback.answer("Нет участников!", show_alert=True)
        return
    buttons = [[InlineKeyboardButton(
        text=p[1], callback_data="training_remove_confirm:" + str(tid) + ":" + str(p[0]))]
        for p in tp]
    buttons.append(back_kb("training_view:" + str(tid)))
    await callback.message.edit_text(
        "Кого убрать? (16€ вернётся на баланс)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("training_remove_confirm:"))
async def cb_training_remove_confirm(callback: CallbackQuery):
    _, tid, pid = callback.data.split(":")
    tid, pid = int(tid), int(pid)
    remove_player_from_training(tid, pid)
    p = get_player(pid)
    t = get_training(tid)
    new_bal = p[2] + t[2]
    await callback.answer("↩️ " + p[1] + " убран. Возврат " + str(int(t[2])) + "€. Баланс: " + str(round(new_bal, 0)) + "€")
    await show_training_view(callback.message, tid, edit=True)

@dp.callback_query(F.data.startswith("training_delete:"))
async def cb_training_delete(callback: CallbackQuery):
    tid = int(callback.data.split(":")[1])
    t = get_training(tid)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data="training_delete_confirm:" + str(tid)),
         InlineKeyboardButton(text="◀️ Отмена", callback_data="training_view:" + str(tid))]
    ])
    await callback.message.edit_text("Удалить тренировку <b>" + t[1] + "</b>?", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("training_delete_confirm:"))
async def cb_training_delete_confirm(callback: CallbackQuery):
    tid = int(callback.data.split(":")[1])
    delete_training(tid)
    await show_trainings(callback.message, edit=True)

# ==================== FINANCES ====================
@dp.callback_query(F.data == "finances")
async def cb_finances(callback: CallbackQuery):
    months = get_month_finances()
    players = get_players()
    debtors = [p for p in players if p[2] < 0]
    creditors = [p for p in players if p[2] > 0]

    text = "💰 <b>Финансы по месяцам:</b>\n\n"
    total_all = 0
    for month, data in months.items():
        cash = data.get("cash", 0)
        card = data.get("card", 0)
        total = cash + card
        total_all += total
        text += "<b>" + month + ":</b>  " + str(int(total)) + " €\n"
        text += "  💵 Нал: " + str(int(cash)) + " €  💳 Безнал: " + str(int(card)) + " €\n\n"

    text += "<b>Итого: " + str(int(total_all)) + " €</b>\n\n"

    if debtors:
        text += "⚠️ <b>Должники:</b>\n"
        for p in debtors:
            text += "  " + p[1] + ": " + str(int(p[2])) + " €\n"
        text += "\n"
    if creditors:
        text += "✅ <b>Переплата:</b>\n"
        for p in creditors:
            text += "  " + p[1] + ": +" + str(int(p[2])) + " €\n"
    if not debtors and not creditors:
        text += "✅ Все расчёты в порядке"

    await callback.message.edit_text(text, reply_markup=back_button(), parse_mode="HTML")

# ==================== SHUTTLECOCKS ====================
async def show_shuttlecocks(msg, edit=False):
    balance = get_shuttlecock_balance()
    warning = "\n\n⚠️ <b>Пора покупать!</b>" if balance <= 5 else ""
    text = "🏸 <b>Воланы</b>\n\nОстаток: <b>" + str(balance) + " коробок</b>" + warning
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
    await state.set_state(ShuttlecocksState.waiting_add)
    await callback.message.edit_text("Сколько коробок добавить?", reply_markup=back_button("shuttlecocks"))

@dp.message(ShuttlecocksState.waiting_add)
async def process_shuttle_add(message: Message, state: FSMContext):
    try:
        amount = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введи целое число")
        return
    change_shuttlecocks(amount, "покупка")
    balance = get_shuttlecock_balance()
    await state.clear()
    await message.answer("✅ Добавлено " + str(amount) + " коробок. Остаток: " + str(balance), reply_markup=main_menu())

@dp.callback_query(F.data == "shuttle_remove")
async def cb_shuttle_remove(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ShuttlecocksState.waiting_remove)
    await callback.message.edit_text("Сколько коробок списать?", reply_markup=back_button("shuttlecocks"))

@dp.message(ShuttlecocksState.waiting_remove)
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
    await message.answer("✅ Списано " + str(amount) + " коробок. Остаток: " + str(balance) + warning, reply_markup=main_menu())

# ==================== AI ====================
async def parse_ai_command(text: str, trainings: list, players: list):
    import aiohttp
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    if not ANTHROPIC_API_KEY:
        return None

    training_list = "\n".join([t[1] + " (id=" + str(t[0]) + ", цена=" + str(int(t[2])) + "€)" for t in trainings])
    player_list = "\n".join([p[1] + " (id=" + str(p[0]) + ")" for p in players])

    system = """Ты помощник для управления тренировками по бадминтону.
Пользователь может давать разные команды на русском языке.

Список тренировок:
""" + training_list + """

Список участников (уже существующих):
""" + player_list + """

Верни ТОЛЬКО валидный JSON без пояснений и без markdown.

Возможные команды:

1. Добавить нового участника (без привязки к тренировке):
{"command": "add_player", "name": "<полное имя>", "payment": <сумма или null>, "payment_type": "cash" или "card" или null}

2. Записать участников на конкретную тренировку:
{"command": "register", "training_id": <id>, "actions": [{"player_id": <id или null>, "name": "<имя если новый>", "payment": <сумма или null>, "payment_type": "cash" или "card" или null}]}

3. Пополнить баланс существующего участника:
{"command": "payment", "player_id": <id>, "amount": <сумма>, "payment_type": "cash" или "card"}

Правила:
- Если говорят "добавь участника" без упоминания тренировки — это команда add_player
- Если говорят "записались на тренировку" — это команда register
- "нал" / "наличными" / "наличные" = cash, "безнал" / "картой" / "переводом" = card
- Если не понял — {"command": "unknown"}"""

    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 500,
        "system": system,
        "messages": [{"role": "user", "content": text}]
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json=payload
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            raw = data["content"][0]["text"].strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            return json.loads(raw)


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_free_text(message: Message, state: FSMContext):
    if not is_authorized(message.from_user.id):
        return
    current_state = await state.get_state()
    if current_state is not None:
        return

    processing = await message.answer("⏳ Обрабатываю...")
    trainings = get_trainings()
    players = get_players()

    try:
        result = await parse_ai_command(message.text, trainings, players)
    except Exception:
        result = None

    await processing.delete()

    command = result.get("command", "unknown") if result else "unknown"

    if command == "unknown":
        await message.answer(
            "❌ Не смог распознать команду. Примеры:\n"
            "<i>Добавь участника Иван Петров, оплатил налом 32</i>\n"
            "<i>На тренировку 1.07 записались Иванов (безнал 32), Петров</i>",
            parse_mode="HTML", reply_markup=main_menu())
        return

    elif command == "add_player":
        name = result.get("name", "").strip()
        if not name:
            await message.answer("❌ Не понял имя.", reply_markup=main_menu())
            return
        try:
            add_player(name)
            lines = ["✅ Участник <b>" + name + "</b> добавлен!"]
            payment = result.get("payment")
            ptype = result.get("payment_type")
            if payment and ptype:
                p = next((x for x in get_players() if x[1] == name), None)
                if p:
                    add_payment(p[0], payment, ptype)
                    emoji = "💵" if ptype == "cash" else "💳"
                    lines.append(emoji + " Оплата " + str(int(payment)) + "€ зачислена. Баланс: " + str(int(payment)) + "€")
            await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=main_menu())
        except:
            await message.answer("❌ Участник с таким именем уже есть.", reply_markup=main_menu())

    elif command == "payment":
        pid = result.get("player_id")
        amount = result.get("amount")
        ptype = result.get("payment_type", "cash")
        p = get_player(pid)
        if not p or not amount:
            await message.answer("❌ Не смог определить участника или сумму.", reply_markup=main_menu())
            return
        add_payment(pid, amount, ptype)
        emoji = "💵" if ptype == "cash" else "💳"
        await message.answer("✅ " + p[1] + " — пополнено " + str(int(amount)) + "€ " + emoji, reply_markup=main_menu())

    elif command == "register":
        training_id = result.get("training_id")
        t = get_training(training_id)
        if not t:
            await message.answer("❌ Тренировка не найдена.", reply_markup=main_menu())
            return

        actions = result.get("actions", [])
        lines = ["📅 <b>Тренировка " + t[1] + "</b>\n"]

        for action in actions:
            pid = action.get("player_id")
            name = action.get("name", "")

            if not pid and name:
                try:
                    add_player(name)
                    p = next((x for x in get_players() if x[1] == name), None)
                    pid = p[0] if p else None
                    lines.append("➕ Новый участник <b>" + name + "</b> добавлен")
                except:
                    p = next((x for x in get_players() if x[1] == name), None)
                    pid = p[0] if p else None

            if not pid:
                continue

            p = get_player(pid)
            added = add_player_to_training(training_id, pid)
            if added:
                lines.append("✅ " + p[1] + " записан (-" + str(int(t[2])) + "€)")
            else:
                lines.append("ℹ️ " + p[1] + " уже был записан")

            payment = action.get("payment")
            ptype = action.get("payment_type")
            if payment and ptype:
                add_payment(pid, payment, ptype)
                emoji = "💵" if ptype == "cash" else "💳"
                lines.append("   " + emoji + " Оплата " + str(int(payment)) + "€ зачислена")

        await message.answer("\n".join(lines), reply_markup=main_menu(), parse_mode="HTML")


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
