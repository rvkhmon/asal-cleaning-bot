
# ASAL Cleaning Bot — ULTIMATE

Что умеет:
- 📋 План на день с карточками и кнопками (✅/↩️, 🔁 тип, 📝 комментарий)
- 👤 Роли и права: только назначенная горничная или админ может менять статус
- 🙋‍♀️ «Мои номера»: `/my` показывает только задачи для горничной
- 📊 Отчёт: `/report` (с процентом) + автоотчёт каждый день в указанное время; бот пытается закрепить сообщение
- ⤴️ Перенос неубранных на следующий день (команда `/carryover` — при желании можно сделать авто)
- ⬇️ Экспорт `/export_csv` и `/export_xlsx`
- 🌍 Настройка часового пояса `/set_tz`
- 🧾 Загрузка плана `/upload_plan` (CSV: room_no,maid,cleaning_type)

## Развёртывание (рекомендовано: Render — просто и стабильно)
1) Создайте аккаунт render.com → New **Background Worker** → Python.
2) Загрузите ZIP из этого проекта или подключите Git-репозиторий.
3) Build Command: `pip install -r requirements.txt`  
   Start Command: `python main.py`
4) Env Vars:
   - `BOT_TOKEN` — токен бота
   - `TIMEZONE` — `Asia/Tashkent`
   - `ADMIN_IDS` — ID админов через запятую (узнать ID можно у @userinfobot)
   - `REPORT_CHAT_ID` — ID вашей группы (обычно отрицательное число)
   - `REPORT_TIME` — `18:00`
   - `AUTOCARRYOVER` — `true` / `false`
5) Добавьте бота в группу, дайте право закреплять сообщения.

## CSV формат
room_no,maid,cleaning_type
101,Севара,Полная
102,Гульноз,Текущая

## Полезные команды
/start, /plan, /my, /report, /upload_plan, /resetday, /export_csv, /export_xlsx, /set_tz <TZ>, /iam <Имя>

