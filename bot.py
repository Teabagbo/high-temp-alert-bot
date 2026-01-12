import os
import requests
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, PicklePersistence

# Logging setup
logging.basicConfig(level=logging.INFO)

STATION_ID = os.getenv("STATION_ID", "ILONDO288")
API_KEY = os.getenv("WU_API_KEY")
TOKEN = os.getenv("TELEGRAM_TOKEN")

async def get_hoskins_temp():
    url = f"https://api.weather.com/v2/pws/observations/current?stationId={STATION_ID}&format=json&units=m&apiKey={API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        # Return the raw decimal temperature
        return float(data['observations'][0]['metric']['temp'])
    except Exception as e:
        logging.error(f"Weather API Error: {e}")
        return None

async def check_weather_loop(context: ContextTypes.DEFAULT_TYPE):
    thresholds = context.chat_data.get('thresholds', [])
    if not thresholds:
        return

    current_temp = await get_hoskins_temp()
    if current_temp is None:
        return

    # Check each decimal threshold
    for t in thresholds:
        if current_temp >= t:
            await context.bot.send_message(
                chat_id=context.job.chat_id,
                text=f"🚨 ALERT: Hoskins Close is {current_temp}°C\n(Trigger hit: {t}°C)"
            )

async def set_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Convert user input to float to support decimals like 25.1
        val = float(context.args[0])
        thresholds = context.chat_data.get('thresholds', [])
        
        if val not in thresholds:
            thresholds.append(val)
            thresholds.sort() 
            context.chat_data['thresholds'] = thresholds
        
        # Start monitoring if not already active
        job_name = f"monitor_{update.effective_chat.id}"
        if not context.job_queue.get_jobs_by_name(job_name):
            context.job_queue.run_repeating(check_weather_loop, interval=60, first=1, 
                                          chat_id=update.effective_chat.id, name=job_name)
        
        await update.message.reply_text(f"✅ Added {val}°C to your alerts.\nActive: {thresholds}")
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Usage: /set 25.1")

async def list_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    thresholds = context.chat_data.get('thresholds', [])
    if not thresholds:
        await update.message.reply_text("No active alerts.")
    else:
        # Formats decimals to 1 decimal place for clarity
        formatted = ", ".join([f"{t}°C" for t in thresholds])
        await update.message.reply_text(f"📈 Active thresholds: {formatted}")

async def clear_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data['thresholds'] = []
    await update.message.reply_text("🗑 All alerts cleared.")

if __name__ == "__main__":
    # Persistence ensures your decimals are saved on the Render disk
    persistence = PicklePersistence(filepath="/data/bot_persistence")
    application = Application.builder().token(TOKEN).persistence(persistence).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("set", set_alert))
    application.add_handler(CommandHandler("list", list_alerts))
    application.add_handler(CommandHandler("clear", clear_alerts))
    
    application.run_polling()
