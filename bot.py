import logging
import requests
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters, ConversationHandler
from geopy.geocoders import ArcGIS
from keep_alive import keep_alive 

# --- CONFIGURATION ---
CITY, THRESHOLDS = range(2)
USER_AGENT = "MyTelegramWeatherBot/2.0 (contact@example.com)" 

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- NWS API HELPERS ---
def get_lat_lon(city_name):
    """Converts City Name to Latitude/Longitude using ArcGIS"""
    try:
        geolocator = ArcGIS(user_agent=USER_AGENT)
        location = geolocator.geocode(city_name, timeout=10)
        if location:
            return location.latitude, location.longitude
        return None, None
    except Exception as e:
        logging.error(f"Geocoding error: {e}")
        return None, None

def get_nws_endpoints(lat, lon):
    """Gets the Observation station and Forecast grid from NWS"""
    url = f"https://api.weather.gov/points/{lat},{lon}"
    headers = {"User-Agent": USER_AGENT}
    
    try:
        response = requests.get(url, headers=headers).json()
        properties = response.get('properties', {})
        forecast_url = properties.get('forecast')
        stations_url = properties.get('observationStations')
        
        station_id = None
        if stations_url:
            stations_data = requests.get(stations_url, headers=headers).json()
            features = stations_data.get('features', [])
            if features:
                station_id = features[0].get('properties', {}).get('stationIdentifier')
                
        return station_id, forecast_url
    except Exception as e:
        logging.error(f"Error fetching NWS endpoints: {e}")
        return None, None

def get_weather_data(station_id, forecast_url):
    """Fetches Current Temp and Predicted High"""
    headers = {"User-Agent": USER_AGENT}
    current_temp = None
    predicted_high = None

    try:
        # 1. Get Current Temperature
        if station_id:
            obs_url = f"https://api.weather.gov/stations/{station_id}/observations/latest"
            obs_resp = requests.get(obs_url, headers=headers).json()
            if 'properties' in obs_resp and obs_resp['properties'].get('temperature'):
                temp_c = obs_resp['properties']['temperature']['value']
                if temp_c is not None:
                    current_temp = (temp_c * 9/5) + 32  # F
        
        # 2. Get Predicted High
        if forecast_url:
            fore_resp = requests.get(forecast_url, headers=headers).json()
            if 'properties' in fore_resp:
                periods = fore_resp['properties'].get('periods', [])
                if periods:
                    predicted_high = periods[0]['temperature']
                
    except Exception as e:
        logging.error(f"API Error: {e}")

    return current_temp, predicted_high

# --- BOT COMMANDS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **Weather Alert Bot 2.0**\n\n"
        "I can track multiple cities and multiple temperature targets simultaneously.\n\n"
        "**Commands:**\n"
        "➕ `/add` - Add a new city and temperature targets.\n"
        "📋 `/list` - See what you are currently tracking.\n"
        "🗑 `/clear` - Remove all alerts.\n"
        "❌ `/cancel` - Cancel the current action."
    )

# --- ADD ALERT FLOW ---

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✍️ Please type the **City Name** (e.g., 'New York, NY').")
    return CITY

async def set_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = update.message.text
    await update.message.reply_text(f"🔎 Searching for {city}...")
    
    lat, lon = get_lat_lon(city)
    if not lat:
        await update.message.reply_text("❌ Could not find city. Try adding state/country.")
        return CITY
    
    station_id, forecast_url = get_nws_endpoints(lat, lon)
    if not station_id:
        await update.message.reply_text("❌ NWS API not available for this location (US Only).")
        return ConversationHandler.END

    # Temporarily store found location
    context.user_data['temp_city_name'] = city
    context.user_data['temp_station_id'] = station_id
    context.user_data['temp_forecast_url'] = forecast_url
    
    await update.message.reply_text(
        f"✅ Found {city} (Station: {station_id})\n\n"
        "Now, send me the **Temperature Thresholds**.\n"
        "You can send multiple separated by commas.\n"
        "Example: `75, 80.5, 90`"
    )
    return THRESHOLDS

async def set_thresholds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    try:
        # Parse numbers (e.g., "75, 80" -> [75.0, 80.0])
        raw_nums = [float(x.strip()) for x in text.split(',')]
        
        # Initialize user storage if empty
        if 'alerts' not in context.user_data:
            context.user_data['alerts'] = {}

        station_id = context.user_data['temp_station_id']
        city_name = context.user_data['temp_city_name']
        forecast_url = context.user_data['temp_forecast_url']

        # If city already exists, we append. If new, we create.
        if station_id not in context.user_data['alerts']:
            context.user_data['alerts'][station_id] = {
                'city': city_name,
                'forecast_url': forecast_url,
                'targets': {}
            }
        
        # Add targets. 
        # Structure: { 75.0: {'alerted': False}, 80.0: {'alerted': False} }
        count = 0
        for num in raw_nums:
            context.user_data['alerts'][station_id]['targets'][num] = {'alerted': False}
            count += 1
            
        # Ensure Background Job is Running for this user
        chat_id = update.message.chat_id
        jobs = context.job_queue.get_jobs_by_name(str(chat_id))
        if not jobs:
            context.job_queue.run_repeating(check_weather_job, interval=10, first=1, chat_id=chat_id, user_id=chat_id, name=str(chat_id))

        await update.message.reply_text(f"✅ Added **{count}** alerts for **{city_name}**.\nUse `/list` to view them.")
        return ConversationHandler.END

    except ValueError:
        await update.message.reply_text("❌ Invalid format. Please enter numbers separated by commas (e.g. 75, 80).")
        return THRESHOLDS

# --- MANAGEMENT COMMANDS ---

async def list_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    alerts = context.user_data.get('alerts', {})
    if not alerts:
        await update.message.reply_text("You have no active alerts. Use `/add` to start.")
        return

    msg = "📋 **Your Active Alerts:**\n\n"
    for station, data in alerts.items():
        msg += f"📍 **{data['city']}**\n"
        sorted_targets = sorted(data['targets'].keys())
        for t in sorted_targets:
            status = "✅ Fired" if data['targets'][t]['alerted'] else "⏳ Waiting"
            msg += f"   • {t}°F ({status})\n"
        msg += "\n"
    
    await update.message.reply_text(msg, parse_mode='Markdown')

async def clear_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['alerts'] = {}
    chat_id = update.message.chat_id
    # Stop the background job
    jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    for job in jobs:
        job.schedule_removal()
        
    await update.message.reply_text("🗑 All alerts cleared and monitoring stopped.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Action cancelled.")
    return ConversationHandler.END

# --- BACKGROUND JOB ---

async def check_weather_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    user_data = context.application.user_data.get(job.user_id)
    
    if not user_data or 'alerts' not in user_data or not user_data['alerts']:
        # If no alerts, kill the job to save resources
        job.schedule_removal()
        return

    # Iterate over every city (Station ID)
    for station_id, data in user_data['alerts'].items():
        forecast_url = data['forecast_url']
        targets = data['targets'] # Dictionary of threshold -> status
        
        # 1. Fetch API (Once per city)
        current_temp, predicted_high = get_weather_data(station_id, forecast_url)
        
        if current_temp is None:
            continue # Skip this city if API fails

        # 2. Check all targets for this city
        for limit, status in targets.items():
            
            # Reset Logic: If temp drops 5 degrees below limit, re-arm the alert
            if current_temp < (limit - 5):
                status['alerted'] = False
            
            # Trigger Logic
            if current_temp >= limit and not status['alerted']:
                # SEND ALERT
                msg = (
                    f"🚨 **TEMPERATURE ALERT!** 🚨\n\n"
                    f"📍 **{data['city']}**\n"
                    f"🌡 Current: **{current_temp:.1f}°F**\n"
                    f"🎯 Target Met: **{limit}°F**\n"
                    f"📅 Today's High: **{predicted_high}°F**"
                )
                await context.bot.send_message(job.chat_id, text=msg, parse_mode='Markdown')
                status['alerted'] = True

# --- MAIN ---
if __name__ == '__main__':
    keep_alive()
    
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    if not TOKEN:
        print("Error: TELEGRAM_TOKEN not found!")
    else:
        app = ApplicationBuilder().token(TOKEN).build()
        
        # Conversation for Adding Alerts
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('add', add_start)],
            states={
                CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_city)],
                THRESHOLDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_thresholds)],
            },
            fallbacks=[CommandHandler('cancel', cancel)]
        )
        
        app.add_handler(CommandHandler('start', start))
        app.add_handler(CommandHandler('list', list_alerts))
        app.add_handler(CommandHandler('clear', clear_alerts))
        app.add_handler(conv_handler)
        
        app.run_polling()
