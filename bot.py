import os
import requests
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# --- CONFIGURATION ---
STATION_ID = "ILONDO288"
# Ensure these are set in your Render.com Environment Variables
API_KEY = os.getenv("WU_API_KEY") 
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Global threshold (stores in memory; resets if Render sleeps)
alert_threshold = None

async def get_hoskins_temp():
    """Fetches the absolute latest temp from Hoskins close (ILONDO288)"""
    # This is the PWS Current Observations endpoint
    url = f"https://api.weather.com/v2/pws/observations/current?stationId={STATION_ID}&format=json&units=m&apiKey={API_KEY}"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        # Navigating the WU JSON structure
        temp = data['observations'][0]['metric']['temp']
        return temp
    except Exception as e:
        print(f"Weather Underground API Error: {e}")
        return None

async def check_weather_loop(context: ContextTypes.DEFAULT_TYPE):
    """Background task that runs every 60 seconds"""
    global alert_threshold
    chat_id = context.job.chat_id
    
    if alert_threshold is None:
        return

    current_temp = await get_hoskins_temp()
    
    if current_temp is not None:
        if current_temp >= alert_threshold:
            await context.bot.send_message(
                chat_id=chat_id, 
                text=f"🔥 ALERT: Hoskins Close is now {current_temp}°C! (Target: {alert_threshold}°C)"
            )

async def set_temp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command to set the threshold: /set 22.5"""
    global alert_threshold
    try:
        val = float(context.args[0])
        alert_threshold = val
        
        # Ensure the background job is running for this chat
        job_name = f"monitor_{update.effective_chat.id}"
        existing_jobs = context.job_queue.get_jobs_by_name(job_name)
        
        if not existing_jobs:
            # interval=60 is the safest 'fast' speed for the workaround key
            context.job_queue.run_repeating(
                check_weather_loop, 
                interval=60, 
                first=1, 
                chat_id=update.effective_chat.id, 
                name=job_name
            )
            
        await update.message.reply_text(f"✅ Monitoring Hoskins Close (ILONDO288).\nAlert set for: {val}°C")
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Please provide a number. Example: /set 25")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hoskins Temp Alert Bot is online. Use /set [temp] to begin.")

if __name__ == "__main__":
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("set", set_temp))
    
    print("Bot is running...")
    application.run_polling()

from flask import Flask
import threading

app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running!", 200

def run_flask():
    app.run(host='0.0.0.0', port=10000)

# ... (Include your existing bot logic here) ...

if __name__ == "__main__":
    # Start Flask in a background thread
    threading.Thread(target=run_flask, daemon=True).start()
    
    # Start your Telegram Bot
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    # ... (rest of your handlers) ...
    application.run_polling()
