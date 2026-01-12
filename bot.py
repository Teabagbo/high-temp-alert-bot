import os
import requests
import logging
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, PicklePersistence

# 1. Setup Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)

# 2. Configuration from Render Environment Variables
STATION_ID = os.getenv("STATION_ID", "ILONDO288")
API_KEY = os.getenv("WU_API_KEY")
TOKEN = os.getenv("TELEGRAM_TOKEN")

# 3. Weather Fetching Functions
async def get_hoskins_temp():
    """Fetches real-time decimal temperature from the station sensors."""
    url = f"https://api.weather.com/v2/pws/observations/current?stationId={STATION_ID}&format=json&units=m&apiKey={API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        return float(data['observations'][0]['metric']['temp'])
    except Exception as e:
        logging.error(f"PWS API Error: {e}")
        return None

async def get_hoskins_forecast():
    """Fetches high forecast for Today and Tomorrow for the E16 area."""
    # Coordinates for Hoskins Close area
    lat, lon = "51.511", "0.046"
    url = f"https://api.weather.com/v3/wx/forecast/daily/5day?geocode={lat},{lon}&format=json&units=m&language=en-US&apiKey={API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        # [0] is today, [1] is tomorrow
        today_max = data['calendarDayTemperatureMax'][0]
        tomorrow_max = data['calendarDayTemperatureMax'][1]
        return today_max, tomorrow_max
    except Exception as e:
        logging.error(f"Forecast API Error: {e}")
        return "N/A", "N/A"

# 4. Background Monitoring Loop
async def check_weather_loop(context: ContextTypes.DEFAULT_TYPE):
    thresholds = context.chat_data.get('thresholds', [])
    if not thresholds:
        return

    current_temp = await get_hoskins_temp()
    if current_temp is None:
        return

    # Identify which thresholds were crossed
    hit_thresholds = [t for t in thresholds if current_temp >= t]
    
    if hit_thresholds:
        today_max, tomorrow_max = await get_hoskins_forecast()
        
        # We find the highest threshold hit to display
        target_hit = max(hit_thresholds)
        
        text = (
            f"🚨 *HOSKINS TEMP ALERT*\n\n"
            f"📍 Station: `{STATION_ID}`\n"
            f"🌡 Current: *{current_temp}°C*\n"
            f"🎯 Trigger: `{target_hit}°C`\n\n"
            f"📅 *Forecast Max:*\n"
            f"☀️ Today: `{today_max}°C`\n"
            f"🌅 Tomorrow: `{tomorrow_max}°C`"
        )
        
        await context.bot.send_message(
            chat_id=context.job.chat_id,
            text=text,
            parse_mode='Markdown'
        )

# 5. Telegram Commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome = (
        "👋 *Hoskins Alert Bot Active!*\n\n"
        "Commands:\n"
        "/set [number] - Add a decimal alert (e.g. `/set 25.1`)\n"
        "/list - Show your active alerts\n"
        "/clear - Remove all alerts"
    )
    await update.message.reply_text(welcome, parse_mode='Markdown')

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
            context.job_queue.run_repeating(
                check_weather_loop, interval=60, first=1, 
                chat_id=update.effective_chat.id, name=job_name
            )
        
        await update.message.reply_text(f"✅ Added {val}°C. Active alerts: {thresholds}")
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Usage: `/set 25.1`", parse_mode='Markdown')

async def list_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    thresholds = context.chat_data.get('thresholds', [])
    if not thresholds:
        await update.message.reply_text("No active alerts.")
    else:
        await update.message.reply_text(f"📈 Active thresholds: {thresholds}")

async def clear_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data['thresholds'] = []
    await update.message.reply_text("🗑 All alerts cleared.")

# 6. Main Execution
if __name__ == "__main__":
    # Ensure this path matches your Render Disk Mount Path
    persistence = PicklePersistence(filepath="/data/bot_persistence")

    application = Application.builder().token(TOKEN).persistence(persistence).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("set", set_alert))
    application.add_handler(CommandHandler("list", list_alerts))
    application.add_handler(CommandHandler("clear", clear_alerts))
    
    print("Bot is starting...")
    application.run_polling(drop_pending_updates=True)
