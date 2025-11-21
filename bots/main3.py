import discord
from discord import app_commands
from discord.ext import commands
import asyncpg, asyncio, os
from dotenv import load_dotenv
import uuid

# Cargar .env
load_dotenv()
TOKEN = os.getenv("DISCORD_EQUIPOS_BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL")

# ====================================================
#   CLASE PRINCIPAL DEL BOT
# ====================================================
class ClippingEquipos(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(command_prefix="!", intents=intents)
        self.db_pool = None

    async def setup_hook(self):
        # Conectar a la base de datos
        self.db_pool = await asyncpg.create_pool(
            os.getenv('DATABASE_URL'),
            ssl='require',
            min_size=1,
            max_size=2
        )
        await self.create_tables()
        await self.tree.sync()
        print("✅ Clipping Equipos conectado y comandos sincronizados")

    async def create_tables(self):
        async with self.db_pool.acquire() as conn:
            # Tabla de equipos
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS teams (
                id SERIAL PRIMARY KEY,
                team_name TEXT NOT NULL,
                owner_id BIGINT REFERENCES users(discord_id),
                commission_rate NUMERIC DEFAULT 5,
                invite_code TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
            ''')

            # Tabla de miembros
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS team_members (
                id SERIAL PRIMARY KEY,
                team_id INT REFERENCES teams(id) ON DELETE CASCADE,
                user_id BIGINT REFERENCES users(discord_id),
                joined_at TIMESTAMP DEFAULT NOW(),
                UNIQUE (team_id, user_id)
            )
            ''')
        print("✅ Tablas 'teams' y 'team_members' listas")

    async def on_ready(self):
        print(f"✅ Clipping Equipos conectado como {self.user} (ID: {self.user.id})")


bot = ClippingEquipos()

# ====================================================
#   /team-create
# ====================================================
@bot.tree.command(name="team-create", description="Crea un nuevo equipo con una comisión personalizada")
@app_commands.describe(nombre="Nombre del equipo", comision="Porcentaje de comisión (default 5%)")
async def team_create(interaction: discord.Interaction, nombre: str, comision: float = 5.0):
    try:
        async with bot.db_pool.acquire() as conn:
            user = await conn.fetchrow(
                "SELECT * FROM users WHERE discord_id = $1",
                interaction.user.id
            )
            payment = await conn.fetchrow(
                "SELECT * FROM payment_methods WHERE discord_id = $1",
                interaction.user.id
            )

            if not user or not payment:
                embed = discord.Embed(
                    title="❌ Account Not Found",
                    description=(
                        "We could not find your account in our records.\n\n"
                        "**To create a team, you must first:**\n"
                        "• Register a social media account in one of our clipping program servers.\n"
                        "• Provide payment methods using `/add-paypal`.\n\n"
                        "Please complete registration before creating a team."
                    ),
                    color=0xff0000
                )
                embed.set_footer(text="Clipping Equipos • 2025")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            existing_team = await conn.fetchrow(
                "SELECT * FROM teams WHERE owner_id = $1",
                interaction.user.id
            )
            if existing_team:
                await interaction.response.send_message(
                    "⚠️ Ya tenés un equipo creado.",
                    ephemeral=True
                )
                return

            invite_code = str(uuid.uuid4())[:8]
            await conn.execute('''
            INSERT INTO teams (team_name, owner_id, commission_rate, invite_code)
            VALUES ($1, $2, $3, $4)
            ''', nombre, interaction.user.id, comision, invite_code)

            embed = discord.Embed(
                title="✅ Team Created",
                description=f"Your team **{nombre}** has been created successfully!",
                color=0x00ff00
            )
            embed.add_field(name="💰 Commission Rate", value=f"{comision} %", inline=True)
            embed.add_field(name="🔑 Invite Code", value=f"`{invite_code}`", inline=True)
            embed.set_footer(text="Share this code with your members to let them join your team.")
            await interaction.response.send_message(embed=embed, ephemeral=True)

    except Exception as e:
        print("❌ ERROR en /team-create:", e)
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Ocurrió un error interno al crear el equipo.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ Ocurrió un error interno al crear el equipo.",
                    ephemeral=True
                )
        except Exception as send_err:
            print("❌ Error enviando respuesta de error en /team-create:", send_err)


# ====================================================
#   /team-join
# ====================================================
@bot.tree.command(name="team-join", description="Únete a un equipo usando su código de invitación")
@app_commands.describe(codigo="Código de invitación del equipo")
async def team_join(interaction: discord.Interaction, codigo: str):
    try:
        async with bot.db_pool.acquire() as conn:
            team = await conn.fetchrow(
                "SELECT * FROM teams WHERE invite_code = $1",
                codigo
            )
            if not team:
                await interaction.response.send_message(
                    "❌ Código inválido. Verificá que sea correcto.",
                    ephemeral=True
                )
                return

            # Evitar que el owner se una como miembro
            if team["owner_id"] == interaction.user.id:
                await interaction.response.send_message(
                    "⚠️ No podés unirte a tu propio equipo.",
                    ephemeral=True
                )
                return

            # Verificar si ya es miembro
            existing = await conn.fetchrow(
                "SELECT * FROM team_members WHERE team_id = $1 AND user_id = $2",
                team["id"], interaction.user.id
            )
            if existing:
                await interaction.response.send_message(
                    "ℹ️ Ya sos miembro de este equipo.",
                    ephemeral=True
                )
                return

            await conn.execute(
                "INSERT INTO team_members (team_id, user_id) VALUES ($1, $2)",
                team["id"], interaction.user.id
            )

            embed = discord.Embed(
                title="🎉 Joined Team Successfully",
                description=f"You have joined **{team['team_name']}**!",
                color=0x00ff00
            )
            embed.add_field(name="Owner", value=f"<@{team['owner_id']}>", inline=True)
            embed.add_field(name="Commission Rate", value=f"{team['commission_rate']}%", inline=True)
            embed.set_footer(text="Clipping Equipos • 2025")
            await interaction.response.send_message(embed=embed, ephemeral=True)

    except Exception as e:
        print("❌ ERROR en /team-join:", e)
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Ocurrió un error interno al unirte al equipo.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ Ocurrió un error interno al unirte al equipo.",
                    ephemeral=True
                )
        except Exception as send_err:
            print("❌ Error enviando respuesta de error en /team-join:", send_err)


# ====================================================
#   /team-info
# ====================================================
@bot.tree.command(name="team-info", description="Muestra información de tu equipo o del que sos miembro")
async def team_info(interaction: discord.Interaction):
    try:
        async with bot.db_pool.acquire() as conn:
            team = await conn.fetchrow(
                "SELECT * FROM teams WHERE owner_id = $1",
                interaction.user.id
            )
            if not team:
                # Buscar si el usuario es miembro de algún equipo
                member_of = await conn.fetchrow("""
                    SELECT t.* FROM teams t
                    JOIN team_members m ON m.team_id = t.id
                    WHERE m.user_id = $1
                """, interaction.user.id)
                if not member_of:
                    await interaction.response.send_message(
                        "❌ No pertenecés a ningún equipo.",
                        ephemeral=True
                    )
                    return
                team = member_of

            members = await conn.fetch(
                "SELECT user_id FROM team_members WHERE team_id = $1",
                team["id"]
            )

            embed = discord.Embed(
                title=f"📊 Team Info: {team['team_name']}",
                color=0x00b0f0
            )
            embed.add_field(name="💰 Commission", value=f"{team['commission_rate']}%", inline=True)
            embed.add_field(name="🔑 Invite Code", value=f"`{team['invite_code']}`", inline=True)
            embed.add_field(name="👑 Owner", value=f"<@{team['owner_id']}>", inline=True)
            embed.add_field(name="👥 Members", value=f"{len(members)}", inline=True)
            embed.set_footer(text="Use /team-edit-commission or /team-new-inv for updates")
            await interaction.response.send_message(embed=embed, ephemeral=True)

    except Exception as e:
        print("❌ ERROR en /team-info:", e)
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Ocurrió un error interno al obtener la información del equipo.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ Ocurrió un error interno al obtener la información del equipo.",
                    ephemeral=True
                )
        except Exception as send_err:
            print("❌ Error enviando respuesta de error en /team-info:", send_err)


# ====================================================
#   /team-edit-commission
# ====================================================
@bot.tree.command(name="team-edit-commission", description="Edita la comisión de tu equipo")
@app_commands.describe(nueva_comision="Nuevo porcentaje de comisión")
async def team_edit_commission(interaction: discord.Interaction, nueva_comision: float):
    try:
        async with bot.db_pool.acquire() as conn:
            team = await conn.fetchrow(
                "SELECT * FROM teams WHERE owner_id = $1",
                interaction.user.id
            )
            if not team:
                await interaction.response.send_message(
                    "❌ No tenés ningún equipo creado.",
                    ephemeral=True
                )
                return

            await conn.execute(
                "UPDATE teams SET commission_rate = $1 WHERE owner_id = $2",
                nueva_comision, interaction.user.id
            )

            embed = discord.Embed(
                title="💼 Commission Updated",
                description=f"Your team's commission rate is now **{nueva_comision}%**.",
                color=0x00b0f0
            )
            embed.add_field(name="Team", value=team["team_name"], inline=True)
            embed.set_footer(text="Clipping Equipos • 2025")
            await interaction.response.send_message(embed=embed, ephemeral=True)

    except Exception as e:
        print("❌ ERROR en /team-edit-commission:", e)
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Ocurrió un error interno al actualizar la comisión.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ Ocurrió un error interno al actualizar la comisión.",
                    ephemeral=True
                )
        except Exception as send_err:
            print("❌ Error enviando respuesta de error en /team-edit-commission:", send_err)


# ====================================================
#   /team-new-inv
# ====================================================
@bot.tree.command(name="team-new-inv", description="Genera un nuevo código de invitación para tu equipo")
async def team_new_inv(interaction: discord.Interaction):
    try:
        async with bot.db_pool.acquire() as conn:
            team = await conn.fetchrow(
                "SELECT * FROM teams WHERE owner_id = $1",
                interaction.user.id
            )
            if not team:
                await interaction.response.send_message(
                    "❌ No tenés ningún equipo creado.",
                    ephemeral=True
                )
                return

            new_invite = str(uuid.uuid4())[:8]
            await conn.execute(
                "UPDATE teams SET invite_code = $1 WHERE id = $2",
                new_invite, team['id']
            )

            embed = discord.Embed(
                title="🔄 New Invite Code Generated",
                description="Your old invite has been replaced with a new one.",
                color=0xf1c40f
            )
            embed.add_field(name="Team", value=team['team_name'], inline=True)
            embed.add_field(name="New Code", value=f"`{new_invite}`", inline=True)
            embed.set_footer(text="Share this new code with your members.")
            await interaction.response.send_message(embed=embed, ephemeral=True)

    except Exception as e:
        print("❌ ERROR en /team-new-inv:", e)
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Ocurrió un error interno al generar el nuevo código.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ Ocurrió un error interno al generar el nuevo código.",
                    ephemeral=True
                )
        except Exception as send_err:
            print("❌ Error enviando respuesta de error en /team-new-inv:", send_err)


# ====================================================
#   RUN BOT (solo si se ejecuta este archivo directamente)
# ====================================================
if __name__ == "__main__":
    if not TOKEN:
        print("❌ ERROR: DISCORD_EQUIPOS_BOT_TOKEN no encontrado en las variables de entorno")
    else:
        bot.run(TOKEN)