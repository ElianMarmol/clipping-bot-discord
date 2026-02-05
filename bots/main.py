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
# HELPER: DETECTOR DE PLATAFORMAS (Pon esto al inicio)
# ====================================================
def detectar_plataforma(url: str):
    url = url.lower().strip()
    if "tiktok.com" in url:
        return "tiktok", "tracked_posts_tiktok", "tiktok_url"
    elif "youtube.com" in url or "youtu.be" in url:
        return "youtube", "tracked_posts", "post_url"
    elif "instagram.com" in url:
        return "instagram", "tracked_posts_instagram", "instagram_url"
    return None, None, None

# ==========================================
# CLASE: VISTA DE REGISTRO (Botón Azul)
# ==========================================
class RegistrationView(discord.ui.View):
    def __init__(self):
        # timeout=None es CRÍTICO para que el botón funcione para siempre
        super().__init__(timeout=None)

    @discord.ui.button(label="Registrarse", style=discord.ButtonStyle.blurple, custom_id="latin_clipping:register_btn")
    async def register_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # --- TEXTO DEL MENSAJE 2 (Instrucciones Ocultas) ---
        texto_instrucciones = """
**¡Nos alegra que hayas elegido contribuir en Latin Clipping!** 🚀

Nuestros registros indican que quizás aún no has vinculado tus cuentas. Vamos a solucionarlo.

**1. Vincula tus redes** 🔗
Usa el comando `/registrar` seguido de la plataforma y tu usuario.
> *Ejemplo: `/registrar tiktok @miusuario`*

**2. Verifica tu propiedad** ✅
Una vez añadida, usa el comando `/verificar` para obtener tu código secreto y ponlo en tu biografía.
> *Ejemplo: `/verificar tiktok @miusuario`*

**3. Configura tu pago** 💸
Es vital para poder cobrar.
> *Ejemplo: `/add-paypal tu@email.com Nombre Apellido`*

**¡Ahora la mejor parte!**
Lee los requisitos de la campaña en los canales correspondientes y ¡empieza a subir clips!

Si necesitas ayuda, únete a nuestro soporte o abre un ticket.
**¡Gracias por elegir Latin Clipping!**
"""
        embed = discord.Embed(
            title="Bienvenido a Latin Clipping (Panel de Usuario)",
            description=texto_instrucciones,
            color=0x3498db # Azul estilo Clipping
        )
        embed.set_footer(text="Latin Clipping 2025")
        
        # Enviamos el mensaje oculto (ephemeral=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)           
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
        self.add_view(RegistrationView())
        print("👀 Vista de Registro cargada y persistente.")

    async def create_tables(self):
        async with self.db_pool.acquire() as conn:
            # --- 1. USUARIOS Y CUENTAS ---
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

            # --- 2. POSTS (YouTube, TikTok e Instagram) ---
            # Definimos las columnas comunes para no repetir código y evitar errores
            common_columns = """
                id SERIAL PRIMARY KEY,
                discord_id TEXT,
                video_id TEXT,
                is_bounty BOOLEAN DEFAULT FALSE,
                bounty_tag TEXT,
                uploaded_at TIMESTAMP DEFAULT NOW(),
                views INTEGER DEFAULT 0,
                likes INTEGER DEFAULT 0,
                shares INTEGER DEFAULT 0,
                starting_views INTEGER DEFAULT 0,
                final_earned_usd NUMERIC DEFAULT 0
            """
            
            # YouTube
            await conn.execute(f'''
                CREATE TABLE IF NOT EXISTS tracked_posts (
                    {common_columns},
                    post_url TEXT UNIQUE
                )
            ''')
            
            # TikTok
            await conn.execute(f'''
                CREATE TABLE IF NOT EXISTS tracked_posts_tiktok (
                    {common_columns},
                    tiktok_url TEXT UNIQUE
                )
            ''')
            
            # Instagram (NUEVO: Agregado para que no falle /stats)
            await conn.execute(f'''
                CREATE TABLE IF NOT EXISTS tracked_posts_instagram (
                    {common_columns},
                    instagram_url TEXT UNIQUE
                )
            ''')

            # --- 3. CONFIGURACIÓN Y CAMPAÑAS ---
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
            
            # Actualizamos definición de campañas (message_id, platforms, etc)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS campaigns (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    category TEXT,
                    platforms TEXT,
                    payrate TEXT,
                    invite_link TEXT,
                    thumbnail_url TEXT,
                    message_id TEXT,
                    channel_id TEXT,
                    created_by TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # --- 4. PRECIOS (NUEVO) ---
            # Tabla centralizada para manejar precios estándar y bounties
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS payment_rates (
                    rate_key TEXT PRIMARY KEY,
                    amount_per_1k NUMERIC DEFAULT 0.60,
                    description TEXT
                )
            ''')
            
            # Mantenemos bounty_rates por compatibilidad si la usas en otro lado, 
            # pero el sistema nuevo usa payment_rates
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS bounty_rates (
                    id SERIAL PRIMARY KEY,
                    bounty_tag TEXT UNIQUE,
                    amount_usd NUMERIC,
                    per_views INT
                )
            ''')
            
            print("✅ Tablas verificadas y actualizadas (Estructura Completa)")
            
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
                        # 1. Traemos de las 3 tablas
                        yt_posts = await conn.fetch("SELECT discord_id, post_url, bounty_tag, views FROM tracked_posts WHERE is_bounty = TRUE")
                        tt_posts = await conn.fetch("SELECT discord_id, tiktok_url AS post_url, bounty_tag, views FROM tracked_posts_tiktok WHERE is_bounty = TRUE")
                        ig_posts = await conn.fetch("SELECT discord_id, instagram_url AS post_url, bounty_tag, views FROM tracked_posts_instagram WHERE is_bounty = TRUE") # <--- FALTABA ESTO
                        
                        all_posts = yt_posts + tt_posts + ig_posts

                        for post in all_posts:
                            url = post["post_url"]
                            # Lógica para elegir tabla
                            if "youtube.com" in url or "youtu.be" in url:
                                table = "tracked_posts"
                            elif "instagram.com" in url:
                                table = "tracked_posts_instagram"
                            else:
                                table = "tracked_posts_tiktok"
                            
                            await calculate_bounty_earnings(
                                conn, table, str(post['discord_id']), url, post['bounty_tag'], post['views'] or 0
                            )
                except Exception as e:
                    print(f"❌ Error en bounty_loop: {e}")
                await asyncio.sleep(300)

main_bot = MainBot()

# =============================================
# COMANDOS DE CAMPAÑAS (RESTAURADOS)
# =============================================

@main_bot.tree.command(name="publicar-campaña", description="Publicar campaña oficial")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    nombre="Nombre de la campaña",
    descripcion="Frase gancho (ej: Gana dinero posteando clips)",
    categoria="Ej: IRL, Gaming, Podcast",
    plataformas="Ej: TikTok, Instagram, Youtube",
    payrate="Ej: $0.60 per 1,000 views",
    invite_link="Link del Servidor de Discord",
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
    
    # 1. Usar el canal actual
    channel = interaction.channel
    
    # 2. Guardar en Base de Datos (Insertar y obtener ID)
    try:
        async with main_bot.db_pool.acquire() as conn:
            # Agregamos 'platforms' al insert y usamos RETURNING id
            campaign_id = await conn.fetchval('''
                INSERT INTO campaigns (name, description, category, platforms, payrate, invite_link, thumbnail_url, created_by) 
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
            ''', nombre, descripcion, categoria, plataformas, payrate, invite_link, thumbnail_url, str(interaction.user.id))
    except Exception as e:
        return await interaction.response.send_message(f"❌ Error guardando en DB: {e}", ephemeral=True)
    
    # 3. Construir el contenido
    texto_contenido = f"""
**{descripcion}** 🔥

## Detalles de campaña 🚀
**Categoría:** {categoria}
**Plataformas:** {plataformas}
**Audiencia:** Global 🌎

## Detalles de pago 💸
**Sistema de pago:** {payrate}
**Minimo de Views para Pago:** 10,000 views
**Método de Pago:** PayPal

## Únete al servidor ➡️
Click en el boton debajo para Empezar!
"""

    # 4. Crear Embed
    embed = discord.Embed(
        title=f"{nombre} x Latin Clipping", 
        description=texto_contenido, 
        color=0x00ff00 # Verde Clipping
    )

    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)

    # Footer con el ID para referencia
    embed.set_footer(text=f"Campaña ID: {campaign_id} | Nota: 🚨 Violacion en reglas de Campaña = Insta-Ban")

    # 5. Botón
    class JoinButton(View):
        def __init__(self, link): 
            super().__init__()
            self.add_item(Button(label="Join Server", style=discord.ButtonStyle.link, url=link, emoji="➡️"))
    
    # 6. ENVIAR MENSAJE
    msg = await channel.send(embed=embed, view=JoinButton(invite_link))
    
    # 7. CRÍTICO: Actualizar la DB con el ID del mensaje enviado
    # Esto permite que el comando 'editar' encuentre este mensaje después
    async with main_bot.db_pool.acquire() as conn:
        await conn.execute('''
            UPDATE campaigns 
            SET message_id = $1, channel_id = $2 
            WHERE id = $3
        ''', str(msg.id), str(channel.id), campaign_id)

    # 8. Confirmación invisible para ti
    await interaction.response.send_message(f"✅ Campaña **#{campaign_id}** publicada y guardada exitosamente.", ephemeral=True)



@main_bot.tree.command(name="editar-campaña", description="Edita una campaña activa y actualiza su mensaje")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    id_campana="El ID numérico de la campaña (mira el footer del mensaje)",
    nombre="(Opcional) Nuevo nombre",
    descripcion="(Opcional) Nueva frase gancho",
    categoria="(Opcional) Nueva categoría",
    plataformas="(Opcional) Nuevas plataformas",
    payrate="(Opcional) Nuevo pago",
    invite_link="(Opcional) Nuevo link",
    thumbnail_url="(Opcional) Nueva imagen"
)
async def edit_campaign(interaction: discord.Interaction, 
                        id_campana: int, 
                        nombre: str = None, 
                        descripcion: str = None, 
                        categoria: str = None,
                        plataformas: str = None,
                        payrate: str = None, 
                        invite_link: str = None,
                        thumbnail_url: str = None):
    
    await interaction.response.defer(ephemeral=True)

    async with main_bot.db_pool.acquire() as conn:
        # 1. Obtener datos actuales
        camp = await conn.fetchrow("SELECT * FROM campaigns WHERE id = $1", id_campana)
        
        if not camp:
            return await interaction.followup.send("❌ Campaña no encontrada (Revisa el ID).")
        
        if not camp['message_id'] or not camp['channel_id']:
            return await interaction.followup.send("⚠️ Esta campaña es antigua o no se guardó bien el mensaje. No puedo editarla visualmente.")

        # 2. Mezclar datos nuevos con viejos (Si no envías nada, mantiene lo anterior)
        new_name = nombre or camp['name']
        new_desc = descripcion or camp['description']
        new_cat = categoria or camp['category']
        new_plat = plataformas or camp.get('platforms', 'TikTok, Instagram, Youtube') 
        new_rate = payrate or camp['payrate']
        new_link = invite_link or camp['invite_link']
        new_thumb = thumbnail_url or camp['thumbnail_url']

        # 3. Actualizar Base de Datos
        await conn.execute('''
            UPDATE campaigns 
            SET name=$1, description=$2, category=$3, platforms=$4, payrate=$5, invite_link=$6, thumbnail_url=$7 
            WHERE id=$8
        ''', new_name, new_desc, new_cat, new_plat, new_rate, new_link, new_thumb, id_campana)

    # 4. ACTUALIZAR EL MENSAJE EN DISCORD
    try:
        channel = interaction.client.get_channel(int(camp['channel_id']))
        if not channel:
            return await interaction.followup.send("❌ El canal original ya no existe.")
            
        message = await channel.fetch_message(int(camp['message_id']))
        
        # 5. Reconstruir el Embed (Estilo Gigante)
        texto_contenido = f"""
**{new_desc}** 🔥

## Detalles de campaña 🚀
**Categoría:** {new_cat}
**Plataformas:** {new_plat}
**Audiencia:** Global 🌎

## Detalles de pago 💸
**Sistema de pago:** {new_rate}
**Minimo de Views para Pago:** 10,000 views
**Método de Pago:** PayPal

## Únete al servidor ➡️
Click en el boton debajo para Empezar!
"""
        new_embed = discord.Embed(
            title=f"{new_name} x Latin Clipping", 
            description=texto_contenido, 
            color=0x00ff00
        )
        
        if new_thumb:
            new_embed.set_thumbnail(url=new_thumb)
            
        new_embed.set_footer(text=f"Campaña ID: {id_campana} | Nota: 🚨 Violacion en reglas de Campaña = Insta-Ban")

        # Botón actualizado
        class JoinButton(discord.ui.View):
            def __init__(self, link): 
                super().__init__()
                self.add_item(discord.ui.Button(label="Join Server", style=discord.ButtonStyle.link, url=link, emoji="➡️"))

        # 6. Edición Mágica
        await message.edit(embed=new_embed, view=JoinButton(new_link))
        
        await interaction.followup.send(f"✅ Campaña **#{id_campana}** actualizada visualmente.", ephemeral=True)

    except discord.NotFound:
        await interaction.followup.send("⚠️ Datos guardados en DB, pero no encontré el mensaje original (quizás fue borrado).")
    except Exception as e:
        await interaction.followup.send(f"❌ Error técnico al editar: {e}")

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

@main_bot.tree.command(name="guia-comandos", description="Publica la guía de ayuda para usuarios")
@app_commands.default_permissions(administrator=True)
async def post_user_guide(interaction: discord.Interaction):
    
    # Texto con formato Markdown para que sea fácil de leer
    contenido_guia = """
Aquí tienes los comandos esenciales para gestionar tu cuenta y empezar a ganar dinero.

### 🔗 Vincular Cuentas
`/registrar [plataforma] [usuario]`
> **Paso 1:** Vincula tu TikTok, YouTube o Instagram.
> *Ejemplo: `/registrar plataforma:TikTok usuario:@miusuario`*

`/verificar [plataforma] [usuario]`
> **Paso 2:** Confirma que eres el dueño. El bot te dará un código para poner en tu biografía.
> *Ejemplo: `/verificar plataforma:TikTok usuario:@miusuario`*

### 💰 Pagos y Ganancias
`/add-paypal [email] [nombre] [apellido]`
> **Importante:** Configura esto para recibir tus pagos automáticamente.

`/mis-videos`
> Mira el rendimiento de tus clips subidos, vistas acumuladas y dinero estimado.

### 📊 Información
`/info`
> Estadísticas globales del servidor (pagos totales, usuarios, etc).
"""

    embed = discord.Embed(
        title="📚 Guía de Comandos | Latin Clipping",
        description=contenido_guia,
        color=0x00ff00 # Verde marca
    )
    
    # Puedes poner una imagen pequeña o logo si quieres
    # embed.set_thumbnail(url="TU_LOGO_AQUI") 
    
    embed.set_footer(text="¿Tienes dudas? Abre un ticket en #soporte 🎫")

    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Guía publicada en este canal.", ephemeral=True)

@main_bot.tree.command(name="publicar-reglas", description="Publica las reglas con formato GIGANTE")
@app_commands.default_permissions(administrator=True)
async def post_campaign_rules(interaction: discord.Interaction):
    
    # Usamos Markdown para controlar el tamaño:
    # #  -> Título Gigante
    # ### -> Subtítulo Grande
    
    reglas_texto = """
# Reglas de la Campaña 🚨

### 1. Prohibido el uso de bots 🤖
> El uso de bots, granjas de clicks o interacción falsa está terminantemente prohibido.

### 2. Audiencia real requerida 🌎
> No participes en campañas que pidan una audiencia (país/idioma) que no coincida con la tuya.

### 3. Contenido fiel a los requisitos 📋
> Tu video debe cumplir estrictamente lo que pide la marca. Nada de contenido engañoso.

### 4. Cero colaboraciones artificiales 🤝
> No se permite la función "Colaboración" de Instagram/TikTok ni grupos de engagement para inflar números.

### 5. Métricas visibles 👁️
> Está prohibido ocultar el recuento de "Me gusta" o los comentarios. Todo debe ser público.

### 6. Calidad ante todo ✨
> Videos de baja calidad, pantalla negra o sin esfuerzo serán eliminados y el usuario baneado.

### 7. No re-subir contenido (Spam) ♻️
> No puedes subir el mismo video varias veces en la misma cuenta.

### 8. Mantener público hasta el pago 💰
> Si borras o archivas el video antes de recibir el pago, no se te pagará. Los clientes revisan todo.

### 9. Decisión del Staff ⚖️
> Las decisiones de los administradores son definitivas. El incumplimiento conlleva expulsión inmediata.
"""

    # Nota: Ponemos todo en la descripción para que funcionen los tamaños grandes
    embed = discord.Embed(
        description=reglas_texto, # <--- AQUÍ VA EL TEXTO PARA QUE SE VEA GRANDE
        color=0xff0000 # Rojo
    )
    
    # Opcional: Imagen decorativa abajo o arriba
    embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/1022/1022300.png") 
    
    embed.set_footer(text="⚠️ Violación de reglas = Ban Permanente")

    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Reglas publicadas con formato grande.", ephemeral=True)

@main_bot.tree.command(name="publicar-info", description="Publica la información detallada de pagos y funcionamiento")
@app_commands.default_permissions(administrator=True)
async def post_campaign_info(interaction: discord.Interaction):
    
    # Texto formateado con Markdown para tamaño Gigante (#) y Grande (###)
    info_texto = """
# Información de la Campaña ℹ️

## ⏳ Duración y Finalización
Las campañas se pueden llevar a cabo de dos maneras:

### 1. Basada en un plazo 📅
> Se selecciona y publica una **fecha específica**, hasta la cual se permite enviar publicaciones. Después de esa fecha, la campaña finaliza.

### 2. Basada en el Presupuesto 💰
> No hay fecha límite fija. La campaña continúa hasta que se agote el presupuesto del patrocinador.
> *Nota: La mayoría de nuestras campañas funcionan así.*

# Pagos 💸

### 🧮 Cálculo de pagos
Existen dos sistemas para calcular recompensas:
> **A. Tasa de pago:** Tarifa fija (Ej: $1 por cada 1000 views).
> **B. Tipo Bote:** Pago proporcional a tu % del total de visualizaciones de toda la campaña.

### 📉 Requisitos Mínimos
> **Publicación Individual:** Cada video debe superar las **1,000 views**.
> **Total de Campaña:** La suma de todas tus publicaciones debe superar el mínimo de la campaña (usualmente **25,000 views**) para poder cobrar.

### 🗓️ Plazos de Pago
> Los pagos **NO son inmediatos**. Se envían tras la finalización de la campaña y la revisión manual para descartar fraudes.

### 💳 Método y Transmisión
> Se paga únicamente por el método designado (ej: PayPal).
> Los pagos se envían a los datos registrados al finalizar la campaña. Si tus datos están mal, es tu responsabilidad.

# Visualizaciones 👁️

### ⏱️ Seguimiento
> El trackeo comienza al enviar el link. Las views se actualizan **cada 12 horas**.

### 📺 YouTube (Calidad)
> En YouTube monitorizamos **"Visualizaciones con interacción"**, no el contador superficial. Esto indica quién vio el contenido de verdad.
"""

    embed = discord.Embed(
        description=info_texto, # <--- Todo en description para el efecto Gigante
        color=0xe67e22 # Color Naranja/Dorado para Información
    )
    
    embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/189/189665.png") # Icono de Info

    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Información publicada correctamente.", ephemeral=True)

# ==========================================
# COMANDO: SETUP REGISTRO (Admin)
# ==========================================
@main_bot.tree.command(name="setup-registro", description="Publica el panel de bienvenida y registro")
@app_commands.default_permissions(administrator=True)
async def setup_registro(interaction: discord.Interaction):
    
    # --- TEXTO DEL MENSAJE 1 (Público) ---
    texto_bienvenida = """
**Por favor, haz clic en el botón de abajo para comenzar el proceso de registro.** 👇

Si has usado nuestros servicios antes, tendrás acceso a todas tus cuentas vinculadas en el ecosistema de Latin Clipping inmediatamente.

Si no, serás guiado a través del proceso de registro de cuenta paso a paso.

Si aún no lo has hecho, por favor configura el método de pago requerido para este programa también.

**¡Gracias por elegir Latin Clipping!**
"""

    embed = discord.Embed(
        title="Bienvenido a Latin Clipping Bot",
        description=texto_bienvenida,
        color=0x3498db # Azul
    )
    embed.set_footer(text="Latin Clipping 2025")

    # Enviamos el embed con la Vista (el botón)
    await interaction.channel.send(embed=embed, view=RegistrationView())
    
    await interaction.response.send_message("✅ Panel de registro publicado.", ephemeral=True)


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

# ==========================================
# 1. CONFIGURACIÓN DE PAGOS (ADMIN)
# ==========================================

@main_bot.tree.command(name="config-pago", description="Configura el precio por 1,000 vistas")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    tipo="Usa 'STANDARD' para el base, o el #TAG para bounties",
    precio_por_1k="Precio en USD (ej: 0.60 o 5.00)"
)
async def set_payrate(interaction: discord.Interaction, tipo: str, precio_por_1k: float):
    key = tipo.upper().strip() # Guardamos siempre en mayúsculas
    
    async with main_bot.db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO payment_rates (rate_key, amount_per_1k) 
            VALUES ($1, $2)
            ON CONFLICT (rate_key) 
            DO UPDATE SET amount_per_1k = $2
        ''', key, precio_por_1k)
        
    await interaction.response.send_message(f"✅ Precio actualizado: **{key}** = **${precio_por_1k}** / 1k views.", ephemeral=True)


# ==========================================
# 2. UPLOAD (Campaña Normal)
# ==========================================
@main_bot.tree.command(name="upload", description="Sube videos para trackear (Separa links con coma)")
@app_commands.describe(links="Ej: link1, link2, link3")
async def upload_post(interaction: discord.Interaction, links: str):
    await interaction.response.defer(ephemeral=True)
    
    lista_links = [l.strip() for l in links.split(',') if l.strip()][:10]
    discord_id = str(interaction.user.id)
    reporte = []

    async with main_bot.db_pool.acquire() as conn:
        for url in lista_links:
            plat, table, col_url = detectar_plataforma(url)
            
            if not plat:
                reporte.append(f"❌ Ignorado (Link no válido): {url[:20]}...")
                continue

            try:
                # Insertamos como Normal (is_bounty = FALSE)
                await conn.execute(f'''
                    INSERT INTO {table} (discord_id, {col_url}, is_bounty, uploaded_at) 
                    VALUES ($1, $2, FALSE, NOW())
                    ON CONFLICT ({col_url}) DO NOTHING
                ''', discord_id, url)
                reporte.append(f"✅ **{plat.capitalize()}:** Guardado.")
            except Exception as e:
                reporte.append(f"⚠️ Error: {e}")

    embed = discord.Embed(title="📤 Resultado de Carga", description="\n".join(reporte) or "Ningún link válido.", color=0x3498db)
    await interaction.followup.send(embed=embed, ephemeral=True)


# ==========================================
# 3. BOUNTY UPLOAD (Misiones Especiales)
# ==========================================
@main_bot.tree.command(name="bounty-upload", description="Sube videos para una Misión/Bounty")
@app_commands.describe(links="Links separados por coma", tag="Tag de la misión (ej: #Navidad)")
async def upload_bounty(interaction: discord.Interaction, links: str, tag: str):
    await interaction.response.defer(ephemeral=True)
    
    lista_links = [l.strip() for l in links.split(',') if l.strip()][:10]
    discord_id = str(interaction.user.id)
    reporte = []

    # Estandarizamos el tag a mayúsculas para que coincida con la tabla de precios
    tag_limpio = tag.upper().strip() 

    async with main_bot.db_pool.acquire() as conn:
        for url in lista_links:
            plat, table, col_url = detectar_plataforma(url)
            
            if not plat:
                reporte.append(f"❌ Link inválido.")
                continue

            try:
                # Insertamos/Actualizamos como Bounty
                await conn.execute(f'''
                    INSERT INTO {table} (discord_id, {col_url}, is_bounty, bounty_tag, uploaded_at) 
                    VALUES ($1, $2, TRUE, $3, NOW())
                    ON CONFLICT ({col_url}) 
                    DO UPDATE SET is_bounty = TRUE, bounty_tag = $3
                ''', discord_id, url, tag_limpio)
                reporte.append(f"🎯 **{plat.capitalize()} (Bounty):** Asignado a `{tag_limpio}`")
            except Exception as e:
                reporte.append(f"⚠️ Error: {e}")

    embed = discord.Embed(title="🎯 Carga de Bounty", description="\n".join(reporte), color=0xff9900)
    await interaction.followup.send(embed=embed, ephemeral=True)


# ==========================================
# 4. REMOVE VIDEO
# ==========================================
@main_bot.tree.command(name="remove-video", description="Deja de trackear videos")
async def remove_video(interaction: discord.Interaction, links: str):
    await interaction.response.defer(ephemeral=True)
    
    lista_links = [l.strip() for l in links.split(',')]
    discord_id = str(interaction.user.id)
    eliminados = 0

    async with main_bot.db_pool.acquire() as conn:
        for url in lista_links:
            plat, table, col_url = detectar_plataforma(url)
            if plat:
                res = await conn.execute(f"DELETE FROM {table} WHERE {col_url} = $1 AND discord_id = $2", url, discord_id)
                if "1" in res: eliminados += 1

    await interaction.followup.send(f"🗑️ Se han eliminado **{eliminados}** videos.", ephemeral=True)


# ==========================================
# 5. STATS (Calculadora Real)
# ==========================================
@main_bot.tree.command(name="stats", description="Ver mis estadísticas y ganancias calculadas")
async def stats(interaction: discord.Interaction):
    discord_id = str(interaction.user.id)
    await interaction.response.defer(ephemeral=True)

    async with main_bot.db_pool.acquire() as conn:
        # A. Obtener precios
        rate_std = await conn.fetchval("SELECT amount_per_1k FROM payment_rates WHERE rate_key = 'STANDARD'") or 0.60
        bounty_rows = await conn.fetch("SELECT rate_key, amount_per_1k FROM payment_rates WHERE rate_key != 'STANDARD'")
        bounty_map = {row['rate_key']: float(row['amount_per_1k']) for row in bounty_rows}

        # B. Traer videos de las 3 tablas
        query = """
            SELECT post_url as url, views, is_bounty, bounty_tag, 'YouTube' as plat FROM tracked_posts WHERE discord_id = $1
            UNION ALL
            SELECT tiktok_url as url, views, is_bounty, bounty_tag, 'TikTok' as plat FROM tracked_posts_tiktok WHERE discord_id = $1
            UNION ALL
            SELECT instagram_url as url, views, is_bounty, bounty_tag, 'Instagram' as plat FROM tracked_posts_instagram WHERE discord_id = $1
        """
        videos = await conn.fetch(query, discord_id)

    if not videos:
        return await interaction.followup.send("📭 No tienes videos trackeados.", ephemeral=True)

    total_views = 0
    total_earned = 0.0
    lista_txt = ""
    
    for v in videos:
        views = v['views'] or 0
        tag = (v['bounty_tag'] or "").upper().strip()
        
        # Lógica de Precio
        if v['is_bounty'] and tag in bounty_map:
            rate = bounty_map[tag]
            tipo_lbl = f"🎯 {tag}"
        else:
            rate = float(rate_std)
            tipo_lbl = "📹 Normal"
            
        ganancia = (views / 1000) * rate
        total_views += views
        total_earned += ganancia
        
        # Solo mostrar detalles de los últimos 5 para no llenar la pantalla
        if len(lista_txt) < 900: 
            lista_txt += f"**{v['plat']}** ({tipo_lbl})\nViews: {views:,} | Ganado: **${ganancia:.2f}**\n\n"

    embed = discord.Embed(title="📊 Mis Estadísticas", color=0x9146FF)
    embed.add_field(name="Global", value=f"👁️ **Vistas:** {total_views:,}\n💰 **Saldo:** ${total_earned:.2f}", inline=False)
    embed.add_field(name="Últimos Videos", value=lista_txt or "...", inline=False)
    embed.set_footer(text=f"Base Rate: ${rate_std}/1k views")
    
    await interaction.followup.send(embed=embed, ephemeral=True)


# ==========================================
# 6. LEADERBOARD
# ==========================================
@main_bot.tree.command(name="leaderboard", description="Top 10 Usuarios Globales")
async def leaderboard(interaction: discord.Interaction):
    await interaction.response.defer()

    query = """
    WITH all_views AS (
        SELECT discord_id, COALESCE(views, 0) as views FROM tracked_posts
        UNION ALL
        SELECT discord_id, COALESCE(views, 0) as views FROM tracked_posts_tiktok
        UNION ALL
        SELECT discord_id, COALESCE(views, 0) as views FROM tracked_posts_instagram
    )
    SELECT discord_id, SUM(views) as total_views
    FROM all_views
    GROUP BY discord_id
    ORDER BY total_views DESC
    LIMIT 10;
    """
    
    async with main_bot.db_pool.acquire() as conn:
        top_users = await conn.fetch(query)

    embed = discord.Embed(title="🏆 Leaderboard (Top 10)", color=0xFFD700)
    
    texto = ""
    for i, user in enumerate(top_users, 1):
        medal = "🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else f"#{i}"
        
        member = interaction.guild.get_member(int(user['discord_id']))
        name = member.display_name if member else f"Usuario...{str(user['discord_id'])[-4:]}"
        
        texto += f"**{medal} {name}** — {user['total_views']:,} views\n"

    embed.description = texto if texto else "Aún no hay datos."
    await interaction.followup.send(embed=embed)

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
        ig = await conn.fetch("SELECT instagram_url as url, views, likes, uploaded_at, 'Instagram' as platform FROM tracked_posts_instagram WHERE discord_id = $1", discord_id) # <--- FALTABA ESTO

    # Unimos las 3 listas
    all_videos = [dict(v) for v in yt] + [dict(v) for v in tt] + [dict(v) for v in ig]
    all_videos.sort(key=lambda x: x['uploaded_at'] or datetime.min, reverse=True)

    if not all_videos:
        await interaction.response.send_message("📭 No tienes videos registrados aún.", ephemeral=True)
        return

    embed = discord.Embed(title="🎬 Mis Videos", color=0x9146FF)
    for v in all_videos[:10]:
        # Elegimos emoji según plataforma
        emoji = "▶️" if v['platform'] == 'YouTube' else "📸" if v['platform'] == 'Instagram' else "🎵"
        embed.add_field(name=f"{emoji} {v['platform']}", value=f"[Link]({v['url']})\n👁️ {v['views']} | ❤️ {v['likes']}", inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@main_bot.tree.command(name="set-bounty", description="Activa campaña en un video")
@app_commands.default_permissions(administrator=True)
async def set_bounty(interaction: discord.Interaction, plataforma: str, post_url: str, bounty_tag: str):
    plataforma = plataforma.lower()
    
    # Selección de tabla correcta
    if plataforma == "youtube":
        table = "tracked_posts"
        url_col = "post_url"
    elif plataforma == "instagram": # <--- AGREGADO
        table = "tracked_posts_instagram"
        url_col = "instagram_url"
    else: # Asumimos TikTok por descarte o explícito
        table = "tracked_posts_tiktok"
        url_col = "tiktok_url"

    async with main_bot.db_pool.acquire() as conn:
        exists = await conn.fetchval(f"SELECT 1 FROM {table} WHERE {url_col} = $1", post_url)
        if not exists:
            await interaction.response.send_message("❌ Video no encontrado en DB.", ephemeral=True)
            return
        await conn.execute(f"UPDATE {table} SET is_bounty = TRUE, bounty_tag = $1, starting_views = views WHERE {url_col} = $2", bounty_tag, post_url)
    
    await interaction.response.send_message(f"✅ Bounty **{bounty_tag}** activado en {plataforma}.")

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

# -----------------------------------------------------------------------------
# CLASES DE INTERFAZ (UI) - PANEL DE CONTROL
# -----------------------------------------------------------------------------

class AdminControlView(discord.ui.View):
    def __init__(self, bot_ref):
        super().__init__(timeout=None)
        self.bot = bot_ref
        self.current_user_id = None

    # --- 1. MENÚ PARA SELECCIONAR USUARIO ---
    @discord.ui.select(placeholder="👥 Selecciona un usuario con deuda...", custom_id="select_user", min_values=1, max_values=1)
    async def select_user_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        # Guardamos el ID del usuario seleccionado
        self.current_user_id = int(select.values[0])
        await self.mostrar_detalle_usuario(interaction)

    # --- MÉTODO PRINCIPAL: MOSTRAR DETALLE ---
    async def mostrar_detalle_usuario(self, interaction: discord.Interaction):
        user_id = self.current_user_id
        
        # Recuperar datos frescos de la DB
        async with self.bot.db_pool.acquire() as conn:
            # Datos Usuario
            user_data = await conn.fetchrow("SELECT payment_email, payment_name FROM social_accounts WHERE discord_id = $1 LIMIT 1", str(user_id))
            
            # Videos y Ganancias
            yt_vids = await conn.fetch("SELECT 'YouTube' as src, post_url, final_earned_usd, bounty_tag FROM tracked_posts WHERE discord_id = $1", str(user_id))
            tt_vids = await conn.fetch("SELECT 'TikTok' as src, tiktok_url as post_url, final_earned_usd, bounty_tag FROM tracked_posts_tiktok WHERE discord_id = $1", str(user_id))
            ig_vids = await conn.fetch("SELECT 'Instagram' as src, instagram_url as post_url, final_earned_usd, bounty_tag FROM tracked_posts_instagram WHERE discord_id = $1", str(user_id))

        all_vids = yt_vids + tt_vids + ig_vids
        total_deuda = sum([v['final_earned_usd'] or 0 for v in all_vids])
        
        # Construir Embed
        embed = discord.Embed(title=f"🕵️ Auditoría de Usuario", color=discord.Color.blue())
        embed.description = f"<@{user_id}>\n**Deuda Total:** `${total_deuda:.2f}`"
        
        paypal_info = user_data['payment_email'] if user_data and user_data['payment_email'] else "⚠️ No configurado"
        nombre_pago = user_data['payment_name'] if user_data and user_data['payment_name'] else ""
        embed.add_field(name="💳 Datos de Pago", value=f"Email: `{paypal_info}`\nTitular: {nombre_pago}", inline=False)

        # Crear lista de videos para el texto y para el menú de borrar
        lista_texto = ""
        options_borrar = []
        
        for i, vid in enumerate(all_vids[:20]): # Límite visual de 20
            ganancia = vid['final_earned_usd'] or 0
            tag = vid['bounty_tag'] or "Std"
            url_corta = vid['post_url'][-15:] # Solo mostramos el final del link
            lista_texto += f"**{i+1}.** [{vid['src']}] `${ganancia:.2f}` ({tag}) -> [Link]({vid['post_url']})\n"
            
            # Opción para el menú de borrar
            label = f"{i+1}. {vid['src']} (${ganancia:.2f})"
            options_borrar.append(discord.SelectOption(label=label, value=vid['post_url'], description=f"Borrar: {url_corta}", emoji="🗑️"))

        if not lista_texto: lista_texto = "No hay videos activos."
        embed.add_field(name="📹 Videos Activos", value=lista_texto, inline=False)

        # --- ACTUALIZAR LA VISTA (BOTONES) ---
        self.clear_items() # Borramos el selector de usuarios

        # 1. Menú de Borrar (Si hay videos)
        if options_borrar:
            select_borrar = discord.ui.Select(placeholder="🗑️ Selecciona un video para ELIMINARLO", options=options_borrar, custom_id="del_vid")
            select_borrar.callback = self.borrar_video_callback
            self.add_item(select_borrar)

        # 2. Botón Pagar
        btn_pagar = discord.ui.Button(label="💸 Pagar y Resetear", style=discord.ButtonStyle.green, custom_id="pay_btn")
        btn_pagar.callback = self.pagar_callback
        self.add_item(btn_pagar)

        # 3. Botón Volver
        btn_volver = discord.ui.Button(label="🔙 Volver a Lista", style=discord.ButtonStyle.grey, custom_id="back_btn")
        btn_volver.callback = self.volver_callback
        self.add_item(btn_volver)

        await interaction.response.edit_message(embed=embed, view=self)

    # --- CALLBACK: BORRAR VIDEO ---
    async def borrar_video_callback(self, interaction: discord.Interaction):
        video_url = interaction.data['values'][0] # La URL viene en el value
        
        async with self.bot.db_pool.acquire() as conn:
            # Borramos de las 3 tablas por si acaso
            await conn.execute("DELETE FROM tracked_posts WHERE post_url = $1", video_url)
            await conn.execute("DELETE FROM tracked_posts_tiktok WHERE tiktok_url = $1", video_url)
            await conn.execute("DELETE FROM tracked_posts_instagram WHERE instagram_url = $1", video_url)
        
        # Recargamos la vista del usuario
        await self.mostrar_detalle_usuario(interaction) # Refresca todo

    # --- CALLBACK: PAGAR ---
    async def pagar_callback(self, interaction: discord.Interaction):
        user_id = self.current_user_id
        async with self.bot.db_pool.acquire() as conn:
            # Resetear saldos (Setear final_earned_usd a 0 o borrar filas según tu lógica de negocio)
            # Aquí asumimos que borrar los videos PAGADOS es lo mejor para limpiar, 
            # O puedes poner views_paid = views. (Para simplificar, borramos los videos cobrados o los ponemos a 0 ganancia)
            
            # Opción A: Borrar todo lo pagado (Más limpio)
            await conn.execute("DELETE FROM tracked_posts WHERE discord_id = $1", str(user_id))
            await conn.execute("DELETE FROM tracked_posts_tiktok WHERE discord_id = $1", str(user_id))
            await conn.execute("DELETE FROM tracked_posts_instagram WHERE discord_id = $1", str(user_id))
            
        embed = discord.Embed(title="✅ Pago Registrado", description=f"Se ha reseteado la cuenta de <@{user_id}>.", color=discord.Color.green())
        
        # Volver al menú principal
        self.clear_items()
        btn_back = discord.ui.Button(label="🏠 Volver al Inicio", style=discord.ButtonStyle.primary)
        btn_back.callback = self.volver_callback
        self.add_item(btn_back)
        
        await interaction.response.edit_message(embed=embed, view=self)

    # --- CALLBACK: VOLVER ---
    async def volver_callback(self, interaction: discord.Interaction):
        # Reiniciamos la vista llamando al comando original logicamente
        # Necesitamos repoblar el select de usuarios
        await generar_vista_principal(self.bot, interaction)

# -----------------------------------------------------------------------------
# COMANDO PRINCIPAL Y FUNCIÓN HELPER
# -----------------------------------------------------------------------------

async def generar_vista_principal(bot, interaction):
    # Buscar usuarios con deuda > 0
    async with bot.db_pool.acquire() as conn:
        users = await conn.fetch("SELECT discord_id, payment_name FROM social_accounts")
        
    options = []
    async with bot.db_pool.acquire() as conn:
        for u in users:
            uid = u['discord_id']
            # Calcular deuda rápida
            yt = await conn.fetchval("SELECT COALESCE(SUM(final_earned_usd),0) FROM tracked_posts WHERE discord_id=$1", uid)
            tt = await conn.fetchval("SELECT COALESCE(SUM(final_earned_usd),0) FROM tracked_posts_tiktok WHERE discord_id=$1", uid)
            ig = await conn.fetchval("SELECT COALESCE(SUM(final_earned_usd),0) FROM tracked_posts_instagram WHERE discord_id=$1", uid)
            total = (yt or 0) + (tt or 0) + (ig or 0)
            
            if total > 0:
                # Discord limita los select options a 25. Mostramos los top 25.
                label = f"{u['payment_name'] or 'Usuario'} (${total:.2f})"
                options.append(discord.SelectOption(label=label, value=str(uid), description=f"ID: {uid}"))

    if not options:
        embed = discord.Embed(title="👍 Todo al día", description="No hay usuarios con saldo pendiente de cobro.", color=discord.Color.green())
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=None)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # Crear Vista
    view = AdminControlView(bot)
    # Reemplazamos las opciones del select placeholder con los usuarios reales
    view.children[0].options = options[:25] 

    embed = discord.Embed(title="🎛️ Panel de Control Financiero", description="Selecciona un usuario para auditar, borrar videos o registrar pagos.", color=discord.Color.gold())
    
    if interaction.response.is_done():
        await interaction.edit_original_response(embed=embed, view=view)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="admin-control", description="ADMIN: Panel interactivo para auditar, borrar videos y pagar")
@app_commands.checks.has_permissions(administrator=True)
async def admin_control(interaction: discord.Interaction):
    await generar_vista_principal(bot, interaction)    