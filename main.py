import asyncio
import datetime
import time
import os  # Добавь этот импорт
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from database import SessionLocal, Appointment

# --- НАСТРОЙКИ (Берем из переменных окружения) ---
API_TOKEN = os.getenv("BOT_TOKEN")
MY_ID = int(os.getenv("ADMIN_ID", 7082183196)) # 7082183196 - ID Асель по умолчанию

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- АНТИ-СПАМ СИСТЕМА ---
user_last_action = {}

def is_spamming(user_id):
    current_time = time.time()
    last_time = user_last_action.get(user_id, 0)
    if current_time - last_time < 0.8: # Лимит 0.8 сек между нажатиями
        return True
    user_last_action[user_id] = current_time
    return False

# Длительность услуг в минутах
DURATIONS = {
    "Стрижка": 60,
    "Окрашивание": 180
}

class Booking(StatesGroup):
    service = State()
    date = State()
    time = State()
    photo_current = State()
    photo_target = State()
    comment = State()
    name = State()
    phone = State()

class AdminManualBooking(StatesGroup):
    service = State()
    date = State()
    time = State()
    name = State()
    phone = State()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def time_to_min(t_str):
    h, m = map(int, t_str.split(':'))
    return h * 60 + m

def min_to_time(m):
    return f"{m // 60:02d}:{m % 60:02d}"

def get_occupied_intervals(date_val):
    db = SessionLocal()
    apps = db.query(Appointment).filter(
        Appointment.date_time.contains(date_val),
        Appointment.status.in_(["pending", "confirmed", "blocked"])
    ).all()
    db.close()
    
    occupied_slots = []
    for a in apps:
        if "FULL_DAY" in a.date_time:
            return "FULL"
        try:
            time_part = a.date_time.split(" ")[1]
            start_m = time_to_min(time_part)
            duration = DURATIONS.get(a.service_type, 60)
            if a.status == "blocked": duration = 60
            
            for m in range(start_m, start_m + duration, 30):
                occupied_slots.append(m)
        except:
            continue
    return occupied_slots

def get_user_link(user_id, username=None):
    if username:
        return f"https://t.me/{username}"
    return f"tg://user?id={user_id}"

# --- ФОНОВАЯ ЗАДАЧА: НАПОМИНАНИЯ ---

async def send_reminders():
    while True:
        await asyncio.sleep(60)
        db = SessionLocal()
        now = datetime.datetime.now()
        confirmed = db.query(Appointment).filter(Appointment.status == "confirmed").all()
        
        for app in confirmed:
            try:
                app_dt = datetime.datetime.strptime(f"{now.year}.{app.date_time}", "%Y.%d.%m %H:%M")
                diff = app_dt - now
                if 119 <= diff.total_seconds() / 60 <= 120:
                    if app.client_id and app.client_id.isdigit():
                        await bot.send_message(
                            app.client_id, 
                            f"🌸 Напоминаем, вы записаны сегодня к Асель!\n"
                            f"⏰ Время: {app.date_time.split(' ')[1]}\n"
                            f"✂️ Услуга: {app.service_type}\n\nДо встречи!"
                        )
            except:
                continue
        db.close()

# --- КЛИЕНТСКАЯ ЧАСТЬ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    if is_spamming(message.from_user.id): return
    
    # Защита от анонимов (без username)
    if not message.from_user.username and message.from_user.id != MY_ID:
        await message.answer("⚠️ Для использования бота, пожалуйста, установите **Username** (Имя пользователя) в настройках вашего Telegram профиля. Это нужно для связи мастера с вами.")
        return

    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="✂️ Записаться на стрижку", callback_data="service_Стрижка")
    kb.button(text="🎨 Записаться на окрашивание", callback_data="service_Окрашивание")
    kb.button(text="📅 Мои записи", callback_data="my_bookings")
    kb.adjust(1)
    await message.answer(f"Здравствуйте, {message.from_user.first_name}! 🌸\nЯ бот Асель. Выберите действие:", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "my_bookings")
async def show_my_bookings(callback: types.CallbackQuery):
    if is_spamming(callback.from_user.id): return
    db = SessionLocal()
    user_id = str(callback.from_user.id)
    active = db.query(Appointment).filter(
        Appointment.client_id == user_id,
        Appointment.status.in_(["pending", "confirmed"])
    ).all()
    db.close()
    
    if not active:
        await callback.message.answer("У вас нет активных записей.")
        return

    for b in active:
        kb = InlineKeyboardBuilder()
        kb.button(text="❌ Отменить запись", callback_data=f"cancel_client_{b.id}")
        await callback.message.answer(
            f"Запись: {b.date_time}\nУслуга: {b.service_type}\nСтатус: {'Ожидает подтверждения' if b.status == 'pending' else 'Подтверждена'}",
            reply_markup=kb.as_markup()
        )

@dp.callback_query(F.data.startswith("cancel_client_"))
async def cancel_by_client(callback: types.CallbackQuery):
    if is_spamming(callback.from_user.id): return
    idx = int(callback.data.split("_")[2])
    db = SessionLocal()
    app = db.query(Appointment).filter(Appointment.id == idx).first()
    if app:
        app.status = "rejected"
        db.commit()
        await bot.send_message(MY_ID, f"⚠️ Клиент {app.client_name} ОТМЕНИЛ запись на {app.date_time}")
        await callback.message.edit_text("✅ Запись отменена.")
    db.close()

@dp.callback_query(F.data.startswith("service_"))
async def select_service(callback: types.CallbackQuery, state: FSMContext):
    if is_spamming(callback.from_user.id): return
    service = callback.data.split("_")[1]
    await state.update_data(service=service)
    kb = InlineKeyboardBuilder()
    for i in range(14):
        d = datetime.date.today() + datetime.timedelta(days=i)
        date_str = d.strftime("%d.%m")
        kb.button(text=date_str, callback_data=f"date_{date_str}")
    kb.adjust(3)
    await callback.message.edit_text("Выберите дату:", reply_markup=kb.as_markup())
    await state.set_state(Booking.date)

@dp.callback_query(F.data.startswith("date_"))
async def select_time(callback: types.CallbackQuery, state: FSMContext):
    if is_spamming(callback.from_user.id): return
    date_val = callback.data.split("_")[1]
    data = await state.get_data()
    
    current_state = await state.get_state()
    is_admin = current_state in [AdminManualBooking.date, AdminManualBooking.time]
    
    service = data['service']
    duration = DURATIONS[service]
    await state.update_data(date=date_val)
    
    occupied = get_occupied_intervals(date_val)
    if occupied == "FULL":
        await callback.answer("Этот день полностью занят.", show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    start_working = time_to_min("10:00")
    end_working = time_to_min("19:00")
    
    for m in range(start_working, end_working, 30):
        is_free = True
        for check_m in range(m, m + duration, 30):
            if check_m in occupied or check_m >= end_working:
                is_free = False
                break
        t_str = min_to_time(m)
        if is_free:
            kb.button(text=t_str, callback_data=f"time_{t_str}")
        else:
            kb.button(text="занято", callback_data="ignore")
            
    kb.adjust(4)
    await callback.message.edit_text(f"Услуга: {service}\nДата: {date_val}\nВыберите время:", reply_markup=kb.as_markup())
    
    if is_admin:
        await state.set_state(AdminManualBooking.time)
    else:
        await state.set_state(Booking.time)

@dp.callback_query(F.data.startswith("time_"))
async def handle_time_selection(callback: types.CallbackQuery, state: FSMContext):
    if is_spamming(callback.from_user.id): return
    time_val = callback.data.split("_")[1]
    data = await state.update_data(time=time_val)
    
    current_state = await state.get_state()
    if current_state == AdminManualBooking.time:
        await callback.message.edit_text("Введите ИМЯ клиентки (из WhatsApp):")
        await state.set_state(AdminManualBooking.name)
        return

    skip_kb = InlineKeyboardBuilder().button(text="Пропустить ➡️", callback_data="skip_photo").as_markup()
    if data['service'] == "Окрашивание":
        await callback.message.edit_text("📸 Фото волос СЕЙЧАС:", reply_markup=skip_kb)
        await state.set_state(Booking.photo_current)
    else:
        await callback.message.edit_text("📸 Фото желаемой стрижки:", reply_markup=skip_kb)
        await state.set_state(Booking.photo_target)

@dp.message(Booking.photo_current, F.photo)
async def handle_photo_current(message: types.Message, state: FSMContext):
    await state.update_data(photo_current=message.photo[-1].file_id)
    skip_kb = InlineKeyboardBuilder().button(text="Пропустить ➡️", callback_data="skip_photo").as_markup()
    await message.answer("📸 Фото желаемого результата:", reply_markup=skip_kb)
    await state.set_state(Booking.photo_target)

@dp.message(Booking.photo_target, F.photo)
async def handle_photo_target(message: types.Message, state: FSMContext):
    await state.update_data(photo_target=message.photo[-1].file_id)
    skip_kb = InlineKeyboardBuilder().button(text="Пропустить ➡️", callback_data="skip_comment").as_markup()
    await message.answer("Комментарий (необязательно):", reply_markup=skip_kb)
    await state.set_state(Booking.comment)

@dp.callback_query(F.data == "skip_photo")
async def skip_photo_step(callback: types.CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state == Booking.photo_current:
        await callback.message.edit_text("Ок. Фото ЖЕЛАЕМОГО результата:", reply_markup=InlineKeyboardBuilder().button(text="Пропустить ➡️", callback_data="skip_photo").as_markup())
        await state.set_state(Booking.photo_target)
    else:
        await callback.message.edit_text("Комментарий к записи:", reply_markup=InlineKeyboardBuilder().button(text="Пропустить ➡️", callback_data="skip_comment").as_markup())
        await state.set_state(Booking.comment)

@dp.message(Booking.comment)
async def handle_comment(message: types.Message, state: FSMContext):
    await state.update_data(comment=message.text)
    await message.answer("Как вас зовут?")
    await state.set_state(Booking.name)

@dp.callback_query(F.data == "skip_comment")
async def skip_comment_step(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(comment="Нет комментария")
    await callback.message.edit_text("Как вас зовут?")
    await state.set_state(Booking.name)

@dp.message(Booking.name)
async def handle_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    kb = ReplyKeyboardBuilder().button(text="📱 Отправить номер", request_contact=True).as_markup(resize_keyboard=True, one_time_keyboard=True)
    await message.answer("Отправьте номер телефона:", reply_markup=kb)
    await state.set_state(Booking.phone)

@dp.message(Booking.phone, F.contact | F.text)
async def handle_phone(message: types.Message, state: FSMContext):
    phone = message.contact.phone_number if message.contact else message.text
    data = await state.get_data()
    full_time = f"{data['date']} {data['time']}"
    db = SessionLocal()
    new_app = Appointment(client_id=str(message.from_user.id), client_name=data['name'], phone=phone, service_type=data['service'], date_time=full_time)
    db.add(new_app)
    db.commit()
    db.refresh(new_app)
    
    user_link = get_user_link(message.from_user.id, message.from_user.username)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Ок", callback_data=f"adm_confirm_{new_app.id}")
    kb.button(text="❌ Нет", callback_data=f"adm_reject_{new_app.id}")
    kb.button(text="💬 Чат", url=user_link)
    kb.adjust(2, 1)

    msg_mom = (f"📩 **ЗАПИСЬ!**\n👤 {data['name']}\n📞 {phone}\n"
               f"✂️ {data['service']}\n📅 {full_time}\n💬: {data.get('comment', '-')}")
    
    if 'photo_current' in data:
        await bot.send_photo(MY_ID, data['photo_current'], caption="📸 ТЕКУЩИЕ")
    if 'photo_target' in data:
        await bot.send_photo(MY_ID, data['photo_target'], caption="📸 ЖЕЛАЕМЫЕ")
        
    await bot.send_message(MY_ID, msg_mom, reply_markup=kb.as_markup())
    await message.answer("✅ Готово! Мастер скоро подтвердит запись.", reply_markup=types.ReplyKeyboardRemove())
    await state.clear()
    db.close()

# --- АДМИН-ЧАСТЬ (Защита MY_ID) ---

@dp.message(Command("admin"), F.from_user.id == MY_ID)
async def admin_panel(message: types.Message):
    kb = InlineKeyboardBuilder()
    kb.button(text="📝 Записать вручную (WhatsApp)", callback_data="adm_man_start")
    kb.button(text="📅 Журнал записей", callback_data="adm_journal_menu")
    kb.button(text="🔒 Блок / 🔓 Разблок", callback_data="adm_block_start")
    kb.adjust(1)
    await message.answer("Админ-панель Асель:", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "adm_man_start", F.from_user.id == MY_ID)
async def adm_manual_start(callback: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardBuilder()
    kb.button(text="✂️ Стрижка", callback_data="adm_man_srv_Стрижка")
    kb.button(text="🎨 Окрашивание", callback_data="adm_man_srv_Окрашивание")
    await callback.message.edit_text("Выберите услугу:", reply_markup=kb.as_markup())
    await state.set_state(AdminManualBooking.service)

@dp.callback_query(F.data.startswith("adm_man_srv_"), F.from_user.id == MY_ID)
async def adm_man_service(callback: types.CallbackQuery, state: FSMContext):
    service = callback.data.split("_")[3]
    await state.update_data(service=service)
    kb = InlineKeyboardBuilder()
    for i in range(14):
        d = datetime.date.today() + datetime.timedelta(days=i)
        date_str = d.strftime("%d.%m")
        kb.button(text=date_str, callback_data=f"date_{date_str}")
    kb.adjust(3)
    await callback.message.edit_text(f"Услуга: {service}. Дата:", reply_markup=kb.as_markup())
    await state.set_state(AdminManualBooking.date)

@dp.message(AdminManualBooking.name, F.from_user.id == MY_ID)
async def adm_man_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Номер телефона (или '0'):")
    await state.set_state(AdminManualBooking.phone)

@dp.message(AdminManualBooking.phone, F.from_user.id == MY_ID)
async def adm_man_finish(message: types.Message, state: FSMContext):
    data = await state.get_data()
    full_time = f"{data['date']} {data['time']}"
    db = SessionLocal()
    new_app = Appointment(client_id="MANUAL", client_name=data['name'], phone=message.text, service_type=data['service'], date_time=full_time, status="confirmed")
    db.add(new_app)
    db.commit()
    db.close()
    await message.answer(f"✅ Внесено: {data['name']} на {full_time}")
    await state.clear()

@dp.callback_query(F.data == "adm_journal_menu", F.from_user.id == MY_ID)
async def journal_menu(callback: types.CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.button(text="🗓 Сегодня", callback_data="list_today")
    kb.button(text="🗓 Завтра", callback_data="list_tomorrow")
    kb.button(text="📚 Все активные", callback_data="list_all")
    kb.button(text="⬅️ Назад", callback_data="adm_back")
    kb.adjust(2, 1, 1)
    await callback.message.edit_text("Просмотр записей:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("list_"), F.from_user.id == MY_ID)
async def list_appointments(callback: types.CallbackQuery):
    period = callback.data.split("_")[1]
    db = SessionLocal()
    today = datetime.date.today().strftime("%d.%m")
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%d.%m")
    query = db.query(Appointment).filter(Appointment.status.in_(["pending", "confirmed"]), Appointment.client_name != "ADMIN")
    if period == "today": query = query.filter(Appointment.date_time.contains(today))
    elif period == "tomorrow": query = query.filter(Appointment.date_time.contains(tomorrow))
    active = query.order_by(Appointment.date_time).all()
    if not active:
        await callback.message.answer("Записей нет.")
        db.close()
        return
    await callback.message.delete()
    for b in active:
        kb = InlineKeyboardBuilder()
        if b.client_id != "MANUAL":
            kb.button(text="💬 Чат", url=f"tg://user?id={b.client_id}")
        if b.status == "confirmed":
            kb.button(text="🎉 Выполнено", callback_data=f"adm_done_{b.id}")
        else:
            kb.button(text="✅ Ок", callback_data=f"adm_confirm_{b.id}")
        kb.button(text="❌ Удалить", callback_data=f"adm_reject_{b.id}")
        kb.adjust(2, 1)
        await callback.message.answer(f"{'⏳' if b.status=='pending' else '✅'} {b.date_time}\n👤 {b.client_name}\n📞 {b.phone}", reply_markup=kb.as_markup())
    db.close()

@dp.callback_query(F.data == "adm_block_start", F.from_user.id == MY_ID)
async def block_main_menu(callback: types.CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.button(text="🔒 Блок времени", callback_data="adm_block_date_select")
    kb.button(text="🔓 Разблок (Список)", callback_data="adm_unblock_list")
    kb.button(text="⬅️ Назад", callback_data="adm_back")
    kb.adjust(1)
    await callback.message.edit_text("Блокировки:", reply_markup=kb.as_markup())

@dp.callback_query(F.data == "adm_unblock_list", F.from_user.id == MY_ID)
async def unblock_list(callback: types.CallbackQuery):
    db = SessionLocal()
    blocks = db.query(Appointment).filter(Appointment.status == "blocked").all()
    db.close()
    if not blocks:
        await callback.answer("Нет блокировок", show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    for bl in blocks:
        kb.button(text=f"🗑 {bl.date_time}", callback_data=f"unblock_exec_{bl.id}")
    kb.button(text="⬅️ Назад", callback_data="adm_block_start")
    kb.adjust(1)
    await callback.message.edit_text("Нажмите для удаления блока:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("unblock_exec_"), F.from_user.id == MY_ID)
async def unblock_execute(callback: types.CallbackQuery):
    idx = int(callback.data.split("_")[2])
    db = SessionLocal()
    bl = db.query(Appointment).filter(Appointment.id == idx).first()
    if bl:
        db.delete(bl)
        db.commit()
        await callback.answer("Разблокировано")
        await unblock_list(callback)
    db.close()

@dp.callback_query(F.data == "adm_block_date_select", F.from_user.id == MY_ID)
async def block_choice_date(callback: types.CallbackQuery):
    kb = InlineKeyboardBuilder()
    for i in range(14):
        d = datetime.date.today() + datetime.timedelta(days=i)
        date_str = d.strftime("%d.%m")
        kb.button(text=date_str, callback_data=f"bl_date_{date_str}")
    kb.adjust(3)
    await callback.message.edit_text("Выберите дату:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("bl_date_"), F.from_user.id == MY_ID)
async def block_type_choice(callback: types.CallbackQuery):
    date_val = callback.data.split("_")[2]
    kb = InlineKeyboardBuilder()
    kb.button(text="🚫 Весь день", callback_data=f"bl_full_{date_val}")
    kb.button(text="⏰ Час", callback_data=f"bl_hour_{date_val}")
    kb.button(text="⬅️ Назад", callback_data="adm_block_date_select")
    kb.adjust(1)
    await callback.message.edit_text(f"Блок {date_val}:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("bl_full_"), F.from_user.id == MY_ID)
async def block_full_day(callback: types.CallbackQuery):
    date_val = callback.data.split("_")[2]
    db = SessionLocal()
    db.add(Appointment(client_id="ADMIN", client_name="ADMIN", service_type="BLOCK", date_time=f"{date_val} FULL_DAY", status="blocked"))
    db.commit()
    db.close()
    await callback.message.edit_text(f"✅ День {date_val} закрыт.")

@dp.callback_query(F.data.startswith("bl_hour_"), F.from_user.id == MY_ID)
async def block_hour_select(callback: types.CallbackQuery):
    date_val = callback.data.split("_")[2]
    kb = InlineKeyboardBuilder()
    for h in range(10, 19):
        for m in ["00", "30"]:
            t = f"{h:02d}:{m}"
            kb.button(text=t, callback_data=f"blockfin_{date_val}_{t}")
    kb.adjust(4)
    await callback.message.edit_text(f"Выберите время ({date_val}):", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("blockfin_"), F.from_user.id == MY_ID)
async def block_hour_final(callback: types.CallbackQuery):
    _, d_v, t_v = callback.data.split("_")
    db = SessionLocal()
    db.add(Appointment(client_id="ADMIN", client_name="ADMIN", service_type="BLOCK", date_time=f"{d_v} {t_v}", status="blocked"))
    db.commit()
    db.close()
    await callback.message.edit_text(f"✅ {t_v} ({d_v}) закрыто.")

@dp.callback_query(F.data.startswith("adm_confirm_"), F.from_user.id == MY_ID)
async def adm_confirm(callback: types.CallbackQuery):
    idx = int(callback.data.split("_")[2])
    db = SessionLocal()
    app = db.query(Appointment).filter(Appointment.id == idx).first()
    if app:
        app.status = "confirmed"
        db.commit()
        if app.client_id != "MANUAL":
            await bot.send_message(app.client_id, f"✅ Мастер подтвердил запись на {app.date_time}!")
        await callback.message.edit_text(callback.message.text + "\n\n✅ ПОДТВЕРЖДЕНО")
    db.close()

@dp.callback_query(F.data.startswith("adm_done_"), F.from_user.id == MY_ID)
async def adm_done(callback: types.CallbackQuery):
    idx = int(callback.data.split("_")[2])
    db = SessionLocal()
    app = db.query(Appointment).filter(Appointment.id == idx).first()
    if app:
        app.status = "completed"
        db.commit()
        await callback.message.edit_text(callback.message.text + "\n\n🏁 ВЫПОЛНЕНО")
    db.close()

@dp.callback_query(F.data.startswith("adm_reject_"), F.from_user.id == MY_ID)
async def adm_reject(callback: types.CallbackQuery):
    idx = int(callback.data.split("_")[2])
    db = SessionLocal()
    app = db.query(Appointment).filter(Appointment.id == idx).first()
    if app:
        app.status = "rejected"
        db.commit()
        if app.client_id != "MANUAL":
            await bot.send_message(app.client_id, f"❌ Запись на {app.date_time} отклонена.")
        await callback.message.delete()
    db.close()

@dp.callback_query(F.data == "adm_back", F.from_user.id == MY_ID)
async def back_to_admin(callback: types.CallbackQuery):
    await admin_panel(callback.message)

async def main():
    asyncio.create_task(send_reminders())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())