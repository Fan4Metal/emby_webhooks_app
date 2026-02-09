from contextlib import asynccontextmanager
from datetime import datetime

import aiosqlite
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


# Инициализация БД
async def init_db():
    async with aiosqlite.connect("webhooks.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS webhooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                event TEXT,
                date TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS playback_state (
                play_session_id TEXT PRIMARY KEY,
                last_event TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


# Приём вебхуков от Emby
@app.post("/emby/webhook")
async def emby_webhook(request: Request):
    data = await request.json()

    # Извлекаем данные
    # title = data.get("Title", "Нет Title")
    server_name = data.get("Server", {}).get("Name", "Неизвестный сервер")
    user_name = data.get("User", {}).get("Name", "Неизвестный пользователь")
    event = data.get("Event", "Нет Event")
    item_name = data.get("Item", {}).get("Name", "Неизвестный контент")
    year = data.get("Item", {}).get("ProductionYear", "")
    # tmdb_id = data.get("Item", {}).get("ProviderIds", {}).get("Tmdb", "")
    device_name = data.get("Session", {}).get("DeviceName", "Неизвестное устройство")
    date = data.get("Date", "")

    # Дедупликация: ключ просмотра (лучше всего PlaySessionId)
    play_session_id = (data.get("PlaybackInfo") or {}).get("PlaySessionId")
    session_id = (data.get("Session") or {}).get("Id")
    dedupe_key = play_session_id or (f"sess:{session_id}" if session_id else None)

    # Сопоставление событий с действиями
    event_actions = {
        "playback.start": "начал просмотр",
        "playback.stop": "остановил просмотр",
        "playback.pause": "поставил на паузу",
        "playback.unpause": "возобновил просмотр",
        "system.notificationtest": "тестовое уведомление",
    }
    action = event_actions.get(event, event)

    # Формируем сообщение
    if event in event_actions:
        if event == "system.notificationtest":
            message = f"{server_name}: {action}"
        else:
            message = f"{server_name}: {user_name} {action} «{item_name} ({year})» на {device_name}"
    else:
        message = f"{server_name}: User: {user_name}, Event: {event}, Item: {item_name} ({year}), Device: {device_name}"

    # Преобразуем дату
    pretty_date = date
    try:
        dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
        pretty_date = dt.strftime("%d.%m.%Y %H:%M:%S")
    except Exception:
        pass

    # Сохраняем в БД (с дедупликацией для playback.* событий)
    dedupe_events = {"playback.start", "playback.stop", "playback.pause", "playback.unpause"}

    async with aiosqlite.connect("webhooks.db") as db:
        # 1) Если это одно из playback-событий и есть ключ сессии — пишем только при смене состояния
        if event in dedupe_events and dedupe_key:
            async with db.execute(
                "SELECT last_event FROM playback_state WHERE play_session_id=?",
                (dedupe_key,),
            ) as cursor:
                row = await cursor.fetchone()

            if row and row[0] == event:
                # Дубликат — игнорируем
                return {"status": "ok", "deduped": True}

            now = datetime.utcnow().isoformat()
            await db.execute(
                """
                INSERT INTO playback_state(play_session_id, last_event, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(play_session_id) DO UPDATE SET
                  last_event=excluded.last_event,
                  updated_at=excluded.updated_at
                """,
                (dedupe_key, event, now),
            )

        # 2) Логируем событие
        await db.execute(
            "INSERT INTO webhooks (title, event, date) VALUES (?, ?, ?)",
            (message, event, pretty_date),
        )

        # 3) После stop можно сбросить state, чтобы следующий просмотр начинался "с нуля"
        if event == "playback.stop" and dedupe_key:
            await db.execute("DELETE FROM playback_state WHERE play_session_id=?", (dedupe_key,))

        await db.commit()

    return {"status": "ok"}


# Главная страница
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# API для получения последних данных
@app.get("/data")
async def get_data():
    async with aiosqlite.connect("webhooks.db") as db:
        async with db.execute("SELECT title, event, date FROM webhooks ORDER BY id DESC LIMIT 50") as cursor:
            rows = await cursor.fetchall()

    return JSONResponse([{"title": r[0], "event": r[1], "date": r[2]} for r in rows])


# Очистка логов
@app.post("/clear")
async def clear_logs():
    async with aiosqlite.connect("webhooks.db") as db:
        await db.execute("DELETE FROM webhooks")
        await db.execute("DELETE FROM playback_state")
        await db.commit()
    return RedirectResponse("/", status_code=303)
