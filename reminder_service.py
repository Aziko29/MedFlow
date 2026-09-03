# reminder_service.py
"""
Telegram orqali navbat eslatma va o'z-o'ziga xizmat ko'rsatish boti
(bepul, SMS shart emas).

v2.0.0 — "SuperBot" kengaytmasi: eski versiya faqat eslatma va bekor
qilishni bilardi. Bu versiyada bemor botning o'zidan:
  - yangi navbat olishi (shifokor -> sana -> vaqt -> tasdiqlash),
  - mavjud navbatini ko'chirishi (reschedule),
  - navbatlar tarixini ko'rishi,
  - interfeys tilini (uz/ru/en) tanlashi,
  - eslatma vaqtlarini o'zi sozlashi yoki o'chirishi (mute),
  - klinika manzilini va SOS (favqulodda qo'ng'iroq) tugmasini
    ko'rishi mumkin.

Environment o'zgaruvchilari (.env):
    TELEGRAM_BOT_TOKEN     - @BotFather'dan olingan token (majburiy)
    MEDFLOW_BASE_URL       - masalan https://clinic.example.com (bekor qilish
                              linki shu domenga quriladi; standart: http://localhost:8000)
    REMINDER_POLL_SECONDS  - eslatma job oralig'i, standart 900 (15 daqiqa)
    CLINIC_NAME            - xabarlarda ko'rsatiladigan klinika nomi
    CLINIC_PHONE           - SOS tugmasidagi qo'ng'iroq raqami, masalan +998901234567
    CLINIC_ADDRESS         - "📍 Manzil" bo'limida ko'rsatiladigan matn
    CLINIC_LAT / CLINIC_LON- ixtiyoriy, berilsa Telegram xaritada joylashuv yuboriladi
    BOOKING_SLOT_MINUTES   - yangi navbat/(ko'chirish) uchun vaqt slotlari oralig'i, standart 30
    BOOKING_DAYS_AHEAD     - nechchi kunga oldindan yozilish mumkin, standart 7

TELEGRAM_BOT_TOKEN o'rnatilmasa, servis butunlay o'chirilgan holda ishga
tushadi (log yozib, jim o'tib ketadi) — dev muhitida majburiy emas.
"""
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

import models
from audit import log_action
from crypto_fields import blind_index
from database import SessionLocal
from utils.timezone import get_clinic_timezone, now_in_clinic_tz

logger = logging.getLogger("medflow.reminders")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else None
BASE_URL = os.environ.get("MEDFLOW_BASE_URL", "http://localhost:8000").rstrip("/")
CLINIC_NAME = os.environ.get("CLINIC_NAME", "ClinicFlow")
CLINIC_PHONE = os.environ.get("CLINIC_PHONE", "").strip()
CLINIC_ADDRESS = os.environ.get("CLINIC_ADDRESS", "").strip()
CLINIC_LAT = os.environ.get("CLINIC_LAT", "").strip()
CLINIC_LON = os.environ.get("CLINIC_LON", "").strip()
POLL_SECONDS = int(os.environ.get("REMINDER_POLL_SECONDS", "900"))  # 15 daqiqa
BOOKING_SLOT_MINUTES = int(os.environ.get("BOOKING_SLOT_MINUTES", "30"))
BOOKING_DAYS_AHEAD = int(os.environ.get("BOOKING_DAYS_AHEAD", "7"))

WINDOW_SLACK = timedelta(seconds=POLL_SECONDS)
# Eng katta mumkin bo'lgan "necha soat oldin" sozlamasi — reminder job
# qaysi oraliqdagi navbatlarni tekshirishi kerakligini shu belgilaydi.
MAX_REMINDER_HOURS = 72

# Menyu cheklovlari
MAX_LISTED_APPOINTMENTS = 8   # bitta ro'yxatda ko'rsatiladigan faol navbatlar soni
MAX_LISTED_DOCTORS = 10       # yangi navbat uchun ko'rsatiladigan shifokorlar soni
MAX_LISTED_SLOTS = 24         # bitta kun uchun ko'rsatiladigan bo'sh vaqt soni
PHONE_DIGITS_RE = re.compile(r"^\+?\d{9,15}$")
WORKING_HOURS_RE = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")

# Eslatma vaqtini sozlashda taklif etiladigan variantlar (soat).
REMINDER_FIRST_PRESETS = [48, 24, 12, 6]
REMINDER_SECOND_PRESETS = [4, 2, 1]

SUPPORTED_LANGUAGES = ("uz", "ru", "en")

_scheduler: Optional[BackgroundScheduler] = None
_polling_thread: Optional[threading.Thread] = None
_stop_polling = threading.Event()
_lock_file = None  # noqa: E501 — pastdagi _acquire_singleton_lock() uchun


def _acquire_singleton_lock() -> bool:
    """🔒 KRITIK: gunicorn --workers 4 bilan ishga tushirilsa, main.py HAR
    BIR worker'da import qilinadi va start_reminder_service() 4 marta
    chaqiriladi. Lock bo'lmasa — 4 ta getUpdates polling thread bir xil
    Telegram xabarlarini 4 marta qayta ishlaydi (dublikat eslatma, hatto
    bitta navbatga 4 marta urinish poydevor darajasida oldini olingan
    bo'lsa ham keraksiz yuklama). Fayl darajasidagi flock() faqat BITTA
    process/worker'ga botni boshqarishga ruxsat beradi; qolganlari faqat
    HTTP so'rovlarga xizmat qiladi."""
    global _lock_file
    lock_path = os.environ.get("REMINDER_LOCK_FILE", "/tmp/medflow_reminder_bot.lock")
    try:
        import fcntl
        _lock_file = open(lock_path, "w")
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except ImportError:
        # Windows (dev muhiti) — fcntl yo'q, odatda bitta process bilan
        # ishga tushiriladi, shuning uchun lock shart emas.
        return True
    except OSError:
        return False


# ==============================================
# TELEGRAM BOT API — past darajadagi HTTP chaqiruvlar
# ==============================================
def _telegram_get(method: str, params: dict, timeout: float = 35.0) -> Optional[dict]:
    if not TELEGRAM_API_BASE:
        return None
    try:
        resp = httpx.get(f"{TELEGRAM_API_BASE}/{method}", params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("Telegram API GET %s xato: %s", method, exc)
        return None


def _telegram_post(method: str, payload: dict, timeout: float = 10.0) -> Optional[dict]:
    if not TELEGRAM_API_BASE:
        return None
    try:
        resp = httpx.post(f"{TELEGRAM_API_BASE}/{method}", json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("Telegram API POST %s xato: %s", method, exc)
        return None


def _telegram_post_file(method: str, data: dict, files: dict, timeout: float = 20.0) -> Optional[dict]:
    if not TELEGRAM_API_BASE:
        return None
    try:
        resp = httpx.post(f"{TELEGRAM_API_BASE}/{method}", data=data, files=files, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("Telegram API file POST %s xato: %s", method, exc)
        return None


def send_telegram_message(chat_id, text: str, reply_markup: Optional[dict] = None) -> bool:
    if not TELEGRAM_API_BASE:
        logger.info("TELEGRAM_BOT_TOKEN o'rnatilmagan — xabar yuborilmadi (chat_id=%s)", chat_id)
        return False
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    result = _telegram_post("sendMessage", payload)
    return bool(result and result.get("ok"))


def _send_document(chat_id, filename: str, content_bytes: bytes, caption: str = "") -> bool:
    result = _telegram_post_file(
        "sendDocument",
        data={"chat_id": chat_id, "caption": caption},
        files={"document": (filename, content_bytes, "text/calendar")},
    )
    return bool(result and result.get("ok"))


def _send_location(chat_id, lat: float, lon: float) -> bool:
    result = _telegram_post("sendLocation", {"chat_id": chat_id, "latitude": lat, "longitude": lon})
    return bool(result and result.get("ok"))


def _edit_message(chat_id, message_id: int, text: str, reply_markup: Optional[dict] = None) -> bool:
    """Menyu tugmasi bosilganda YANGI xabar yubormasdan, mavjudini
    tahrirlaydi — chat ichida spam bo'lmasligi uchun."""
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    result = _telegram_post("editMessageText", payload)
    return bool(result and result.get("ok"))


def _answer_callback(callback_query_id: str, text: str = "", show_alert: bool = False) -> None:
    """Tugma bosilganda Telegram'dagi 'yuklanmoqda...' holatini olib
    tashlaydi. Muhim: har bir callback_query albatta javob olishi kerak,
    aks holda foydalanuvchi tugmasi soatlab 'kutish' holatida qoladi."""
    _telegram_post(
        "answerCallbackQuery",
        {"callback_query_id": callback_query_id, "text": text, "show_alert": show_alert},
    )


def _request_contact_keyboard(lang: str) -> dict:
    return {
        "keyboard": [[{"text": TXT[lang]["btn_share_phone"], "request_contact": True}]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def _remove_keyboard() -> dict:
    return {"remove_keyboard": True}


# ==============================================
# I18N — uz / ru / en
# ==============================================
TXT = {
    "uz": {
        "welcome_new": "👋 {clinic} eslatma botiga xush kelibsiz!\n\nNavbat eslatmalarini olish uchun telefon raqamingizni tasdiqlang.",
        "welcome_back": "👋 Xush kelibsiz, {name}!",
        "menu_prompt": "Quyidagi menyudan tanlang 👇",
        "btn_appointments": "📅 Mening navbatlarim",
        "btn_new_appointment": "➕ Yangi navbat olish",
        "btn_history": "🕘 Navbatlar tarixi",
        "btn_settings": "⚙️ Sozlamalar",
        "btn_relink": "🔄 Raqamni qayta ulash",
        "btn_help": "ℹ️ Yordam",
        "btn_back": "⬅️ Orqaga",
        "btn_share_phone": "📱 Telefon raqamimni yuborish",
        "help_text": (
            "ℹ️ <b>Yordam</b>\n\n"
            "📅 <i>Mening navbatlarim</i> — barcha faol navbatlaringiz.\n"
            "➕ <i>Yangi navbat olish</i> — shifokor, sana va vaqt tanlab yoziling.\n"
            "🕘 <i>Navbatlar tarixi</i> — o'tgan qabullaringiz.\n"
            "⚙️ <i>Sozlamalar</i> — til, eslatma vaqti, ovozsiz rejim, manzil, SOS.\n\n"
            "Navbat vaqti yaqinlashganda sizga avtomatik eslatma yuboriladi "
            "(sozlanadigan vaqtda — ⚙️ Sozlamalar bo'limiga qarang)."
        ),
        "link_prompt": "Davom etish uchun avval telefon raqamingizni tasdiqlang 👇",
        "link_wrong_format": "❌ Telefon raqami formati noto'g'ri ko'rinmoqda. Iltimos, qaytadan urinib ko'ring yoki klinikaga murojaat qiling.",
        "link_not_found": "❌ Bu raqam bo'yicha bemor topilmadi. Iltimos, klinikada ro'yxatdan o'tgan raqamingizni yuboring.",
        "link_success": "✅ Raqamingiz tasdiqlandi, {name}! Endi navbatlaringiz haqida eslatma olasiz.",
        "unknown_command": "🤔 Buyruqni tanimadim. /menu buyrug'ini yuboring yoki quyidagi menyudan foydalaning.",
        "need_link_first": "Avval raqamingizni tasdiqlang",
        "no_active_appointments": "📭 Hozircha faol navbatlaringiz yo'q.",
        "active_list_title": "📅 <b>Sizning faol navbatlaringiz:</b>",
        "appt_not_found": "Navbat topilmadi",
        "invalid_request": "Noto'g'ri so'rov",
        "appt_detail": (
            "📋 <b>Navbat tafsilotlari</b>\n\n"
            "👨‍⚕️ Shifokor: {doctor}\n"
            "🗓 Sana/vaqt: {when}\n"
            "📌 Holat: {status}\n"
            "💵 Narx: {price} UZS"
        ),
        "btn_cancel_appt": "❌ Bekor qilish",
        "btn_reschedule_appt": "🔁 Ko'chirish",
        "btn_ics": "📎 Kalendarga qo'shish (.ics)",
        "cancel_confirm": "⚠️ Rostdan ham ushbu navbatni bekor qilmoqchimisiz?",
        "btn_confirm_yes": "✅ Ha, bekor qilish",
        "btn_confirm_no": "🚫 Yo'q",
        "already_closed": "Bu navbat allaqachon yopilgan",
        "cancel_done": "✅ Navbat bekor qilindi.",
        "cancel_done_alert": "Bekor qilindi ✅",
        "history_title": "🕘 <b>Navbatlar tarixi:</b>",
        "history_empty": "🗂 Hozircha tarix bo'sh.",
        "settings_title": "⚙️ <b>Sozlamalar</b>",
        "btn_language": "🌐 Til",
        "btn_reminder_time": "🔔 Eslatma vaqti",
        "btn_mute_on": "🔕 Ovozsiz rejimni yoqish",
        "btn_mute_off": "🔔 Ovozsiz rejimni o'chirish",
        "btn_location": "📍 Klinika manzili",
        "btn_sos": "🆘 SOS — favqulodda qo'ng'iroq",
        "language_prompt": "🌐 Tilni tanlang:",
        "language_set": "✅ Til o'zgartirildi.",
        "mute_on_done": "🔕 Avtomatik eslatmalar o'chirildi. Botdan qo'lda foydalanishda davom etishingiz mumkin.",
        "mute_off_done": "🔔 Avtomatik eslatmalar yoqildi.",
        "reminder_settings_title": "🔔 <b>Eslatma vaqtlarini sozlash</b>\n\nHozirgi: 1-eslatma — {first}, 2-eslatma — {second}",
        "reminder_pick_first": "Birinchi eslatma qachon yuborilsin?",
        "reminder_pick_second": "Ikkinchi eslatma qachon yuborilsin?",
        "reminder_hours_fmt": "{h} soat oldin",
        "reminder_off": "O'chirilgan",
        "btn_reminder_off": "🚫 O'chirish",
        "reminder_saved": "✅ Saqlandi.",
        "location_text": "📍 <b>{clinic}</b>\n{address}",
        "location_no_address": "Manzil hali kiritilmagan. Klinikaga murojaat qiling.",
        "sos_text": "🆘 <b>Favqulodda holat</b>\n\nKlinikaga bevosita qo'ng'iroq qiling:\n<b>{phone}</b>",
        "sos_no_phone": "🆘 Favqulodda raqam hali kiritilmagan. Iltimos, klinika bilan boshqa yo'l orqali bog'laning.",
        "btn_call": "☎️ Qo'ng'iroq qilish",
        "new_appt_choose_doctor": "➕ <b>Yangi navbat</b>\n\nShifokorni tanlang:",
        "new_appt_no_doctors": "Hozircha faol shifokorlar mavjud emas.",
        "new_appt_choose_date": "🗓 Sanani tanlang ({doctor}):",
        "new_appt_choose_time": "🕐 Vaqtni tanlang ({date}):",
        "new_appt_no_slots": "Bu kunga bo'sh vaqt qolmagan. Boshqa sanani tanlang.",
        "new_appt_confirm": (
            "📋 <b>Tasdiqlang</b>\n\n"
            "👨‍⚕️ Shifokor: {doctor}\n"
            "🗓 Sana/vaqt: {when}\n"
            "💵 Narx: {price} UZS"
        ),
        "btn_confirm_book": "✅ Tasdiqlash",
        "slot_taken": "⚠️ Afsuski bu vaqt band bo'lib qoldi. Boshqa vaqt tanlang.",
        "new_appt_done": "✅ Navbatingiz muvaffaqiyatli yozildi!",
        "resch_choose_date": "🔁 <b>Ko'chirish</b> — yangi sanani tanlang:",
        "resch_done": "✅ Navbat muvaffaqiyatli ko'chirildi!",
        "reminder_message": (
            "🔔 <b>{clinic}</b>\n\n"
            "Sizda {label} keyin navbat bor:\n"
            "👨‍⚕️ Shifokor: {doctor}\n"
            "🗓 Sana/vaqt: {when}\n\n"
            "Kela olmasangiz, quyidagi link orqali bekor qiling:\n{link}"
        ),
        "error_generic": "Xatolik yuz berdi, qayta urinib ko'ring",
        "status_labels": {
            "waiting": "⏳ Navbatda", "in_progress": "🩺 Qabulda", "delayed": "⌛ Kechiktirilgan",
            "completed": "✅ Yakunlangan", "cancelled": "❌ Bekor qilingan", "no_show": "🚫 Kelmadi",
        },
        "weekdays": ["Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"],
    },
    "ru": {
        "welcome_new": "👋 Добро пожаловать в бот напоминаний {clinic}!\n\nПодтвердите номер телефона, чтобы получать напоминания о записи.",
        "welcome_back": "👋 С возвращением, {name}!",
        "menu_prompt": "Выберите пункт меню 👇",
        "btn_appointments": "📅 Мои записи",
        "btn_new_appointment": "➕ Новая запись",
        "btn_history": "🕘 История записей",
        "btn_settings": "⚙️ Настройки",
        "btn_relink": "🔄 Обновить номер",
        "btn_help": "ℹ️ Помощь",
        "btn_back": "⬅️ Назад",
        "btn_share_phone": "📱 Отправить номер телефона",
        "help_text": (
            "ℹ️ <b>Помощь</b>\n\n"
            "📅 <i>Мои записи</i> — все активные записи.\n"
            "➕ <i>Новая запись</i> — выберите врача, дату и время.\n"
            "🕘 <i>История записей</i> — прошлые визиты.\n"
            "⚙️ <i>Настройки</i> — язык, время напоминаний, тихий режим, адрес, SOS.\n\n"
            "Перед визитом вы получите автоматическое напоминание "
            "(время настраивается в ⚙️ Настройках)."
        ),
        "link_prompt": "Чтобы продолжить, подтвердите номер телефона 👇",
        "link_wrong_format": "❌ Похоже, формат номера неверный. Попробуйте снова или обратитесь в клинику.",
        "link_not_found": "❌ Пациент с этим номером не найден. Отправьте номер, зарегистрированный в клинике.",
        "link_success": "✅ Номер подтверждён, {name}! Теперь вы будете получать напоминания о записях.",
        "unknown_command": "🤔 Команда не распознана. Отправьте /menu или используйте меню ниже.",
        "need_link_first": "Сначала подтвердите номер",
        "no_active_appointments": "📭 Активных записей пока нет.",
        "active_list_title": "📅 <b>Ваши активные записи:</b>",
        "appt_not_found": "Запись не найдена",
        "invalid_request": "Неверный запрос",
        "appt_detail": (
            "📋 <b>Детали записи</b>\n\n"
            "👨‍⚕️ Врач: {doctor}\n"
            "🗓 Дата/время: {when}\n"
            "📌 Статус: {status}\n"
            "💵 Цена: {price} UZS"
        ),
        "btn_cancel_appt": "❌ Отменить",
        "btn_reschedule_appt": "🔁 Перенести",
        "btn_ics": "📎 Добавить в календарь (.ics)",
        "cancel_confirm": "⚠️ Вы уверены, что хотите отменить эту запись?",
        "btn_confirm_yes": "✅ Да, отменить",
        "btn_confirm_no": "🚫 Нет",
        "already_closed": "Эта запись уже закрыта",
        "cancel_done": "✅ Запись отменена.",
        "cancel_done_alert": "Отменено ✅",
        "history_title": "🕘 <b>История записей:</b>",
        "history_empty": "🗂 История пока пуста.",
        "settings_title": "⚙️ <b>Настройки</b>",
        "btn_language": "🌐 Язык",
        "btn_reminder_time": "🔔 Время напоминаний",
        "btn_mute_on": "🔕 Включить тихий режим",
        "btn_mute_off": "🔔 Выключить тихий режим",
        "btn_location": "📍 Адрес клиники",
        "btn_sos": "🆘 SOS — экстренный звонок",
        "language_prompt": "🌐 Выберите язык:",
        "language_set": "✅ Язык изменён.",
        "mute_on_done": "🔕 Автоматические напоминания отключены. Ботом можно пользоваться вручную.",
        "mute_off_done": "🔔 Автоматические напоминания включены.",
        "reminder_settings_title": "🔔 <b>Настройка времени напоминаний</b>\n\nСейчас: 1-е — {first}, 2-е — {second}",
        "reminder_pick_first": "Когда отправить первое напоминание?",
        "reminder_pick_second": "Когда отправить второе напоминание?",
        "reminder_hours_fmt": "за {h} ч.",
        "reminder_off": "Отключено",
        "btn_reminder_off": "🚫 Отключить",
        "reminder_saved": "✅ Сохранено.",
        "location_text": "📍 <b>{clinic}</b>\n{address}",
        "location_no_address": "Адрес пока не указан. Обратитесь в клинику.",
        "sos_text": "🆘 <b>Экстренная ситуация</b>\n\nПозвоните напрямую в клинику:\n<b>{phone}</b>",
        "sos_no_phone": "🆘 Экстренный номер пока не указан. Свяжитесь с клиникой другим способом.",
        "btn_call": "☎️ Позвонить",
        "new_appt_choose_doctor": "➕ <b>Новая запись</b>\n\nВыберите врача:",
        "new_appt_no_doctors": "Активных врачей пока нет.",
        "new_appt_choose_date": "🗓 Выберите дату ({doctor}):",
        "new_appt_choose_time": "🕐 Выберите время ({date}):",
        "new_appt_no_slots": "На этот день свободного времени не осталось. Выберите другую дату.",
        "new_appt_confirm": (
            "📋 <b>Подтвердите</b>\n\n"
            "👨‍⚕️ Врач: {doctor}\n"
            "🗓 Дата/время: {when}\n"
            "💵 Цена: {price} UZS"
        ),
        "btn_confirm_book": "✅ Подтвердить",
        "slot_taken": "⚠️ К сожалению, это время уже занято. Выберите другое.",
        "new_appt_done": "✅ Вы успешно записаны!",
        "resch_choose_date": "🔁 <b>Перенос</b> — выберите новую дату:",
        "resch_done": "✅ Запись успешно перенесена!",
        "reminder_message": (
            "🔔 <b>{clinic}</b>\n\n"
            "У вас запись через {label}:\n"
            "👨‍⚕️ Врач: {doctor}\n"
            "🗓 Дата/время: {when}\n\n"
            "Если не сможете прийти, отмените по ссылке:\n{link}"
        ),
        "error_generic": "Произошла ошибка, попробуйте ещё раз",
        "status_labels": {
            "waiting": "⏳ В очереди", "in_progress": "🩺 На приёме", "delayed": "⌛ Задержано",
            "completed": "✅ Завершено", "cancelled": "❌ Отменено", "no_show": "🚫 Не пришёл",
        },
        "weekdays": ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"],
    },
    "en": {
        "welcome_new": "👋 Welcome to the {clinic} reminder bot!\n\nConfirm your phone number to receive appointment reminders.",
        "welcome_back": "👋 Welcome back, {name}!",
        "menu_prompt": "Choose from the menu below 👇",
        "btn_appointments": "📅 My appointments",
        "btn_new_appointment": "➕ Book new appointment",
        "btn_history": "🕘 Appointment history",
        "btn_settings": "⚙️ Settings",
        "btn_relink": "🔄 Re-link phone number",
        "btn_help": "ℹ️ Help",
        "btn_back": "⬅️ Back",
        "btn_share_phone": "📱 Share my phone number",
        "help_text": (
            "ℹ️ <b>Help</b>\n\n"
            "📅 <i>My appointments</i> — all your active appointments.\n"
            "➕ <i>Book new appointment</i> — pick a doctor, date and time.\n"
            "🕘 <i>Appointment history</i> — your past visits.\n"
            "⚙️ <i>Settings</i> — language, reminder timing, mute, address, SOS.\n\n"
            "You'll get an automatic reminder before your visit "
            "(timing is configurable under ⚙️ Settings)."
        ),
        "link_prompt": "Please confirm your phone number to continue 👇",
        "link_wrong_format": "❌ That phone number format looks wrong. Please try again or contact the clinic.",
        "link_not_found": "❌ No patient found with this number. Please send the number registered at the clinic.",
        "link_success": "✅ Number confirmed, {name}! You'll now receive appointment reminders.",
        "unknown_command": "🤔 I didn't recognize that. Send /menu or use the menu below.",
        "need_link_first": "Please confirm your number first",
        "no_active_appointments": "📭 You have no active appointments right now.",
        "active_list_title": "📅 <b>Your active appointments:</b>",
        "appt_not_found": "Appointment not found",
        "invalid_request": "Invalid request",
        "appt_detail": (
            "📋 <b>Appointment details</b>\n\n"
            "👨‍⚕️ Doctor: {doctor}\n"
            "🗓 Date/time: {when}\n"
            "📌 Status: {status}\n"
            "💵 Price: {price} UZS"
        ),
        "btn_cancel_appt": "❌ Cancel",
        "btn_reschedule_appt": "🔁 Reschedule",
        "btn_ics": "📎 Add to calendar (.ics)",
        "cancel_confirm": "⚠️ Are you sure you want to cancel this appointment?",
        "btn_confirm_yes": "✅ Yes, cancel",
        "btn_confirm_no": "🚫 No",
        "already_closed": "This appointment is already closed",
        "cancel_done": "✅ Appointment cancelled.",
        "cancel_done_alert": "Cancelled ✅",
        "history_title": "🕘 <b>Appointment history:</b>",
        "history_empty": "🗂 No history yet.",
        "settings_title": "⚙️ <b>Settings</b>",
        "btn_language": "🌐 Language",
        "btn_reminder_time": "🔔 Reminder timing",
        "btn_mute_on": "🔕 Turn on mute",
        "btn_mute_off": "🔔 Turn off mute",
        "btn_location": "📍 Clinic address",
        "btn_sos": "🆘 SOS — emergency call",
        "language_prompt": "🌐 Choose a language:",
        "language_set": "✅ Language updated.",
        "mute_on_done": "🔕 Automatic reminders are off. You can still use the bot manually.",
        "mute_off_done": "🔔 Automatic reminders are on.",
        "reminder_settings_title": "🔔 <b>Reminder timing</b>\n\nCurrent: 1st — {first}, 2nd — {second}",
        "reminder_pick_first": "When should the first reminder be sent?",
        "reminder_pick_second": "When should the second reminder be sent?",
        "reminder_hours_fmt": "{h}h before",
        "reminder_off": "Off",
        "btn_reminder_off": "🚫 Turn off",
        "reminder_saved": "✅ Saved.",
        "location_text": "📍 <b>{clinic}</b>\n{address}",
        "location_no_address": "No address on file yet. Please contact the clinic.",
        "sos_text": "🆘 <b>Emergency</b>\n\nCall the clinic directly:\n<b>{phone}</b>",
        "sos_no_phone": "🆘 No emergency number on file yet. Please contact the clinic another way.",
        "btn_call": "☎️ Call",
        "new_appt_choose_doctor": "➕ <b>New appointment</b>\n\nChoose a doctor:",
        "new_appt_no_doctors": "No active doctors available right now.",
        "new_appt_choose_date": "🗓 Choose a date ({doctor}):",
        "new_appt_choose_time": "🕐 Choose a time ({date}):",
        "new_appt_no_slots": "No free slots left for this day. Please choose another date.",
        "new_appt_confirm": (
            "📋 <b>Confirm</b>\n\n"
            "👨‍⚕️ Doctor: {doctor}\n"
            "🗓 Date/time: {when}\n"
            "💵 Price: {price} UZS"
        ),
        "btn_confirm_book": "✅ Confirm",
        "slot_taken": "⚠️ Sorry, that slot was just taken. Please pick another one.",
        "new_appt_done": "✅ Your appointment is booked!",
        "resch_choose_date": "🔁 <b>Reschedule</b> — choose a new date:",
        "resch_done": "✅ Appointment rescheduled!",
        "reminder_message": (
            "🔔 <b>{clinic}</b>\n\n"
            "You have an appointment in {label}:\n"
            "👨‍⚕️ Doctor: {doctor}\n"
            "🗓 Date/time: {when}\n\n"
            "If you can't make it, cancel via this link:\n{link}"
        ),
        "error_generic": "Something went wrong, please try again",
        "status_labels": {
            "waiting": "⏳ Waiting", "in_progress": "🩺 In progress", "delayed": "⌛ Delayed",
            "completed": "✅ Completed", "cancelled": "❌ Cancelled", "no_show": "🚫 No-show",
        },
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    },
}


def _lang_of(patient: Optional["models.Patient"]) -> str:
    lang = getattr(patient, "telegram_language", None) if patient else None
    return lang if lang in SUPPORTED_LANGUAGES else "uz"


def t(lang: str, key: str, **kwargs) -> str:
    lang = lang if lang in SUPPORTED_LANGUAGES else "uz"
    template = TXT[lang].get(key, TXT["uz"].get(key, key))
    return template.format(**kwargs) if kwargs else template


# ==============================================
# INLINE MENYULAR
# ==============================================
def _main_menu_keyboard(lang: str) -> dict:
    return {
        "inline_keyboard": [
            [{"text": t(lang, "btn_appointments"), "callback_data": "menu:appointments"}],
            [{"text": t(lang, "btn_new_appointment"), "callback_data": "menu:newappt"}],
            [{"text": t(lang, "btn_history"), "callback_data": "menu:history"}],
            [{"text": t(lang, "btn_settings"), "callback_data": "menu:settings"}],
            [{"text": t(lang, "btn_relink"), "callback_data": "menu:relink"}],
            [{"text": t(lang, "btn_help"), "callback_data": "menu:help"}],
        ]
    }


def _back_button(lang: str, callback_data: str = "menu:main") -> list:
    return [{"text": t(lang, "btn_back"), "callback_data": callback_data}]


def _appointments_list_keyboard(lang: str, appts: list) -> dict:
    rows = []
    for appt in appts:
        when = appt.scheduled_time.strftime("%d.%m %H:%M")
        doctor = appt.doctor.fullname if appt.doctor else "—"
        rows.append([{"text": f"🗓 {when} — {doctor}", "callback_data": f"appt:show:{appt.id}"}])
    rows.append(_back_button(lang))
    return {"inline_keyboard": rows}


def _history_list_keyboard(lang: str, appts: list) -> dict:
    rows = []
    for appt in appts:
        when = appt.scheduled_time.strftime("%d.%m %H:%M")
        doctor = appt.doctor.fullname if appt.doctor else "—"
        rows.append([{"text": f"🗓 {when} — {doctor}", "callback_data": f"hist:show:{appt.id}"}])
    rows.append(_back_button(lang, "menu:main"))
    return {"inline_keyboard": rows}


def _appointment_detail_keyboard(lang: str, appt: "models.Appointment") -> dict:
    rows = []
    if appt.status in ("waiting", "in_progress", "delayed"):
        rows.append([{"text": t(lang, "btn_reschedule_appt"), "callback_data": f"appt:resch_ask:{appt.id}"}])
        rows.append([{"text": t(lang, "btn_cancel_appt"), "callback_data": f"appt:cancel_ask:{appt.id}"}])
    rows.append([{"text": t(lang, "btn_ics"), "callback_data": f"appt:ics:{appt.id}"}])
    rows.append(_back_button(lang, "menu:appointments"))
    return {"inline_keyboard": rows}


def _history_detail_keyboard(lang: str) -> dict:
    return {"inline_keyboard": [_back_button(lang, "menu:history")]}


def _cancel_confirm_keyboard(lang: str, appt_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": t(lang, "btn_confirm_yes"), "callback_data": f"appt:cancel_do:{appt_id}"},
                {"text": t(lang, "btn_confirm_no"), "callback_data": f"appt:show:{appt_id}"},
            ]
        ]
    }


def _settings_menu_keyboard(lang: str, muted: bool) -> dict:
    mute_label = t(lang, "btn_mute_off") if muted else t(lang, "btn_mute_on")
    return {
        "inline_keyboard": [
            [{"text": t(lang, "btn_language"), "callback_data": "set:lang"}],
            [{"text": t(lang, "btn_reminder_time"), "callback_data": "set:remind"}],
            [{"text": mute_label, "callback_data": "set:mute:toggle"}],
            [{"text": t(lang, "btn_location"), "callback_data": "set:location"}],
            [{"text": t(lang, "btn_sos"), "callback_data": "set:sos"}],
            _back_button(lang),
        ]
    }


def _language_keyboard(lang: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "🇺🇿 O'zbekcha", "callback_data": "set:lang:uz"},
                {"text": "🇷🇺 Русский", "callback_data": "set:lang:ru"},
                {"text": "🇬🇧 English", "callback_data": "set:lang:en"},
            ],
            _back_button(lang, "menu:settings"),
        ]
    }


def _reminder_stage_keyboard(lang: str, stage: str, presets: List[int]) -> dict:
    rows = [[{"text": t(lang, "reminder_hours_fmt", h=h), "callback_data": f"set:remind:{stage}:{h}"}] for h in presets]
    rows.append([{"text": t(lang, "btn_reminder_off"), "callback_data": f"set:remind:{stage}:0"}])
    rows.append(_back_button(lang, "set:remind"))
    return {"inline_keyboard": rows}


def _reminder_settings_keyboard(lang: str) -> dict:
    return {
        "inline_keyboard": [
            [{"text": t(lang, "reminder_pick_first"), "callback_data": "set:remind:pick:first"}],
            [{"text": t(lang, "reminder_pick_second"), "callback_data": "set:remind:pick:second"}],
            _back_button(lang, "menu:settings"),
        ]
    }


def _sos_keyboard(lang: str) -> dict:
    rows = []
    if CLINIC_PHONE:
        rows.append([{"text": t(lang, "btn_call"), "url": f"tel:{CLINIC_PHONE}"}])
    rows.append(_back_button(lang, "menu:settings"))
    return {"inline_keyboard": rows}


def _doctor_list_keyboard(lang: str, doctors: list, prefix: str) -> dict:
    rows = []
    for doc in doctors:
        rows.append([{"text": f"👨‍⚕️ {doc.fullname} ({doc.specialty})", "callback_data": f"{prefix}:doc:{doc.id}"}])
    rows.append(_back_button(lang))
    return {"inline_keyboard": rows}


def _date_picker_keyboard(lang: str, entity_id: int, prefix: str, back_cb: str) -> dict:
    rows = []
    today = date.today()
    weekday_labels = TXT[lang]["weekdays"]
    row: list = []
    for i in range(BOOKING_DAYS_AHEAD):
        d = today + timedelta(days=i)
        label = f"{d.strftime('%d.%m')} {weekday_labels[d.weekday()]}"
        row.append({"text": label, "callback_data": f"{prefix}:date:{entity_id}:{d.strftime('%Y%m%d')}"})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(_back_button(lang, back_cb))
    return {"inline_keyboard": rows}


def _time_picker_keyboard(lang: str, entity_id: int, day: date, slots: List[datetime], prefix: str) -> dict:
    rows = []
    row: list = []
    for slot in slots[:MAX_LISTED_SLOTS]:
        row.append({"text": slot.strftime("%H:%M"), "callback_data": f"{prefix}:time:{entity_id}:{int(slot.timestamp())}"})
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(_back_button(lang, f"{prefix}:doc:{entity_id}" if prefix == "nb" else f"appt:resch_ask:{entity_id}"))
    return {"inline_keyboard": rows}


def _confirm_keyboard(lang: str, prefix: str, entity_id: int, ts: int) -> dict:
    return {
        "inline_keyboard": [
            [{"text": t(lang, "btn_confirm_book"), "callback_data": f"{prefix}:confirm:{entity_id}:{ts}"}],
            _back_button(lang, "menu:main"),
        ]
    }


# ==============================================
# XABAR MATNI YORDAMCHILARI
# ==============================================
def _build_reminder_text(lang: str, appt: "models.Appointment", hours: int) -> str:
    doctor_name = f"{appt.doctor.fullname} ({appt.doctor.specialty})" if appt.doctor else "—"
    when = appt.scheduled_time.strftime("%d.%m.%Y %H:%M")
    cancel_link = f"{BASE_URL}/reminders/cancel/{appt.cancel_token}"
    label = t(lang, "reminder_hours_fmt", h=hours)
    return t(lang, "reminder_message", clinic=CLINIC_NAME, label=label, doctor=doctor_name, when=when, link=cancel_link)


def _appointment_detail_text(lang: str, appt: "models.Appointment") -> str:
    doctor_name = f"{appt.doctor.fullname} ({appt.doctor.specialty})" if appt.doctor else "—"
    when = appt.scheduled_time.strftime("%d.%m.%Y %H:%M")
    status = TXT[lang]["status_labels"].get(appt.status, appt.status)
    price = f"{appt.price:,}".replace(",", " ")
    return t(lang, "appt_detail", doctor=doctor_name, when=when, status=status, price=price)


def _build_ics(appt: "models.Appointment", db=None) -> bytes:
    """Minimal .ics (RFC 5545) fayl — DTSTART/DTEND endi UTC (Z-suffiks)
    formatida yoziladi. appt.scheduled_time DBda naive datetime sifatida,
    klinika LOKAL devor-vaqti sifatida saqlanadi (utils/timezone.py'dagi
    izohga qarang), shu sababli avval uni klinika vaqt zonasiga bog'lab,
    keyin UTC ga konvertatsiya qilamiz — turli qurilma/kalendar
    ilovalarida noto'g'ri ko'rsatilishining oldini olish uchun."""
    clinic_tz = get_clinic_timezone(db)
    start_local = appt.scheduled_time.replace(tzinfo=clinic_tz)
    end_local = (appt.scheduled_time + timedelta(minutes=30)).replace(tzinfo=clinic_tz)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)
    doctor_name = appt.doctor.fullname if appt.doctor else "Shifokor"
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{CLINIC_NAME}//Reminder Bot//UZ",
        "BEGIN:VEVENT",
        f"UID:appointment-{appt.id}@{CLINIC_NAME.lower().replace(' ', '')}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{start_utc.strftime('%Y%m%dT%H%M%SZ')}",
        f"DTEND:{end_utc.strftime('%Y%m%dT%H%M%SZ')}",
        f"SUMMARY:{CLINIC_NAME} — {doctor_name}",
        f"DESCRIPTION:Navbat #{appt.id}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


# ==============================================
# NAVBAT/SLOT YORDAMCHILARI (booking va reschedule uchun umumiy)
# ==============================================
def _parse_working_hours(working_hours: Optional[str]) -> tuple:
    if working_hours:
        m = WORKING_HOURS_RE.search(working_hours)
        if m:
            h1, m1, h2, m2 = (int(x) for x in m.groups())
            return (h1, m1), (h2, m2)
    return (9, 0), (18, 0)


def _free_slots_for_day(db, doctor: "models.Doctor", day: date, exclude_appt_id: Optional[int] = None) -> List[datetime]:
    (h1, m1), (h2, m2) = _parse_working_hours(doctor.working_hours)
    day_start = datetime.combine(day, datetime.min.time()).replace(hour=h1, minute=m1)
    day_end = datetime.combine(day, datetime.min.time()).replace(hour=h2, minute=m2)

    booked_q = db.query(models.Appointment.scheduled_time).filter(
        models.Appointment.doctor_id == doctor.id,
        models.Appointment.status.in_(("waiting", "in_progress", "delayed", "completed")),
        models.Appointment.scheduled_time >= day_start,
        models.Appointment.scheduled_time < day_end + timedelta(days=1),
    )
    if exclude_appt_id is not None:
        booked_q = booked_q.filter(models.Appointment.id != exclude_appt_id)
    booked_times = {row[0] for row in booked_q.all()}

    now = now_in_clinic_tz(db)
    min_start = now + timedelta(minutes=15)  # bugungi kunga juda yaqin vaqtga yozilmasin
    slots = []
    cursor = day_start
    step = timedelta(minutes=BOOKING_SLOT_MINUTES)
    while cursor + step <= day_end + timedelta(seconds=1) and cursor < day_end:
        if cursor >= min_start and cursor not in booked_times:
            slots.append(cursor)
        cursor += step
    return slots


# ==============================================
# ESLATMA JOB — APScheduler har POLL_SECONDS'da chaqiradi
# ==============================================
def check_and_send_reminders() -> None:
    if not TELEGRAM_API_BASE:
        return

    db = SessionLocal()
    try:
        now = now_in_clinic_tz(db)
        active_statuses = ("waiting", "in_progress", "delayed")
        horizon = now + timedelta(hours=MAX_REMINDER_HOURS)

        appts = (
            db.query(models.Appointment)
            .join(models.Patient, models.Appointment.patient_id == models.Patient.id)
            .filter(
                models.Appointment.status.in_(active_statuses),
                models.Appointment.scheduled_time >= now,
                models.Appointment.scheduled_time <= horizon,
                models.Patient.telegram_chat_id.isnot(None),
                models.Patient.telegram_muted.is_(False),
            )
            .all()
        )

        for appt in appts:
            patient = appt.patient
            lang = _lang_of(patient)
            stages = (
                ("first", "reminder_sent_24h", patient.reminder_first_hours),
                ("second", "reminder_sent_2h", patient.reminder_second_hours),
            )
            for _stage_name, flag_attr, hours in stages:
                if not hours:
                    continue
                if getattr(appt, flag_attr):
                    continue
                target_time = appt.scheduled_time - timedelta(hours=hours)
                if abs((now - target_time).total_seconds()) <= WINDOW_SLACK.total_seconds():
                    text = _build_reminder_text(lang, appt, hours)
                    sent = send_telegram_message(patient.telegram_chat_id, text)
                    if sent:
                        setattr(appt, flag_attr, True)
                        db.add(appt)
                        logger.info("Eslatma yuborildi: appointment_id=%s stage=%s hours=%s", appt.id, _stage_name, hours)
                    else:
                        logger.warning("Eslatma yuborilmadi: appointment_id=%s stage=%s", appt.id, _stage_name)

        db.commit()
    except Exception:
        db.rollback()
        logger.exception("check_and_send_reminders xato bilan tugadi")
    finally:
        db.close()


# ==============================================
# YORDAMCHI: bog'langan bemorni topish
# ==============================================
def _get_linked_patient(db, chat_id) -> Optional["models.Patient"]:
    return db.query(models.Patient).filter(models.Patient.telegram_chat_id == str(chat_id)).first()


def _active_appointments(db, patient_id: int) -> list:
    return (
        db.query(models.Appointment)
        .filter(
            models.Appointment.patient_id == patient_id,
            models.Appointment.status.in_(("waiting", "in_progress", "delayed")),
            models.Appointment.scheduled_time >= now_in_clinic_tz(db),
        )
        .order_by(models.Appointment.scheduled_time.asc())
        .limit(MAX_LISTED_APPOINTMENTS)
        .all()
    )


def _history_appointments(db, patient_id: int) -> list:
    return (
        db.query(models.Appointment)
        .filter(
            models.Appointment.patient_id == patient_id,
            models.Appointment.status.in_(("completed", "cancelled", "no_show")),
        )
        .order_by(models.Appointment.scheduled_time.desc())
        .limit(MAX_LISTED_APPOINTMENTS)
        .all()
    )


def _send_link_prompt(chat_id, lang: str = "uz") -> None:
    send_telegram_message(
        str(chat_id),
        t(lang, "link_prompt"),
        reply_markup=_request_contact_keyboard(lang),
    )


# ==============================================
# XABAR (message) ISHLOVCHISI — /start, /menu, /help, kontakt
# ==============================================
def _handle_message(db, message: dict) -> None:
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return

    text = (message.get("text") or "").strip()
    contact = message.get("contact")
    patient = _get_linked_patient(db, chat_id)
    lang = _lang_of(patient)

    if text in ("/start", "/menu"):
        if patient:
            send_telegram_message(str(chat_id), t(lang, "welcome_back", name=patient.fullname))
            send_telegram_message(str(chat_id), t(lang, "menu_prompt"), reply_markup=_main_menu_keyboard(lang))
        else:
            send_telegram_message(str(chat_id), t(lang, "welcome_new", clinic=CLINIC_NAME))
            _send_link_prompt(chat_id, lang)
        return

    if text == "/help":
        send_telegram_message(str(chat_id), t(lang, "help_text"), reply_markup=_main_menu_keyboard(lang))
        return

    if contact and contact.get("phone_number"):
        _link_patient_by_contact(db, chat_id, contact, lang)
        return

    # ── Cheklov: tushunarsiz xabarga tushunarli javob (xatolikni oldini olish) ──
    send_telegram_message(str(chat_id), t(lang, "unknown_command"), reply_markup=_main_menu_keyboard(lang))


def _link_patient_by_contact(db, chat_id, contact: dict, lang: str) -> None:
    phone_raw = (contact.get("phone_number") or "").strip()

    # ── Validatsiya: raqam formatini tekshirish (xatolikning oldini olish) ──
    digits_only = re.sub(r"[^\d+]", "", phone_raw)
    if not PHONE_DIGITS_RE.match(digits_only):
        send_telegram_message(str(chat_id), t(lang, "link_wrong_format"))
        return

    phone_norm = digits_only if digits_only.startswith("+") else f"+{digits_only}"
    bidx = blind_index(phone_norm)
    patient = db.query(models.Patient).filter(models.Patient.phone_bidx == bidx).first()
    if not patient:
        bidx_alt = blind_index(digits_only)
        patient = db.query(models.Patient).filter(models.Patient.phone_bidx == bidx_alt).first()

    if not patient:
        send_telegram_message(str(chat_id), t(lang, "link_not_found"))
        return

    # ── Cheklov: bu chat allaqachon boshqa bemorga bog'langan bo'lsa, avval bo'shatamiz ──
    other = _get_linked_patient(db, chat_id)
    if other and other.id != patient.id:
        other.telegram_chat_id = None
        db.add(other)

    patient.telegram_chat_id = str(chat_id)
    db.add(patient)
    db.commit()

    real_lang = _lang_of(patient)
    send_telegram_message(
        str(chat_id),
        t(real_lang, "link_success", name=patient.fullname),
        reply_markup=_remove_keyboard(),
    )
    send_telegram_message(str(chat_id), t(real_lang, "menu_prompt"), reply_markup=_main_menu_keyboard(real_lang))
    log_action(db, None, "patient.telegram_link", "Patient", patient.id, f"chat_id={chat_id}")


# ==============================================
# CALLBACK QUERY (inline tugmalar) ISHLOVCHISI
# ==============================================
def _handle_callback_query(db, callback_query: dict) -> None:
    cq_id = callback_query.get("id")
    data = (callback_query.get("data") or "").strip()
    msg = callback_query.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    message_id = msg.get("id") or msg.get("message_id")

    if chat_id is None or message_id is None:
        _answer_callback(cq_id)
        return

    patient = _get_linked_patient(db, chat_id)
    lang = _lang_of(patient)

    try:
        parts = data.split(":")
        action = parts[0]

        if action == "menu":
            _handle_menu_callback(db, chat_id, message_id, patient, lang, parts, cq_id)
            return

        if action == "appt":
            _handle_appt_callback(db, chat_id, message_id, patient, lang, parts, cq_id)
            return

        if action == "hist":
            _handle_history_callback(db, chat_id, message_id, patient, lang, parts, cq_id)
            return

        if action == "set":
            _handle_settings_callback(db, chat_id, message_id, patient, lang, parts, cq_id)
            return

        if action in ("nb", "rs"):
            _handle_booking_callback(db, chat_id, message_id, patient, lang, action, parts, cq_id)
            return

        _answer_callback(cq_id)
    except Exception:
        db.rollback()
        logger.exception("_handle_callback_query xato bilan tugadi (data=%s)", data)
        _answer_callback(cq_id, t(lang, "error_generic"), show_alert=True)


def _handle_menu_callback(db, chat_id, message_id, patient, lang, parts, cq_id) -> None:
    if len(parts) < 2:
        _answer_callback(cq_id)
        return
    sub = parts[1]
    if sub == "main":
        _edit_message(chat_id, message_id, t(lang, "menu_prompt"), _main_menu_keyboard(lang))
    elif sub == "help":
        _edit_message(chat_id, message_id, t(lang, "help_text"), {"inline_keyboard": [_back_button(lang)]})
    elif sub == "relink":
        _answer_callback(cq_id)
        _send_link_prompt(chat_id, lang)
        return
    elif sub == "appointments":
        _show_appointments_list(db, chat_id, message_id, patient, lang)
    elif sub == "history":
        _show_history_list(db, chat_id, message_id, patient, lang)
    elif sub == "settings":
        _show_settings_menu(chat_id, message_id, patient, lang)
    elif sub == "newappt":
        _show_doctor_picker(db, chat_id, message_id, lang, "nb")
    _answer_callback(cq_id)


def _show_appointments_list(db, chat_id, message_id, patient, lang) -> None:
    if not patient:
        _edit_message(chat_id, message_id, t(lang, "need_link_first"), {"inline_keyboard": [_back_button(lang)]})
        return
    appts = _active_appointments(db, patient.id)
    if not appts:
        _edit_message(chat_id, message_id, t(lang, "no_active_appointments"), {"inline_keyboard": [_back_button(lang)]})
        return
    _edit_message(chat_id, message_id, t(lang, "active_list_title"), _appointments_list_keyboard(lang, appts))


def _show_history_list(db, chat_id, message_id, patient, lang) -> None:
    if not patient:
        _edit_message(chat_id, message_id, t(lang, "need_link_first"), {"inline_keyboard": [_back_button(lang)]})
        return
    appts = _history_appointments(db, patient.id)
    if not appts:
        _edit_message(chat_id, message_id, t(lang, "history_empty"), {"inline_keyboard": [_back_button(lang)]})
        return
    _edit_message(chat_id, message_id, t(lang, "history_title"), _history_list_keyboard(lang, appts))


def _show_settings_menu(chat_id, message_id, patient, lang) -> None:
    muted = bool(patient.telegram_muted) if patient else False
    _edit_message(chat_id, message_id, t(lang, "settings_title"), _settings_menu_keyboard(lang, muted))


def _show_doctor_picker(db, chat_id, message_id, lang, prefix) -> None:
    doctors = (
        db.query(models.Doctor)
        .filter(models.Doctor.is_active.is_(True))
        .order_by(models.Doctor.fullname.asc())
        .limit(MAX_LISTED_DOCTORS)
        .all()
    )
    if not doctors:
        _edit_message(chat_id, message_id, t(lang, "new_appt_no_doctors"), {"inline_keyboard": [_back_button(lang)]})
        return
    _edit_message(chat_id, message_id, t(lang, "new_appt_choose_doctor"), _doctor_list_keyboard(lang, doctors, prefix))


def _handle_appt_callback(db, chat_id, message_id, patient, lang, parts, cq_id) -> None:
    if len(parts) < 3:
        _answer_callback(cq_id)
        return
    sub, appt_id_raw = parts[1], parts[2]
    try:
        appt_id = int(appt_id_raw)
    except ValueError:
        _answer_callback(cq_id, t(lang, "invalid_request"), show_alert=True)
        return

    if not patient:
        _answer_callback(cq_id, t(lang, "need_link_first"), show_alert=True)
        _send_link_prompt(chat_id, lang)
        return

    # ── XAVFSIZLIK: navbat FAQAT shu bemorga tegishli bo'lsa ko'rsatiladi/boshqariladi ──
    appt = (
        db.query(models.Appointment)
        .filter(models.Appointment.id == appt_id, models.Appointment.patient_id == patient.id)
        .first()
    )
    if not appt:
        _answer_callback(cq_id, t(lang, "appt_not_found"), show_alert=True)
        _show_appointments_list(db, chat_id, message_id, patient, lang)
        return

    if sub == "show":
        _edit_message(chat_id, message_id, _appointment_detail_text(lang, appt), _appointment_detail_keyboard(lang, appt))
        _answer_callback(cq_id)
        return

    if sub == "ics":
        ics_bytes = _build_ics(appt, db)
        sent = _send_document(chat_id, f"appointment_{appt.id}.ics", ics_bytes, caption=CLINIC_NAME)
        if not sent:
            _answer_callback(cq_id, t(lang, "error_generic"), show_alert=True)
        else:
            _answer_callback(cq_id)
        return

    if sub == "cancel_ask":
        if appt.status not in ("waiting", "in_progress", "delayed"):
            _answer_callback(cq_id, t(lang, "already_closed"), show_alert=True)
            return
        _edit_message(chat_id, message_id, t(lang, "cancel_confirm"), _cancel_confirm_keyboard(lang, appt.id))
        _answer_callback(cq_id)
        return

    if sub == "cancel_do":
        if appt.status not in ("waiting", "in_progress", "delayed"):
            _answer_callback(cq_id, t(lang, "already_closed"), show_alert=True)
            _edit_message(chat_id, message_id, _appointment_detail_text(lang, appt), _appointment_detail_keyboard(lang, appt))
            return
        appt.status = "cancelled"
        appt.cancel_reason = "Bemor tomonidan Telegram bot orqali bekor qilindi"
        db.add(appt)
        db.commit()
        log_action(db, None, "appointment.cancel_via_telegram_bot", "Appointment", appt.id, f"telegram_chat_id={chat_id}")
        _edit_message(chat_id, message_id, t(lang, "cancel_done"), {"inline_keyboard": [_back_button(lang, "menu:appointments")]})
        _answer_callback(cq_id, t(lang, "cancel_done_alert"))
        return

    if sub == "resch_ask":
        if appt.status not in ("waiting", "in_progress", "delayed"):
            _answer_callback(cq_id, t(lang, "already_closed"), show_alert=True)
            return
        _edit_message(
            chat_id, message_id,
            t(lang, "resch_choose_date"),
            _date_picker_keyboard(lang, appt.id, "rs", back_cb=f"appt:show:{appt.id}"),
        )
        _answer_callback(cq_id)
        return

    _answer_callback(cq_id)


def _handle_history_callback(db, chat_id, message_id, patient, lang, parts, cq_id) -> None:
    if len(parts) < 3 or parts[1] != "show":
        _answer_callback(cq_id)
        return
    if not patient:
        _answer_callback(cq_id, t(lang, "need_link_first"), show_alert=True)
        return
    try:
        appt_id = int(parts[2])
    except ValueError:
        _answer_callback(cq_id, t(lang, "invalid_request"), show_alert=True)
        return
    appt = (
        db.query(models.Appointment)
        .filter(models.Appointment.id == appt_id, models.Appointment.patient_id == patient.id)
        .first()
    )
    if not appt:
        _answer_callback(cq_id, t(lang, "appt_not_found"), show_alert=True)
        return
    _edit_message(chat_id, message_id, _appointment_detail_text(lang, appt), _history_detail_keyboard(lang))
    _answer_callback(cq_id)


def _handle_settings_callback(db, chat_id, message_id, patient, lang, parts, cq_id) -> None:
    if len(parts) < 2:
        _answer_callback(cq_id)
        return
    sub = parts[1]

    if sub == "lang" and len(parts) == 2:
        _edit_message(chat_id, message_id, t(lang, "language_prompt"), _language_keyboard(lang))
        _answer_callback(cq_id)
        return

    if sub == "lang" and len(parts) == 3:
        new_lang = parts[2]
        if new_lang not in SUPPORTED_LANGUAGES or not patient:
            _answer_callback(cq_id, t(lang, "invalid_request"), show_alert=True)
            return
        patient.telegram_language = new_lang
        db.add(patient)
        db.commit()
        log_action(db, None, "patient.telegram_language_change", "Patient", patient.id, f"lang={new_lang}")
        _edit_message(chat_id, message_id, t(new_lang, "settings_title"), _settings_menu_keyboard(new_lang, bool(patient.telegram_muted)))
        _answer_callback(cq_id, t(new_lang, "language_set"))
        return

    if sub == "mute" and len(parts) == 3 and parts[2] == "toggle":
        if not patient:
            _answer_callback(cq_id, t(lang, "need_link_first"), show_alert=True)
            return
        patient.telegram_muted = not bool(patient.telegram_muted)
        db.add(patient)
        db.commit()
        log_action(db, None, "patient.telegram_mute_toggle", "Patient", patient.id, f"muted={patient.telegram_muted}")
        _edit_message(chat_id, message_id, t(lang, "settings_title"), _settings_menu_keyboard(lang, bool(patient.telegram_muted)))
        alert = t(lang, "mute_on_done") if patient.telegram_muted else t(lang, "mute_off_done")
        _answer_callback(cq_id, alert, show_alert=True)
        return

    if sub == "remind" and len(parts) == 2:
        if not patient:
            _answer_callback(cq_id, t(lang, "need_link_first"), show_alert=True)
            return
        first_label = t(lang, "reminder_hours_fmt", h=patient.reminder_first_hours) if patient.reminder_first_hours else t(lang, "reminder_off")
        second_label = t(lang, "reminder_hours_fmt", h=patient.reminder_second_hours) if patient.reminder_second_hours else t(lang, "reminder_off")
        _edit_message(
            chat_id, message_id,
            t(lang, "reminder_settings_title", first=first_label, second=second_label),
            _reminder_settings_keyboard(lang),
        )
        _answer_callback(cq_id)
        return

    if sub == "remind" and len(parts) == 4 and parts[2] == "pick":
        stage = parts[3]
        presets = REMINDER_FIRST_PRESETS if stage == "first" else REMINDER_SECOND_PRESETS
        prompt_key = "reminder_pick_first" if stage == "first" else "reminder_pick_second"
        _edit_message(chat_id, message_id, t(lang, prompt_key), _reminder_stage_keyboard(lang, stage, presets))
        _answer_callback(cq_id)
        return

    if sub == "remind" and len(parts) == 4 and parts[2] in ("first", "second"):
        if not patient:
            _answer_callback(cq_id, t(lang, "need_link_first"), show_alert=True)
            return
        stage = parts[2]
        try:
            hours = int(parts[3])
        except ValueError:
            _answer_callback(cq_id, t(lang, "invalid_request"), show_alert=True)
            return
        if stage == "first":
            patient.reminder_first_hours = hours or None
        else:
            patient.reminder_second_hours = hours or None
        db.add(patient)
        db.commit()
        log_action(db, None, f"patient.telegram_reminder_{stage}_change", "Patient", patient.id, f"hours={hours}")
        first_label = t(lang, "reminder_hours_fmt", h=patient.reminder_first_hours) if patient.reminder_first_hours else t(lang, "reminder_off")
        second_label = t(lang, "reminder_hours_fmt", h=patient.reminder_second_hours) if patient.reminder_second_hours else t(lang, "reminder_off")
        _edit_message(
            chat_id, message_id,
            t(lang, "reminder_settings_title", first=first_label, second=second_label),
            _reminder_settings_keyboard(lang),
        )
        _answer_callback(cq_id, t(lang, "reminder_saved"))
        return

    if sub == "location":
        if CLINIC_LAT and CLINIC_LON:
            try:
                _send_location(chat_id, float(CLINIC_LAT), float(CLINIC_LON))
            except ValueError:
                pass
        address = CLINIC_ADDRESS or t(lang, "location_no_address")
        _edit_message(chat_id, message_id, t(lang, "location_text", clinic=CLINIC_NAME, address=address), {"inline_keyboard": [_back_button(lang, "menu:settings")]})
        _answer_callback(cq_id)
        return

    if sub == "sos":
        if CLINIC_PHONE:
            _edit_message(chat_id, message_id, t(lang, "sos_text", phone=CLINIC_PHONE), _sos_keyboard(lang))
        else:
            _edit_message(chat_id, message_id, t(lang, "sos_no_phone"), {"inline_keyboard": [_back_button(lang, "menu:settings")]})
        _answer_callback(cq_id)
        return

    _answer_callback(cq_id)


def _handle_booking_callback(db, chat_id, message_id, patient, lang, prefix, parts, cq_id) -> None:
    """prefix == 'nb' (yangi navbat) yoki 'rs' (ko'chirish). Har ikkalasi
    ham bir xil sana/vaqt tanlash oqimidan foydalanadi, faqat 'entity_id'
    ma'nosi farq qiladi: 'nb' uchun doctor_id, 'rs' uchun appointment_id."""
    if len(parts) < 2:
        _answer_callback(cq_id)
        return
    if not patient:
        _answer_callback(cq_id, t(lang, "need_link_first"), show_alert=True)
        _send_link_prompt(chat_id, lang)
        return

    sub = parts[1]

    if sub == "doc" and len(parts) == 3 and prefix == "nb":
        try:
            doctor_id = int(parts[2])
        except ValueError:
            _answer_callback(cq_id, t(lang, "invalid_request"), show_alert=True)
            return
        doctor = db.query(models.Doctor).filter(models.Doctor.id == doctor_id, models.Doctor.is_active.is_(True)).first()
        if not doctor:
            _answer_callback(cq_id, t(lang, "invalid_request"), show_alert=True)
            return
        _edit_message(
            chat_id, message_id,
            t(lang, "new_appt_choose_date", doctor=doctor.fullname),
            _date_picker_keyboard(lang, doctor.id, "nb", back_cb="menu:newappt"),
        )
        _answer_callback(cq_id)
        return

    if sub == "date" and len(parts) == 4:
        try:
            entity_id = int(parts[2])
            day = datetime.strptime(parts[3], "%Y%m%d").date()
        except ValueError:
            _answer_callback(cq_id, t(lang, "invalid_request"), show_alert=True)
            return

        doctor, exclude_appt_id = _resolve_booking_target(db, patient, prefix, entity_id)
        if not doctor:
            _answer_callback(cq_id, t(lang, "invalid_request"), show_alert=True)
            return

        slots = _free_slots_for_day(db, doctor, day, exclude_appt_id=exclude_appt_id)
        if not slots:
            _answer_callback(cq_id, t(lang, "new_appt_no_slots"), show_alert=True)
            return
        _edit_message(
            chat_id, message_id,
            t(lang, "new_appt_choose_time", date=day.strftime("%d.%m.%Y")),
            _time_picker_keyboard(lang, entity_id, day, slots, prefix),
        )
        _answer_callback(cq_id)
        return

    if sub == "time" and len(parts) == 4:
        try:
            entity_id = int(parts[2])
            ts = int(parts[3])
        except ValueError:
            _answer_callback(cq_id, t(lang, "invalid_request"), show_alert=True)
            return
        doctor, exclude_appt_id = _resolve_booking_target(db, patient, prefix, entity_id)
        if not doctor:
            _answer_callback(cq_id, t(lang, "invalid_request"), show_alert=True)
            return
        when = datetime.fromtimestamp(ts)
        if _slot_collision(db, doctor.id, when, exclude_appt_id):
            _answer_callback(cq_id, t(lang, "slot_taken"), show_alert=True)
            return
        price = f"{doctor.consultation_price:,}".replace(",", " ")
        _edit_message(
            chat_id, message_id,
            t(lang, "new_appt_confirm", doctor=doctor.fullname, when=when.strftime("%d.%m.%Y %H:%M"), price=price),
            _confirm_keyboard(lang, prefix, entity_id, ts),
        )
        _answer_callback(cq_id)
        return

    if sub == "confirm" and len(parts) == 4:
        try:
            entity_id = int(parts[2])
            ts = int(parts[3])
        except ValueError:
            _answer_callback(cq_id, t(lang, "invalid_request"), show_alert=True)
            return
        doctor, exclude_appt_id = _resolve_booking_target(db, patient, prefix, entity_id)
        if not doctor:
            _answer_callback(cq_id, t(lang, "invalid_request"), show_alert=True)
            return
        when = datetime.fromtimestamp(ts)

        # ── Poyga sharoiti (race condition) himoyasi: tasdiqlash paytida yana tekshiramiz ──
        if _slot_collision(db, doctor.id, when, exclude_appt_id):
            _answer_callback(cq_id, t(lang, "slot_taken"), show_alert=True)
            return

        if prefix == "nb":
            new_appt = models.Appointment(
                patient_id=patient.id, doctor_id=doctor.id, scheduled_time=when,
                status="waiting", price=doctor.consultation_price,
            )
            db.add(new_appt)
            db.commit()
            db.refresh(new_appt)
            log_action(
                db, None, "appointment.book_via_telegram_bot", "Appointment", new_appt.id,
                f"patient_id={patient.id}, doctor_id={doctor.id}, scheduled_time={when}, chat_id={chat_id}",
            )
            _edit_message(
                chat_id, message_id, t(lang, "new_appt_done"),
                {"inline_keyboard": [
                    [{"text": t(lang, "btn_ics"), "callback_data": f"appt:ics:{new_appt.id}"}],
                    _back_button(lang),
                ]},
            )
        else:  # reschedule
            appt = (
                db.query(models.Appointment)
                .filter(models.Appointment.id == entity_id, models.Appointment.patient_id == patient.id)
                .first()
            )
            if not appt or appt.status not in ("waiting", "in_progress", "delayed"):
                _answer_callback(cq_id, t(lang, "already_closed"), show_alert=True)
                return
            appt.scheduled_time = when
            appt.reminder_sent_24h = False
            appt.reminder_sent_2h = False
            db.add(appt)
            db.commit()
            log_action(db, None, "appointment.reschedule_via_telegram_bot", "Appointment", appt.id, f"new_time={when}, chat_id={chat_id}")
            _edit_message(
                chat_id, message_id, t(lang, "resch_done"),
                {"inline_keyboard": [
                    [{"text": t(lang, "btn_ics"), "callback_data": f"appt:ics:{appt.id}"}],
                    _back_button(lang, "menu:appointments"),
                ]},
            )
        _answer_callback(cq_id)
        return

    _answer_callback(cq_id)


def _resolve_booking_target(db, patient, prefix: str, entity_id: int):
    """Qaytaradi: (doctor, exclude_appt_id). 'nb' uchun entity_id shifokor
    id'si, exclude_appt_id yo'q. 'rs' uchun entity_id navbat id'si — o'sha
    navbatning shifokori qaytariladi va shu navbatning o'zi kolliziya
    tekshiruvidan chiqarib tashlanadi."""
    if prefix == "nb":
        doctor = db.query(models.Doctor).filter(models.Doctor.id == entity_id, models.Doctor.is_active.is_(True)).first()
        return doctor, None
    appt = (
        db.query(models.Appointment)
        .filter(models.Appointment.id == entity_id, models.Appointment.patient_id == patient.id)
        .first()
    )
    if not appt:
        return None, None
    return appt.doctor, appt.id


def _slot_collision(db, doctor_id: int, when: datetime, exclude_appt_id: Optional[int]) -> bool:
    q = db.query(models.Appointment.id).filter(
        models.Appointment.doctor_id == doctor_id,
        models.Appointment.scheduled_time == when,
        models.Appointment.status.in_(("waiting", "in_progress", "delayed", "completed")),
    )
    if exclude_appt_id is not None:
        q = q.filter(models.Appointment.id != exclude_appt_id)
    return db.query(q.exists()).scalar()


# ==============================================
# UPDATE MARSHRUTLOVCHISI
# ==============================================
def _handle_telegram_update(update: dict) -> None:
    db = SessionLocal()
    try:
        if "callback_query" in update:
            _handle_callback_query(db, update["callback_query"])
            return
        if "message" in update:
            _handle_message(db, update["message"])
            return
    except Exception:
        db.rollback()
        logger.exception("_handle_telegram_update xato bilan tugadi")
    finally:
        db.close()


def _telegram_polling_loop() -> None:
    logger.info("Telegram polling loop ishga tushdi (long-polling, getUpdates).")
    offset = None
    while not _stop_polling.is_set():
        params = {"timeout": 30}
        if offset is not None:
            params["offset"] = offset
        result = _telegram_get("getUpdates", params, timeout=35.0)
        if not result or not result.get("ok"):
            time.sleep(3)
            continue
        for update in result.get("result", []):
            offset = update["update_id"] + 1
            try:
                _handle_telegram_update(update)
            except Exception:
                logger.exception("Update qayta ishlanmadi: %s", update.get("update_id"))


# ==============================================
# ISHGA TUSHIRISH — main.py shundan chaqiradi
# ==============================================
def start_reminder_service() -> None:
    global _scheduler, _polling_thread

    if not TELEGRAM_BOT_TOKEN:
        logger.warning(
            "TELEGRAM_BOT_TOKEN o'rnatilmagan — Telegram eslatma tizimi ISHGA TUSHIRILMADI. "
            "Yoqish uchun .env fayliga TELEGRAM_BOT_TOKEN qo'shing."
        )
        return

    if _scheduler is not None:
        return  # allaqachon ishga tushirilgan (masalan --reload restart)

    if not _acquire_singleton_lock():
        logger.info(
            "Bu worker'da Telegram bot ishga tushirilmadi — boshqa worker "
            "allaqachon uni boshqarmoqda (ko'p-worker rejimida normal holat)."
        )
        return

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        check_and_send_reminders,
        trigger=IntervalTrigger(seconds=POLL_SECONDS),
        id="appointment_reminders",
        replace_existing=True,
        next_run_time=datetime.now(),  # ilk tekshiruv darhol
    )
    _scheduler.start()
    logger.info("APScheduler ishga tushdi: eslatma job'i har %s soniyada.", POLL_SECONDS)

    _polling_thread = threading.Thread(target=_telegram_polling_loop, daemon=True)
    _polling_thread.start()


def stop_reminder_service() -> None:
    _stop_polling.set()
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
