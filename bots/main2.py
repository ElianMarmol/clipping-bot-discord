import discord
from discord.ext import commands
from discord import app_commands
import os
import asyncpg
from datetime import datetime
import asyncio
import json
from dotenv import load_dotenv

load_dotenv()

class AdminBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.bans = True
        
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
             ssl='require',
             min_size=1,
             max_size=1
            )
        await self.create_tables()
        await self.tree.sync()
        print("✅ Bot de Administración conectado y comandos sincronizados")

    async def create_tables(self):
        async with self.db_pool.acquire() as conn:
            # Tabla de backups
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS server_backups (
                    id SERIAL PRIMARY KEY,
                    guild_id BIGINT,
                    backup_name TEXT,
                    member_count INTEGER,
                    backup_data JSONB,
                    created_by BIGINT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # Tabla de anuncios
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS announcements (
                    id SERIAL PRIMARY KEY,
                    guild_id BIGINT,
                    title TEXT,
                    message TEXT,
                    channel_id BIGINT,
                    created_by BIGINT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            print("✅ Tablas de administración creadas")

    async def on_ready(self):
        self.start_time = datetime.now()
        print(f'✅ Bot de Administración conectado como {self.user.name}')
        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching, 
            name="Sistema de Administración"
        ))

# Inicializar el bot de administración
admin_bot = AdminBot()

# =============================================
# 1. COMANDO: ENCONTRAR USUARIO POR REDES SOCIALES
# =============================================

@admin_bot.tree.command(name="encontrar-usuario", description="Encuentra un usuario por su nombre de usuario en una plataforma")
@app_commands.describe(
    plataforma="Plataforma donde buscar",
    nombre_usuario="Nombre de usuario a buscar"
)
@app_commands.choices(plataforma=[
    app_commands.Choice(name="Twitter/X", value="twitter"),
    app_commands.Choice(name="Instagram", value="instagram"),
    app_commands.Choice(name="TikTok", value="tiktok"),
    app_commands.Choice(name="YouTube", value="youtube"),
    app_commands.Choice(name="Twitch", value="twitch"),
    app_commands.Choice(name="Facebook", value="facebook")
])
@app_commands.default_permissions(administrator=True)
async def encontrar_usuario(interaction: discord.Interaction, plataforma: str, nombre_usuario: str):
    """Encuentra usuarios del servidor por sus redes sociales"""
    
    async with admin_bot.db_pool.acquire() as conn:
        # Buscar en la base de datos
        usuarios = await conn.fetch(
            '''
            SELECT u.discord_id, u.username, sa.username as social_username, sa.is_verified
            FROM users u
            JOIN social_accounts sa ON u.discord_id = sa.discord_id
            WHERE sa.platform = $1 AND sa.username ILIKE $2 AND sa.is_verified = true
            ''',
            plataforma, f"%{nombre_usuario}%"
        )
    
    if not usuarios:
        embed = discord.Embed(
            title="🔍 Búsqueda de Usuario",
            description=f"No se encontraron usuarios con **{nombre_usuario}** en **{plataforma}**",
            color=0xff0000
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    embed = discord.Embed(
        title="🔍 Resultados de Búsqueda",
        description=f"Usuarios encontrados en **{plataforma}** con: `{nombre_usuario}`",
        color=0x00ff00
    )
    
    for i, usuario in enumerate(usuarios[:10], 1):  # Mostrar máximo 10 resultados
        try:
            user = await interaction.guild.fetch_member(usuario['discord_id'])
            user_mention = user.mention
        except:
            user_mention = f"`{usuario['username']}` (No en el servidor)"
        
        embed.add_field(
            name=f"#{i} {usuario['social_username']}",
            value=f"Discord: {user_mention}\nEstado: {'✅ Verificado' if usuario['is_verified'] else '❌ No verificado'}",
            inline=False
        )
    
    if len(usuarios) > 10:
        embed.set_footer(text=f"Mostrando 10 de {len(usuarios)} resultados encontrados")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# =============================================
# 2. COMANDO: BACKUP DEL SERVIDOR
# =============================================

@admin_bot.tree.command(name="backup-servidor", description="Hacer backup de los miembros del servidor y sus cuentas")
@app_commands.describe(
    nombre_backup="Nombre para identificar este backup"
)
@app_commands.default_permissions(administrator=True)
async def backup_servidor(interaction: discord.Interaction, nombre_backup: str = None):
    """Crea un backup completo del servidor"""
    
    await interaction.response.defer(ephemeral=True)
    
    # Recolectar datos de miembros
    miembros_data = []
    total_miembros = 0
    miembros_con_cuentas = 0
    
    async with admin_bot.db_pool.acquire() as conn:
        for member in interaction.guild.members:
            if not member.bot:  # Ignorar bots
                total_miembros += 1
                
                # Obtener cuentas sociales del miembro
                cuentas_sociales = await conn.fetch(
                    'SELECT platform, username, is_verified FROM social_accounts WHERE discord_id = $1',
                    member.id
                )
                
                miembro_info = {
                    'discord_id': member.id,
                    'username': str(member),
                    'joined_at': member.joined_at.isoformat() if member.joined_at else None,
                    'cuentas_sociales': [
                        {
                            'platform': cuenta['platform'],
                            'username': cuenta['username'],
                            'verified': cuenta['is_verified']
                        } for cuenta in cuentas_sociales
                    ]
                }
                
                if cuentas_sociales:
                    miembros_con_cuentas += 1
                
                miembros_data.append(miembro_info)
        
        # Guardar backup en la base de datos
        backup_name = nombre_backup or f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        await conn.execute(
            '''
            INSERT INTO server_backups (guild_id, backup_name, member_count, backup_data, created_by)
            VALUES ($1, $2, $3, $4, $5)
            ''',
            interaction.guild.id, backup_name, total_miembros, json.dumps(miembros_data), interaction.user.id
        )
    
    # Crear embed de resultados
    embed = discord.Embed(
        title="💾 Backup del Servidor Completado",
        description=f"Backup guardado como: **{backup_name}**",
        color=0x00ff00,
        timestamp=datetime.now()
    )
    
    embed.add_field(name="👥 Miembros Totales", value=total_miembros, inline=True)
    embed.add_field(name="📱 Con Cuentas", value=miembros_con_cuentas, inline=True)
    embed.add_field(name="👤 Creado por", value=interaction.user.mention, inline=True)
    
    embed.add_field(
        name="📊 Estadísticas",
        value=(
            f"• **Miembros respaldados:** {total_miembros}\n"
            f"• **Cuentas vinculadas:** {miembros_con_cuentas}\n"
            f"• **Porcentaje con cuentas:** {round((miembros_con_cuentas/total_miembros)*100 if total_miembros > 0 else 0, 1)}%"
        ),
        inline=False
    )
    
    embed.set_footer(text="El backup incluye información de miembros y sus cuentas sociales verificadas")
    
    await interaction.followup.send(embed=embed, ephemeral=True)

# =============================================
# 3. COMANDO: VERIFICAR BANEOS
# =============================================

@admin_bot.tree.command(name="verificar-baneo", description="Verifica si un usuario está baneado y ve los detalles")
@app_commands.describe(usuario="Usuario a verificar")
@app_commands.default_permissions(ban_members=True)
async def verificar_ban(interaction: discord.Interaction, usuario: discord.User):
    """Verifica el estado de baneo de un usuario"""
    
    try:
        # Intentar obtener información del baneo
        ban_info = await interaction.guild.fetch_ban(usuario)
        
        embed = discord.Embed(
            title="🔨 Usuario Baneado",
            color=0xff0000,
            timestamp=datetime.now()
        )
        
        embed.add_field(name="👤 Usuario", value=f"{usuario.mention} (`{usuario.id}`)", inline=True)
        embed.add_field(name="🔍 Estado", value="✅ Baneado", inline=True)
        
        if ban_info.reason:
            embed.add_field(
                name="📝 Razón del Baneo",
                value=ban_info.reason,
                inline=False
            )
        
        # Intentar obtener información adicional de la base de datos
        async with admin_bot.db_pool.acquire() as conn:
            user_data = await conn.fetchrow(
                'SELECT username, created_at FROM users WHERE discord_id = $1',
                usuario.id
            )
            
            if user_data:
                embed.add_field(
                    name="📊 Información Adicional",
                    value=f"Registrado: {user_data['created_at'].strftime('%Y-%m-%d')}",
                    inline=False
                )
        
        embed.set_footer(text=f"Verificado por {interaction.user.name}")
        
    except discord.NotFound:
        # Usuario no está baneado
        embed = discord.Embed(
            title="✅ Usuario No Baneado",
            description=f"{usuario.mention} no está baneado en este servidor.",
            color=0x00ff00
        )
        
        embed.add_field(name="👤 Usuario", value=f"{usuario.mention} (`{usuario.id}`)", inline=True)
        embed.add_field(name="🔍 Estado", value="✅ No baneado", inline=True)
    
    except Exception as e:
        embed = discord.Embed(
            title="❌ Error al Verificar",
            description=f"No se pudo verificar el estado de baneo: {str(e)}",
            color=0xff0000
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# =============================================
# 4. COMANDO: CREAR ANUNCIO
# =============================================

@admin_bot.tree.command(name="crear-anuncio", description="Crear un anuncio para el servidor")
@app_commands.describe(
    titulo="Título del anuncio",
    mensaje="Mensaje del anuncio",
    canal="Canal donde publicar el anuncio"
)
@app_commands.default_permissions(administrator=True)
async def crear_anuncio(
    interaction: discord.Interaction, 
    titulo: str, 
    mensaje: str, 
    canal: discord.TextChannel = None
):
    """Crea y publica un anuncio en el servidor"""
    
    # Si no se especifica canal, usar el canal actual
    if not canal:
        canal = interaction.channel
    
    # Crear embed del anuncio
    anuncio_embed = discord.Embed(
        title=f"📢 {titulo}",
        description=mensaje,
        color=0xffd700,
        timestamp=datetime.now()
    )
    
    anuncio_embed.add_field(
        name="ℹ️ Información",
        value="Este es un anuncio oficial del servidor",
        inline=False
    )
    
    anuncio_embed.set_footer(text=f"Anuncio por {interaction.user.name}")
    
    try:
        # Publicar anuncio
        mensaje_anuncio = await canal.send(embed=anuncio_embed)
        
        # Opcional: mencionar everyone/here si es un canal de anuncios
        if "anuncios" in canal.name.lower() or "announcements" in canal.name.lower():
            await mensaje_anuncio.edit(content="@everyone")
        
        # Guardar en base de datos
        async with admin_bot.db_pool.acquire() as conn:
            await conn.execute(
                '''
                INSERT INTO announcements (guild_id, title, message, channel_id, created_by)
                VALUES ($1, $2, $3, $4, $5)
                ''',
                interaction.guild.id, titulo, mensaje, canal.id, interaction.user.id
            )
        
        # Embed de confirmación
        confirm_embed = discord.Embed(
            title="✅ Anuncio Publicado",
            description=f"El anuncio ha sido publicado en {canal.mention}",
            color=0x00ff00
        )
        
        confirm_embed.add_field(name="📢 Título", value=titulo, inline=True)
        confirm_embed.add_field(name="📝 Canal", value=canal.mention, inline=True)
        confirm_embed.add_field(name="👤 Publicado por", value=interaction.user.mention, inline=True)
        
        await interaction.response.send_message(embed=confirm_embed, ephemeral=True)
        
        print(f"📢 Anuncio publicado por {interaction.user.name} en {canal.name}")
        
    except Exception as e:
        error_embed = discord.Embed(
            title="❌ Error al Publicar Anuncio",
            description=f"No se pudo publicar el anuncio: {str(e)}",
            color=0xff0000
        )
        await interaction.response.send_message(embed=error_embed, ephemeral=True)

# =============================================
# 5. COMANDO: REMOVER CUENTA
# =============================================

@admin_bot.tree.command(name="remover-cuenta", description="Remover una cuenta del sistema")
@app_commands.describe(
    usuario="Usuario de Discord",
    plataforma="Plataforma de la cuenta a remover",
    nombre_usuario="Nombre de usuario específico (opcional)"
)
@app_commands.choices(plataforma=[
    app_commands.Choice(name="Todas las plataformas", value="all"),
    app_commands.Choice(name="Twitter/X", value="twitter"),
    app_commands.Choice(name="Instagram", value="instagram"),
    app_commands.Choice(name="TikTok", value="tiktok"),
    app_commands.Choice(name="YouTube", value="youtube"),
    app_commands.Choice(name="Twitch", value="twitch")
])
@app_commands.default_permissions(administrator=True)
async def remover_cuenta(
    interaction: discord.Interaction, 
    usuario: discord.User, 
    plataforma: str,
    nombre_usuario: str = None
):
    """Remueve cuentas sociales del sistema"""
    
    async with admin_bot.db_pool.acquire() as conn:
        if plataforma == "all":
            # Remover todas las cuentas del usuario
            result = await conn.execute(
                'DELETE FROM social_accounts WHERE discord_id = $1',
                usuario.id
            )
            
            if result == "DELETE 0":
                await interaction.response.send_message(
                    f"❌ {usuario.mention} no tiene cuentas registradas en el sistema.",
                    ephemeral=True
                )
                return
            
            embed = discord.Embed(
                title="✅ Todas las Cuentas Removidas",
                description=f"Todas las cuentas de {usuario.mention} han sido removidas del sistema.",
                color=0x00ff00
            )
            
        else:
            # Remover cuenta específica
            if nombre_usuario:
                result = await conn.execute(
                    'DELETE FROM social_accounts WHERE discord_id = $1 AND platform = $2 AND username = $3',
                    usuario.id, plataforma, nombre_usuario
                )
                
                if result == "DELETE 0":
                    await interaction.response.send_message(
                        f"❌ No se encontró la cuenta {nombre_usuario} en {plataforma} para {usuario.mention}.",
                        ephemeral=True
                    )
                    return
                
                embed = discord.Embed(
                    title="✅ Cuenta Específica Removida",
                    description=f"Cuenta `{nombre_usuario}` en **{plataforma}** ha sido removida de {usuario.mention}.",
                    color=0x00ff00
                )
                
            else:
                # Remover todas las cuentas de una plataforma
                result = await conn.execute(
                    'DELETE FROM social_accounts WHERE discord_id = $1 AND platform = $2',
                    usuario.id, plataforma
                )
                
                if result == "DELETE 0":
                    await interaction.response.send_message(
                        f"❌ {usuario.mention} no tiene cuentas en {plataforma}.",
                        ephemeral=True
                    )
                    return
                
                embed = discord.Embed(
                    title="✅ Cuentas de Plataforma Removidas",
                    description=f"Todas las cuentas de **{plataforma}** han sido removidas de {usuario.mention}.",
                    color=0x00ff00
                )
        
        embed.add_field(name="👤 Usuario", value=usuario.mention, inline=True)
        embed.add_field(name="👨‍💼 Acción por", value=interaction.user.mention, inline=True)
        embed.add_field(name="🕒 Fecha", value=datetime.now().strftime("%Y-%m-%d %H:%M"), inline=True)
        
        embed.set_footer(text="Esta acción no se puede deshacer")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# =============================================
# COMANDO SYNC PARA ADMIN BOT
# =============================================

@admin_bot.tree.command(name="sync-admin", description="Sincronizar comandos del bot de administración")
async def sync_admin(interaction: discord.Interaction):
    """Comando temporal para sincronizar comandos slash del admin bot"""
    TU_USER_ID = 551092070136283136  # ⬅️ CAMBIA ESTO POR TU ID
    
    if interaction.user.id != TU_USER_ID:
        await interaction.response.send_message("❌ Solo el owner puede usar este comando.", ephemeral=True)
        return
    
    try:
        synced = await admin_bot.tree.sync()
        await interaction.response.send_message(
            f"✅ Sincronizados {len(synced)} comandos de administración.", 
            ephemeral=True
        )
        print(f"✅ {len(synced)} comandos de admin sincronizados")
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Error sincronizando: {str(e)}", 
            ephemeral=True
        )

# =============================================
# EJECUCIÓN DEL BOT DE ADMINISTRACIÓN
# =============================================

if __name__ == "__main__":
    token = os.getenv('DISCORD_ADMIN_BOT_TOKEN')  # ⚠️ Diferente token para el bot de admin
    
    if not token:
        print("❌ ERROR: DISCORD_ADMIN_BOT_TOKEN no encontrado")
        print("💡 Crea un NUEVO bot en Discord Developer Portal para el bot de administración")
        exit(1)
    
    admin_bot.run(token)