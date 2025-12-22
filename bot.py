import asyncio
import logging
import sqlite3
import os
from datetime import datetime, timezone, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import aiohttp
from aioscheduler import TimedScheduler

# ==================== CONFIGURATION ====================
API_TOKEN = os.getenv("BOT_TOKEN")

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

DEFAULT_THRESHOLD = 90  # °F

# =====================================================

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

conn = sqlite3.connect("alerts.db", check_same_thread=False)
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

headers = {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}

async def get_current_high(station: str) -> int | None:
    url = f"https://api.weather.gov/stations/{station}/observations/latest"
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            temp_c = data["properties"]["temperature"]["value"]
            if temp_c is None:
                return None
            return round(temp_c * 9/5 + 32)

async def get_forecast_high(lat: float, lon: float) -> int | None:
    point_url = f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}"
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
                start_time = datetime.fromisoformat(period["startTime"].rstrip("Z") + "+00:00")
                if start_time.date() == today and period["isDaytime"]:
                    return period["temperature"]
    return None

COORDS = {
    "KMIA": (25.7617, -80.1918),
    "KLAX": (33.9416, -118.4085),
    "KNYC": (40.7789, -73.9692),
    "KMDW": (41.7860, -87.7522),
    "KAUS": (30.1945, -97.6699),
    "KDEN": (39.8561, -104.6737),
    "KPHL": (39.8733, -75.2268),
}

async def check_temperatures():
    chat_ids = [row[0] for row in cursor.execute("SELECT DISTINCT chat_id FROM thresholds").fetchall()]

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for city, station in CITIES.items():
        current_temp_f = await get_current_high(station)
        if current_temp_f is None:
            continue

        for chat_id in chat_ids:
            cursor.execute("SELECT threshold FROM thresholds WHERE chat_id = ? AND city = ?", (chat_id, city))
            row = cursor.fetchone()
            threshold = row[0] if row else DEFAULT_THRESHOLD

            if current_temp_f >= threshold:
                cursor.execute("SELECT 1 FROM last_alert WHERE city = ? AND date = ?", (city, today_str))
                if cursor.fetchone():
                    continue  # Already alerted today

                lat, lon = COORDS[station]
                forecast_high = await get_forecast_high(lat, lon)
                forecast_text = f"{forecast_high}°F" if forecast_high is not None else "unavailable"

                message = (
                    f"🌡️ <b>High Temperature Alert</b> for <b>{city}</b>!\n\n"
                    f"Current temperature: <b>{current_temp_f}°F</b>\n"
                    f"Your threshold: ≥<b>{threshold}°F</b>\n\n"
                    f"Today's predicted high: <b>{forecast_text}</b>\n"
                    f"Stay safe! 🥵"
                )

                await bot.send_message(chat_id, message, parse_mode="HTML")

                cursor.execute("INSERT OR REPLACE INTO last_alert (city, date) VALUES (?, ?)", (city, today_str))
                conn.commit()

    # Schedule next run in 10 seconds
    next_run = datetime.now(timezone.utc) + timedelta(seconds=10)
    scheduler.schedule(check_temperatures, next_run)

# ==================== COMMANDS ====================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    chat_id = message.chat.id
    for city in CITIES:
        cursor.execute("INSERT OR IGNORE INTO thresholds (chat_id, city, threshold) VALUES (?, ?, ?)",
                       (chat_id, city, DEFAULT_THRESHOLD))
    conn.commit()
    await message.answer(
        "🚨 <b>Welcome to your High Temp Alert Bot!</b>\n\n"
        "Monitoring 7 US cities with default threshold 90°F.\n\n"
        "Commands:\n"
        "/list – Current temps & thresholds\n"
        "/setthreshold Miami 85 – Change threshold\n"
        "/current – Force check now",
        parse_mode="HTML"
    )

@dp.message(Command("list"))
async def cmd_list(message: types.Message):
    text = "<b>Current Status:</b>\n\n"
    for city, station in CITIES.items():
        temp = await get_current_high(station)
        temp_text = f"{temp}°F" if temp is not None else "no data"
        row = cursor.execute("SELECT threshold FROM thresholds WHERE chat_id = ? AND city = ?",
                             (message.chat.id, city)).fetchone()
        thresh = row[0] if row else DEFAULT_THRESHOLD
        text += f"• <b>{city}</b>: {temp_text} | threshold ≥{thresh}°F\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("setthreshold"))
async def cmd_setthreshold(message: types.Message):
    try:
        parts = message.text.split(maxsplit=2)
        if len(parts) < 3:
            raise ValueError
        city = parts[1].title()
        if city == "Los": city = "Los Angeles"
        if city == "Nyc": city = "NYC"
        threshold = int(parts[2])
        if city not in CITIES:
            await message.answer("Available cities: " + ", ".join(CITIES.keys()))
            return
        cursor.execute("INSERT OR REPLACE INTO thresholds (chat_id, city, threshold) VALUES (?, ?, ?)",
                       (message.chat.id, city, threshold))
        conn.commit()
        await message.answer(f"<b>{city}</b> threshold updated to ≥<b>{threshold}°F</b>", parse_mode="HTML")
    except:
        await message.answer("Usage: /setthreshold <city> <temperature>\nExample: /setthreshold Miami 88")

@dp.message(Command("current"))
async def cmd_current(message: types.Message):
    await message.answer("🔄 Checking all stations now...")
    await check_temperatures()
    await message.answer("✅ Check complete!")

# ==================== SCHEDULER ====================

scheduler = TimedScheduler()

async def main():
    scheduler.start()

    # Start the repeating checks
    first_run = datetime.now(timezone.utc)
    scheduler.schedule(check_temperatures, first_run)

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
