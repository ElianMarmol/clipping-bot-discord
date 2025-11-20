import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

_pool = None

async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    return _pool

async def register_user(discord_id, username):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (discord_id, username)
            VALUES ($1, $2)
            ON CONFLICT (discord_id)
            DO UPDATE SET username = EXCLUDED.username;
        """, discord_id, username)

async def get_user(discord_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT * FROM users WHERE discord_id = $1;
        """, discord_id)
    return row
