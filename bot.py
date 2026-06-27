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
        payment_type TEXT DEFAULT 'cash',
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
    # Migrate old DB
    try:
        c.execute("ALTER TABLE training_players ADD COLUMN payment_type TEXT DEFAULT 'cash'")
        conn.commit()
    except:
        pass
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

def duplicate_training(training_id, new_date):
    t = get_training(training_id)
    new_id = add_training(new_date, t[2])
    tp = get_training_players(training_id)
    with db() as conn:
        for p in tp:
            conn.execute("INSERT INTO training_players (training_id, player_id) VALUES (?, ?)",
                         (new_id, p[2]))
        conn.commit()
    return new_id

def delete_training(training_id):
    with db() as conn:
        conn.execute("DELETE FROM trainings WHERE id=?", (training_id,))
        conn.execute("DELETE FROM training_players WHERE training_id=?", (training_id,))
        conn.commit()

def get_training_players(training_id):
    with db() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT tp.id, tp.training_id, tp.player_id, tp.status, tp.paid, tp.payment_type, tp.note, p.name
            FROM training_players tp
            JOIN players p ON tp.player_id = p.id
            WHERE tp.training_id = ?
            ORDER BY p.name
        """, (training_id,)).fetchall()
        return [tuple(r) for r in rows]

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

def set_player_paid(training_id, player_id, amount, payment_type="cash"):
    with db() as conn:
        old = conn.execute("SELECT paid FROM training_players WHERE training_id=? AND player_id=?",
                           (training_id, player_id)).fetchone()
        old_paid = old[0] if old else 0
        conn.execute("UPDATE training_players SET paid=?, payment_type=?, status='paid' WHERE training_id=? AND player_id=?",
                     (amount, payment_type, training_id, player_id))
        conn.commit()
        delta = amount - old_paid
        conn.execute("UPDATE players SET balance = balance + ? WHERE id=?", (delta, player_id))
        conn.commit()

def get_player_stats(player_id):
    with db() as conn:
        total_trainings = conn.execute(
            "SELECT COUNT(*) FROM training_players WHERE player_id=? AND status != 'cancelled'",
            (player_id,)).fetchone()[0]
        total_paid = conn.execute(
            "SELECT SUM(paid) FROM training_players WHERE player_id=?",
            (player_id,)).fetchone()[0] or 0
        total_cash = conn.execute(
            "SELECT SUM(paid) FROM training_players WHERE player_id=? AND payment_type='cash'",
            (player_id,)).fetchone()[0] or 0
        total_card = conn.execute(
            "SELECT SUM(paid) FROM training_players WHERE player_id=? AND payment_type='card'",
            (player_id,)).fetchone()[0] or 0
        return total_trainings, total_paid, total_cash, total_card

def get_finances_by_month():
    with db() as conn:
        rows = conn.execute("""
            SELECT t.date, tp.paid, tp.payment_type
            FROM training_players tp
            JOIN trainings t ON tp.training_id = t.id
            WHERE tp.paid > 0
        """).fetchall()

    months = {}
    for date, paid, ptype in rows:
        try:
            parts = date.split(".")
            if len(parts) == 3:
                month_key = f"{parts[1]}.{parts[2]}"
            else:
                month_key = date[:7]
        except:
            month_key = "other"

        if month_key not in months:
            months[month_key] = {"cash": 0, "card": 0}
        months[month_key][ptype] = months[month_key].get(ptype, 0) + paid

    return dict(sorted(months.items(), reverse=True))

def get_shuttlecock_balance():
    with db() as conn:
        row = conn.execute("SELECT SUM(change) FROM shuttlecocks").fetchone()
        return row[0] or 0

def change_shuttlecocks(amount, reason):
    with db() as conn:
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

class DuplicateTraining(StatesGroup):
    waiting_date = State()

class EditPaid(StatesGroup):
    waiting_amount = State()
    waiting_type = State()

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

def back_kb(to="main"):
    return [InlineKeyboardButton(text="◀️ Назад", callback_data=f"back:{to}")]

def back_button(to="main"):
    return InlineKeyboardMarkup(inline_keyboard=[back_kb(to)])

# --- Handlers ---
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
    elif dest.startswith("training_view:"):
        training_id = int(dest.split(":")[1])
        await show_training_view(callback.message, training_id, edit=True)

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
                callback_data=f"player_view:{p[0]}")])
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
        await message.answer(f"✅ Участник {name} добавлен!", reply_markup=main_menu())
    except:
        await message.answer(f"❌ Участник с таким именем уже есть.", reply_markup=main_menu())

@dp.callback_query(F.data.startswith("player_view:"))
async def cb_player_view(callback: CallbackQuery):
    player_id = int(callback.data.split(":")[1])
    p = get_player(player_id)
    if not p:
        await callback.message.edit_text("❌ Участник не найден.", reply_markup=main_menu())
        return
    total_trainings, total_paid, total_cash, total_card = get_player_stats(player_id)
    balance = p[2]
    bal_emoji = "✅" if balance >= 0 else "⚠️"
    text = (f"👤 <b>{p[1]}</b>\n\n"
            f"{bal_emoji} Баланс: {'+' if balance > 0 else ''}{balance:.2f} €\n\n"
            f"📊 <b>Статистика:</b>\n"
            f"Тренировок: {total_trainings}\n"
            f"Всего оплачено: {total_paid:.0f} €\n"
            f"  💵 Нал: {total_cash:.0f} €\n"
            f"  💳 Безнал: {total_card:.0f} €")
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
            paid_count = sum(1 for p in tp if p[4] > 0)
            buttons.append([InlineKeyboardButton(
                text=f"{t[1]}  👥{len(tp)}  💰{paid_count}/{len(tp)}",
                callback_data=f"training_view:{t[0]}")])
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
    add_training(message.text.strip())
    await message.answer(f"✅ Тренировка {message.text.strip()} создана!", reply_markup=main_menu())

async def show_training_view(msg, training_id, edit=False):
    t = get_training(training_id)
    tp = get_training_players(training_id)
    total_paid = sum(p[4] for p in tp)
    total_cash = sum(p[4] for p in tp if p[5] == "cash")
    total_card = sum(p[4] for p in tp if p[5] == "card")
    expected = len(tp) * t[2]

    text = (f"📅 <b>Тренировка {t[1]}</b>\n"
            f"Цена: {t[2]:.0f} €  |  Участников: {len(tp)}\n"
            f"Собрано: {total_paid:.0f} € / {expected:.0f} €\n"
            f"  💵 Нал: {total_cash:.0f} €  💳 Безнал: {total_card:.0f} €\n\n")

    status_map = {"registered": "⬜️", "paid": "✅", "cancelled": "❌", "no_show": "🚫"}
    type_map = {"cash": "💵", "card": "💳"}
    for p in tp:
        s = status_map.get(p[4], "⬜️")
        paid_text = f" {p[4]:.0f}€{type_map.get(p[5],'')}" if p[4] > 0 else ""
        note_text = f" ({p[6]})" if p[6] else ""
        text += f"{s} {p[7]}{paid_text}{note_text}\n"

    buttons = [
        [InlineKeyboardButton(text="➕ Добавить участника", callback_data=f"training_add_player:{training_id}")],
        [InlineKeyboardButton(text="💰 Отметить оплату", callback_data=f"training_pay:{training_id}"),
         InlineKeyboardButton(text="❌ Отменить участника", callback_data=f"training_cancel:{training_id}")],
        [InlineKeyboardButton(text="📋 Дублировать", callback_data=f"training_duplicate:{training_id}"),
         InlineKeyboardButton(text="🗑 Удалить", callback_data=f"training_delete:{training_id}")],
        back_kb("trainings")
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    if edit:
        await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await msg.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("training_view:"))
async def cb_training_view(callback: CallbackQuery):
    training_id = int(callback.data.split(":")[1])
    await show_training_view(callback.message, training_id, edit=True)

@dp.callback_query(F.data.startswith("training_duplicate:"))
async def cb_training_duplicate(callback: CallbackQuery, state: FSMContext):
    training_id = int(callback.data.split(":")[1])
    t = get_training(training_id)
    await state.update_data(training_id=training_id)
    await state.set_state(DuplicateTraining.waiting_date)
    await callback.message.edit_text(
        f"Дублируем тренировку {t[1]}.\nВведи дату новой тренировки:",
        reply_markup=back_button(f"training_view:{training_id}"))

@dp.message(DuplicateTraining.waiting_date)
async def process_duplicate_date(message: Message, state: FSMContext):
    data = await state.get_data()
    new_id = duplicate_training(data["training_id"], message.text.strip())
    await state.clear()
    await message.answer(f"✅ Тренировка скопирована на {message.text.strip()}!\nУчастники перенесены, оплата сброшена.",
                         reply_markup=main_menu())

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
    buttons = [[InlineKeyboardButton(text=p[1], callback_data=f"training_add_player_confirm:{training_id}:{p[0]}")]
               for p in available]
    buttons.append(back_kb(f"training_view:{training_id}"))
    await callback.message.edit_text(f"Выбери участника для {t[1]}:",
                                      reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("training_add_player_confirm:"))
async def cb_training_add_player_confirm(callback: CallbackQuery):
    _, training_id, player_id = callback.data.split(":")
    training_id, player_id = int(training_id), int(player_id)
    add_player_to_training(training_id, player_id)
    p = get_player(player_id)
    t = get_training(training_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💵 Нал", callback_data=f"training_pay_type:{training_id}:{player_id}:cash"),
         InlineKeyboardButton(text="💳 Безнал", callback_data=f"training_pay_type:{training_id}:{player_id}:card")],
        [InlineKeyboardButton(text="⏳ Ещё не оплатил", callback_data=f"training_view:{training_id}")]
    ])
    text = "✅ <b>" + p[1] + "</b> добавлен на тренировку " + t[1] + ".\n\nОн уже оплатил?"
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("training_pay:"))
async def cb_training_pay(callback: CallbackQuery):
    training_id = int(callback.data.split(":")[1])
    tp = get_training_players(training_id)
    unpaid = [p for p in tp if p[4] == 0 and p[3] != "cancelled"]
    if not unpaid:
        await callback.answer("Все уже оплатили!", show_alert=True)
        return
    buttons = [[InlineKeyboardButton(text=p[7], callback_data=f"training_pay_player:{training_id}:{p[2]}")]
               for p in unpaid]
    buttons.append(back_kb(f"training_view:{training_id}"))
    await callback.message.edit_text("Кто оплатил?",
                                      reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("training_pay_player:"))
async def cb_training_pay_player(callback: CallbackQuery, state: FSMContext):
    _, training_id, player_id = callback.data.split(":")
    training_id, player_id = int(training_id), int(player_id)
    t = get_training(training_id)
    p = get_player(player_id)
    await state.update_data(training_id=training_id, player_id=player_id, default_price=t[2])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💵 Нал", callback_data=f"training_pay_type:{training_id}:{player_id}:cash"),
         InlineKeyboardButton(text="💳 Безнал", callback_data=f"training_pay_type:{training_id}:{player_id}:card")],
        back_kb(f"training_pay:{training_id}")
    ])
    await callback.message.edit_text(f"Как оплатил <b>{p[1]}</b>?", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("training_pay_type:"))
async def cb_training_pay_type(callback: CallbackQuery, state: FSMContext):
    _, training_id, player_id, ptype = callback.data.split(":")
    training_id, player_id = int(training_id), int(player_id)
    t = get_training(training_id)
    p = get_player(player_id)
    await state.update_data(training_id=training_id, player_id=player_id, payment_type=ptype)
    await state.set_state(EditPaid.waiting_amount)
    type_text = "💵 Нал" if ptype == "cash" else "💳 Безнал"
    await callback.message.edit_text(
        f"{type_text} | <b>{p[1]}</b>\nСколько заплатил? (стандартно {t[2]:.0f} €)",
        reply_markup=back_button(f"training_pay:{training_id}"), parse_mode="HTML")

@dp.message(EditPaid.waiting_amount)
async def process_paid_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("❌ Введи число")
        return
    data = await state.get_data()
    set_player_paid(data["training_id"], data["player_id"], amount, data.get("payment_type", "cash"))
    await state.clear()
    p = get_player(data["player_id"])
    type_text = "💵" if data.get("payment_type") == "cash" else "💳"
    await message.answer(f"✅ {p[1]} оплатил {amount:.0f} € {type_text}", reply_markup=main_menu())

@dp.callback_query(F.data.startswith("training_cancel:"))
async def cb_training_cancel(callback: CallbackQuery):
    training_id = int(callback.data.split(":")[1])
    tp = get_training_players(training_id)
    active = [p for p in tp if p[4] != "cancelled"]
    buttons = [[InlineKeyboardButton(text=p[7], callback_data=f"training_cancel_confirm:{training_id}:{p[2]}")]
               for p in active]
    buttons.append(back_kb(f"training_view:{training_id}"))
    await callback.message.edit_text("Кто отменяет?",
                                      reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("training_cancel_confirm:"))
async def cb_training_cancel_confirm(callback: CallbackQuery):
    _, training_id, player_id = callback.data.split(":")
    training_id, player_id = int(training_id), int(player_id)
    p = get_player(player_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Возврат", callback_data=f"training_cancel_refund:{training_id}:{player_id}"),
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
    if tp_row and tp_row[4] > 0:
        set_player_paid(training_id, player_id, 0)
    set_player_status(training_id, player_id, "cancelled", "возврат")
    p = get_player(player_id)
    await callback.answer(f"✅ Отмена + возврат для {p[1]}")
    await show_training_view(callback.message, training_id, edit=True)

@dp.callback_query(F.data.startswith("training_cancel_transfer:"))
async def cb_cancel_transfer(callback: CallbackQuery):
    _, training_id, player_id = callback.data.split(":")
    training_id, player_id = int(training_id), int(player_id)
    set_player_status(training_id, player_id, "cancelled", "перенос")
    p = get_player(player_id)
    await callback.answer(f"✅ Отмена + перенос для {p[1]}")
    await show_training_view(callback.message, training_id, edit=True)

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
    months = get_finances_by_month()
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
        text += f"<b>{month}:</b>  {total:.0f} €\n"
        text += f"  💵 Нал: {cash:.0f} €  💳 Безнал: {card:.0f} €\n\n"

    text += f"<b>Итого собрано: {total_all:.0f} €</b>\n\n"

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
