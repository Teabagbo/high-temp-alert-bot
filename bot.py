import asyncio
import logging
import sqlite3
import os
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import aiohttp
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ==================== CONFIGURATION ====================
API_TOKEN = os.getenv("BOT_TOKEN")
if not API_TOKEN:
    # Fallback for local testing if env var isn't set, 
    # BUT on Render you must set the Environment Variable.
    print("Warning: BOT_TOKEN is not set!")

USER_AGENT = "Personal High Temp Alert Bot (socialteabag@gmail.com)"

CITIES = {
    "Miami": "KMIA",
    "Los Angeles": "KLAX",
    "NYC": "KNYC",
    "Chicago": "KMDW",
    "Austin": "KAUS",
    "Denver": "KDEN",
    "Philadelphia": "KPHL",
}

COORDS = {
    "KMIA": (25.7617, -80.1918),
    "KLAX": (33.9416, -118.4085),
    "KNYC": (40.7789, -73.9692),
    "KMDW": (41.7860, -87.7522),
    "KAUS": (30.1945, -97.6699),
    "KDEN": (39.8561, -104.6737),
    "KPHL": (39.8733, -75.2268),
}

DEFAULT_THRESHOLD = 90  # °F

# ==================== SETUP ====================

logging.basicConfig(level=logging.INFO)
# Initialize bot only if token exists to avoid immediate crash on import
bot = Bot(token=API_TOKEN) if API_TOKEN else None
dp = Dispatcher()

# Check_same_thread=False allows async access
conn = sqlite3.connect("alerts.db", check_same_thread=False)

def init_db():
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS thresholds (
        chat_id INTEGER,
        city TEXT,
        threshold INTEGER,
        PRIMARY KEY (chat_id, city)
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS last_alert (
        city TEXT,
        date TEXT,
        PRIMARY KEY (city, date)
    )
    """)
    conn.commit()
    cursor.close()

init_db()

headers = {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}

# ==================== WEATHER LOGIC ====================

async def get_current_high(station: str) -> int | None:
    url = f"https://api.weather.gov/stations/{station}/observations/latest"
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logging.error(f"Error fetching current temp for {station}: {resp.status}")
                    return None
                data = await resp.json()
                if "properties" not in data or "temperature" not in data["properties"]:
                    return None
                
                temp_c = data["properties"]["temperature"]["value"]
                if temp_c is None:
                    return None
                return round(temp_c * 9/5 + 32)
    except Exception as e:
        logging.error(f"Exception in get_current_high: {e}")
        return None

async def get_forecast_high(lat: float, lon: float) -> int | None:
    point_url = f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}"
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(point_url) as resp:
                if resp.status != 200:
                    return None
                point_data = await resp.json()
                forecast_url = point_data["properties"]["forecast"]

            async with session.get(forecast_url) as resp:
                if resp.status != 200:
                    return None
                forecast_data = await resp.json()
                periods = forecast_data["properties"]["periods"]
                
                today = datetime.now(timezone.utc).date()
                for period in periods:
                    start_time = datetime.fromisoformat(period["startTime"])
                    if start_time.date() == datetime.now().date() and period["isDaytime"]:
                        return period["temperature"]
        return None
    except Exception as e:
        logging.error(f"Forecast error: {e}")
        return None

async def check_temperatures():
    logging.info("Running scheduled temperature check...")
    cursor = conn.cursor()
    try:
        chat_ids = [row[0] for row in cursor.execute("SELECT DISTINCT chat_id FROM thresholds").fetchall()]
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        for city, station in CITIES.items():
            current_temp_f = await get_current_high(station)
            if current_temp_f is None:
                continue

            # Check global alert for city/date to prevent spamming repeats
            cursor.execute("SELECT 1 FROM last_alert WHERE city = ? AND date = ?", (city, today_str))
            if cursor.fetchone():
                continue 

            lat, lon = COORDS[station]
            forecast_high = await get_forecast_high(lat, lon)
            forecast_text = f"{forecast_high}°F" if forecast_high is not None else "unavailable"

            message = (
                f"🌡️ <b>High Temperature Alert</b> for <b>{city}</b>!\n\n"
                f"Current temperature: <b>{current_temp_f}°F</b>\n"
                f"Today's predicted high: <b>{forecast_text}</b>\n"
                f"Stay safe! 🥵"
            )

            alert_count = 0
            user_cursor = conn.cursor()
            users_monitoring = user_cursor.execute("SELECT chat_id, threshold FROM thresholds WHERE city = ?", (city,)).fetchall()
            
            for uid, user_thresh in users_monitoring:
                if current_temp_f >= user_thresh:
                    try:
                        await bot.send_message(uid, message, parse_mode="HTML")
                        alert_count += 1
                    except Exception as e:
                        logging.error(f"Failed to send to {uid}: {e}")
            
            user_cursor.close()

            if alert_count > 0:
                cursor.execute("INSERT OR REPLACE INTO last_alert (city, date) VALUES (?, ?)", (city, today_str))
                conn.commit()

    except Exception as e:
        logging.error(f"Error in check_temperatures: {e}")
    finally:
        cursor.close()

# ==================== COMMANDS ====================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    chat_id = message.chat.id
    cursor = conn.cursor()
    for city in CITIES:
        cursor.execute("INSERT OR IGNORE INTO thresholds (chat_id, city, threshold) VALUES (?, ?, ?)",
                       (chat_id, city, DEFAULT_THRESHOLD))
    conn.commit()
    cursor.close()
    
    await message.answer(
        "🚨 <b>Welcome to your High Temp Alert Bot!</b>\n\n"
        "Monitoring 7 US cities.\n\n"
        "Commands:\n"
        "/list – Current temps & thresholds\n"
        "/setthreshold Miami 85 – Change threshold\n"
        "/current – Force check now",
        parse_mode="HTML"
    )

@dp.message(Command("list"))
async def cmd_list(message: types.Message):
    text = "<b>Current Status:</b>\n\n"
    cursor = conn.cursor()
    for city, station in CITIES.items():
        temp = await get_current_high(station)
        temp_text = f"{temp}°F" if temp is not None else "no data"
        row = cursor.execute("SELECT threshold FROM thresholds WHERE chat_id = ? AND city = ?",
                             (message.chat.id, city)).fetchone()
        thresh = row[0] if row else DEFAULT_THRESHOLD
        text += f"• <b>{city}</b>: {temp_text} | threshold ≥{thresh}°F\n"
    cursor.close()
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("setthreshold"))
async def cmd_setthreshold(message: types.Message):
    args = message.text.split()
    if len(args) < 3:
        await message.answer("Usage: /setthreshold <City Name> <Temp>\nExample: /setthreshold Los Angeles 95")
        return

    try:
        threshold = int(args[-1])
        city_input = " ".join(args[1:-1]) 
        
        target_city = None
        for c in CITIES.keys():
            if c.lower() == city_input.lower():
                target_city = c
                break
        
        if not target_city and city_input.lower() in ["nyc", "new york", "new york city"]:
            target_city = "NYC"

        if not target_city:
            await message.answer(f"City '{city_input}' not found. Available: {', '.join(CITIES.keys())}")
            return

        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO thresholds (chat_id, city, threshold) VALUES (?, ?, ?)",
                       (message.chat.id, target_city, threshold))
        conn.commit()
        cursor.close()
        
        await message.answer(f"<b>{target_city}</b> threshold updated to ≥<b>{threshold}°F</b>", parse_mode="HTML")

    except ValueError:
        await message.answer("Temperature must be a number.")

@dp.message(Command("current"))
async def cmd_current(message: types.Message):
    await message.answer("🔄 Checking all stations now...")
    await check_temperatures()
    await message.answer("✅ Check complete!")

# ==================== RENDER WEB SERVER ====================

async def health_check(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    port = int(os.environ.get("PORT", 8080))
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Web server started on port {port}")

# ==================== MAIN ====================

scheduler = AsyncIOScheduler()

async def main():
    if not API_TOKEN:
        logging.error("No BOT_TOKEN found! Exiting.")
        return

    scheduler.add_job(check_temperatures, "interval", seconds=60)
    scheduler.start()

    # Start the Dummy Web Server (For Render)
    await start_web_server()

    print("Bot is running...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
