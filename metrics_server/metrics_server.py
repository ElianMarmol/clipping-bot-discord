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

app.on_event("startup")
async def startup():
    print("⏳ Conectando metrics_server a DB...")
    app.db_pool = await asyncpg.create_pool(
        os.getenv("DATABASE_URL"),
        ssl="require",
        min_size=1,
        max_size=5
    )
    
    # --- AUTO-FIX DE BASE DE DATOS (EL DOCTOR 👨‍⚕️) ---
    print("🔧 Ejecutando mantenimiento de tablas...")
    async with app.db_pool.acquire() as conn:
        tables = ["tracked_posts", "tracked_posts_tiktok", "tracked_posts_instagram"]
        for table in tables:
            try:
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS video_id TEXT;")
                
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS shares INTEGER DEFAULT 0;")
                
                print(f"✅ Columnas verificadas en {table}")
                
            except Exception as e:
                print(f"⚠️ Nota sobre {table}: {e}")
    # ---------------------------------------------------

    print("🟢 metrics_server conectado y tablas actualizadas.")

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
# ENDPOINT 2: Recibir Métricas Y CALCULAR DINERO
# ---------------------------------------------------------
@app.post("/metrics/ingest")
async def save_metrics(payload: MetricsPayload):
    print(f"📩 Métricas recibidas para {payload.platform} ({len(payload.videos)} videos)")
    
    # 1. Definir la TARIFA (Esto hace que sea real)
    # $0.025 por cada 1,000 vistas
    RATE_PER_1K = 0.025 

    # 2. Seleccionar la tabla y columna correcta según la red social
    if payload.platform == "youtube":
        table_name = "tracked_posts"
        url_col = "post_url"
    elif payload.platform == "instagram":
        table_name = "tracked_posts_instagram"
        url_col = "instagram_url"
    else:
        table_name = "tracked_posts_tiktok"
        url_col = "tiktok_url"

    async with app.db_pool.acquire() as conn:
        for v in payload.videos:
            # 3. 🧮 CÁLCULO MATEMÁTICO AUTOMÁTICO
            # Si tiene 10,000 vistas -> (10000 / 1000) * 0.025 = $0.25
            dinero_generado = (v.views / 1000) * RATE_PER_1K
            
            # 4. GUARDAR TODO EN LA BASE DE DATOS (Vistas + Dinero)
            # Fíjate que ahora insertamos 'final_earned_usd'
            await conn.execute(f'''
                INSERT INTO {table_name} (discord_id, {url_col}, video_id, views, likes, shares, final_earned_usd)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT ({url_col})  
                DO UPDATE SET 
                    views = EXCLUDED.views,
                    likes = EXCLUDED.likes,
                    shares = EXCLUDED.shares,
                    final_earned_usd = EXCLUDED.final_earned_usd  -- 🔄 Actualiza el dinero si suben las vistas
            ''',
                str(payload.discord_id),
                v.url,
                v.video_id,
                v.views,
                v.likes,
                v.shares,
                dinero_generado  # <--- Aquí va el valor calculado automáticamente
            )

    return {
        "status": "ok", 
        "processed": len(payload.videos), 
        "mode": "REAL_MONEY_CALCULATION"
    }

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