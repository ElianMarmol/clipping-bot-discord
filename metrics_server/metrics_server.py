from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import asyncpg
import uvicorn
import os
from typing import List, Optional

# Modelos de datos
class MetricItem(BaseModel):
    video_id: str
    views: int
    likes: int
    shares: int = 0
    url: str  # URL completa del post

class MetricsPayload(BaseModel):
    discord_id: str
    platform: str  # 'youtube' o 'tiktok'
    videos: List[MetricItem]

class VerificationPayload(BaseModel):
    discord_id: str
    platform: str
    is_verified: bool

app = FastAPI()
app.db_pool = None

@app.on_event("startup")
async def startup():
    print("⏳ Conectando metrics_server a DB...")
    app.db_pool = await asyncpg.create_pool(
        os.getenv("DATABASE_URL"),
        ssl="require",
        min_size=1,
        max_size=5
    )
    print("🟢 metrics_server conectado.")

# ---------------------------------------------------------
# ENDPOINT 1: Para que n8n sepa qué cuentas scrapear (CRON)
# ---------------------------------------------------------
@app.get("/users/active")
async def get_active_users(platform: str):
    """Devuelve usuarios verificados para que n8n los procese"""
    async with app.db_pool.acquire() as conn:
        users = await conn.fetch('''
            SELECT discord_id, username, platform 
            FROM social_accounts 
            WHERE is_verified = TRUE AND platform = $1
        ''', platform.lower())
    
    return [dict(u) for u in users]

# ---------------------------------------------------------
# ENDPOINT 2: Recibir Métricas (Separado por plataforma)
# ---------------------------------------------------------
@app.post("/metrics/ingest")
async def save_metrics(payload: MetricsPayload):
    """Guarda o actualiza métricas recibidas de n8n"""
    print(f"📩 Métricas recibidas para {payload.platform} ({len(payload.videos)} videos)")
    
    # 1. LÓGICA DE SELECCIÓN DE TABLA (Aquí faltaba Instagram)
    if payload.platform == "youtube":
        table_name = "tracked_posts"
        url_col = "post_url"
    elif payload.platform == "instagram":  # <--- NUEVO BLOQUE
        table_name = "tracked_posts_instagram"
        url_col = "instagram_url"
    else:
        # Por defecto TikTok
        table_name = "tracked_posts_tiktok"
        url_col = "tiktok_url"

    async with app.db_pool.acquire() as conn:
        for v in payload.videos:
            # ⚠️ CORRECCIÓN: Agregamos 'video_id' al INSERT y a los VALUES
            await conn.execute(f'''
                INSERT INTO {table_name} (discord_id, {url_col}, video_id, views, likes, shares)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT ({url_col})
                DO UPDATE SET 
                    views = EXCLUDED.views,
                    likes = EXCLUDED.likes,
                    shares = EXCLUDED.shares
            ''',
                str(payload.discord_id),
                v.url,
                v.video_id,  # <--- AHORA SÍ ENVIAMOS EL ID DEL VIDEO
                v.views,
                v.likes,
                v.shares
            )

    return {"status": "ok", "processed": len(payload.videos)}

# ---------------------------------------------------------
# ENDPOINT 3: Confirmar Verificación (Desde n8n)
# ---------------------------------------------------------
@app.post("/users/confirm-verification")
async def confirm_verification(payload: VerificationPayload):
    print(f"🕵️ n8n intentó verificar {payload.platform} para {payload.discord_id}. Resultado: {payload.is_verified}")
    
    # Si n8n dice que NO está verificado (código no encontrado)
    if not payload.is_verified:
        # Devolvemos verified: False para que el bot avise al usuario
        return {"status": "ignored", "verified": False, "reason": "code_not_found"}

    try:
        async with app.db_pool.acquire() as conn:
            await conn.execute('''
                UPDATE social_accounts 
                SET is_verified = TRUE, verified_at = NOW()
                WHERE discord_id = $1 AND platform = $2
            ''', payload.discord_id, payload.platform)
            
        return {"status": "success", "verified": True}
        
    except Exception as e:
        print(f"❌ Error DB verification: {e}")
        return {"status": "error", "verified": False, "message": str(e)}

async def start_metrics_server():
    port = int(os.getenv("PORT", 8000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()