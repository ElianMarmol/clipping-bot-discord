from fastapi import FastAPI
from pydantic import BaseModel
import asyncpg
import uvicorn
import os

class MetricsPayload(BaseModel):
    discord_id: int
    youtube_username: str
    channel_id: str
    videos: list

app = FastAPI()

@app.on_event("startup")
async def startup():
    print("⏳ Conectando a la base de datos (metrics_server)...")
    app.db = await asyncpg.create_pool(
        os.getenv("DATABASE_URL"),
        ssl="require"
    )
    print("🟢 Base de datos conectada (metrics_server)")

@app.post("/youtube/metrics")
async def save_metrics(payload: MetricsPayload):
    print("📩 Recibido payload de N8N")

    async with app.db.acquire() as conn:
        for v in payload.videos:
            await conn.execute('''
                INSERT INTO tracked_posts (discord_id, post_url, views, likes, shares)
                VALUES ($1, $2, $3, $4, 0)
                ON CONFLICT (discord_id, post_url)
                DO UPDATE SET 
                    views = EXCLUDED.views,
                    likes = EXCLUDED.likes
            ''',
                payload.discord_id,
                f"https://youtube.com/watch?v={v['video_id']}",
                v["views"],
                v["likes"]
            )

    return {"status": "ok", "message": "Métricas guardadas correctamente"}


# 🚀 ESTO ES LO IMPORTANTE: función async para ejecutar en paralelo
async def start_metrics_server():
    port = int(os.getenv("PORT", 5005))  # Railway define PORT automáticamente

    config = uvicorn.Config(app, host="0.0.0.0", port=port)
    server = uvicorn.Server(config)

    print(f"🌐 metrics_server escuchando en el puerto {port}")

    await server.serve()