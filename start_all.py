import asyncio
from bots.main import main_bot
from bots.main2 import admin_bot
from bots.main3 import bot as equipos_bot
from metrics_server import start_metrics_server  # ← IMPORTANTE
import os
from dotenv import load_dotenv

load_dotenv()

async def run_all_bots():
    print("🚀 Iniciando los 3 bots + metrics_server…")

    # 🔥 arranca el servidor de métricas
    task0 = asyncio.create_task(start_metrics_server())

    # 🔥 arranca los bots
    task1 = asyncio.create_task(main_bot.start(os.getenv("DISCORD_MAIN_BOT_TOKEN")))
    task2 = asyncio.create_task(admin_bot.start(os.getenv("DISCORD_ADMIN_BOT_TOKEN")))
    task3 = asyncio.create_task(equipos_bot.start(os.getenv("DISCORD_EQUIPOS_BOT_TOKEN")))

    await asyncio.gather(task0, task1, task2, task3)

if __name__ == "__main__":
    asyncio.run(run_all_bots())