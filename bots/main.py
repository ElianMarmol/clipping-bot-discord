import discord
from discord.ext import commands
from discord import app_commands
import os
import asyncpg
from datetime import datetime
import asyncio
import aiohttp
import json
from dotenv import load_dotenv

load_dotenv()

# ====================================================
#   FUNCIONES AUXILIARES
# ====================================================

async def calculate_bounty_earnings(conn, table, discord_id, post_url, bounty_tag, current_views):
    """Calcula y actualiza el total ganado en USD para un video en campaña"""
    # Verificamos la tarifa de la campaña
    rate = await conn.fetchrow(
        "SELECT amount_usd, per_views FROM bounty_rates WHERE bounty_tag = $1",
        bounty_tag
    )

    if not rate:
        return

    amount = float(rate["amount_usd"])
    per = int(rate["per_views"])

    # Obtenemos datos del video (views iniciales y ganancia previa)
    video = await conn.fetchrow(
        f"SELECT starting_views, final_earned_usd FROM {table} WHERE post_url = $1",
        post_url
    )

    if not video:
        return

    starting = int(video["starting_views"] or 0)
    earned_before = float(video["final_earned_usd"] or 0)

    # Cálculo de ganancias (Views actuales - Views iniciales)
    gained = max(current_views - starting, 0)
    earned_usd = round((gained / per) * amount, 4)

    # Solo actualizamos si hubo cambios
    if earned_usd != earned_before:
        await conn.execute(
            f"UPDATE {table} SET final_earned_usd = $1 WHERE post_url = $2",
            earned_usd, post_url
        )

# ====================================================
#   CLASE PRINCIPAL DEL BOT
# ====================================================

class MainBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        
        super().__init__(
            command_prefix='!',
            intents=intents,
            help_command=None
        )
        self.db_pool = None
        self.start_time = datetime.now()

    async def setup_hook(self):
        # Conexión a Base de Datos
        self.db_pool = await asyncpg.create_pool(
            os.getenv('DATABASE_URL'),
            ssl='require',
            min_size=1,
            max_size=5
        )

        await self.create_tables()
        print("✅ Bot Principal - Base de datos conectada")
        
        # Iniciar loop de pagos en segundo plano
        self.bounty_task = asyncio.create_task(self.bounty_loop())

    async def create_tables(self):
        async with self.db_pool.acquire() as conn:
            # ⚠️ CAMBIO IMPORTANTE: Usamos TEXT para discord_id para evitar problemas de INT/STR
            
            # 1. Tabla de Usuarios
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    discord_id TEXT PRIMARY KEY, 
                    username TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')

            # 2. Cuentas Sociales
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS social_accounts (
                    id SERIAL PRIMARY KEY,
                    discord_id TEXT,
                    platform TEXT,
                    username TEXT,
                    verification_code TEXT,
                    is_verified BOOLEAN DEFAULT FALSE,
                    verified_at TIMESTAMP,
                    FOREIGN KEY (discord_id) REFERENCES users(discord_id),
                    UNIQUE (discord_id, platform, username)
                )
            ''')

            # 3. Métodos de Pago
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS payment_methods (
                    id SERIAL PRIMARY KEY,
                    discord_id TEXT,
                    method_type TEXT,
                    paypal_email TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    added_at TIMESTAMP DEFAULT NOW(),
                    FOREIGN KEY (discord_id) REFERENCES users(discord_id),
                    UNIQUE (discord_id, method_type)
                )
            ''')

            # 4. Posts YouTube
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS tracked_posts (
                    id SERIAL PRIMARY KEY,
                    discord_id TEXT,
                    post_url TEXT UNIQUE,
                    video_id TEXT,
                    is_bounty BOOLEAN DEFAULT FALSE,
                    bounty_tag TEXT,
                    uploaded_at TIMESTAMP DEFAULT NOW(),
                    views INTEGER DEFAULT 0,
                    likes INTEGER DEFAULT 0,
                    shares INTEGER DEFAULT 0,
                    starting_views INTEGER DEFAULT 0,
                    final_earned_usd NUMERIC DEFAULT 0
                )
            ''')

            # 5. Posts TikTok
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS tracked_posts_tiktok (
                    id SERIAL PRIMARY KEY,
                    discord_id TEXT,
                    tiktok_url TEXT UNIQUE,
                    video_id TEXT,
                    is_bounty BOOLEAN DEFAULT FALSE,
                    bounty_tag TEXT,
                    uploaded_at TIMESTAMP DEFAULT NOW(),
                    views INTEGER DEFAULT 0,
                    likes INTEGER DEFAULT 0,
                    shares INTEGER DEFAULT 0,
                    starting_views INTEGER DEFAULT 0,
                    final_earned_usd NUMERIC DEFAULT 0
                )
            ''')
            
            # 6. Configuración Server
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS server_settings (
                    guild_id BIGINT PRIMARY KEY,
                    attachmentspam_enabled BOOLEAN DEFAULT TRUE,
                    attachmentspam_limit INTEGER DEFAULT 5,
                    attachmentspam_timeframe INTEGER DEFAULT 10,
                    attachmentspam_punishment TEXT DEFAULT 'warn',
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')

            # 7. Campañas
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS campaigns (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    category TEXT,
                    payrate TEXT,
                    invite_link TEXT,
                    thumbnail_url TEXT,
                    created_by TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # 8. Tarifas de Bounties
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS bounty_rates (
                    id SERIAL PRIMARY KEY,
                    bounty_tag TEXT UNIQUE,
                    amount_usd NUMERIC,
                    per_views INT
                )
            ''')

            print("✅ Tablas verificadas y listas.")

    async def on_ready(self):
        print(f"🔵 {self.user} conectado (ID: {self.user.id})")
        # Sincronización Global de Comandos
        try:
            synced = await self.tree.sync()
            print(f"🌍 Comandos globales sincronizados: {len(synced)}")
        except Exception as e:
            print(f"❌ Error sync global: {e}")

    async def bounty_loop(self):
        """Loop infinito: revisa videos en campaña y actualiza ganancias"""
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                async with self.db_pool.acquire() as conn:
                    # Traer videos activos de YouTube
                    yt_posts = await conn.fetch("""
                        SELECT discord_id, post_url, bounty_tag, views
                        FROM tracked_posts
                        WHERE is_bounty = TRUE
                    """)

                    # Traer videos activos de TikTok (mapeando tiktok_url a post_url)
                    tt_posts = await conn.fetch("""
                        SELECT discord_id, tiktok_url AS post_url, bounty_tag, views
                        FROM tracked_posts_tiktok
                        WHERE is_bounty = TRUE
                    """)

                    all_posts = yt_posts + tt_posts

                    for post in all_posts:
                        # Detectar plataforma por URL
                        is_youtube = "youtube.com" in post["post_url"] or "youtu.be" in post["post_url"]
                        table = "tracked_posts" if is_youtube else "tracked_posts_tiktok"
                        
                        await calculate_bounty_earnings(
                            conn,
                            table,
                            str(post['discord_id']),
                            post['post_url'],
                            post['bounty_tag'],
                            post['views'] or 0
                        )
            except Exception as e:
                print(f"❌ Error en bounty_loop: {e}")
            
            await asyncio.sleep(300) # Esperar 5 minutos

main_bot = MainBot()

# =============================================
# COMANDO: REGISTRAR (Conecta con n8n)
# =============================================
@main_bot.tree.command(name="registrar", description="Registra tus cuentas de redes sociales")
@app_commands.describe(plataforma="Plataforma", usuario="Tu usuario")
@app_commands.choices(plataforma=[
    app_commands.Choice(name="TikTok", value="tiktok"),
    app_commands.Choice(name="YouTube", value="youtube"),
    app_commands.Choice(name="Instagram", value="instagram")
])
async def registrar(interaction: discord.Interaction, plataforma: str, usuario: str):
    await interaction.response.defer(ephemeral=True)
    
    usuario_limpio = usuario.lstrip('@')
    discord_id = str(interaction.user.id) # ⚠️ ID como String
    verification_code = f"CLIP{interaction.user.id}{plataforma[:3].upper()}"
    plataforma = plataforma.lower()

    async with main_bot.db_pool.acquire() as conn:
        try:
            # Upsert Usuario
            await conn.execute('''
                INSERT INTO users (discord_id, username) VALUES ($1, $2)
                ON CONFLICT (discord_id) DO UPDATE SET username = $2
            ''', discord_id, str(interaction.user))

            # Upsert Cuenta Social
            await conn.execute('''
                INSERT INTO social_accounts (discord_id, platform, username, verification_code, is_verified)
                VALUES ($1, $2, $3, $4, FALSE)
                ON CONFLICT (discord_id, platform, username) 
                DO UPDATE SET verification_code = EXCLUDED.verification_code
            ''', discord_id, plataforma, usuario_limpio, verification_code)

            # Notificar a n8n (Opcional, para iniciar scraping inicial si se desea)
            n8n_url = os.getenv(f"N8N_{plataforma.upper()}_WEBHOOK")
            if n8n_url:
                payload = {
                    "discord_id": discord_id,
                    "username": usuario_limpio,
                    "platform": plataforma,
                    "verification_code": verification_code,
                    "tiktok_profile_url": f"https://www.tiktok.com/@{usuario_limpio}"
                }
                # Ejecutar llamada sin bloquear si falla
                try:
                    async with aiohttp.ClientSession() as session:
                        await session.post(n8n_url, json=payload)
                except Exception as n8n_error:
                    print(f"⚠️ Aviso: No se pudo contactar a n8n en el registro: {n8n_error}")

            # Respuesta al usuario
            embed = discord.Embed(title="📝 Registro Iniciado", color=0x00ff00)
            embed.add_field(name="🔑 Código de Verificación", value=f"```{verification_code}```", inline=False)
            embed.add_field(name="Instrucciones", value=f"1. Pon este código en tu bio de **{plataforma}**.\n2. Espera unos segundos.\n3. Usa `/verificar`.", inline=False)
            
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ Error al registrar: {str(e)}", ephemeral=True)

# =============================================
# COMANDO: VERIFICAR (Llama a n8n Verify)
# =============================================
@main_bot.tree.command(name="verificar", description="Valida el código en tu bio usando n8n")
@app_commands.describe(plataforma="Plataforma a verificar", usuario="Tu usuario")
@app_commands.choices(plataforma=[
    app_commands.Choice(name="TikTok", value="tiktok"),
    app_commands.Choice(name="YouTube", value="youtube"),
    app_commands.Choice(name="Instagram", value="instagram")
])
async def verificar(interaction: discord.Interaction, plataforma: str, usuario: str):
    await interaction.response.defer(ephemeral=True)
    
    discord_id = str(interaction.user.id) # ⚠️ ID como String
    plataforma = plataforma.lower()
    
    # 1. Verificar si existe en DB
    async with main_bot.db_pool.acquire() as conn:
        cuenta = await conn.fetchrow(
            'SELECT * FROM social_accounts WHERE discord_id = $1 AND platform = $2',
            discord_id, plataforma
        )

    if not cuenta:
        await interaction.followup.send(f"❌ No tienes cuenta registrada. Usa `/registrar` primero.", ephemeral=True)
        return

    if cuenta['is_verified']:
        await interaction.followup.send("✅ Esta cuenta ya está verificada.", ephemeral=True)
        return

    # 2. Llamar al Webhook de Verificación de n8n
    webhook_url = os.getenv(f"N8N_VERIFY_WEBHOOK_{plataforma.upper()}")
    if not webhook_url:
        await interaction.followup.send("❌ Error de configuración: No hay webhook de verificación definido.", ephemeral=True)
        return

    payload = {
        "discord_id": discord_id,
        "username": cuenta['username'],
        "platform": plataforma,
        "verification_code": cuenta['verification_code']
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("verified"):
                        embed = discord.Embed(title="✅ ¡Verificado!", description="Código encontrado correctamente.", color=0x00ff00)
                        await interaction.followup.send(embed=embed, ephemeral=True)
                    else:
                        embed = discord.Embed(title="❌ No verificado", description=f"No encontramos el código `{cuenta['verification_code']}` en tu bio. Intenta de nuevo en unos momentos.", color=0xff0000)
                        await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.followup.send(f"❌ Error de comunicación con n8n (Status: {resp.status}).", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error interno al verificar: {e}", ephemeral=True)

# =============================================
# COMANDO: MIS VIDEOS (Muestra YT + TikTok)
# =============================================
@main_bot.tree.command(name="mis-videos", description="Muestra tus videos trackeados")
async def mis_videos(interaction: discord.Interaction):
    discord_id = str(interaction.user.id) # ⚠️ ID como String

    async with main_bot.db_pool.acquire() as conn:
        # YouTube
        yt = await conn.fetch('''
            SELECT post_url as url, views, likes, shares, uploaded_at, 'YouTube' as platform
            FROM tracked_posts WHERE discord_id = $1
        ''', discord_id)

        # TikTok
        tt = await conn.fetch('''
            SELECT tiktok_url as url, views, likes, shares, uploaded_at, 'TikTok' as platform
            FROM tracked_posts_tiktok WHERE discord_id = $1
        ''', discord_id)

    # Combinar resultados
    all_videos = [dict(v) for v in yt] + [dict(v) for v in tt]
    
    # Ordenar por fecha (manejar casos donde uploaded_at sea None)
    all_videos.sort(key=lambda x: x['uploaded_at'] or datetime.min, reverse=True)

    if not all_videos:
        await interaction.response.send_message("📭 No tienes videos registrados aún. Recuerda que se actualizan cada 6 horas.", ephemeral=True)
        return

    embed = discord.Embed(title="🎬 Mis Videos Trackeados", color=0x9146FF)
    
    # Mostrar solo los 10 más recientes
    for v in all_videos[:10]:
        emoji = "▶️" if v['platform'] == 'YouTube' else "🎵"
        embed.add_field(
            name=f"{emoji} {v['platform']}",
            value=f"[Link]({v['url']})\n👁️ {v['views'] or 0} | ❤️ {v['likes'] or 0}",
            inline=False
        )
    
    if len(all_videos) > 10:
        embed.set_footer(text=f"Mostrando 10 de {len(all_videos)} videos totales.")

    await interaction.response.send_message(embed=embed, ephemeral=True)

# =============================================
# COMANDO: INFO (Estadísticas Totales)
# =============================================
@main_bot.tree.command(name="info", description="Estadísticas del bot")
async def info(interaction: discord.Interaction):
    async with main_bot.db_pool.acquire() as conn:
        users = await conn.fetchval('SELECT COUNT(*) FROM users')
        
        # Sumar posts de ambas tablas
        yt_count = await conn.fetchval('SELECT COUNT(*) FROM tracked_posts')
        tt_count = await conn.fetchval('SELECT COUNT(*) FROM tracked_posts_tiktok')
        total_posts = (yt_count or 0) + (tt_count or 0)

        # Sumar views de ambas tablas
        yt_views = await conn.fetchval('SELECT COALESCE(SUM(views), 0) FROM tracked_posts')
        tt_views = await conn.fetchval('SELECT COALESCE(SUM(views), 0) FROM tracked_posts_tiktok')
        total_views = (yt_views or 0) + (tt_views or 0)

    embed = discord.Embed(title="📊 Estadísticas del Bot", color=0x00ff00)
    embed.add_field(name="👥 Usuarios", value=str(users), inline=True)
    embed.add_field(name="🎬 Videos Trackeados", value=str(total_posts), inline=True)
    embed.add_field(name="👁️ Vistas Totales", value=f"{total_views:,}", inline=True)
    
    await interaction.response.send_message(embed=embed)

# =============================================
# COMANDO: SET BOUNTY (Manual)
# =============================================
@main_bot.tree.command(name="set-bounty", description="Activa campaña en un video")
@app_commands.describe(plataforma="youtube o tiktok", post_url="URL del video", bounty_tag="Tag de campaña")
@app_commands.default_permissions(administrator=True)
async def set_bounty(interaction: discord.Interaction, plataforma: str, post_url: str, bounty_tag: str):
    plataforma = plataforma.lower()
    if plataforma not in ["youtube", "tiktok"]:
        await interaction.response.send_message("❌ Plataforma inválida.", ephemeral=True)
        return

    table = "tracked_posts" if plataforma == "youtube" else "tracked_posts_tiktok"
    url_col = "post_url" if plataforma == "youtube" else "tiktok_url"

    async with main_bot.db_pool.acquire() as conn:
        # Verificar si existe
        exists = await conn.fetchval(f"SELECT 1 FROM {table} WHERE {url_col} = $1", post_url)
        if not exists:
            await interaction.response.send_message(f"❌ El video no está en la base de datos ({table}). Asegúrate de que el scraper ya lo haya detectado.", ephemeral=True)
            return

        # Activar bounty
        await conn.execute(f'''
            UPDATE {table}
            SET is_bounty = TRUE, bounty_tag = $1, starting_views = views, final_earned_usd = 0
            WHERE {url_col} = $2
        ''', bounty_tag, post_url)

    await interaction.response.send_message(f"✅ Bounty **{bounty_tag}** activado para el video.")

# =============================================
# COMANDOS DE PAGO Y UTILIDADES (Sin cambios mayores)
# =============================================

@main_bot.tree.command(name="add-paypal", description="Configura tu PayPal")
async def add_paypal(interaction: discord.Interaction, email: str, nombre: str, apellido: str):
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        await interaction.response.send_message("❌ Email inválido.", ephemeral=True)
        return

    discord_id = str(interaction.user.id)
    async with main_bot.db_pool.acquire() as conn:
        await conn.execute('INSERT INTO users (discord_id, username) VALUES ($1, $2) ON CONFLICT (discord_id) DO NOTHING', discord_id, str(interaction.user))
        await conn.execute('''
            INSERT INTO payment_methods (discord_id, method_type, paypal_email, first_name, last_name)
            VALUES ($1, 'paypal', $2, $3, $4)
            ON CONFLICT (discord_id, method_type) DO UPDATE SET paypal_email = $2, first_name = $3, last_name = $4
        ''', discord_id, email, nombre, apellido)
    await interaction.response.send_message("✅ PayPal guardado exitosamente.", ephemeral=True)

@main_bot.tree.command(name="payment-details", description="Ver tus datos de pago")
async def payment_details(interaction: discord.Interaction):
    discord_id = str(interaction.user.id)
    async with main_bot.db_pool.acquire() as conn:
        data = await conn.fetchrow('SELECT * FROM payment_methods WHERE discord_id = $1 AND method_type = $2', discord_id, 'paypal')
    
    if data:
        await interaction.response.send_message(f"📧 PayPal: {data['paypal_email']} ({data['first_name']} {data['last_name']})", ephemeral=True)
    else:
        await interaction.response.send_message("❌ No has configurado PayPal.", ephemeral=True)

@main_bot.tree.command(name="sync", description="Sincronizar comandos (Admin)")
@app_commands.default_permissions(administrator=True)
async def sync(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        synced = await main_bot.tree.sync()
        await interaction.followup.send(f"✅ Sincronizados {len(synced)} comandos.")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

# =============================================
# EJECUCIÓN
# =============================================
if __name__ == "__main__":
    token = os.getenv('DISCORD_MAIN_BOT_TOKEN')
    if not token:
        print("❌ ERROR: Falta TOKEN")
        exit(1)
    main_bot.run(token)