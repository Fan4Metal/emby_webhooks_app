from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import aiosqlite
from datetime import datetime, timedelta

app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


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
        await db.commit()


@app.on_event("startup")
async def startup():
    await init_db()


# Приём вебхуков от Emby
@app.post("/emby/webhook")
async def emby_webhook(request: Request):
    data = await request.json()
    title = data.get("Title", "Нет Title")
    event = data.get("Event", "Нет Event")
    date = data.get("Date", "")

    # Преобразуем дату в человекочитаемый формат
    pretty_date = date
    try:
        dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
        dt_local = dt + timedelta(hours=3) 
        pretty_date = dt_local.strftime("%d.%m.%Y %H:%M:%S")
    except Exception:
        pass

    async with aiosqlite.connect("webhooks.db") as db:
        await db.execute(
            "INSERT INTO webhooks (title, event, date) VALUES (?, ?, ?)",
            (title, event, pretty_date),
        )
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
        async with db.execute(
            "SELECT title, event, date FROM webhooks ORDER BY id DESC LIMIT 50"
        ) as cursor:
            rows = await cursor.fetchall()

    return JSONResponse([{"title": r[0], "event": r[1], "date": r[2]} for r in rows])


# Очистка логов
@app.post("/clear")
async def clear_logs():
    async with aiosqlite.connect("webhooks.db") as db:
        await db.execute("DELETE FROM webhooks")
        await db.commit()
    return RedirectResponse("/", status_code=303)
