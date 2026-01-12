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
    # Lat/Lon for Hoskins Close (E16)
    lat, lon = "51.511", "0.046"
    url = f"https://api.weather.com/v3/wx/forecast/daily/5day?geocode={lat},{lon}&format=json&units=m&language=en-US&apiKey={API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        # [0] is today, [1] is tomorrow
        return data['calendarDayTemperatureMax'][0], data['calendarDayTemperatureMax'][1]
    except Exception:
        return "N/A", "N/A"

# 3. Task Functions (Hourly & Alerts)
async def hourly_status(context: ContextTypes.DEFAULT_TYPE):
    current_temp = await get_hoskins_temp()
    if current_temp is not None:
        await context.bot.send_message(
            chat_id=context.job.chat_id,
            text=f"🕒 *Hourly Update*\nHoskins Close: *{current_temp}°C*",
            parse_mode='Markdown'
        )

async def check_weather_loop(context: ContextTypes.DEFAULT_TYPE):
    thresholds = context.chat_data.get('thresholds', [])
    if not thresholds: return
    current_temp = await get_hoskins_temp()
    if current_temp is None: return

    # Triggered if temp >= threshold
    triggered = [t for t in thresholds if current_temp >= t]
    if triggered:
        today_max, tomorrow_max = await get_hoskins_forecast()
        target_hit = max(triggered)
        msg = (
            f"🔥 *TEMPERATURE ALERT*\n\n"
            f"Current: *{current_temp}°C*\n"
            f"Target hit: `{target_hit}°C` or higher\n\n"
            f"📅 *Forecast Highs:*\n"
            f"Today: `{today_max}°C` | Tomorrow: `{tomorrow_max}°C`"
        )
        await context.bot.send_message(chat_id=context.job.chat_id, text=msg, parse_mode='Markdown')
        # Remove triggered alerts
        context.chat_data['thresholds'] = [t for t in thresholds if t not in triggered]

# 4. Command Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Bot Ready.\n/set [temp]\n/updates (Hourly On/Off)\n/list\n/clear")

async def set_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(context.args[0])
        thresholds = context.chat_data.get('thresholds', [])
        if val not in thresholds:
            thresholds.append(val)
            thresholds.sort()
            context.chat_data['thresholds'] = thresholds
        
        # Immediate Update with Forecast
        current_temp = await get_hoskins_temp()
        today_max, tomorrow_max = await get_hoskins_forecast()
        
        confirmation = (
            f"✅ *Alert set for {val}°C*\n\n"
            f"🌡 *Right Now:* {current_temp}°C\n"
            f"☀️ *Today's Max:* {today_max}°C\n"
            f"🌅 *Tomorrow's Max:* {tomorrow_max}°C"
        )
        await update.message.reply_text(confirmation, parse_mode='Markdown')

        # Start background check if not running
        job_name = f"monitor_{update.effective_chat.id}"
        if not context.job_queue.get_jobs_by_name(job_name):
            context.job_queue.run_repeating(check_weather_loop, interval=60, first=1, chat_id=update.effective_chat.id, name=job_name)
    except:
        await update.message.reply_text("Usage: `/set 25.1`", parse_mode='Markdown')

async def toggle_updates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    job_name = f"hourly_{chat_id}"
    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    if current_jobs:
        for job in current_jobs: job.schedule_removal()
        await update.message.reply_text("🔕 Hourly updates OFF.")
    else:
        context.job_queue.run_repeating(hourly_status, interval=3600, first=5, chat_id=chat_id, name=job_name)
        await update.message.reply_text("🔔 Hourly updates ON.")

async def list_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = context.chat_data.get('thresholds', [])
    await update.message.reply_text(f"📈 Active alerts: {t}" if t else "No active alerts.")

async def clear_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data['thresholds'] = []
    await update.message.reply_text("🗑 All alerts cleared.")

if __name__ == "__main__":
    persistence = PicklePersistence(filepath="/data/bot_persistence")
    app = Application.builder().token(TOKEN).persistence(persistence).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set", set_alert))
    app.add_handler(CommandHandler("updates", toggle_updates))
    app.add_handler(CommandHandler("list", list_alerts))
    app.add_handler(CommandHandler("clear", clear_alerts))
    
    app.run_polling(drop_pending_updates=True)
