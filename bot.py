import os
import requests
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, PicklePersistence

# 1. Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
STATION_ID = os.getenv("STATION_ID", "ILONDO288")
API_KEY = os.getenv("WU_API_KEY")
TOKEN = os.getenv("TELEGRAM_TOKEN")

# 2. Robust Weather Fetching
async def get_hoskins_temp():
    """Fetches real-time temp with fallback."""
    url = f"https://api.weather.com/v2/pws/observations/current?stationId={STATION_ID}&format=json&units=m&apiKey={API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            obs = data.get('observations', [])
            if obs:
                return float(obs[0]['metric']['temp'])
        logger.warning(f"Station {STATION_ID} returned no data. Status: {response.status_code}")
        return None
    except Exception as e:
        logger.error(f"PWS Fetch Error: {e}")
        return None

async def get_hoskins_forecast():
    """Fetches daily highs for E16 area with smart parsing."""
    lat, lon = "51.511", "0.046"
    url = f"https://api.weather.com/v3/wx/forecast/daily/5day?geocode={lat},{lon}&format=json&units=m&language=en-US&apiKey={API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            # The API returns a list. Day 0 is today, Day 1 is tomorrow.
            max_temps = data.get('calendarDayTemperatureMax', [])
            
            # Use 'N/A' if the list is too short or value is None
            today = max_temps[0] if len(max_temps) > 0 and max_temps[0] is not None else "---"
            tomorrow = max_temps[1] if len(max_temps) > 1 and max_temps[1] is not None else "---"
            return today, tomorrow
        return "N/A", "N/A"
    except Exception:
        return "ERR", "ERR"

# 3. Tasks & Logic
async def check_weather_loop(context: ContextTypes.DEFAULT_TYPE):
    thresholds = context.chat_data.get('thresholds', [])
    if not thresholds: return

    current_temp = await get_hoskins_temp()
    if current_temp is None: return

    # Alert if current >= any threshold
    triggered = [t for t in thresholds if current_temp >= t]
    if triggered:
        today_max, tomorrow_max = await get_hoskins_forecast()
        target = max(triggered)
        msg = (
            f"🔥 *HOSKINS ALERT*\n\n"
            f"Current: *{current_temp}°C*\n"
            f"Target hit: `{target}°C`+\n\n"
            f"📅 *Daily Highs*\n"
            f"Today: `{today_max}°C` | Tomorrow: `{tomorrow_max}°C`"
        )
        await context.bot.send_message(chat_id=context.job.chat_id, text=msg, parse_mode='Markdown')
        # Cleanup
        context.chat_data['thresholds'] = [t for t in thresholds if t not in triggered]

async def hourly_status(context: ContextTypes.DEFAULT_TYPE):
    current_temp = await get_hoskins_temp()
    if current_temp is not None:
        await context.bot.send_message(
            chat_id=context.job.chat_id,
            text=f"🕒 *Hourly Update*\nHoskins Close: *{current_temp}°C*",
            parse_mode='Markdown'
        )

# 4. Command Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 *Hoskins Bot Online*\n/set [temp]\n/updates (Hourly)\n/list\n/clear", parse_mode='Markdown')

async def set_alert(update: Update, context: ContextTypes.DEFAULT_TEXT):
    try:
        val = float(context.args[0])
        thresholds = context.chat_data.get('thresholds', [])
        if val not in thresholds:
            thresholds.append(val)
            thresholds.sort()
            context.chat_data['thresholds'] = thresholds
        
        # Immediate verification call
        curr = await get_hoskins_temp()
        t_max, tom_max = await get_hoskins_forecast()
        
        # Friendly feedback if data is missing
        curr_str = f"{curr}°C" if curr is not None else "⚠️ Station Offline"
        
        await update.message.reply_text(
            f"✅ *Target {val}°C Set*\n\n"
            f"🌡 *Now:* {curr_str}\n"
            f"☀️ *Today High:* {t_max}°C\n"
            f"🌅 *Tomorrow:* {tom_max}°C",
            parse_mode='Markdown'
        )

        job_name = f"monitor_{update.effective_chat.id}"
        if not context.job_queue.get_jobs_by_name(job_name):
            context.job_queue.run_repeating(check_weather_loop, interval=60, first=1, chat_id=update.effective_chat.id, name=job_name)
    except:
        await update.message.reply_text("❌ Usage: `/set 24.5`", parse_mode='Markdown')

async def toggle_updates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    job_name = f"hourly_{chat_id}"
    jobs = context.job_queue.get_jobs_by_name(job_name)
    if jobs:
        for j in jobs: j.schedule_removal()
        await update.message.reply_text("🔕 Hourly updates deactivated.")
    else:
        context.job_queue.run_repeating(hourly_status, interval=3600, first=5, chat_id=chat_id, name=job_name)
        await update.message.reply_text("🔔 Hourly updates activated.")

async def list_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = context.chat_data.get('thresholds', [])
    await update.message.reply_text(f"📈 Active alerts: `{t}`", parse_mode='Markdown' if t else "No active alerts.")

async def clear_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data['thresholds'] = []
    await update.message.reply_text("🗑 All alerts cleared.")

if __name__ == "__main__":
    # Persistence path for Render Disk
    pers = PicklePersistence(filepath="/data/bot_persistence")
    app = Application.builder().token(TOKEN).persistence(pers).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set", set_alert))
    app.add_handler(CommandHandler("updates", toggle_updates))
    app.add_handler(CommandHandler("list", list_alerts))
    app.add_handler(CommandHandler("clear", clear_alerts))
    
    app.run_polling(drop_pending_updates=True)
