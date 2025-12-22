# ... (Keep all your imports and previous code) ...
from aiohttp import web # Make sure this is imported

# ... (Keep all your existing functions: get_current_high, check_temperatures, etc.) ...

# ==================== RENDER WEB SERVER ====================
async def health_check(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    # Render assigns a random port in the PORT env var. Default to 8080 for local testing.
    port = int(os.environ.get("PORT", 8080))
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"Web server started on port {port}")

# ==================== MAIN ====================

async def main():
    # 1. Start the Scheduler
    scheduler.add_job(check_temperatures, "interval", seconds=60)
    scheduler.start()

    # 2. Start the Dummy Web Server (For Render)
    await start_web_server()

    # 3. Start the Bot
    print("Bot is running...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
