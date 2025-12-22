import logging
import requests
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters, ConversationHandler
from geopy.geocoders import ArcGIS  # CHANGED: Using ArcGIS instead of Nominatim
from keep_alive import keep_alive 

# --- CONFIGURATION ---
CITY, TEMPERATURE = range(2)
USER_AGENT = "MyTelegramWeatherBot/1.0 (contact@example.com)" 

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- NWS API HELPERS ---
def get_lat_lon(city_name):
    """Converts City Name to Latitude/Longitude using ArcGIS"""
    try:
        # ArcGIS is much more stable on cloud servers (Render/Heroku) than Nominatim
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
        
        # Get Forecast URL (for predicted high)
        forecast_url = properties.get('forecast')
        
        # Get Observation Stations URL (for current temp)
        stations_url = properties.get('observationStations')
        
        # Get the first station ID
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
            
            # Check if value exists (sometimes NWS returns null)
            if 'properties' in obs_resp and obs_resp['properties'].get('temperature'):
                temp_c = obs_resp['properties']['temperature']['value']
                if temp_c is not None:
                    current_temp = (temp_c * 9/5) + 32  # Convert to Fahrenheit
        
        # 2. Get Predicted High (Today)
        if forecast_url:
            fore_resp = requests.get(forecast_url, headers=headers).json()
            if 'properties' in fore_resp:
                periods = fore_resp['properties']['periods']
                if periods:
                    # The first period is usually "Today" or "This Afternoon"
                    predicted_high = periods[0]['temperature']
                
    except Exception as e:
        logging.error(f"API Error: {e}")

    return current_temp, predicted_high

# --- BOT COMMANDS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! I will alert you when the temperature hits a specific number.\n\n"
        "Please type the name of your **City** (e.g., 'New York' or 'Austin, TX')."
    )
    return CITY

async def set_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = update.message.text
    await update.message.reply_text(f"🔎 Searching for {city}...")
    
    lat, lon = get_lat_lon(city)
    
    if not lat:
        await update.message.reply_text("❌ Could not find that city. Please try again (e.g., add the state or country).")
        return CITY
    
    # Fetch NWS Metadata
    station_id, forecast_url = get_nws_endpoints(lat, lon)
    
    if not station_id or not forecast_url:
        await update.message.reply_text("❌ Error connecting to NWS for this location. The NWS API covers the US only. If this is a US city, try a larger nearby city.")
        return CITY

    # Store user data
    context.user_data['city'] = city
    context.user_data['station_id'] = station_id
    context.user_data['forecast_url'] = forecast_url
    
    await update.message.reply_text(f"✅ Found location!\n\nNow, send me the **Temperature Threshold** (in Fahrenheit) you want to be alerted at (e.g., 85).")
    return TEMPERATURE

async def set_temperature(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        limit = float(update.message.text)
        context.user_data['limit'] = limit
        context.user_data['alert_sent_today'] = False # Reset alert flag
        
        # Start the background job for this chat
        chat_id = update.message.chat_id
        
        # Remove existing jobs if any to prevent duplicates
        current_jobs = context.job_queue.get_jobs_by_name(str(chat_id))
        for job in current_jobs:
            job.schedule_removal()

        context.job_queue.run_repeating(check_weather, interval=10, first=1, chat_id=chat_id, user_id=chat_id, name=str(chat_id))
        
        await update.message.reply_text(
            f"✅ Alert Set!\n\nI will check the NWS API **every 10 seconds**.\n"
            f"If the current temperature in {context.user_data['city']} hits {limit}°F, I will alert you."
        )
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number.")
        return TEMPERATURE

# --- BACKGROUND JOB ---

async def check_weather(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    # Retrieve user data
    user_data = context.application.user_data.get(job.user_id)
    
    if not user_data:
        return

    station_id = user_data.get('station_id')
    forecast_url = user_data.get('forecast_url')
    limit = user_data.get('limit')
    alert_sent = user_data.get('alert_sent_today', False)

    # 1. Fetch Data
    current_temp, predicted_high = get_weather_data(station_id, forecast_url)

    if current_temp is None:
        return # Skip if API failed

    # 2. Check Conditions
    # Reset alert if temp drops 5 degrees below limit
    if current_temp < (limit - 5): 
        user_data['alert_sent_today'] = False

    if current_temp >= limit and not alert_sent:
        # TRIGGER ALERT
        msg = (
            f"🚨 **TEMPERATURE ALERT!** 🚨\n\n"
            f"Current Temp: **{current_temp:.1f}°F**\n"
            f"Your Threshold: **{limit}°F**\n"
            f"Today's Predicted High: **{predicted_high}°F**\n"
            f"Location: {user_data['city']}"
        )
        await context.bot.send_message(job.chat_id, text=msg, parse_mode='Markdown')
        user_data['alert_sent_today'] = True

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    # Remove jobs
    current_jobs = context.job_queue.get_jobs_by_name(str(chat_id))
    for job in current_jobs:
        job.schedule_removal()
        
    await update.message.reply_text("Monitoring stopped.")
    return ConversationHandler.END

# --- MAIN ---
if __name__ == '__main__':
    # 1. Start the dummy server for Render
    keep_alive()
    
    # 2. Run the Bot
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    if not TOKEN:
        print("Error: TELEGRAM_TOKEN not found in environment variables!")
    else:
        app = ApplicationBuilder().token(TOKEN).build()
        
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('start', start)],
            states={
                CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_city)],
                TEMPERATURE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_temperature)],
            },
            fallbacks=[CommandHandler('cancel', cancel)]
        )
        
        app.add_handler(conv_handler)
        app.run_polling()
