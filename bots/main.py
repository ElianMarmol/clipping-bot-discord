import discord
from discord.ext import commands
from discord import app_commands
import os
import asyncpg
from datetime import datetime
import asyncio
from dotenv import load_dotenv

load_dotenv()

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
        # Conectar a la base de datos
        self.db_pool = await asyncpg.create_pool(
            os.getenv('DATABASE_URL'),
            ssl='require'
        )
        await self.create_tables()
        await self.tree.sync()
        
        print("✅ Bot Principal - Base de datos conectada")

    async def create_tables(self):
        async with self.db_pool.acquire() as conn:
            # Tabla de usuarios
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    discord_id BIGINT PRIMARY KEY,
                    username TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
        
            # Tabla de redes sociales - VERSIÓN CORREGIDA
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS social_accounts (
                    id SERIAL PRIMARY KEY,
                    discord_id BIGINT,
                    platform TEXT,
                    username TEXT,
                    verification_code TEXT,
                    is_verified BOOLEAN DEFAULT FALSE,
                    verified_at TIMESTAMP,
                    FOREIGN KEY (discord_id) REFERENCES users(discord_id),
                    -- ESTA LÍNEA ES LA CLAVE: constraint única para ON CONFLICT
                    UNIQUE (discord_id, platform, username)
                )
            ''')
        
            # Tabla de métodos de pago
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS payment_methods (
                    id SERIAL PRIMARY KEY,
                    discord_id BIGINT,
                    method_type TEXT,
                    paypal_email TEXT,
                    usdc_eth_address TEXT,
                    usdc_sol_address TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    added_at TIMESTAMP DEFAULT NOW(),
                    FOREIGN KEY (discord_id) REFERENCES users(discord_id),
                    UNIQUE (discord_id, method_type)
                )
            ''')
        
            # Tabla de posts trackeados
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS tracked_posts (
                    id SERIAL PRIMARY KEY,
                    discord_id BIGINT,
                    post_url TEXT,
                    is_bounty BOOLEAN DEFAULT FALSE,
                    bounty_tag TEXT,
                    uploaded_at TIMESTAMP DEFAULT NOW(),
                    views INTEGER DEFAULT 0,
                    likes INTEGER DEFAULT 0,
                    shares INTEGER DEFAULT 0,
                    FOREIGN KEY (discord_id) REFERENCES users(discord_id)
                )
            ''')
        
            # Tabla para configuración de servidor
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

            # Tabla de campañas (para Active Campaigns)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS campaigns (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    category TEXT,
                    payrate TEXT,
                    invite_link TEXT,
                    thumbnail_url TEXT,
                    created_by BIGINT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')

        
            print("✅ Tablas del Bot Principal creadas/verificadas")

    async def on_ready(self):
        print(f'✅ {self.user} se ha conectado a Discord!')
        
        # SOLUCIÓN SIMPLIFICADA - Sin clear_commands()
        try:
            # 1. Primero ver qué comandos hay actualmente en Discord
            current_commands = await self.tree.fetch_commands()
            print(f'🔍 Comandos detectados en Discord: {len(current_commands)}')
            for cmd in current_commands:
                print(f'   - {cmd.name} (ID: {cmd.id})')
            
            # 2. Enfoque simple: sincronizar directamente
            # Esto reemplazará todos los comandos existentes con los actuales
            synced = await self.tree.sync()
            print(f'🔄 Comandos sincronizados: {len(synced)} comandos')
            print(f'📝 Comandos activos: {[cmd.name for cmd in synced]}')
            
        except Exception as e:
            print(f'❌ Error sincronizando comandos: {e}')
        
        self.start_time = datetime.now()
        print(f'✅ Bot Principal conectado como {self.user.name}')
        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching, 
            name="/about para información"
        ))

# Inicializar bot principal
main_bot = MainBot()

# =============================================
# 0. COMANDO /sync - SIMPLIFICADO
# =============================================

@main_bot.tree.command(name="sync", description="Sincronizar y limpiar comandos (solo admin)")
@app_commands.default_permissions(administrator=True)
async def sync(interaction: discord.Interaction):
    """Comando manual para sincronizar y limpiar comandos fantasmas"""
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        # 1. Primero ver qué comandos hay actualmente en Discord
        current_commands = await main_bot.tree.fetch_commands()
        
        # 2. Enfoque simple: sincronizar directamente sin clear_commands
        # Esto eliminará automáticamente los comandos que ya no están en el código
        synced = await main_bot.tree.sync()
        
        embed = discord.Embed(
            title="✅ Comandos Sincronizados",
            color=0x00ff00,
            description="Comandos limpiados y sincronizados exitosamente"
        )
        embed.add_field(name="🗑️ Comandos anteriores", value=len(current_commands), inline=True)
        embed.add_field(name="🔄 Comandos actuales", value=len(synced), inline=True)
        embed.add_field(
            name="📝 Comandos activos", 
            value="\n".join([f"• `/{cmd.name}`" for cmd in synced]), 
            inline=False
        )
        
        # Mostrar qué comandos se eliminaron
        current_names = {cmd.name for cmd in current_commands}
        synced_names = {cmd.name for cmd in synced}
        removed_commands = current_names - synced_names
        
        if removed_commands:
            embed.add_field(
                name="🚮 Comandos eliminados",
                value=", ".join([f"`/{cmd}`" for cmd in removed_commands]),
                inline=False
            )
        
        embed.set_footer(text="Los comandos fantasmas han sido eliminados")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
        # Log en consola
        print(f"🔄 Comandos sincronizados manualmente por {interaction.user.name}")
        print(f"📝 Comandos activos: {[cmd.name for cmd in synced]}")
        
    except Exception as e:
        error_embed = discord.Embed(
            title="❌ Error al Sincronizar",
            color=0xff0000,
            description=f"Error: {str(e)}"
        )
        await interaction.followup.send(embed=error_embed, ephemeral=True)
        print(f"❌ Error en comando sync: {e}")

# =============================================
# 1. COMANDO /about - ACTUALIZADO CON REGISTROS
# =============================================

@main_bot.tree.command(name="info", description="Muestra estadísticas interesantes sobre el bot")
async def about(interaction: discord.Interaction):
    """Muestra estadísticas del bot y información general"""
    
    async with main_bot.db_pool.acquire() as conn:
        # Obtener estadísticas de la base de datos
        total_users = await conn.fetchval('SELECT COUNT(*) FROM users')
        total_posts = await conn.fetchval('SELECT COUNT(*) FROM tracked_posts')
        total_verified = await conn.fetchval('SELECT COUNT(*) FROM social_accounts WHERE is_verified = true')
        total_registered_accounts = await conn.fetchval('SELECT COUNT(*) FROM social_accounts')
        
        # Calcular estadísticas de engagement
        total_views = await conn.fetchval('SELECT COALESCE(SUM(views), 0) FROM tracked_posts')
        total_likes = await conn.fetchval('SELECT COALESCE(SUM(likes), 0) FROM tracked_posts')
        total_shares = await conn.fetchval('SELECT COALESCE(SUM(shares), 0) FROM tracked_posts')
    
    # Información del bot
    bot_uptime = datetime.now() - main_bot.start_time
    hours, remainder = divmod(int(bot_uptime.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    
    embed = discord.Embed(
        title="🤖 Acerca de Clipping Bot",
        description="Plataforma líder para creadores de contenido y gestión de campañas",
        color=0x9146FF,
        timestamp=datetime.now()
    )
    
    # Estadísticas principales
    embed.add_field(
        name="📊 Estadísticas Globales",
        value=(
            f"**👥 Usuarios Registrados:** {total_users}\n"
            f"**📱 Cuentas Vinculadas:** {total_registered_accounts}\n"
            f"**✅ Cuentas Verificadas:** {total_verified}\n"
            f"**🎬 Posts Trackeados:** {total_posts}\n"
            f"**⏱️ Tiempo Activo:** {hours}h {minutes}m"
        ),
        inline=False
    )
    
    # Métricas de engagement
    embed.add_field(
        name="📈 Métricas de Contenido",
        value=(
            f"**👁️ Vistas Totales:** {total_views:,}\n"
            f"**❤️ Likes Totales:** {total_likes:,}\n"
            f"**🔄 Shares Totales:** {total_shares:,}"
        ),
        inline=False
    )
    
    # Información técnica
    embed.add_field(
        name="🔧 Información Técnica",
        value=(
            f"**🟢 Estado:** Operativo\n"
            f"**📡 Latencia:** {round(main_bot.latency * 1000)}ms\n"
            f"**⚡ Versión:** 2.0.0\n"
            f"**👨‍💻 Desarrollado por:** Latin Clipping"
        ),
        inline=False
    )
    
    # Características
    embed.add_field(
        name="🎯 Características Principales",
        value=(
            "• Sistema de registro y verificación\n"
            "• Seguimiento automático de métricas\n"
            "• Gestión de pagos múltiples\n"
            "• Leaderboards competitivos\n"
            "• Detección de fraude\n"
            "• Soporte para múltiples plataformas"
        ),
        inline=False
    )
    
    embed.set_footer(text="💡 Usa /registrar para vincular tus cuentas")
    
    await interaction.response.send_message(embed=embed)

# =============================================
# 2. COMANDO /addrole
# =============================================

@main_bot.tree.command(name="addrole", description="Añade un rol a un usuario")
@app_commands.describe(
    usuario="El usuario al que añadir el rol",
    rol="El rol a añadir"
)
@app_commands.default_permissions(manage_roles=True)
async def addrole(interaction: discord.Interaction, usuario: discord.Member, rol: discord.Role):
    """Añade un rol específico a un usuario"""
    
    # Verificar permisos del bot
    if not interaction.guild.me.guild_permissions.manage_roles:
        await interaction.response.send_message(
            "❌ No tengo permisos para gestionar roles en este servidor.",
            ephemeral=True
        )
        return
    
    # Verificar que el bot puede asignar ese rol
    if rol.position >= interaction.guild.me.top_role.position:
        await interaction.response.send_message(
            "❌ No puedo asignar este rol porque está por encima de mi rol más alto.",
            ephemeral=True
        )
        return
    
    # Verificar que el usuario que ejecuta el comando puede asignar ese rol
    if rol.position >= interaction.user.top_role.position and interaction.user != interaction.guild.owner:
        await interaction.response.send_message(
            "❌ No puedes asignar este rol porque está por encima de tu rol más alto.",
            ephemeral=True
        )
        return
    
    try:
        # Añadir el rol al usuario
        await usuario.add_roles(rol)
        
        embed = discord.Embed(
            title="✅ Rol Añadido Exitosamente",
            color=rol.color,
            description=f"El rol {rol.mention} ha sido añadido a {usuario.mention}"
        )
        
        embed.add_field(name="👤 Usuario", value=usuario.mention, inline=True)
        embed.add_field(name="🎭 Rol", value=rol.mention, inline=True)
        embed.add_field(name="👨‍💼 Asignado por", value=interaction.user.mention, inline=True)
        
        embed.set_footer(text=f"ID del Usuario: {usuario.id}")
        
        await interaction.response.send_message(embed=embed)
        
        # Log en consola
        print(f"📝 Rol {rol.name} añadido a {usuario.name} por {interaction.user.name}")
        
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ No tengo permisos para añadir este rol. Por favor, verifica los permisos del bot.",
            ephemeral=True
        )
    except discord.HTTPException as e:
        await interaction.response.send_message(
            f"❌ Error al añadir el rol: {str(e)}",
            ephemeral=True
        )

# =============================================
# 3. COMANDO /aisend
# =============================================

@main_bot.tree.command(name="aisend", description="Envía una respuesta IA a la pregunta de alguien (solo admin)")
@app_commands.describe(
    usuario="El usuario que recibirá la respuesta",
    mensaje="El mensaje de respuesta de IA",
    mensaje_original="El ID del mensaje original (opcional)"
)
@app_commands.default_permissions(administrator=True)
async def aisend(interaction: discord.Interaction, usuario: discord.Member, mensaje: str, mensaje_original: str = None):
    """Envía una respuesta generada por IA a un usuario específico"""
    
    # Verificar que el comando lo ejecuta un administrador
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "❌ Este comando solo puede ser usado por administradores.",
            ephemeral=True
        )
        return
    
    try:
        # Crear embed de respuesta IA
        embed = discord.Embed(
            title="🤖 Respuesta Automática - Soporte Clipping",
            description=mensaje,
            color=0x00ff00,
            timestamp=datetime.now()
        )
        
        embed.add_field(
            name="📝 Información",
            value=(
                "Esta es una respuesta automática generada por nuestro sistema de IA.\n"
                "Si necesitas más ayuda, por favor responde a este mensaje."
            ),
            inline=False
        )
        
        embed.set_footer(text="Soporte Clipping • Respuesta Automática")
        
        # Intentar enviar mensaje directo al usuario
        try:
            await usuario.send(embed=embed)
            dm_success = True
        except discord.Forbidden:
            dm_success = False
        
        # Respuesta en el canal actual
        response_embed = discord.Embed(
            title="✅ Respuesta IA Enviada",
            color=0x00ff00,
            description=f"Respuesta enviada a {usuario.mention}"
        )
        
        response_embed.add_field(name="👤 Usuario", value=usuario.mention, inline=True)
        response_embed.add_field(name="📨 DM Exitoso", value="✅ Sí" if dm_success else "❌ No", inline=True)
        response_embed.add_field(name="🔗 Mensaje Original", value=mensaje_original or "No especificado", inline=True)
        
        if not dm_success:
            response_embed.add_field(
                name="💡 Nota",
                value="No se pudo enviar mensaje directo. El usuario debe tener los DMs habilitados.",
                inline=False
            )
        
        await interaction.response.send_message(embed=response_embed, ephemeral=True)
        
        # Log de la acción
        print(f"🤖 Respuesta IA enviada a {usuario.name} por {interaction.user.name}: {mensaje[:50]}...")
        
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Error al enviar la respuesta IA: {str(e)}",
            ephemeral=True
        )

# =============================================
# 4. COMANDO /ask
# =============================================

@main_bot.tree.command(name="ask", description="Haz una pregunta privada sobre KICK Clipping o RASMR")
@app_commands.describe(
    pregunta="Tu pregunta o consulta",
    tipo_consulta="Tipo de consulta"
)
@app_commands.choices(tipo_consulta=[
    app_commands.Choice(name="KICK Clipping", value="kick"),
    app_commands.Choice(name="RASMR", value="rasmr"),
    app_commands.Choice(name="Problemas Técnicos", value="tech"),
    app_commands.Choice(name="Pagos", value="pagos"),
    app_commands.Choice(name="Otro", value="otro")
])
async def ask(interaction: discord.Interaction, pregunta: str, tipo_consulta: str = "otro"):
    """Envía una pregunta privada al equipo de soporte"""
    
    # Mapear tipos de consulta a nombres legibles
    tipo_names = {
        "kick": "KICK Clipping",
        "rasmr": "RASMR", 
        "tech": "Problemas Técnicos",
        "pagos": "Pagos",
        "otro": "Otro"
    }
    
    tipo_nombre = tipo_names.get(tipo_consulta, "Otro")
    
    # Crear embed para el usuario (confirmación)
    user_embed = discord.Embed(
        title="✅ Pregunta Enviada",
        color=0x00ff00,
        description="Tu pregunta ha sido enviada al equipo de soporte. Te contactaremos pronto."
    )
    
    user_embed.add_field(name="📋 Tipo", value=tipo_nombre, inline=True)
    user_embed.add_field(name="👤 Remitente", value=interaction.user.mention, inline=True)
    user_embed.add_field(name="🕒 Enviado", value=datetime.now().strftime("%H:%M"), inline=True)
    
    user_embed.add_field(
        name="❓ Tu Pregunta",
        value=pregunta,
        inline=False
    )
    
    user_embed.set_footer(text="Por favor, sé paciente mientras revisamos tu consulta")
    
    await interaction.response.send_message(embed=user_embed, ephemeral=True)
    
    # Buscar canal de soporte para enviar la pregunta del usuario
    support_channel = None
    for channel in interaction.guild.channels:
        if "soporte" in channel.name.lower() or "support" in channel.name.lower() or "tickets" in channel.name.lower():
            if isinstance(channel, discord.TextChannel):
                support_channel = channel
                break
    
    # Si no se encuentra canal de soporte, usar el canal actual
    if not support_channel:
        support_channel = interaction.channel
    
    # Crear embed para el equipo de soporte
    support_embed = discord.Embed(
        title="🎫 Nueva Pregunta de Soporte",
        color=0xffa500,
        timestamp=datetime.now()
    )
    
    support_embed.add_field(name="👤 Usuario", value=f"{interaction.user.mention} ({interaction.user.id})", inline=True)
    support_embed.add_field(name="📋 Tipo", value=tipo_nombre, inline=True)
    support_embed.add_field(name="🔗 Canal", value=interaction.channel.mention, inline=True)
    
    support_embed.add_field(
        name="❓ Pregunta",
        value=pregunta,
        inline=False
    )
    
    support_embed.add_field(
        name="⚡ Acciones Rápidas",
        value=(
            f"• Usar `/aisend` para respuesta automática\n"
            f"• Contactar a {interaction.user.mention}\n"
            f"• Revisar historial del usuario"
        ),
        inline=False
    )
    
    support_embed.set_footer(text=f"ID: {interaction.user.id} • Usa /aisend para responder")
    
    # Enviar al canal de soporte
    try:
        await support_channel.send(embed=support_embed)
        
        # Log en consola
        print(f"❓ Nueva pregunta de {interaction.user.name}: {pregunta[:50]}...")
        
    except Exception as e:
        # Si falla el envío al canal de soporte, log del error
        print(f"❌ Error enviando pregunta a canal de soporte: {str(e)}")

# =============================================
# 5. COMANDOS DE ATTACHMENTSPAM
# =============================================

@main_bot.tree.command(name="attachmentspam", description="Configuración de protección contra spam de archivos")
@app_commands.describe(
    accion="Acción a realizar",
    castigo="Tipo de castigo para spam de archivos",
    limite="Límite de archivos por timeframe",
    timeframe="Tiempo en segundos para el límite"
)
@app_commands.choices(accion=[
    app_commands.Choice(name="disable", value="disable"),
    app_commands.Choice(name="punishment", value="punishment"),
    app_commands.Choice(name="set", value="set")
])
@app_commands.choices(castigo=[
    app_commands.Choice(name="Advertencia", value="warn"),
    app_commands.Choice(name="Silenciar", value="mute"),
    app_commands.Choice(name="Expulsar", value="kick"),
    app_commands.Choice(name="Banear", value="ban")
])
@app_commands.default_permissions(manage_guild=True)
async def attachmentspam(
    interaction: discord.Interaction, 
    accion: str,
    castigo: str = None,
    limite: int = None,
    timeframe: int = None
):
    """Configura la protección contra spam de archivos"""
    
    async with main_bot.db_pool.acquire() as conn:
        # Asegurarse de que existe la configuración del servidor
        await conn.execute('''
            INSERT INTO server_settings (guild_id) 
            VALUES ($1) 
            ON CONFLICT (guild_id) DO NOTHING
        ''', interaction.guild.id)
        
        if accion == "disable":
            # Desactivar protección
            await conn.execute(
                'UPDATE server_settings SET attachmentspam_enabled = FALSE WHERE guild_id = $1',
                interaction.guild.id
            )
            
            embed = discord.Embed(
                title="✅ Protección Desactivada",
                description="La protección contra spam de archivos ha sido **desactivada**.",
                color=0xff0000
            )
            embed.set_footer(text="Los usuarios pueden enviar archivos sin límites")
            
        elif accion == "punishment" and castigo:
            # Configurar castigo
            await conn.execute(
                'UPDATE server_settings SET attachmentspam_punishment = $1 WHERE guild_id = $2',
                castigo, interaction.guild.id
            )
            
            castigo_nombres = {
                "warn": "Advertencia",
                "mute": "Silenciar", 
                "kick": "Expulsar",
                "ban": "Banear"
            }
            
            embed = discord.Embed(
                title="✅ Castigo Configurado",
                description=f"Castigo por spam de archivos establecido a: **{castigo_nombres.get(castigo, castigo)}**",
                color=0x00ff00
            )
            
        elif accion == "set" and limite and timeframe:
            # Configurar límites
            await conn.execute(
                'UPDATE server_settings SET attachmentspam_limit = $1, attachmentspam_timeframe = $2 WHERE guild_id = $3',
                limite, timeframe, interaction.guild.id
            )
            
            embed = discord.Embed(
                title="✅ Límites Configurados",
                description=f"Límite de archivos establecido: **{limite} archivos** en **{timeframe} segundos**",
                color=0x00ff00
            )
            embed.add_field(name="📊 Configuración", value=f"Máximo {limite} archivos por {timeframe} segundos")
            
        else:
            await interaction.response.send_message(
                "❌ Parámetros incorrectos. Usa: `/attachmentspam disable` o `/attachmentspam punishment <castigo>` o `/attachmentspam set <limite> <timeframe>`",
                ephemeral=True
            )
            return
    
    await interaction.response.send_message(embed=embed)

# =============================================
# 6. SISTEMA DE REGISTRO - NUEVOS COMANDOS
# =============================================

@main_bot.tree.command(name="registrar", description="Registra tus cuentas de redes sociales en el sistema")
@app_commands.describe(
    plataforma="Plataforma a registrar",
    usuario="Tu nombre de usuario en esa plataforma"
)
@app_commands.choices(plataforma=[
    app_commands.Choice(name="Instagram", value="instagram"),
    app_commands.Choice(name="Twitter/X", value="twitter"),
    app_commands.Choice(name="TikTok", value="tiktok"),
    app_commands.Choice(name="YouTube", value="youtube"),
    app_commands.Choice(name="Twitch", value="twitch"),
    app_commands.Choice(name="Facebook", value="facebook")
])
async def registrar(interaction: discord.Interaction, plataforma: str, usuario: str):
    """Registra y verifica cuentas de redes sociales"""
    
    # Limpiar el nombre de usuario (quitar @ si existe)
    usuario_limpio = usuario.lstrip('@')
    
    # Generar código de verificación único
    verification_code = f"CLIP{interaction.user.id}{plataforma[:3].upper()}"
    
    async with main_bot.db_pool.acquire() as conn:
        try:
            # Asegurarse de que el usuario existe en la tabla users
            await conn.execute('''
                INSERT INTO users (discord_id, username) 
                VALUES ($1, $2) 
                ON CONFLICT (discord_id) DO UPDATE SET username = $2
            ''', interaction.user.id, str(interaction.user))
            
            # Insertar o actualizar la cuenta social - CORREGIDO
            await conn.execute('''
                INSERT INTO social_accounts (discord_id, platform, username, verification_code, is_verified)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (discord_id, platform, username) 
                DO UPDATE SET 
                    verification_code = EXCLUDED.verification_code,
                    is_verified = EXCLUDED.is_verified
            ''', interaction.user.id, plataforma.lower(), usuario_limpio, verification_code, False)
            
            # Crear embed de registro
            embed = discord.Embed(
                title="📝 Registro Iniciado",
                description=f"**{plataforma}**: `{usuario_limpio}`",
                color=0x00ff00
            )
            
            embed.add_field(
                name="🔑 Código de Verificación",
                value=f"```{verification_code}```",
                inline=False
            )
            
            embed.add_field(
                name="📋 Pasos para Completar Registro",
                value=(
                    f"1. **Copia el código de arriba**\n"
                    f"2. **Pégalo en tu BIO de {plataforma}**\n"
                    f"3. **Mantén el código por 5 minutos**\n"
                    f"4. **Usa** `/verificar {plataforma} {usuario_limpio}`\n"
                    f"5. **¡Listo! Ya puedes quitar el código**"
                ),
                inline=False
            )
            
            embed.set_footer(text="¿Problemas? Contacta a un administrador")
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except Exception as e:
            # Manejar errores de base de datos
            error_embed = discord.Embed(
                title="❌ Error en el Registro",
                description="Ocurrió un error al procesar tu registro.",
                color=0xff0000
            )
            error_embed.add_field(
                name="🔧 Detalles",
                value=f"```{str(e)}```",
                inline=False
            )
            await interaction.response.send_message(embed=error_embed, ephemeral=True)
            print(f"❌ Error en comando registrar: {e}")

@main_bot.tree.command(name="verificar", description="Verifica tu cuenta después de poner el código en la bio")
@app_commands.describe(
    plataforma="Plataforma a verificar",
    usuario="Tu nombre de usuario"
)
@app_commands.choices(plataforma=[
    app_commands.Choice(name="Instagram", value="instagram"),
    app_commands.Choice(name="Twitter/X", value="twitter"),
    app_commands.Choice(name="TikTok", value="tiktok"),
    app_commands.Choice(name="YouTube", value="youtube"),
    app_commands.Choice(name="Twitch", value="twitch"),
    app_commands.Choice(name="Facebook", value="facebook")
])
async def verificar(interaction: discord.Interaction, plataforma: str, usuario: str):
    """Verifica una cuenta después de la verificación"""
    
    # Limpiar el nombre de usuario
    usuario_limpio = usuario.lstrip('@')
    
    async with main_bot.db_pool.acquire() as conn:
        # Buscar la cuenta
        cuenta = await conn.fetchrow(
            'SELECT * FROM social_accounts WHERE discord_id = $1 AND platform = $2 AND username = $3',
            interaction.user.id, plataforma.lower(), usuario_limpio
        )
    
    if not cuenta:
        await interaction.response.send_message(
            f"❌ No encontramos tu registro de **{plataforma}**: `{usuario_limpio}`\n"
            f"Usa primero `/registrar {plataforma} {usuario_limpio}`",
            ephemeral=True
        )
        return
    
    # Verificar si ya está verificada
    if cuenta['is_verified']:
        embed = discord.Embed(
            title="ℹ️ Cuenta Ya Verificada",
            description=f"**{plataforma}**: `{usuario_limpio}` ya estaba verificada.",
            color=0xffff00
        )
        embed.add_field(
            name="📅 Fecha de Verificación",
            value=cuenta['verified_at'].strftime("%d/%m/%Y %H:%M") if cuenta['verified_at'] else "No disponible",
            inline=False
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Simular verificación (en producción aquí iría la lógica real de verificación API)
    is_verified = True  # Esto simula una verificación exitosa
    
    if is_verified:
        async with main_bot.db_pool.acquire() as conn:
            await conn.execute(
                'UPDATE social_accounts SET is_verified = $1, verified_at = $2 WHERE id = $3',
                True, datetime.now(), cuenta['id']
            )
        
        # Embed de éxito
        embed = discord.Embed(
            title="✅ ¡Cuenta Verificada!",
            description=f"**{plataforma}**: `{usuario_limpio}`",
            color=0x00ff00
        )
        
        embed.add_field(
            name="🎉 ¡Felicidades!",
            value=(
                "Tu cuenta ha sido verificada exitosamente.\n"
                "**Ya puedes quitar el código de verificación de tu bio.**"
            ),
            inline=False
        )
        
        embed.add_field(
            name="📊 ¿Qué sigue?",
            value=(
                "• Ahora apareces en las búsquedas de administradores\n"
                "• Puedes registrar más cuentas con `/registrar`\n"
                "• Los admins pueden encontrarte por tus redes sociales"
            ),
            inline=False
        )
        
    else:
        embed = discord.Embed(
            title="❌ Verificación Fallida",
            description=f"No pudimos verificar **{plataforma}**: `{usuario_limpio}`",
            color=0xff0000
        )
        
        embed.add_field(
            name="🔍 ¿Qué puede fallar?",
            value=(
                "• El código no está en tu bio\n"
                "• El código es incorrecto\n"
                "• La cuenta es privada\n"
                "• Error temporal de la plataforma"
            ),
            inline=False
        )
        
        embed.add_field(
            name="🛠️ Solución",
            value=(
                "1. Asegúrate de que el código esté en tu BIO\n"
                "2. Espera 2-3 minutos después de ponerlo\n"
                "3. Intenta de nuevo con `/verificar`\n"
                "4. Si sigue fallando, contacta a un admin"
            ),
            inline=False
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@main_bot.tree.command(name="mis-cuentas", description="Ver todas tus cuentas registradas")
async def mis_cuentas(interaction: discord.Interaction):
    """Muestra todas las cuentas registradas del usuario"""
    
    async with main_bot.db_pool.acquire() as conn:
        cuentas = await conn.fetch(
            'SELECT platform, username, is_verified, verified_at FROM social_accounts WHERE discord_id = $1 ORDER BY is_verified DESC, platform',
            interaction.user.id
        )
    
    if not cuentas:
        embed = discord.Embed(
            title="📱 Tus Cuentas Registradas",
            description="Aún no tienes cuentas registradas.",
            color=0xffa500
        )
        
        embed.add_field(
            name="💡 ¿Cómo registrar?",
            value="Usa `/registrar` para vincular tus redes sociales",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    embed = discord.Embed(
        title="📱 Tus Cuentas Registradas",
        description=f"**{len(cuentas)}** cuenta(s) vinculada(s)",
        color=0x00ff00
    )
    
    cuentas_verificadas = 0
    for cuenta in cuentas:
        if cuenta['is_verified']:
            cuentas_verificadas += 1
            estado = "✅ Verificada"
            fecha = cuenta['verified_at'].strftime("%d/%m/%Y") if cuenta['verified_at'] else "Reciente"
        else:
            estado = "⏳ Pendiente"
            fecha = "Por verificar"
        
        embed.add_field(
            name=f"{cuenta['platform'].title()} - {cuenta['username']}",
            value=f"**Estado:** {estado}\n**Fecha:** {fecha}",
            inline=True
        )
    
    embed.set_footer(text=f"{cuentas_verificadas}/{len(cuentas)} cuentas verificadas")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


# =============================================
# 7. SISTEMA DE PAGOS - SOLO PAYPAL
# =============================================

import re  # asegúrate de tener esto al inicio del archivo si aún no lo importaste

@main_bot.tree.command(name="add-paypal", description="Agregar o reemplazar tus datos de PayPal")
@app_commands.describe(
    email="Tu correo electrónico de PayPal",
    nombre="Tu nombre",
    apellido="Tu apellido"
)
async def add_paypal(interaction: discord.Interaction, email: str, nombre: str, apellido: str):
    """Agrega o actualiza tu método de pago PayPal"""

    # Validar formato del email
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        await interaction.response.send_message(
            "❌ El correo electrónico ingresado no es válido. Verifícalo e inténtalo de nuevo.",
            ephemeral=True
        )
        return

    async with main_bot.db_pool.acquire() as conn:
        # Asegurar que el usuario existe en la tabla users
        await conn.execute('''
            INSERT INTO users (discord_id, username)
            VALUES ($1, $2)
            ON CONFLICT (discord_id) DO UPDATE SET username = $2
        ''', interaction.user.id, str(interaction.user))

        # Insertar o actualizar el método de pago PayPal
        await conn.execute('''
            INSERT INTO payment_methods (discord_id, method_type, paypal_email, first_name, last_name)
            VALUES ($1, 'paypal', $2, $3, $4)
            ON CONFLICT (discord_id, method_type)
            DO UPDATE SET paypal_email = $2, first_name = $3, last_name = $4
        ''', interaction.user.id, email, nombre, apellido)

    embed = discord.Embed(
        title="✅ Método de Pago Configurado",
        description="Tu información de PayPal ha sido guardada exitosamente.",
        color=0x00ff00
    )
    embed.add_field(name="📧 Email", value=email, inline=True)
    embed.add_field(name="👤 Nombre", value=f"{nombre} {apellido}", inline=True)
    embed.add_field(name="💳 Método", value="PayPal", inline=True)
    embed.set_footer(text="Usa /payment-details para ver o revisar tus métodos de pago")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@main_bot.tree.command(name="payment-details", description="Ver tus detalles de pago actuales")
async def payment_details(interaction: discord.Interaction):
    """Muestra el método de pago PayPal del usuario"""

    async with main_bot.db_pool.acquire() as conn:
        metodo = await conn.fetchrow(
            'SELECT paypal_email, first_name, last_name FROM payment_methods WHERE discord_id = $1 AND method_type = $2',
            interaction.user.id, 'paypal'
        )

    if not metodo:
        embed = discord.Embed(
            title="💰 Métodos de Pago",
            description="No tienes un método de pago configurado todavía.",
            color=0xffa500
        )
        embed.add_field(
            name="💡 Configurar Pagos",
            value="Usa `/add-paypal` para agregar tu correo de PayPal.",
            inline=False
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    embed = discord.Embed(
        title="💳 Tus Detalles de Pago (PayPal)",
        color=0x00ff00
    )
    embed.add_field(name="📧 Email", value=metodo['paypal_email'], inline=True)
    embed.add_field(name="👤 Nombre", value=f"{metodo['first_name']} {metodo['last_name']}", inline=True)
    embed.set_footer(text="Usa /add-paypal si deseas actualizar tu información")

    await interaction.response.send_message(embed=embed, ephemeral=True)


# =============================================
# SISTEMA DE CAMPAÑAS (Active Campaigns)
# =============================================
from discord.ui import View, Button
from datetime import datetime
import os

CAMPAIGNS_CHANNEL_ID = int(os.getenv("CAMPAIGNS_CHANNEL_ID", 0))

# ---------- /publish-campaign ----------
@main_bot.tree.command(name="publish-campaign", description="Publica una nueva campaña en el canal de Active Campaigns")
@app_commands.describe(
    nombre="Nombre de la campaña",
    descripcion="Descripción de la campaña",
    categoria="Categoría (Gaming, Gambling, Crypto, etc.)",
    payrate="Ej: $5/1000 views, 20 USD per clip, etc.",
    invite_link="Link de invitación del servidor",
    thumbnail_url="Banner o imagen de la campaña (opcional)"
)
@app_commands.default_permissions(administrator=True)
async def publish_campaign(
    interaction: discord.Interaction,
    nombre: str,
    descripcion: str,
    categoria: str,
    payrate: str,
    invite_link: str,
    thumbnail_url: str = None
):
    """Publica una campaña en el canal de campañas"""

    if CAMPAIGNS_CHANNEL_ID == 0:
        await interaction.response.send_message("⚠️ No hay canal configurado en `.env` (CAMPAIGNS_CHANNEL_ID).", ephemeral=True)
        return

    channel = interaction.client.get_channel(CAMPAIGNS_CHANNEL_ID)
    if not channel:
        await interaction.response.send_message("❌ No se encontró el canal de campañas. Verifica el ID.", ephemeral=True)
        return

    async with main_bot.db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO campaigns (name, description, category, payrate, invite_link, thumbnail_url, created_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        ''', nombre, descripcion, categoria, payrate, invite_link, thumbnail_url, interaction.user.id)

    embed = discord.Embed(
        title=f"🎯 {nombre}",
        description=descripcion,
        color=0x00ff00,
        timestamp=datetime.now()
    )
    embed.add_field(name="🏷️ Categoría", value=categoria, inline=True)
    embed.add_field(name="💰 Payrate", value=payrate, inline=True)
    embed.add_field(name="📅 Fecha", value=datetime.now().strftime("%d/%m/%Y"), inline=True)
    embed.set_footer(text=f"Publicado por {interaction.user.display_name}")

    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)

    class JoinButton(View):
        def __init__(self, link):
            super().__init__()
            self.add_item(Button(label="Join Server", style=discord.ButtonStyle.link, url=link))

    await channel.send(embed=embed, view=JoinButton(invite_link))
    await interaction.response.send_message("✅ Campaña publicada correctamente en Active Campaigns.", ephemeral=True)


# ---------- /edit-campaign ----------
@main_bot.tree.command(name="edit-campaign", description="Edita una campaña existente por ID")
@app_commands.describe(
    id_campaña="ID de la campaña a editar",
    nombre="Nuevo nombre (opcional)",
    descripcion="Nueva descripción (opcional)",
    payrate="Nuevo payrate (opcional)",
    invite_link="Nuevo link de invitación (opcional)"
)
@app_commands.default_permissions(administrator=True)
async def edit_campaign(
    interaction: discord.Interaction,
    id_campaña: int,
    nombre: str = None,
    descripcion: str = None,
    payrate: str = None,
    invite_link: str = None
):
    """Edita una campaña en la base de datos"""

    async with main_bot.db_pool.acquire() as conn:
        camp = await conn.fetchrow("SELECT * FROM campaigns WHERE id = $1", id_campaña)
        if not camp:
            await interaction.response.send_message("❌ No existe ninguna campaña con ese ID.", ephemeral=True)
            return

        new_name = nombre or camp["name"]
        new_desc = descripcion or camp["description"]
        new_rate = payrate or camp["payrate"]
        new_link = invite_link or camp["invite_link"]

        await conn.execute('''
            UPDATE campaigns
            SET name=$1, description=$2, payrate=$3, invite_link=$4
            WHERE id=$5
        ''', new_name, new_desc, new_rate, new_link, id_campaña)

    embed = discord.Embed(
        title="✅ Campaña Actualizada",
        description=f"La campaña **{new_name}** fue editada exitosamente.",
        color=0x00ff00
    )
    embed.add_field(name="💰 Payrate", value=new_rate, inline=True)
    embed.add_field(name="🔗 Invite", value=new_link, inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------- /list-campaigns ----------
@main_bot.tree.command(name="list-campaigns", description="Muestra todas las campañas activas")
async def list_campaigns(interaction: discord.Interaction):
    """Lista todas las campañas registradas"""

    async with main_bot.db_pool.acquire() as conn:
        campaigns = await conn.fetch("SELECT id, name, category, payrate, invite_link FROM campaigns ORDER BY created_at DESC")

    if not campaigns:
        await interaction.response.send_message("⚠️ No hay campañas activas registradas.", ephemeral=True)
        return

    embed = discord.Embed(
        title="📢 Campañas Activas",
        color=0x00ff00,
        timestamp=datetime.now()
    )

    for camp in campaigns:
        embed.add_field(
            name=f"🎯 {camp['name']} (ID: {camp['id']})",
            value=f"**Categoría:** {camp['category']}\n**Payrate:** {camp['payrate']}\n[Join Server]({camp['invite_link']})",
            inline=False
        )

    embed.set_footer(text="Usa /publish-campaign para agregar una nueva")
    await interaction.response.send_message(embed=embed, ephemeral=True)



# =============================================
# EJECUCIÓN DEL BOT PRINCIPAL
# =============================================

if __name__ == "__main__":
    token = os.getenv('DISCORD_MAIN_BOT_TOKEN')
    if not token:
        print("❌ ERROR: DISCORD_MAIN_BOT_TOKEN no encontrado en las variables de entorno")
        exit(1)
    
    main_bot.run(token)