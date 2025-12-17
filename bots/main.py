import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button
import os
import asyncpg
from datetime import datetime
import asyncio
import aiohttp
import json
import re
from dotenv import load_dotenv

load_dotenv()

# Variable global para el canal de campañas
CAMPAIGNS_CHANNEL_ID = int(os.getenv("CAMPAIGNS_CHANNEL_ID", "0"))

# ====================================================
#   FUNCIONES AUXILIARES (BOUNTY)
# ====================================================

async def calculate_bounty_earnings(conn, table, discord_id, post_url, bounty_tag, current_views):
    """Calcula y actualiza el total ganado en USD para un video en campaña"""
    
    rate = await conn.fetchrow(
        "SELECT amount_usd, per_views FROM bounty_rates WHERE bounty_tag = $1",
        bounty_tag
    )

    if not rate:
        return

    amount = float(rate["amount_usd"])
    per = int(rate["per_views"])

    video = await conn.fetchrow(
        f"SELECT starting_views, final_earned_usd FROM {table} WHERE post_url = $1",
        post_url
    )

    if not video:
        return

    starting = int(video["starting_views"] or 0)
    earned_before = float(video["final_earned_usd"] or 0)

    gained = max(current_views - starting, 0)
    earned_usd = round((gained / per) * amount, 4)

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
        self.db_pool = await asyncpg.create_pool(
            os.getenv('DATABASE_URL'),
            ssl='require',
            min_size=1,
            max_size=5
        )
        await self.create_tables()
        print("✅ Bot Principal - Base de datos conectada")
        self.bounty_task = asyncio.create_task(self.bounty_loop())

    async def create_tables(self):
        async with self.db_pool.acquire() as conn:
            # Usuarios y Cuentas (IDs como TEXT)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    discord_id TEXT PRIMARY KEY, 
                    username TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
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
            # Posts
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
            # Config y Campañas
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
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS bounty_rates (
                    id SERIAL PRIMARY KEY,
                    bounty_tag TEXT UNIQUE,
                    amount_usd NUMERIC,
                    per_views INT
                )
            ''')
            print("✅ Tablas verificadas")

    async def on_ready(self):
        print(f"🔵 {self.user} conectado (ID: {self.user.id})")
        try:
            synced = await self.tree.sync()
            print(f"🌍 Comandos globales sincronizados: {len(synced)}")
        except Exception as e:
            print(f"❌ Error sync: {e}")

    async def bounty_loop(self):
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                async with self.db_pool.acquire() as conn:
                    yt_posts = await conn.fetch("SELECT discord_id, post_url, bounty_tag, views FROM tracked_posts WHERE is_bounty = TRUE")
                    tt_posts = await conn.fetch("SELECT discord_id, tiktok_url AS post_url, bounty_tag, views FROM tracked_posts_tiktok WHERE is_bounty = TRUE")
                    all_posts = yt_posts + tt_posts

                    for post in all_posts:
                        is_youtube = "youtube.com" in post["post_url"] or "youtu.be" in post["post_url"]
                        table = "tracked_posts" if is_youtube else "tracked_posts_tiktok"
                        
                        await calculate_bounty_earnings(
                            conn, table, str(post['discord_id']), post['post_url'], post['bounty_tag'], post['views'] or 0
                        )
            except Exception as e:
                print(f"❌ Error en bounty_loop: {e}")
            await asyncio.sleep(300)

main_bot = MainBot()

# =============================================
# COMANDOS DE CAMPAÑAS (RESTAURADOS)
# =============================================

@main_bot.tree.command(name="publicar-campaña", description="Publicar campaña (Letras Grandes)")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    nombre="Nombre de la campaña (ej: Alix Earle)",
    descripcion="Frase gancho (ej: Gana dinero subiendo clips de...)",
    categoria="Ej: IRL, Gaming, Podcast",
    plataformas="Ej: TikTok, Instagram, YT Shorts",
    payrate="Ej: $0.60 por 1,000 vistas",
    invite_link="Link de Discord",
    thumbnail_url="Link DIRECTO a la imagen (.png/.jpg)"
)
async def publish_campaign(interaction: discord.Interaction, 
                           nombre: str, 
                           descripcion: str, 
                           categoria: str, 
                           plataformas: str,
                           payrate: str, 
                           invite_link: str, 
                           thumbnail_url: str = None):
    
    channel = interaction.client.get_channel(CAMPAIGNS_CHANNEL_ID)
    if not channel: 
        return await interaction.response.send_message("❌ Error: Canal no encontrado.", ephemeral=True)
    
    # 1. Guardar en Base de Datos (Igual que antes)
    async with main_bot.db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO campaigns (name, description, category, payrate, invite_link, thumbnail_url, created_by) 
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        ''', nombre, descripcion, categoria, payrate, invite_link, thumbnail_url, str(interaction.user.id))
    
    # 2. Embed con Títulos Grandes (Markdown ##)
    embed = discord.Embed(
        title=f"{nombre} x Latin Clipping", 
        description=f"### {descripcion} 🔥",  # Usamos ### para hacerlo un poco más grande
        color=0x00ff00
    )

    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)

    # --- SECCIÓN 1: DETALLES ---
    # Truco: name="\u200b" (invisible), y ponemos el Título con "##" dentro del value
    detalles_texto = (
        "## Detalles de la Campaña 🚀\n"
        f"**Categoría:** {categoria}\n"
        f"**Plataformas:** {plataformas}\n"
        f"**Audiencia:** Global 🌎"
    )
    embed.add_field(name="\u200b", value=detalles_texto, inline=False)

    # --- SECCIÓN 2: PAGO ---
    pago_texto = (
        "## Detalles de Pago 💸\n"
        f"**Sistema de Pago:** {payrate}\n"
        f"**Mínimo para Cobrar:** 10,000 vistas\n"
        f"**Método de Pago:** PayPal"
    )
    embed.add_field(name="\u200b", value=pago_texto, inline=False)

    # --- SECCIÓN 3: UNIRSE ---
    join_texto = (
        "## Unirse al Servidor ➡️\n"
        "¡Haz clic en el botón de abajo para empezar!"
    )
    embed.add_field(name="\u200b", value=join_texto, inline=False)

    # Footer y Botón
    embed.set_footer(text="Nota: 🚨 Violar las reglas de la campaña = Ban Instantáneo")

    class JoinButton(View):
        def __init__(self, link): 
            super().__init__()
            self.add_item(Button(label="Join Server", style=discord.ButtonStyle.link, url=link, emoji="➡️"))
    
    await channel.send(embed=embed, view=JoinButton(invite_link))
    await interaction.response.send_message("✅ Campaña publicada con estilo Gigante.", ephemeral=True)

@main_bot.tree.command(name="edit-campaign", description="Edita una campaña existente")
@app_commands.default_permissions(administrator=True)
async def edit_campaign(interaction: discord.Interaction, id_campaña: int, nombre: str = None, descripcion: str = None, payrate: str = None, invite_link: str = None):
    async with main_bot.db_pool.acquire() as conn:
        camp = await conn.fetchrow("SELECT * FROM campaigns WHERE id = $1", id_campaña)
        if not camp:
            await interaction.response.send_message("❌ Campaña no encontrada.", ephemeral=True)
            return

        new_name = nombre or camp["name"]
        new_desc = descripcion or camp["description"]
        new_rate = payrate or camp["payrate"]
        new_link = invite_link or camp["invite_link"]

        await conn.execute('''
            UPDATE campaigns SET name=$1, description=$2, payrate=$3, invite_link=$4 WHERE id=$5
        ''', new_name, new_desc, new_rate, new_link, id_campaña)

    embed = discord.Embed(title="✅ Campaña Actualizada", description=f"Campaña **{new_name}** editada.", color=0x00ff00)
    embed.add_field(name="💰 Payrate", value=new_rate)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@main_bot.tree.command(name="list-campaigns", description="Muestra campañas activas")
async def list_campaigns(interaction: discord.Interaction):
    async with main_bot.db_pool.acquire() as conn:
        campaigns = await conn.fetch("SELECT id, name, category, payrate FROM campaigns ORDER BY created_at DESC")

    if not campaigns:
        await interaction.response.send_message("⚠️ No hay campañas.", ephemeral=True)
        return

    embed = discord.Embed(title="📢 Campañas Activas", color=0x00ff00)
    for camp in campaigns:
        embed.add_field(name=f"🎯 {camp['name']} (ID: {camp['id']})", value=f"{camp['category']} | {camp['payrate']}", inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# =============================================
# COMANDO: INFO (DISEÑO RESTAURADO)
# =============================================
@main_bot.tree.command(name="info", description="Muestra estadísticas detalladas")
async def about(interaction: discord.Interaction):
    async with main_bot.db_pool.acquire() as conn:
        total_users = await conn.fetchval('SELECT COUNT(*) FROM users')
        total_verified = await conn.fetchval('SELECT COUNT(*) FROM social_accounts WHERE is_verified = true')
        total_accounts = await conn.fetchval('SELECT COUNT(*) FROM social_accounts')
        
        # Sumas totales (YT + TikTok)
        yt_count = await conn.fetchval('SELECT COUNT(*) FROM tracked_posts')
        tt_count = await conn.fetchval('SELECT COUNT(*) FROM tracked_posts_tiktok')
        total_posts = (yt_count or 0) + (tt_count or 0)
        
        yt_views = await conn.fetchval('SELECT COALESCE(SUM(views), 0) FROM tracked_posts')
        tt_views = await conn.fetchval('SELECT COALESCE(SUM(views), 0) FROM tracked_posts_tiktok')
        total_views = (yt_views or 0) + (tt_views or 0)
        
        yt_likes = await conn.fetchval('SELECT COALESCE(SUM(likes), 0) FROM tracked_posts')
        tt_likes = await conn.fetchval('SELECT COALESCE(SUM(likes), 0) FROM tracked_posts_tiktok')
        total_likes = (yt_likes or 0) + (tt_likes or 0)

        yt_shares = await conn.fetchval('SELECT COALESCE(SUM(shares), 0) FROM tracked_posts')
        tt_shares = await conn.fetchval('SELECT COALESCE(SUM(shares), 0) FROM tracked_posts_tiktok')
        total_shares = (yt_shares or 0) + (tt_shares or 0)
    
    bot_uptime = datetime.now() - main_bot.start_time
    hours, remainder = divmod(int(bot_uptime.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    
    embed = discord.Embed(title="🤖 Acerca de Clipping Bot", description="Plataforma líder para creadores de contenido y gestión de campañas", color=0x9146FF, timestamp=datetime.now())
    embed.add_field(name="📊 Estadísticas Globales", value=f"**👥 Usuarios Registrados:** {total_users}\n**📱 Cuentas Vinculadas:** {total_accounts}\n**✅ Cuentas Verificadas:** {total_verified}\n**🎬 Posts Trackeados:** {total_posts}\n**⏱️ Tiempo Activo:** {hours}h {minutes}m", inline=False)
    embed.add_field(name="📈 Métricas de Contenido", value=f"**👁️ Vistas Totales:** {total_views:,}\n**❤️ Likes Totales:** {total_likes:,}\n**🔄 Shares Totales:** {total_shares:,}", inline=False)
    embed.add_field(name="🔧 Información Técnica", value=f"**🟢 Estado:** Operativo\n**📡 Latencia:** {round(main_bot.latency * 1000)}ms\n**⚡ Versión:** 2.0.0\n**👨‍💻 Desarrollado por:** Latin Clipping", inline=False)
    embed.add_field(name="🎯 Características Principales", value="• Sistema de registro y verificación\n• Seguimiento automático de métricas\n• Gestión de pagos múltiples\n• Leaderboards competitivos\n• Detección de fraude\n• Soporte para múltiples plataformas", inline=False)
    embed.set_footer(text="💡 Usa /registrar para vincular tus cuentas")
    
    await interaction.response.send_message(embed=embed)

# =============================================
# OTROS COMANDOS (REGISTRAR, VERIFICAR, ETC)
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
    discord_id = str(interaction.user.id)
    verification_code = f"CLIP{interaction.user.id}{plataforma[:3].upper()}"
    plataforma = plataforma.lower()

    async with main_bot.db_pool.acquire() as conn:
        try:
            await conn.execute('INSERT INTO users (discord_id, username) VALUES ($1, $2) ON CONFLICT (discord_id) DO UPDATE SET username = $2', discord_id, str(interaction.user))
            await conn.execute('INSERT INTO social_accounts (discord_id, platform, username, verification_code, is_verified) VALUES ($1, $2, $3, $4, FALSE) ON CONFLICT (discord_id, platform, username) DO UPDATE SET verification_code = EXCLUDED.verification_code', discord_id, plataforma, usuario_limpio, verification_code)

            n8n_url = os.getenv(f"N8N_{plataforma.upper()}_WEBHOOK")
            if n8n_url:
                payload = {"discord_id": discord_id, "username": usuario_limpio, "platform": plataforma, "verification_code": verification_code, "tiktok_profile_url": f"https://www.tiktok.com/@{usuario_limpio}"}
                try:
                    async with aiohttp.ClientSession() as session:
                        await session.post(n8n_url, json=payload)
                except:
                    pass

            embed = discord.Embed(title="📝 Registro Iniciado", color=0x00ff00)
            embed.add_field(name="🔑 Código de Verificación", value=f"```{verification_code}```", inline=False)
            embed.add_field(name="Instrucciones", value=f"Pon el código en tu bio de **{plataforma}** y usa `/verificar`.", inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)

@main_bot.tree.command(name="verificar", description="Valida el código en tu bio")
async def verificar(interaction: discord.Interaction, plataforma: str, usuario: str):
    await interaction.response.defer(ephemeral=True)
    discord_id = str(interaction.user.id)
    plataforma = plataforma.lower()
    
    async with main_bot.db_pool.acquire() as conn:
        cuenta = await conn.fetchrow('SELECT * FROM social_accounts WHERE discord_id = $1 AND platform = $2', discord_id, plataforma)

    if not cuenta:
        await interaction.followup.send("❌ No registrado. Usa `/registrar` primero.", ephemeral=True)
        return
    if cuenta['is_verified']:
        await interaction.followup.send("✅ Ya estás verificado.", ephemeral=True)
        return

    webhook_url = os.getenv(f"N8N_VERIFY_WEBHOOK_{plataforma.upper()}")
    if not webhook_url:
        await interaction.followup.send("❌ Error de configuración (Webhook).", ephemeral=True)
        return

    payload = {"discord_id": discord_id, "username": cuenta['username'], "platform": plataforma, "verification_code": cuenta['verification_code']}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("verified"):
                        await interaction.followup.send("✅ ¡Verificado exitosamente!", ephemeral=True)
                    else:
                        await interaction.followup.send("❌ No encontramos el código en tu bio.", ephemeral=True)
                else:
                    await interaction.followup.send("❌ Error al contactar verificador.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

@main_bot.tree.command(name="mis-videos", description="Muestra tus videos trackeados")
async def mis_videos(interaction: discord.Interaction):
    discord_id = str(interaction.user.id)
    async with main_bot.db_pool.acquire() as conn:
        yt = await conn.fetch("SELECT post_url as url, views, likes, uploaded_at, 'YouTube' as platform FROM tracked_posts WHERE discord_id = $1", discord_id)
        tt = await conn.fetch("SELECT tiktok_url as url, views, likes, uploaded_at, 'TikTok' as platform FROM tracked_posts_tiktok WHERE discord_id = $1", discord_id)

    all_videos = [dict(v) for v in yt] + [dict(v) for v in tt]
    all_videos.sort(key=lambda x: x['uploaded_at'] or datetime.min, reverse=True)

    if not all_videos:
        await interaction.response.send_message("📭 No tienes videos registrados aún.", ephemeral=True)
        return

    embed = discord.Embed(title="🎬 Mis Videos", color=0x9146FF)
    for v in all_videos[:10]:
        emoji = "▶️" if v['platform'] == 'YouTube' else "🎵"
        embed.add_field(name=f"{emoji} {v['platform']}", value=f"[Link]({v['url']})\n👁️ {v['views']} | ❤️ {v['likes']}", inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@main_bot.tree.command(name="set-bounty", description="Activa campaña en un video")
@app_commands.default_permissions(administrator=True)
async def set_bounty(interaction: discord.Interaction, plataforma: str, post_url: str, bounty_tag: str):
    plataforma = plataforma.lower()
    table = "tracked_posts" if plataforma == "youtube" else "tracked_posts_tiktok"
    url_col = "post_url" if plataforma == "youtube" else "tiktok_url"

    async with main_bot.db_pool.acquire() as conn:
        exists = await conn.fetchval(f"SELECT 1 FROM {table} WHERE {url_col} = $1", post_url)
        if not exists:
            await interaction.response.send_message("❌ Video no encontrado en DB.", ephemeral=True)
            return
        await conn.execute(f"UPDATE {table} SET is_bounty = TRUE, bounty_tag = $1, starting_views = views WHERE {url_col} = $2", bounty_tag, post_url)
    
    await interaction.response.send_message(f"✅ Bounty **{bounty_tag}** activado.")

@main_bot.tree.command(name="set-bounty-rate", description="Configura el pago por views")
@app_commands.default_permissions(administrator=True)
async def set_bounty_rate(interaction: discord.Interaction, bounty_tag: str, amount_usd: float, per_views: int):
    async with main_bot.db_pool.acquire() as conn:
        await conn.execute('INSERT INTO bounty_rates (bounty_tag, amount_usd, per_views) VALUES ($1, $2, $3) ON CONFLICT (bounty_tag) DO UPDATE SET amount_usd = EXCLUDED.amount_usd, per_views = EXCLUDED.per_views', bounty_tag, amount_usd, per_views)
    await interaction.response.send_message(f"✅ Tarifa configurada para {bounty_tag}: ${amount_usd} cada {per_views} views.", ephemeral=True)

@main_bot.tree.command(name="add-paypal", description="Configura tu PayPal")
async def add_paypal(interaction: discord.Interaction, email: str, nombre: str, apellido: str):
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        await interaction.response.send_message("❌ Email inválido.", ephemeral=True)
        return
    discord_id = str(interaction.user.id)
    async with main_bot.db_pool.acquire() as conn:
        await conn.execute('INSERT INTO users (discord_id, username) VALUES ($1, $2) ON CONFLICT (discord_id) DO NOTHING', discord_id, str(interaction.user))
        await conn.execute('INSERT INTO payment_methods (discord_id, method_type, paypal_email, first_name, last_name) VALUES ($1, $2, $3, $4, $5) ON CONFLICT (discord_id, method_type) DO UPDATE SET paypal_email = $2, first_name = $3, last_name = $4', discord_id, 'paypal', email, nombre, apellido)
    await interaction.response.send_message("✅ PayPal guardado.", ephemeral=True)

@main_bot.tree.command(name="payment-details", description="Ver tus datos de pago")
async def payment_details(interaction: discord.Interaction):
    discord_id = str(interaction.user.id)
    async with main_bot.db_pool.acquire() as conn:
        data = await conn.fetchrow('SELECT * FROM payment_methods WHERE discord_id = $1 AND method_type = $2', discord_id, 'paypal')
    if data:
        await interaction.response.send_message(f"📧 PayPal: {data['paypal_email']} ({data['first_name']} {data['last_name']})", ephemeral=True)
    else:
        await interaction.response.send_message("❌ No has configurado PayPal.", ephemeral=True)

@main_bot.tree.command(name="sync", description="Sincronizar comandos")
@app_commands.default_permissions(administrator=True)
async def sync(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        s = await main_bot.tree.sync()
        await interaction.followup.send(f"✅ Sincronizados {len(s)} comandos.")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

if __name__ == "__main__":
    token = os.getenv('DISCORD_MAIN_BOT_TOKEN')
    main_bot.run(token)