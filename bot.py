import os
import requests
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, PicklePersistence

# 1. Setup Logging
logging.basicConfig(level=logging.INFO)

STATION_ID = os.getenv("STATION_ID", "ILONDO288")
API_KEY = os.getenv("WU_API_KEY")
TOKEN = os.getenv("TELEGRAM_TOKEN")

# 2. Weather & Forecast Logic
async def get_hoskins_temp():
    url = f"https://api.weather.com/v2/pws/observations/current?stationId={STATION_ID}&format=json&units=m&apiKey={API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        return float(data['observations'][0]['metric']['temp'])
    except Exception as e:
        logging.error(f"PWS API Error: {e}")
        return None

async def get_hoskins_forecast():
    # Lat/Lon for Hoskins Close
    lat, lon = "51.511", "0.046"
    url = f"https://api.weather.com/v3/wx/forecast/daily/5day?geocode={lat},{lon}&format=json&units=m&language=en-US&apiKey={API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        return data['calendarDayTemperatureMax'][0], data['calendarDayTemperatureMax'][1]
    except Exception:
        return "N/A", "N/A"

# 3. Alert Logic
async def check_weather_loop(context: ContextTypes.DEFAULT_TYPE):
    thresholds = context.chat_data.get('thresholds', [])
    if not thresholds:
        return

    current_temp = await get_hoskins_temp()
    if current_temp is None:
        return

    # Find any threshold that is hit (Current temp >= Threshold)
    triggered = [t for t in thresholds if current_temp >= t]
    
    if triggered:
        today_max, tomorrow_max = await get_hoskins_forecast()
        
        # We take the highest threshold reached to announce
        target_hit = max(triggered)
        
        message = (
            f"🔥 *TEMPERATURE ALERT*\n\n"
            f"Current Hoskins Temp: *{current_temp}°C*\n"
            f"Threshold Breached: `{target_hit}°C`\n\n"
            f"📅 *Forecasted Highs:*\n"
            f"Today: `{today_max}°C`\n"
            f"Tomorrow: `{tomorrow_max}°C`"
        )
        
        await context.bot.send_message(chat_id=context.job.chat_id, text=message, parse_mode='Markdown')
        
        # REMOVE triggered thresholds to prevent spamming
        new_thresholds = [t for t in thresholds if t not in triggered]
        context.chat_data['thresholds'] = new_thresholds

# 4. Commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hoskins Bot Active! /set 25.1 to start.")

async def set_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(context.args[0])
        thresholds = context.chat_data.get('thresholds', [])
        if val not in thresholds:
            thresholds.append(val)
            thresholds.sort()
            context.chat_data['thresholds'] = thresholds
        
        job_name = f"monitor_{update.effective_chat.id}"
        if not context.job_queue.get_jobs_by_name(job_name):
            context.job_queue.run_repeating(check_weather_loop, interval=60, first=1, chat_id=update.effective_chat.id, name=job_name)
        
        await update.message.reply_text(f"✅ Added {val}°C alert. (Total: {len(thresholds)})")
    except:
        await update.message.reply_text("Usage: /set 25.1")

async def list_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = context.chat_data.get('thresholds', [])
    await update.message.reply_text(f"📈 Active: {t}" if t else "No active alerts.")

async def clear_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data['thresholds'] = []
    await update.message.reply_text("🗑 All alerts cleared.")

if __name__ == "__main__":
    persistence = PicklePersistence(filepath="/data/bot_persistence")
    app = Application.builder().token(TOKEN).persistence(persistence).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set", set_alert))
    app.add_handler(CommandHandler("list", list_alerts))
    app.add_handler(CommandHandler("clear", clear_alerts))
    
    app.run_polling(drop_pending_updates=True)
