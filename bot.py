import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import timedelta, datetime, timezone
import sqlite3
import re
import random
import os
import io
import json
import asyncio
import aiohttp
import time
import uuid
import logging
import traceback
from collections import defaultdict, deque
from typing import Optional, List

from google import genai
from pypdf import PdfReader
from docx import Document
from discord.utils import utcnow

# =========================
# 🔥 MUSIC SYSTEM IMPORTS
# =========================
import wavelink

# =========================
# LOGGING CONFIGURATION
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("HackerBot")

# =========================
# ENVIRONMENT CONFIGURATION
# =========================
TOKEN = os.getenv("TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://api.openai.com/v1")

# Lavalink configuration via environment variables
LAVALINK_HOST = os.getenv("LAVALINK_HOST")
LAVALINK_PORT = int(os.getenv("LAVALINK_PORT", "2333"))
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD")
LAVALINK_SECURE = os.getenv("LAVALINK_SECURE", "false").lower() == "true"

LAVALINK_URI = f"http://{LAVALINK_HOST}:{LAVALINK_PORT}"
if LAVALINK_SECURE:
    LAVALINK_URI = f"https://{LAVALINK_HOST}:{LAVALINK_PORT}"

# Gemini client
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
MODEL_NAME = "gemini-2.5-flash"

# =========================
# DISCORD BOT SETUP (FIXED)
# =========================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.presences = True
intents.voice_states = True


# 🔥 NEW BOT CLASS (THIS is the fix)
class MyBot(commands.Bot):
    async def setup_hook(self):
        lava_node = wavelink.Node(
            identifier="MAIN",
            uri=LAVALINK_URI,
            password=LAVALINK_PASSWORD
        )

        await wavelink.Pool.connect(nodes=[lava_node], client=self)
        await self.tree.sync()


# 🔥 BOT INSTANCE (REPLACES OLD ONE)
bot = MyBot(
    command_prefix="!",
    intents=intents,
    application_id=1502734801696854139
)

START_TIME = datetime.now()

# =========================
# ASYNC DATABASE WRAPPER
# =========================
class AsyncDatabase:
    """Thread-safe async database wrapper to prevent 'database is locked' errors."""
    
    def __init__(self, db_path: str = "moderation.db"):
        self.db_path = db_path
        self._lock = asyncio.Lock()
        self._conn = None
        self._loop = None
    
    async def connect(self):
        """Initialize the database connection."""
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._loop = asyncio.get_event_loop()
        await self._create_tables()
        logger.info("✅ Database connected (WAL mode)")
    
    async def _create_tables(self):
        """Create all required tables."""
        queries = [
            """CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, guild_id INTEGER,
                reason TEXT, moderator TEXT, timestamp TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS memory (
                user_id INTEGER PRIMARY KEY, user_name TEXT, bot_name TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER, user_id INTEGER,
                channel_id INTEGER, status TEXT DEFAULT 'open', created_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS giveaways (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER, channel_id INTEGER,
                prize TEXT, winner_count INTEGER,
                end_time TEXT, host_id INTEGER, message_id INTEGER
            )""",
            """CREATE TABLE IF NOT EXISTS reaction_roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER, channel_id INTEGER,
                message_id INTEGER, emoji TEXT, role_id INTEGER
            )""",
            """CREATE TABLE IF NOT EXISTS leveling (
                user_id INTEGER, guild_id INTEGER,
                xp INTEGER DEFAULT 0, level INTEGER DEFAULT 1,
                PRIMARY KEY (user_id, guild_id)
            )""",
            """CREATE TABLE IF NOT EXISTS economy (
                user_id INTEGER, guild_id INTEGER,
                balance INTEGER DEFAULT 0, bank INTEGER DEFAULT 0,
                daily_streak INTEGER DEFAULT 0, last_daily TEXT,
                PRIMARY KEY (user_id, guild_id)
            )""",
            """CREATE TABLE IF NOT EXISTS custom_commands (
                guild_id INTEGER, name TEXT, response TEXT,
                PRIMARY KEY (guild_id, name)
            )""",
            """CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER, name TEXT, url TEXT, added_by INTEGER
            )""",
            """CREATE TABLE IF NOT EXISTS birthdays (
                user_id INTEGER PRIMARY KEY, guild_id INTEGER,
                date TEXT, year INTEGER
            )""",
            """CREATE TABLE IF NOT EXISTS polls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER, channel_id INTEGER,
                question TEXT, options TEXT, votes TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER, user_id INTEGER, username TEXT,
                event_type TEXT, details TEXT, timestamp TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS daily_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER, date TEXT, summary TEXT,
                total_messages INTEGER, most_active_user_id INTEGER,
                top_topic TEXT, generated_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS message_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER, channel_id INTEGER, user_id INTEGER,
                date TEXT, count INTEGER DEFAULT 0, topics TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS ai_conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER, channel_id INTEGER, user_id INTEGER,
                role TEXT, content TEXT, timestamp TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS ai_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER, user_id INTEGER, key TEXT, value TEXT,
                importance INTEGER DEFAULT 1, created_at TEXT, last_accessed TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS ai_personality (
                guild_id INTEGER, user_id INTEGER,
                trait TEXT, value TEXT,
                PRIMARY KEY (guild_id, user_id, trait)
            )""",
            """CREATE TABLE IF NOT EXISTS music_favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, guild_id INTEGER,
                title TEXT, url TEXT, added_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS music_playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER, name TEXT, user_id INTEGER, created_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS music_playlist_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id INTEGER, title TEXT, url TEXT,
                position INTEGER, added_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS levels (
                user_id INTEGER, guild_id INTEGER,
                xp INTEGER DEFAULT 0, level INTEGER DEFAULT 1,
                total_messages INTEGER DEFAULT 0, last_message INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, guild_id)
            )""",
        ]
        for query in queries:
            self._conn.execute(query)
        self._conn.commit()
    
    async def execute(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a query with the async lock."""
        async with self._lock:
            return await self._loop.run_in_executor(None, lambda: self._conn.execute(query, params))
    
    async def commit(self):
        """Commit with the async lock."""
        async with self._lock:
            await self._loop.run_in_executor(None, self._conn.commit)
    
    async def fetchone(self, query: str, params: tuple = ()):
        """Fetch one row."""
        cursor = await self.execute(query, params)
        return cursor.fetchone()
    
    async def fetchall(self, query: str, params: tuple = ()):
        """Fetch all rows."""
        cursor = await self.execute(query, params)
        return cursor.fetchall()
    
    async def close(self):
        """Close the connection."""
        if self._conn:
            self._conn.close()

db = AsyncDatabase()

# =========================
# DATABASE HELPER FUNCTIONS (async)
# =========================

async def add_warning(user_id, guild_id, reason, moderator=None):
    await db.execute(
        "INSERT INTO warnings (user_id, guild_id, reason, moderator, timestamp) VALUES (?, ?, ?, ?, ?)",
        (user_id, guild_id, reason, moderator, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    await db.commit()

async def get_warnings(user_id, guild_id):
    return await db.fetchall(
        "SELECT id, reason, timestamp FROM warnings WHERE user_id=? AND guild_id=?",
        (user_id, guild_id)
    )

async def remove_warning(warning_id):
    await db.execute("DELETE FROM warnings WHERE id=?", (warning_id,))
    await db.commit()

async def add_history(guild_id, user_id, username, event_type, details):
    await db.execute(
        """INSERT INTO history (guild_id, user_id, username, event_type, details, timestamp)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (guild_id, user_id, username, event_type, details,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    await db.commit()

async def get_user_history(user_id, guild_id, limit=20):
    return await db.fetchall(
        """SELECT event_type, details, timestamp FROM history
           WHERE user_id=? AND guild_id=? ORDER BY id DESC LIMIT ?""",
        (user_id, guild_id, limit)
    )

async def get_guild_history_by_date(guild_id, date_str, limit=50):
    return await db.fetchall(
        """SELECT event_type, details, timestamp, username, user_id FROM history
           WHERE guild_id=? AND timestamp LIKE ? ORDER BY id DESC LIMIT ?""",
        (guild_id, f"{date_str}%", limit)
    )

async def get_guild_history_by_type(guild_id, event_type, limit=20):
    return await db.fetchall(
        """SELECT event_type, details, timestamp, username, user_id FROM history
           WHERE guild_id=? AND event_type=? ORDER BY id DESC LIMIT ?""",
        (guild_id, event_type, limit)
    )

async def get_history_search(guild_id, search_term, limit=20):
    return await db.fetchall(
        """SELECT event_type, details, timestamp, username, user_id FROM history
           WHERE guild_id=? AND (details LIKE ? OR username LIKE ?)
           ORDER BY id DESC LIMIT ?""",
        (guild_id, f"%{search_term}%", f"%{search_term}%", limit)
    )

async def log_message(guild_id, channel_id, user_id, content):
    today = datetime.now().strftime("%Y-%m-%d")
    await db.execute(
        """INSERT INTO message_stats (guild_id, channel_id, user_id, date, count, topics)
           VALUES (?, ?, ?, ?, 1, '')
           ON CONFLICT(guild_id, channel_id, user_id, date)
           DO UPDATE SET count = count + 1""",
        (guild_id, channel_id, user_id, today)
    )
    await db.commit()

async def get_channel_stats(guild_id, channel_id, days=7):
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return await db.fetchall(
        """SELECT date, SUM(count) as total FROM message_stats
           WHERE guild_id=? AND channel_id=? AND date BETWEEN ? AND ?
           GROUP BY date ORDER BY date""",
        (guild_id, channel_id, start_date, end_date)
    )

async def get_top_channels(guild_id, days=7):
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return await db.fetchall(
        """SELECT channel_id, SUM(count) as total FROM message_stats
           WHERE guild_id=? AND date BETWEEN ? AND ?
           GROUP BY channel_id ORDER BY total DESC LIMIT 5""",
        (guild_id, start_date, end_date)
    )

async def get_most_active_user(guild_id, days=7):
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return await db.fetchone(
        """SELECT user_id, SUM(count) as total FROM message_stats
           WHERE guild_id=? AND date BETWEEN ? AND ?
           GROUP BY user_id ORDER BY total DESC LIMIT 1""",
        (guild_id, start_date, end_date)
    )

async def get_busiest_day(guild_id):
    return await db.fetchone(
        """SELECT date, SUM(count) as total FROM message_stats
           WHERE guild_id=? GROUP BY date ORDER BY total DESC LIMIT 1""",
        (guild_id,)
    )

async def get_topic_for_date(guild_id, date_str, channel_id=None):
    if channel_id:
        return await db.fetchall(
            """SELECT details FROM history WHERE guild_id=? AND timestamp LIKE ?
               AND event_type IN ('MESSAGE', 'TOPIC') ORDER BY id DESC LIMIT 20""",
            (guild_id, f"{date_str}%")
        )
    else:
        return await db.fetchall(
            """SELECT details FROM history WHERE guild_id=? AND timestamp LIKE ?
               AND event_type IN ('MESSAGE', 'TOPIC') ORDER BY id DESC LIMIT 50""",
            (guild_id, f"{date_str}%")
        )

async def count_events_for_date(guild_id, date_str, event_type):
    result = await db.fetchone(
        "SELECT COUNT(*) FROM history WHERE guild_id=? AND timestamp LIKE ? AND event_type=?",
        (guild_id, f"{date_str}%", event_type)
    )
    return result[0] if result else 0

async def get_memory(user_id):
    return await db.fetchone("SELECT user_name, bot_name FROM memory WHERE user_id=?", (user_id,))

async def save_memory(user_id, user_name=None, bot_name=None):
    existing = await get_memory(user_id)
    if existing:
        user_name = user_name or existing[0]
        bot_name = bot_name or existing[1]
        await db.execute("UPDATE memory SET user_name=?, bot_name=? WHERE user_id=?", (user_name, bot_name, user_id))
    else:
        await db.execute("INSERT INTO memory (user_id, user_name, bot_name) VALUES (?, ?, ?)", (user_id, user_name, bot_name))
    await db.commit()

async def save_ai_memory(guild_id, user_id, key, value, importance=1):
    await db.execute(
        """INSERT INTO ai_memories (guild_id, user_id, key, value, importance, created_at, last_accessed)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(guild_id, user_id, key)
           DO UPDATE SET value=excluded.value, importance=excluded.importance, last_accessed=excluded.last_accessed""",
        (guild_id, user_id, key, value, importance, datetime.now().isoformat(), datetime.now().isoformat())
    )
    await db.commit()

async def get_ai_memories(guild_id, user_id, limit=20):
    memories = await db.fetchall(
        """SELECT key, value, importance, created_at, last_accessed FROM ai_memories
           WHERE guild_id=? AND user_id=? ORDER BY importance DESC, last_accessed DESC LIMIT ?""",
        (guild_id, user_id, limit)
    )
    await db.execute(
        "UPDATE ai_memories SET last_accessed=? WHERE guild_id=? AND user_id=?",
        (datetime.now().isoformat(), guild_id, user_id)
    )
    await db.commit()
    return memories

async def get_all_guild_memories(guild_id, limit=50):
    return await db.fetchall(
        """SELECT user_id, key, value, importance FROM ai_memories
           WHERE guild_id=? ORDER BY importance DESC, last_accessed DESC LIMIT ?""",
        (guild_id, limit)
    )

async def save_conversation(guild_id, channel_id, user_id, role, content):
    await db.execute(
        "INSERT INTO ai_conversations (guild_id, channel_id, user_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
        (guild_id, channel_id, user_id, role, content[:1000], datetime.now().isoformat())
    )
    await db.commit()

async def get_recent_conversation(guild_id, channel_id, user_id=None, limit=15):
    if user_id:
        results = await db.fetchall(
            """SELECT role, content, timestamp, user_id FROM ai_conversations
               WHERE guild_id=? AND channel_id=? AND user_id=? ORDER BY id DESC LIMIT ?""",
            (guild_id, channel_id, user_id, limit)
        )
    else:
        results = await db.fetchall(
            """SELECT role, content, timestamp, user_id FROM ai_conversations
               WHERE guild_id=? AND channel_id=? ORDER BY id DESC LIMIT ?""",
            (guild_id, channel_id, limit)
        )
    return list(reversed(results))

async def save_personality_trait(guild_id, user_id, trait, value):
    await db.execute(
        """INSERT INTO ai_personality (guild_id, user_id, trait, value)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(guild_id, user_id, trait)
           DO UPDATE SET value=excluded.value""",
        (guild_id, user_id, trait, value)
    )
    await db.commit()

async def get_user_personality(guild_id, user_id):
    return await db.fetchall(
        "SELECT trait, value FROM ai_personality WHERE guild_id=? AND user_id=?",
        (guild_id, user_id)
    )

async def get_balance_db(user_id, guild_id):
    result = await db.fetchone(
        "SELECT balance, bank FROM economy WHERE user_id=? AND guild_id=?",
        (user_id, guild_id)
    )
    if result:
        return {'wallet': result[0], 'bank': result[1]}
    await db.execute(
        "INSERT INTO economy (user_id, guild_id, balance, bank) VALUES (?, ?, 0, 0)",
        (user_id, guild_id)
    )
    await db.commit()
    return {'wallet': 0, 'bank': 0}

async def update_balance_db(user_id, guild_id, amount, account='wallet'):
    await db.execute(
        f"UPDATE economy SET {account} = {account} + ? WHERE user_id=? AND guild_id=?",
        (amount, user_id, guild_id)
    )
    await db.commit()

async def get_balance_simple(user_id):
    result = await db.fetchone(
        "SELECT wallet, bank FROM economy WHERE user_id=?",
        (user_id,)
    )
    if result:
        return result
    await db.execute(
        "INSERT INTO economy (user_id, wallet, bank) VALUES (?, 0, 0)",
        (user_id,)
    )
    await db.commit()
    return (0, 0)

async def add_xp_db(user_id, guild_id, xp_gain, now_ts):
    result = await db.fetchone(
        "SELECT xp, level, total_messages, last_message FROM levels WHERE user_id=? AND guild_id=?",
        (user_id, guild_id)
    )
    if result:
        xp, level, total_messages, last_message = result
        if now_ts - last_message < 60:
            return None
        xp += xp_gain
        total_messages += 1
        needed = 100 + (level * 50)
        if xp >= needed:
            level += 1
            xp = 0
            await db.execute(
                "UPDATE levels SET xp=?, level=?, total_messages=?, last_message=? WHERE user_id=? AND guild_id=?",
                (xp, level, total_messages, now_ts, user_id, guild_id)
            )
            await db.commit()
            return level
        else:
            await db.execute(
                "UPDATE levels SET xp=?, total_messages=?, last_message=? WHERE user_id=? AND guild_id=?",
                (xp, total_messages, now_ts, user_id, guild_id)
            )
            await db.commit()
            return None
    else:
        await db.execute(
            "INSERT INTO levels (user_id, guild_id, xp, level, total_messages, last_message) VALUES (?, ?, ?, 1, 1, ?)",
            (user_id, guild_id, xp_gain, now_ts)
        )
        await db.commit()
        return None

# =========================
# LOG FUNCTION
# =========================
async def log_to_channel(guild, embed_or_text):
    channel = discord.utils.get(guild.text_channels, name="mod-logs")
    if channel:
        if isinstance(embed_or_text, discord.Embed):
            await channel.send(embed=embed_or_text)
        else:
            await channel.send(f"📜 {embed_or_text}")

async def log(guild, text):
    await log_to_channel(guild, text)

# =========================
# AI CONTEXT BUILDING
# =========================
async def build_ai_context(guild_id, channel_id, user_id, username, message_content):
    mem = await get_memory(user_id)
    user_name = mem[0] if mem else username
    bot_name = mem[1] if mem else "AI Bot"
    
    ai_memories = await get_ai_memories(guild_id, user_id)
    traits = await get_user_personality(guild_id, user_id)
    recent_msgs = await get_recent_conversation(guild_id, channel_id, limit=10)
    user_events = await get_user_history(user_id, guild_id, limit=5)
    
    result = await db.fetchone(
        "SELECT level FROM leveling WHERE user_id=? AND guild_id=?",
        (user_id, guild_id)
    )
    level = result[0] if result else 0
    
    bal = await get_balance_db(user_id, guild_id)
    
    await extract_memory_facts(guild_id, user_id, message_content)
    
    context_parts = []
    context_parts.append(f"User's name: {user_name}")
    context_parts.append(f"Bot's name: {bot_name}")
    guild_obj = bot.get_guild(guild_id)
    context_parts.append(f"Server: {guild_obj.name if guild_obj else 'Unknown'}")
    context_parts.append(f"User's Level: {level}")
    context_parts.append(f"User's Wallet Balance: ${bal['wallet']:,}")
    context_parts.append(f"User's Bank Balance: ${bal['bank']:,}")
    
    if traits:
        trait_str = " | ".join([f"{t}: {v}" for t, v in traits])
        context_parts.append(f"Known traits about user: {trait_str}")
    
    if ai_memories:
        memory_str = "; ".join([f"{k}: {v}" for k, v, imp, _, _ in ai_memories if imp >= 2])
        if memory_str:
            context_parts.append(f"Things I remember about this user: {memory_str}")
    
    if recent_msgs:
        conv_lines = []
        for role, content, ts, uid in recent_msgs:
            name = "User" if role == "user" else bot_name
            conv_lines.append(f"{name}: {content[:200]}")
        context_parts.append(f"Recent conversation:\n" + "\n".join(conv_lines))
    
    if user_events:
        event_lines = [f"- {e}: {d[:80]}" for e, d, _ in user_events[:3]]
        context_parts.append(f"Recent user activity: " + " | ".join(event_lines))
    
    return "\n".join(context_parts)

async def extract_memory_facts(guild_id, user_id, message):
    msg_lower = message.lower()
    
    name_patterns = [
        (r"my name is (\w+)", "preferred_name"),
        (r"call me (\w+)", "preferred_name"),
        (r"i'm (\w+)", "preferred_name"),
        (r"i am (\w+)", "preferred_name"),
    ]
    for pattern, key in name_patterns:
        m = re.search(pattern, msg_lower)
        if m and m.group(1).lower() not in ("a", "the", "an", "just", "not", "going", "trying"):
            await save_ai_memory(guild_id, user_id, key, m.group(1).title(), importance=5)
            break
    
    age_m = re.search(r"i(?:')?m (\d+) (?:years old|yr old|yo)", msg_lower)
    if age_m:
        await save_ai_memory(guild_id, user_id, "age", age_m.group(1), importance=4)
    
    loc_m = re.search(r"i(?:')?m (?:from|in) (\w+(?:\s+\w+)?)", msg_lower)
    if loc_m:
        await save_ai_memory(guild_id, user_id, "location", loc_m.group(1).title(), importance=3)
    
    hobby_patterns = [
        (r"i (?:like|love|enjoy) (\w+(?: \w+)?)", "hobby"),
        (r"my (?:hobby|favorite) (?:is|are) (\w+(?: \w+)?)", "hobby"),
        (r"i (?:play|code|draw|write|read|game|stream) (\w+(?: \w+)?)", "interest"),
    ]
    for pattern, key in hobby_patterns:
        m = re.search(pattern, msg_lower)
        if m:
            await save_ai_memory(guild_id, user_id, key, m.group(1).title(), importance=2)
    
    mood_m = re.search(r"i(?:')?m (?:feeling|so|very|really) (\w+)", msg_lower)
    if mood_m:
        await save_ai_memory(guild_id, user_id, "current_mood", mood_m.group(1), importance=1)
        await save_personality_trait(guild_id, user_id, "recent_mood", mood_m.group(1))
    
    pref_patterns = [
        (r"i (?:don't|do not) like (\w+(?: \w+)?)", "dislikes"),
        (r"i love (\w+(?: \w+)?)", "likes"),
    ]
    for pattern, default_key in pref_patterns:
        m = re.search(pattern, msg_lower)
        if m:
            await save_ai_memory(guild_id, user_id, default_key, m.group(1).title(), importance=2)
    
    work_m = re.search(r"i (?:work|study) (?:at|in|as) (\w+(?: \w+)?)", msg_lower)
    if work_m:
        await save_ai_memory(guild_id, user_id, "occupation", work_m.group(1).title(), importance=3)

_ai_response_cache = {}
_ai_rate_limit = defaultdict(float)

async def get_ai_response(prompt, temperature=0.7, max_retries=2):
    if not client:
        return "⚠️ AI is not configured (missing GEMINI_API_KEY)."
    
    cache_key = hash(prompt[:500])
    
    if cache_key in _ai_response_cache:
        cached_time, cached_response = _ai_response_cache[cache_key]
        if (datetime.now() - cached_time).seconds < 5:
            return cached_response
    
    last_error = None
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config={"temperature": temperature, "max_output_tokens": 500}
            )
            reply = response.text
            
            _ai_response_cache[cache_key] = (datetime.now(), reply)
            
            if len(_ai_response_cache) > 100:
                oldest_key = min(_ai_response_cache.keys(), key=lambda k: _ai_response_cache[k][0])
                del _ai_response_cache[oldest_key]
            
            return reply
            
        except Exception as e:
            last_error = e
            logger.error(f"AI response attempt {attempt+1} failed: {e}")
            await asyncio.sleep(0.5 * (attempt + 1))
    
    return f"⚠️ AI Error: {last_error}"

async def summarize_conversation(conversation_text):
    if len(conversation_text) < 500:
        return conversation_text
    
    if not client:
        return conversation_text[-500:]
    
    prompt = f"""Summarize this conversation concisely, keeping key facts, preferences, and topics discussed:

{conversation_text}

Summary:"""
    
    try:
        response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
        return response.text[:500]
    except Exception as e:
        logger.error(f"Summarize error: {e}")
        return conversation_text[-500:]

# =========================
# XP SYSTEM
# =========================
XP_COOLDOWN = {}

async def add_xp(user_id, guild_id):
    if guild_id is None:
        return None
    key = f"{user_id}-{guild_id}"
    now = datetime.now()
    if key in XP_COOLDOWN:
        if (now - XP_COOLDOWN[key]).seconds < 60:
            return None
    XP_COOLDOWN[key] = now
    
    xp_gain = random.randint(15, 25)
    
    result = await db.fetchone(
        "SELECT xp, level FROM leveling WHERE user_id=? AND guild_id=?",
        (user_id, guild_id)
    )
    
    if result:
        xp, level = result
        xp += xp_gain
        xp_needed = level * 100
        if xp >= xp_needed:
            level += 1
            xp = 0
            await db.execute(
                "UPDATE leveling SET xp=?, level=? WHERE user_id=? AND guild_id=?",
                (xp, level, user_id, guild_id)
            )
            await db.commit()
            return level
        else:
            await db.execute(
                "UPDATE leveling SET xp=? WHERE user_id=? AND guild_id=?",
                (xp, user_id, guild_id)
            )
    else:
        await db.execute(
            "INSERT INTO leveling (user_id, guild_id, xp, level) VALUES (?, ?, ?, 1)",
            (user_id, guild_id, xp_gain)
        )
    
    await db.commit()
    return None

async def get_balance(user_id, guild_id):
    result = await db.fetchone(
        "SELECT balance, bank FROM economy WHERE user_id=? AND guild_id=?",
        (user_id, guild_id)
    )
    if result:
        return {'wallet': result[0], 'bank': result[1]}
    await db.execute(
        "INSERT INTO economy (user_id, guild_id, balance, bank) VALUES (?, ?, 0, 0)",
        (user_id, guild_id)
    )
    await db.commit()
    return {'wallet': 0, 'bank': 0}

async def update_balance(user_id, guild_id, amount, account='wallet'):
    await db.execute(
        f"UPDATE economy SET {account} = {account} + ? WHERE user_id=? AND guild_id=?",
        (amount, user_id, guild_id)
    )
    await db.commit()

# =========================
# OWNER CHECK
# =========================
async def owner_check(interaction: discord.Interaction):
    return interaction.user.id == OWNER_ID

def is_owner():
    async def predicate(interaction: discord.Interaction):
        return interaction.user.id == OWNER_ID
    return app_commands.check(predicate)

# =========================
# HELPER FUNCTIONS
# =========================
async def query_ai(prompt: str) -> str:
    """Query the AI API for a response."""
    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {AI_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "gpt-3.5-turbo",
                "messages": [
                    {"role": "system", "content": "You are a helpful Discord bot assistant. Keep responses concise and friendly."},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 500,
                "temperature": 0.7
            }
            
            async with session.post(f"{AI_BASE_URL}/chat/completions", headers=headers, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"].strip()
                else:
                    error_text = await resp.text()
                    logger.error(f"AI API error: {resp.status} - {error_text}")
                    return f"AI service returned status {resp.status}"
    except Exception as e:
        logger.error(f"AI query error: {e}")
        return f"AI error: {str(e)}"

def format_duration(ms: int) -> str:
    """Format milliseconds to a time string."""
    if ms <= 0:
        return "Live"
    seconds = ms // 1000
    minutes, secs = divmod(seconds, 60)
    hours, mins = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"

# =========================
# DM MEMORY
# =========================
dm_memory = {}

BAD_WORDS = ["badword1", "badword2"]
INVITE_REGEX = r"(discord\.gg/|discordapp\.com/invite/)"

# =========================
# WELCOME CONFIG
# =========================
welcome_configs = {}

# =========================
# GIVEAWAY TRACKING (in-memory)
# =========================
active_giveaways = {}  # guild_id -> list of giveaway dicts

# =========================
# MUSIC SYSTEM (WAVELINK ONLY - no old MusicPlayer)
# =========================
URL_REGEX = re.compile(r"https?://(?:www\.)?(?:youtube\.com|youtu\.be|soundcloud\.com|spotify\.com)/\S+")

# =========================
# BACKGROUND TASKS
# =========================
@tasks.loop(minutes=1)
async def check_giveaways():
    """Check and end expired giveaways."""
    now_ts = time.time()
    
    for guild_id, giveaways in list(active_giveaways.items()):
        for g in giveaways[:]:
            if now_ts >= g.get("end_time", 0) and not g.get("ended"):
                g["ended"] = True
                try:
                    await end_giveaway(guild_id, g)
                except Exception as e:
                    logger.error(f"Error ending giveaway in guild {guild_id}: {e}")

async def end_giveaway(guild_id: int, g: dict):
    """End a giveaway and announce winners."""
    guild = bot.get_guild(guild_id)
    if not guild:
        return
    
    channel = guild.get_channel(g["channel_id"])
    if not channel:
        return
    
    try:
        msg = await channel.fetch_message(g["message_id"])
    except Exception as e:
        logger.error(f"Could not fetch giveaway message {g['message_id']}: {e}")
        return
    
    entries = g.get("entries", [])
    
    if not entries:
        try:
            embed = msg.embeds[0]
            embed.title = "🎉 Giveaway Ended - No Winners"
            embed.color = discord.Color.red()
            await msg.edit(embed=embed, view=None)
        except Exception as e:
            logger.error(f"Error updating giveaway message: {e}")
        return
    
    winners_count = min(g["winners"], len(entries))
    winners = random.sample(entries, winners_count)
    
    try:
        embed = msg.embeds[0]
        embed.title = "🎉 Giveaway Ended"
        embed.color = discord.Color.red()
        embed.clear_fields()
        embed.add_field(name="Winner(s)", value=", ".join(f"<@{w}>" for w in winners), inline=False)
        embed.add_field(name="Prize", value=g["prize"], inline=False)
        await msg.edit(embed=embed, view=None)
        
        await channel.send(f"🎉 Congratulations {' '.join(f'<@{w}>' for w in winners)}! You won **{g['prize']}**!")
        
        for winner_id in winners:
            try:
                winner = guild.get_member(winner_id)
                if winner:
                    await add_history(guild_id, winner_id, str(winner), "GIVEAWAY_WIN", f"Won {g['prize']}")
            except Exception as e:
                logger.error(f"Error logging giveaway win for {winner_id}: {e}")
    except Exception as e:
        logger.error(f"Error during giveaway conclusion: {e}")
    
    # Remove from active list
    for gid, giveaways in active_giveaways.items():
        if g in giveaways:
            giveaways.remove(g)

@tasks.loop(minutes=5)
async def check_birthdays():
    """Check for birthdays and announce them."""
    today = datetime.now().strftime("%m-%d")
    results = await db.fetchall(
        "SELECT user_id, guild_id FROM birthdays WHERE date=?",
        (today,)
    )
    for user_id, guild_id in results:
        guild = bot.get_guild(guild_id)
        if guild:
            channel = discord.utils.get(guild.text_channels, name="general")
            if channel:
                try:
                    await channel.send(f"🎂 Happy Birthday <@{user_id}>! 🎉")
                    await add_history(guild_id, user_id, str(user_id), "BIRTHDAY", "Birthday announced")
                except Exception as e:
                    logger.error(f"Birthday announcement error: {e}")

@tasks.loop(hours=24)
async def generate_daily_summary():
    """Generate and post daily server summary."""
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    for guild in bot.guilds:
        try:
            busiest = await get_busiest_day(guild.id)
            most_active = await get_most_active_user(guild.id)
            
            joins = await count_events_for_date(guild.id, yesterday, "JOIN")
            leaves = await count_events_for_date(guild.id, yesterday, "LEAVE")
            warns = await count_events_for_date(guild.id, yesterday, "WARN")
            deletes = await count_events_for_date(guild.id, yesterday, "DELETE")
            kicks = await count_events_for_date(guild.id, yesterday, "KICK")
            bans = await count_events_for_date(guild.id, yesterday, "BAN")
            
            result = await db.fetchone(
                "SELECT SUM(count) FROM message_stats WHERE guild_id=? AND date=?",
                (guild.id, yesterday)
            )
            total_msgs = result[0] or 0
            
            most_active_name = "No one"
            if most_active:
                user = guild.get_member(most_active[0])
                if user:
                    most_active_name = user.display_name
            
            summary = (
                f"📊 **Daily Summary - {yesterday}**\n"
                f"📝 Total Messages: {total_msgs:,}\n"
                f"👋 Joins: {joins} | 👋 Leaves: {leaves}\n"
                f"⚠️ Warnings: {warns} | 🗑️ Deleted: {deletes}\n"
                f"👢 Kicks: {kicks} | 🔨 Bans: {bans}\n"
                f"🏆 Most Active: {most_active_name}"
            )
            
            await db.execute(
                """INSERT INTO daily_summaries (guild_id, date, summary, total_messages, most_active_user_id, top_topic, generated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (guild.id, yesterday, summary, total_msgs, most_active[0] if most_active else 0, "", datetime.now().isoformat())
            )
            await db.commit()
            
            channel = discord.utils.get(guild.text_channels, name="mod-logs")
            if channel and total_msgs > 0:
                await channel.send(summary)
        except Exception as e:
            logger.error(f"Error generating daily summary for {guild.id}: {e}")

@tasks.loop(hours=6)
async def consolidate_memories():
    """Clean up old, low-importance memories."""
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    await db.execute(
        "DELETE FROM ai_memories WHERE importance <= 1 AND last_accessed < ?",
        (cutoff,)
    )
    deleted = db._conn.total_changes if db._conn else 0
    await db.commit()
    if deleted > 0:
        logger.info(f"🧹 Consolidated old memories")

@tasks.loop(minutes=5)
async def clean_inactive_players():
    """Disconnect from empty voice channels."""
    for voice_client in bot.voice_clients:
        try:
            if isinstance(voice_client, wavelink.Player):
                channel = voice_client.channel
                if channel and len(channel.members) == 1 and channel.members[0].id == bot.user.id:
                    if not voice_client.playing and not voice_client.paused:
                        await voice_client.disconnect()
                        logger.info(f"Disconnected from empty voice channel in {voice_client.guild.id}")
        except Exception as e:
            logger.error(f"Clean inactive players error: {e}")

# =========================
# MUSIC CONTROLS VIEW
# =========================
class MusicControls(discord.ui.View):
    """Interactive music controls view."""
    def __init__(self, music_cog, track):
        super().__init__(timeout=600)
        self.music_cog = music_cog
        self.track = track
    
    @discord.ui.button(label="⏸️", style=discord.ButtonStyle.secondary)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = interaction.guild.voice_client
        if not player or not isinstance(player, wavelink.Player):
            return await interaction.response.send_message("Not connected.", ephemeral=True)
        if player.paused:
            await player.resume()
            button.label = "⏸️"
        else:
            await player.pause()
            button.label = "▶️"
        await interaction.response.edit_message(view=self)
    
    @discord.ui.button(label="⏭️", style=discord.ButtonStyle.secondary)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = interaction.guild.voice_client
        if not player or not isinstance(player, wavelink.Player):
            return await interaction.response.send_message("Not connected.", ephemeral=True)
        await player.stop()
        await interaction.response.send_message("⏭️ Skipped", ephemeral=True)
    
    @discord.ui.button(label="🔀", style=discord.ButtonStyle.secondary)
    async def shuffle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.music_cog.shuffle_queue(interaction.guild.id)
        await interaction.response.send_message("🔀 Queue shuffled!", ephemeral=True)
    
    @discord.ui.button(label="⏹️", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = interaction.guild.voice_client
        if not player:
            return await interaction.response.send_message("Not connected.", ephemeral=True)
        player.queue.clear()
        await player.stop()
        await player.disconnect()
        await interaction.response.send_message("⏹️ Stopped and disconnected.", ephemeral=True)

# =========================
# SEARCH SELECT VIEW
# =========================
class SearchSelect(discord.ui.View):
    """Dropdown view for selecting a track from search results."""
    def __init__(self, tracks, music_cog):
        super().__init__(timeout=30)
        self.tracks = tracks
        self.music_cog = music_cog
        
        options = [
            discord.SelectOption(label=f"{t.title[:50]}", value=str(i), description=t.author[:50])
            for i, t in enumerate(tracks)
        ]
        
        select = discord.ui.Select(placeholder="Choose a track...", options=options)
        
        async def select_callback(interaction: discord.Interaction):
            await interaction.response.defer()
            idx = int(select.values[0])
            track = self.tracks[idx]
            
            if not interaction.user.voice or not interaction.user.voice.channel:
                return await interaction.followup.send("❌ You must be in a voice channel.", ephemeral=True)
            
            voice_channel = interaction.user.voice.channel
            guild = interaction.guild
            
            player = guild.voice_client
            if player and isinstance(player, wavelink.Player):
                if player.channel != voice_channel:
                    await player.move_to(voice_channel)
            else:
                if player:
                    await player.disconnect()
                player = await voice_channel.connect(cls=wavelink.Player)
            
            queue = self.music_cog.get_queue(guild.id)
            
            if not player.playing:
                self.music_cog.current[guild.id] = track
                await player.play(track)
                embed = discord.Embed(title="▶️ Now Playing", color=discord.Color.green())
                embed.add_field(name="Title", value=track.title, inline=False)
                embed.add_field(name="Artist", value=track.author, inline=True)
                embed.add_field(name="Duration", value=format_duration(track.length), inline=True)
                if track.artwork:
                    embed.set_thumbnail(url=track.artwork)
                view = MusicControls(self.music_cog, track)
                await interaction.followup.send(embed=embed, view=view)
            else:
                queue.append(track)
                await interaction.followup.send(f"✅ Added **{track.title}** to the queue (position #{len(queue)})")
            
            try:
                await interaction.message.delete()
            except:
                pass
        
        select.callback = select_callback
        self.add_item(select)

# =========================
# TICKET VIEWS
# =========================
class TicketCloseView(discord.ui.View):
    """View with close button for tickets."""
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.channel.name.startswith("ticket-"):
            return await interaction.response.send_message("❌ This is not a ticket channel.", ephemeral=True)
        
        embed = discord.Embed(
            title="🔒 Closing Ticket",
            description="This ticket will be closed in 5 seconds...",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete()
        except Exception as e:
            logger.error(f"Error deleting ticket channel: {e}")

class TicketPanelView(discord.ui.View):
    """View with create ticket button."""
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="🎫 Create Ticket", style=discord.ButtonStyle.primary, custom_id="create_ticket")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        user = interaction.user
        
        # Check if user already has an open ticket
        existing = discord.utils.get(guild.text_channels, name=f"ticket-{user.name.lower().replace(' ', '-')}")
        if existing:
            return await interaction.response.send_message("❌ You already have an open ticket!", ephemeral=True)
        
        # Create ticket channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        }
        
        admin_role = discord.utils.get(guild.roles, name="Admin")
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        
        mod_role = discord.utils.get(guild.roles, name="Moderator")
        if mod_role:
            overwrites[mod_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        
        try:
            channel = await guild.create_text_channel(
                name=f"ticket-{user.name.lower().replace(' ', '-')}",
                topic=f"Support ticket for {user.name} ({user.id})",
                overwrites=overwrites
            )
            
            embed = discord.Embed(
                title="🎫 Ticket Created",
                description=f"Hello {user.mention}! Support will be with you shortly.\n\nType your issue below.",
                color=discord.Color.green()
            )
            
            close_view = TicketCloseView()
            await channel.send(embed=embed, view=close_view)
            await interaction.response.send_message(f"✅ Ticket created: {channel.mention}", ephemeral=True)
            
            await db.execute(
                "INSERT INTO tickets (guild_id, user_id, channel_id, status, created_at) VALUES (?, ?, ?, 'open', ?)",
                (guild.id, user.id, channel.id, datetime.now().isoformat())
            )
            await db.commit()
            
        except Exception as e:
            logger.error(f"Error creating ticket: {e}")
            await interaction.response.send_message("❌ Failed to create ticket. Check my permissions.", ephemeral=True)

# =========================
# GIVEAWAY VIEWS (UNIFIED)
# =========================
class GiveawayView(discord.ui.View):
    """View for entering a giveaway."""
    def __init__(self, giveaway_id: str):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id

    @discord.ui.button(label="🎉 Enter Giveaway", style=discord.ButtonStyle.primary, custom_id="giveaway_enter")
    async def enter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for guild_id, giveaways in active_giveaways.items():
            for g in giveaways:
                if g["id"] == self.giveaway_id:
                    if g.get("ended"):
                        return await interaction.response.send_message("❌ This giveaway has ended.", ephemeral=True)
                    
                    if interaction.user.id in g["entries"]:
                        return await interaction.response.send_message("⚠️ You already entered!", ephemeral=True)

                    g["entries"].append(interaction.user.id)

                    try:
                        embed = interaction.message.embeds[0]
                        # Update the entries field (usually field index 0 or 1)
                        for i, field in enumerate(embed.fields):
                            if field.name == "Entries":
                                embed.set_field_at(i, name="Entries", value=str(len(g["entries"])), inline=True)
                                break
                        await interaction.message.edit(embed=embed)
                    except Exception as e:
                        logger.error(f"Error updating giveaway embed: {e}")

                    return await interaction.response.send_message("✅ You entered the giveaway!", ephemeral=True)

        await interaction.response.send_message("❌ This giveaway has ended.", ephemeral=True)


# =========================
# POLL VIEW
# =========================
class PollView(discord.ui.View):
    def __init__(self, poll_id, options):
        super().__init__(timeout=None)
        self.poll_id = poll_id
        self.votes = {i: [] for i in range(len(options))}
        
        for i, option in enumerate(options):
            button = discord.ui.Button(
                label=f"{self._get_emoji(i)} {option}",
                style=discord.ButtonStyle.secondary,
                custom_id=f"poll_vote_{poll_id}_{i}"
            )
            button.callback = self.make_callback(i)
            self.add_item(button)
    
    def _get_emoji(self, index):
        emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        return emojis[index] if index < len(emojis) else f"{index+1}."
    
    def make_callback(self, option_index):
        async def callback(interaction: discord.Interaction):
            user_id = interaction.user.id
            
            for idx in self.votes:
                if user_id in self.votes[idx]:
                    self.votes[idx].remove(user_id)
            
            self.votes[option_index].append(user_id)
            
            result = await db.fetchone("SELECT votes FROM polls WHERE id=?", (self.poll_id,))
            if result:
                votes_data = json.loads(result[0])
                for idx in votes_data:
                    if user_id in votes_data[idx]:
                        votes_data[idx].remove(user_id)
                votes_data[option_index].append(user_id)
                await db.execute("UPDATE polls SET votes=? WHERE id=?", (json.dumps(votes_data), self.poll_id))
                await db.commit()
            
            await interaction.response.send_message(f"✅ You voted for option {option_index + 1}!", ephemeral=True)
        
        return callback

# =========================
# setup_hook - FIXED: Moved bot.tree.sync() here to prevent MissingApplicationID
# =========================
@bot.event
async def setup_hook():
    await bot.tree.sync()
    logger.info("✅ Slash commands synced in setup_hook")

# =========================
# BOT EVENTS
# =========================
# REMOVED: Duplicate global on_wavelink_node_ready - already in WavelinkEvents cog

@bot.event
async def on_member_join(member):
    try:
        await add_history(member.guild.id, member.id, str(member), "JOIN", "Joined the server")
        
        config = welcome_configs.get(member.guild.id)
        if config:
            channel = member.guild.get_channel(config.get('channel_id'))
            if channel:
                msg = config.get('message', "Welcome {user} to {server}!").format(
                    user=member.mention, server=member.guild.name
                )
                await channel.send(msg)
        
        results = await db.fetchall(
            "SELECT role_id FROM reaction_roles WHERE guild_id=? AND emoji='AUTO_ROLE'",
            (member.guild.id,)
        )
        for (role_id,) in results:
            role = member.guild.get_role(role_id)
            if role:
                await member.add_roles(role)
                await add_history(member.guild.id, member.id, str(member), "ROLE_ADD", f"Auto-assigned role: {role.name}")
    except Exception as e:
        logger.error(f"on_member_join error: {e}")

@bot.event
async def on_member_remove(member):
    try:
        await add_history(member.guild.id, member.id, str(member), "LEAVE", "Left the server")
        await log(member.guild, f"👋 {member} left the server")
    except Exception as e:
        logger.error(f"on_member_remove error: {e}")

@bot.event
async def on_message_delete(message):
    if message.author.bot:
        return
    try:
        await add_history(
            message.guild.id,
            message.author.id,
            str(message.author),
            "DELETE",
            message.content[:500] if message.content else "[Attachment/Embed]"
        )
    except Exception as e:
        logger.error(f"on_message_delete error: {e}")

@bot.event
async def on_message_edit(before, after):
    if before.author.bot:
        return
    if before.content == after.content:
        return
    try:
        await add_history(
            before.guild.id,
            before.author.id,
            str(before.author),
            "EDIT",
            f"{before.content[:200]} -> {after.content[:200]}"
        )
    except Exception as e:
        logger.error(f"on_message_edit error: {e}")

@bot.event
async def on_guild_channel_create(channel):
    try:
        await add_history(
            channel.guild.id, 0, "System", "CHANNEL_CREATE",
            f"Channel created: #{channel.name} ({channel.type})"
        )
    except Exception as e:
        logger.error(f"on_guild_channel_create error: {e}")

@bot.event
async def on_guild_channel_delete(channel):
    try:
        await add_history(
            channel.guild.id, 0, "System", "CHANNEL_DELETE",
            f"Channel deleted: #{channel.name}"
        )
    except Exception as e:
        logger.error(f"on_guild_channel_delete error: {e}")

@bot.event
async def on_voice_state_update(member, before, after):
    try:
        if before.channel != after.channel:
            if after.channel:
                await add_history(member.guild.id, member.id, str(member), "VOICE_JOIN", f"Joined {after.channel.name}")
                await log(member.guild, f"🔊 {member} joined {after.channel.name}")
            elif before.channel:
                await add_history(member.guild.id, member.id, str(member), "VOICE_LEAVE", f"Left {before.channel.name}")
                await log(member.guild, f"🔇 {member} left {before.channel.name}")
    except Exception as e:
        logger.error(f"on_voice_state_update error: {e}")

@bot.event
async def on_member_update(before, after):
    try:
        if before.nick != after.nick:
            await add_history(
                after.guild.id, after.id, str(after), "NICK_CHANGE",
                f"{before.nick or before.name} -> {after.nick or after.name}"
            )
        
        added_roles = set(after.roles) - set(before.roles)
        removed_roles = set(before.roles) - set(after.roles)
        
        for role in added_roles:
            if role.name != "@everyone":
                await add_history(after.guild.id, after.id, str(after), "ROLE_ADD", f"Role added: {role.name}")
        
        for role in removed_roles:
            if role.name != "@everyone":
                await add_history(after.guild.id, after.id, str(after), "ROLE_REMOVE", f"Role removed: {role.name}")
    except Exception as e:
        logger.error(f"on_member_update error: {e}")

# =========================
# MAIN on_message (UNIFIED - only one listener)
# =========================
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    try:
        if message.guild:
            await log_message(message.guild.id, message.channel.id, message.author.id, message.content)
        
        # =========================
        # DM AI
        # =========================
        if isinstance(message.channel, discord.DMChannel):
            if message.author.id != OWNER_ID:
                await message.channel.send("👋 Only owner can use AI.")
                return
            
            user_id = message.author.id
            key = str(user_id)
            
            if key not in dm_memory:
                dm_memory[key] = ""
            
            mem = await get_memory(user_id)
            if not mem:
                await save_memory(user_id, user_name=message.author.name, bot_name="AI Bot")
            
            dm_memory[key] += f"User: {message.content}\n"
            
            await extract_memory_facts(0, user_id, message.content)
            
            msg = message.content.lower()
            m1 = re.search(r"my name is (.+)", msg)
            if m1:
                await save_memory(user_id, user_name=m1.group(1).strip().title())
            
            m2 = re.search(r"your name is (.+)", msg)
            if m2:
                await save_memory(user_id, bot_name=m2.group(1).strip().title())
            
            mem = await get_memory(user_id)
            user_name, bot_name = mem if mem else (None, None)
            
            ai_mems = await get_ai_memories(0, user_id, limit=15)
            memory_context = ""
            if ai_mems:
                facts = [f"- {k}: {v}" for k, v, imp, _, _ in ai_mems if imp >= 2]
                if facts:
                    memory_context = "Things I remember about you:\n" + "\n".join(facts) + "\n"
            
            prompt = f"""
You are a Discord AI bot. You have persistent memory and learn about the user over time.

User name: {user_name or "unknown"}
Bot name: {bot_name or "AI Bot"}

{memory_context}

Recent conversation:
{dm_memory[key][-2000:]}

Respond naturally and conversationally. If the user mentions something new about themselves, remember it for next time.
"""
            
            try:
                if message.attachments:
                    attachment = message.attachments[0]
                    
                    if attachment.content_type and attachment.content_type.startswith("image/"):
                        if client:
                            img = await attachment.read()
                            uploaded = client.files.upload(file=img, config={"mime_type": attachment.content_type})
                            response = client.models.generate_content(model=MODEL_NAME, contents=[prompt, uploaded])
                            reply = response.text
                        else:
                            reply = "⚠️ AI not configured for image analysis."
                    
                    elif attachment.filename.endswith(".pdf"):
                        pdf_data = await attachment.read()
                        pdf = PdfReader(io.BytesIO(pdf_data))
                        text = ""
                        for page in pdf.pages:
                            t = page.extract_text()
                            if t:
                                text += t + "\n"
                        reply = await get_ai_response(f"{prompt}\nPDF:\n{text}")
                    
                    elif attachment.filename.endswith(".docx"):
                        doc_data = await attachment.read()
                        doc = Document(io.BytesIO(doc_data))
                        text = "\n".join(p.text for p in doc.paragraphs)
                        reply = await get_ai_response(f"{prompt}\nDOCX:\n{text}")
                    
                    elif attachment.filename.endswith(".txt"):
                        txt = await attachment.read()
                        text = txt.decode("utf-8", errors="ignore")
                        reply = await get_ai_response(f"{prompt}\nTXT:\n{text}")
                    
                    else:
                        reply = await get_ai_response(prompt)
                
                else:
                    reply = await get_ai_response(prompt)
            
            except Exception as e:
                logger.error(f"DM AI error: {e}")
                reply = f"⚠️ AI error: {e}"
            
            await save_conversation(0, 0, user_id, "user", message.content)
            await save_conversation(0, 0, user_id, "assistant", reply)
            
            dm_memory[key] += f"Bot: {reply}\n"
            dm_memory[key] = dm_memory[key][-8000:]
            
            while len(reply) > 1900:
                await message.channel.send(reply[:1900])
                reply = reply[1900:]
            
            await message.channel.send(reply)
            return
        
        # =========================
        # GUILD MESSAGE HANDLING
        # =========================
        if message.guild:
            # Leveling
            new_level = await add_xp(message.author.id, message.guild.id)
            if new_level:
                await message.channel.send(f"🎉 {message.author.mention} leveled up to **Level {new_level}**!")
                await add_history(message.guild.id, message.author.id, str(message.author), "LEVEL_UP", f"Reached level {new_level}")
            
            # Bad words filter
            for word in BAD_WORDS:
                if word in message.content.lower():
                    await message.delete()
                    await message.channel.send(f"{message.author.mention} Watch your language!", delete_after=5)
                    await add_warning(message.author.id, message.guild.id, f"Bad word: {word}", "AutoMod")
                    await add_history(message.guild.id, message.author.id, str(message.author), "AUTO_MOD", f"Bad word filter: {word}")
                    break
            
            # Invite link filter
            if re.search(INVITE_REGEX, message.content.lower()):
                await message.delete()
                await message.channel.send(f"{message.author.mention} No invite links!", delete_after=5)
                await add_warning(message.author.id, message.guild.id, "Invite link", "AutoMod")
                await add_history(message.guild.id, message.author.id, str(message.author), "AUTO_MOD", "Invite link filtered")
            
            # Custom commands
            if message.content.startswith('?'):
                cmd_name = message.content[1:].lower().split()[0]
                result = await db.fetchone(
                    "SELECT response FROM custom_commands WHERE guild_id=? AND name=?",
                    (message.guild.id, cmd_name)
                )
                if result:
                    await message.channel.send(result[0])
    
    except Exception as e:
        logger.error(f"on_message error: {e}")
    
    await bot.process_commands(message)

# =========================
# on_ready - FIXED & SAFE
# =========================
_tasks_started = False

@bot.event
async def on_ready():
    global _tasks_started

    if _tasks_started:
        return
    _tasks_started = True

    logger.info(f"🤖 Logged in as {bot.user}")

    # Start background tasks safely (prevents AlreadyRunning errors)
    if not check_giveaways.is_running():
        check_giveaways.start()

    if not check_birthdays.is_running():
        check_birthdays.start()

    if not generate_daily_summary.is_running():
        generate_daily_summary.start()

    if not consolidate_memories.is_running():
        consolidate_memories.start()

    if not clean_inactive_players.is_running():
        clean_inactive_players.start()

    # Status logs
    logger.info("✅ Bot is fully online and tasks are running!")
    logger.info(f"   Servers: {len(bot.guilds)}")
    logger.info(f"   Commands: {len(bot.tree.get_commands())}")


# =========================
# 🎮 MODERATION COG
# =========================

class Moderation(commands.Cog, name="moderation"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    mod_group = app_commands.Group(name="mod", description="Moderation commands")

    @mod_group.command(name="clear", description="Delete messages")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def mod_clear(self, interaction: discord.Interaction, amount: int):
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(limit=amount)
            await interaction.followup.send(f"🧹 Deleted {len(deleted)} messages", ephemeral=True)
            await log(interaction.guild, f"CLEAR | {len(deleted)}")
        except Exception as e:
            logger.error(f"Clear error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @mod_group.command(name="clearall", description="Wipe channel")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def mod_clearall(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        total = 0
        try:
            while True:
                deleted = await interaction.channel.purge(limit=100)
                total += len(deleted)
                if not deleted:
                    break
            await interaction.followup.send(f"Cleared {total}", ephemeral=True)
            await log(interaction.guild, f"CLEARALL | {total}")
        except Exception as e:
            logger.error(f"Clearall error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @mod_group.command(name="ban", description="Ban member")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(ban_members=True)
    async def mod_ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
        await interaction.response.defer()
        try:
            await member.ban(reason=reason)
            await add_history(interaction.guild.id, member.id, str(member), "BAN", f"Banned by {interaction.user}: {reason}")
            await interaction.followup.send(f"🔨 Banned {member}")
            await log(interaction.guild, f"BAN | {member} | {reason}")
        except Exception as e:
            logger.error(f"Ban error: {e}")
            await interaction.followup.send(f"❌ Error: {e}")

    @mod_group.command(name="softban", description="Ban and immediately unban to clear messages")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(ban_members=True)
    async def mod_softban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "Softban"):
        await interaction.response.defer()
        try:
            await member.ban(reason=reason)
            await member.unban(reason="Softban complete")
            await add_history(interaction.guild.id, member.id, str(member), "SOFTBAN", f"Softbanned by {interaction.user}: {reason}")
            await interaction.followup.send(f"🧹 Softbanned {member}")
            await log(interaction.guild, f"SOFTBAN | {member}")
        except Exception as e:
            logger.error(f"Softban error: {e}")
            await interaction.followup.send(f"❌ Error: {e}")

    @mod_group.command(name="mute", description="Timeout member")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mod_mute(self, interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "No reason"):
        await interaction.response.defer()
        try:
            await member.timeout(utcnow() + timedelta(minutes=minutes), reason=reason)
            await add_history(interaction.guild.id, member.id, str(member), "MUTE", f"Muted for {minutes}min by {interaction.user}: {reason}")
            await interaction.followup.send(f"🔇 Muted {member.mention} for {minutes} minutes")
            await log(interaction.guild, f"MUTE | {member} | {minutes}min | {reason}")
        except Exception as e:
            logger.error(f"Mute error: {e}")
            await interaction.followup.send(f"❌ Error: {e}")

    @mod_group.command(name="unmute", description="Unmute member")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mod_unmute(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer()
        try:
            await member.timeout(None)
            await add_history(interaction.guild.id, member.id, str(member), "UNMUTE", f"Unmuted by {interaction.user}")
            await interaction.followup.send(f"🔊 Unmuted {member.mention}")
            await log(interaction.guild, f"UNMUTE | {member}")
        except Exception as e:
            logger.error(f"Unmute error: {e}")
            await interaction.followup.send(f"❌ Error: {e}")

    @mod_group.command(name="warn", description="Warn a member")
    @app_commands.describe(member="Member to warn", reason="Reason for the warning")
    async def mod_warn(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        await interaction.response.defer(ephemeral=True)
        
        if not interaction.user.guild_permissions.moderate_members:
            return await interaction.followup.send("❌ You don't have permission to warn members.", ephemeral=True)
        
        if member.top_role >= interaction.user.top_role:
            return await interaction.followup.send("❌ You cannot warn this member (role hierarchy).", ephemeral=True)
        
        try:
            await add_warning(member.id, interaction.guild.id, reason, str(interaction.user))
            await add_history(interaction.guild.id, member.id, str(member), "WARN", f"Warned by {interaction.user}: {reason}")
            
            try:
                dm_embed = discord.Embed(
                    title=f"You were warned in {interaction.guild.name}",
                    description=f"**Reason:** {reason}",
                    color=discord.Color.orange()
                )
                await member.send(embed=dm_embed)
            except:
                pass
            
            embed = discord.Embed(title="⚠️ Warned User", color=discord.Color.orange())
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.set_footer(text=f"User ID: {member.id}")
            
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Warn error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @mod_group.command(name="kick", description="Kick a member from the server")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(kick_members=True)
    @app_commands.describe(member="Member to kick", reason="Reason for kicking")
    async def mod_kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        await interaction.response.defer(ephemeral=True)

        if member.top_role >= interaction.user.top_role:
            return await interaction.followup.send(
                "❌ You cannot kick this member (role hierarchy).",
                ephemeral=True
            )

        try:
            await member.kick(reason=reason)

            embed = discord.Embed(
                title="👢 Kicked User",
                color=discord.Color.red()
            )
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            embed.add_field(name="Reason", value=reason, inline=False)

            await interaction.followup.send(embed=embed)

            await add_history(
                interaction.guild.id,
                member.id,
                str(member),
                "KICK",
                f"Kicked by {interaction.user}: {reason}"
            )

            await log(interaction.guild, f"KICK | {member} | {reason}")

        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I don't have permission to kick that member.",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Kick error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @mod_group.command(name="clean", description="Clean a number of messages from a channel")
    @app_commands.describe(amount="Number of messages to delete", member="Only delete messages from this member (optional)")
    async def mod_clean(self, interaction: discord.Interaction, amount: int, member: discord.Member = None):
        await interaction.response.defer(ephemeral=True)
        
        if not interaction.user.guild_permissions.manage_messages:
            return await interaction.followup.send("❌ You don't have permission to manage messages.", ephemeral=True)
        
        amount = min(amount, 100)
        
        def check(msg):
            return True if member is None else msg.author.id == member.id
        
        try:
            deleted = await interaction.channel.purge(limit=amount, check=check)
            await interaction.followup.send(f"✅ Deleted {len(deleted)} messages.", ephemeral=True)
        except Exception as e:
            logger.error(f"Clean error: {e}")
            await interaction.followup.send("❌ Failed to delete messages.", ephemeral=True)


# =========================
# 🎵 MUSIC COG
# =========================

async def get_music_player(guild: discord.Guild, voice_channel: discord.VoiceChannel) -> Optional[wavelink.Player]:
    """Get or create a wavelink player for a guild."""
    player = guild.voice_client
    if player and isinstance(player, wavelink.Player):
        if player.channel != voice_channel:
            await player.move_to(voice_channel)
        return player
    
    if player:
        await player.disconnect()
    
    player = await voice_channel.connect(cls=wavelink.Player)
    return player


class Music(commands.Cog, name="music"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.queue = {}      # guild_id: list[wavelink.Playable]
        self.current = {}    # guild_id: wavelink.Playable
        self.history = {}    # guild_id: list[wavelink.Playable]
        self.loop = {}       # guild_id: bool
        self.volume = {}     # guild_id: int

    def get_queue(self, guild_id: int):
        return self.queue.setdefault(guild_id, [])

    def get_history(self, guild_id: int):
        return self.history.setdefault(guild_id, [])

    async def ensure_voice(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send(
                "❌ You must be in a voice channel first.",
                ephemeral=True
            )
            return False
        return True

    music_group = app_commands.Group(name="music", description="Music commands")

   # =========================
# PLAY COMMAND
# =========================
@music_group.command(name="play", description="Play a song from a query or URL")
async def music_play(self, interaction: discord.Interaction, query: str):
    await interaction.response.defer()

    if not await self.ensure_voice(interaction):
        return

    guild = interaction.guild
    channel = interaction.user.voice.channel

    try:
        player: wavelink.Player = guild.voice_client

        if not player:
            player = await channel.connect(cls=wavelink.Player)

        tracks = await wavelink.Playable.search(query)

        if not tracks:
            return await interaction.followup.send("❌ No results found.")

        # Playlist handling
        if isinstance(tracks, wavelink.Playlist):
            playlist_tracks = list(tracks)

            queue = self.get_queue(guild.id)
            queue.extend(playlist_tracks)

            if not player.playing:
                self.current[guild.id] = playlist_tracks[0]
                await player.play(playlist_tracks[0])

                embed = discord.Embed(title="▶️ Now Playing (Playlist)", color=discord.Color.green())
                embed.add_field(name="Title", value=playlist_tracks[0].title, inline=False)
                embed.add_field(name="Tracks", value=len(playlist_tracks), inline=True)

                if getattr(playlist_tracks[0], "artwork", None):
                    embed.set_thumbnail(url=playlist_tracks[0].artwork)

                return await interaction.followup.send(embed=embed)

            return await interaction.followup.send(
                f"📋 Added playlist with **{len(playlist_tracks)} tracks** to queue."
            )

        # Single track
        track = tracks[0]
        queue = self.get_queue(guild.id)

        if not player.playing:
            self.current[guild.id] = track
            await player.play(track)

            embed = discord.Embed(title="▶️ Now Playing", color=discord.Color.green())
            embed.add_field(name="Title", value=track.title, inline=False)
            embed.add_field(name="Artist", value=getattr(track, "author", "Unknown"), inline=True)

            if getattr(track, "length", None):
                embed.add_field(name="Duration", value=f"{track.length}s", inline=True)

            if getattr(track, "artwork", None):
                embed.set_thumbnail(url=track.artwork)

            return await interaction.followup.send(embed=embed)

        queue.append(track)

        await interaction.followup.send(f"📋 Added **{track.title}** to queue.")

    except Exception as e:
        logger.error(f"Music play error: {e}")
        await interaction.followup.send(f"❌ Error: {str(e)}")


# =========================
# SEARCH COMMAND
# =========================
@music_group.command(name="search", description="Search for a song and select from results")
async def music_search(self, interaction: discord.Interaction, query: str):
    await interaction.response.defer()

    if not await self.ensure_voice(interaction):
        return

    try:
        tracks = await wavelink.Playable.search(query)

        if not tracks:
            return await interaction.followup.send("❌ No results found.")

        tracks = tracks[:5]

        embed = discord.Embed(title="🔍 Search Results", color=discord.Color.blue())
        for i, track in enumerate(tracks, 1):
            embed.add_field(
                name=f"{i}. {track.title}",
                value=f"{track.author}",
                inline=False
            )

        view = SearchSelect(tracks, self)
        await interaction.followup.send(embed=embed, view=view)

    except Exception as e:
        logger.error(f"Music search error: {e}")
        await interaction.followup.send(f"❌ Error: {str(e)}")


# =========================
# PAUSE / RESUME
# =========================
@music_group.command(name="pause")
async def music_pause(self, interaction: discord.Interaction):
    player = interaction.guild.voice_client

    if not player or not isinstance(player, wavelink.Player) or not player.playing:
        return await interaction.response.send_message("❌ Nothing playing.")

    await player.pause()
    await interaction.response.send_message("⏸️ Paused")


@music_group.command(name="resume")
async def music_resume(self, interaction: discord.Interaction):
    player = interaction.guild.voice_client

    if not player or not isinstance(player, wavelink.Player):
        return await interaction.response.send_message("❌ Not connected.")

    await player.resume()
    await interaction.response.send_message("▶️ Resumed")


# =========================
# SKIP (FIXED PLACEMENT)
# =========================
@music_group.command(name="skip")
async def skip(self, interaction: discord.Interaction):
    player = interaction.guild.voice_client

    if not player or not player.playing:
        return await interaction.response.send_message("❌ Nothing is playing.")

    await player.stop()
    await interaction.response.send_message("⏭️ Skipped")


# =========================
# 🎵 WAVELINK EVENTS COG
# =========================

class WavelinkEvents(commands.Cog):
    """Handles wavelink track lifecycle events."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload):
        guild = self.bot.get_guild(payload.player.guild.id)
        if not guild:
            return
        
        music_cog = self.bot.get_cog("music")
        if music_cog:
            track = payload.track
            music_cog.current[guild.id] = track
    
    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        guild = self.bot.get_guild(payload.player.guild.id)
        if not guild:
            return
        
        music_cog = self.bot.get_cog("music")
        if not music_cog:
            return
        
        # Handle loop
        if music_cog.loop.get(guild.id, False) and music_cog.current.get(guild.id):
            track = music_cog.current[guild.id]
            await payload.player.play(track)
            return
        
        # Save to history
        if guild.id in music_cog.current and music_cog.current[guild.id]:
            history = music_cog.get_history(guild.id)
            history.append(music_cog.current[guild.id])
            if len(history) > 20:
                history.pop(0)
        
        # Play next in queue
        queue = music_cog.get_queue(guild.id)
        if queue:
            next_track = queue.pop(0)
            music_cog.current[guild.id] = next_track
            await payload.player.play(next_track)
        else:
            music_cog.current[guild.id] = None
    
    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, node: wavelink.Node):
        logger.info(f"✅ Wavelink node {node.identifier} is ready!")


# =========================
# 🎮 FUN COG
# =========================

class Fun(commands.Cog, name="fun"):
    fun_group = app_commands.Group(name="fun", description="Fun commands")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @fun_group.command(name="8ball", description="Ask the magic 8ball a question")
    @app_commands.describe(question="Your question")
    async def fun_8ball(self, interaction: discord.Interaction, question: str):
        responses = [
            "It is certain.", "It is decidedly so.", "Without a doubt.", "Yes definitely.",
            "You may rely on it.", "As I see it, yes.", "Most likely.", "Outlook good.",
            "Yes.", "Signs point to yes.", "Reply hazy, try again.", "Ask again later.",
            "Better not tell you now.", "Cannot predict now.", "Concentrate and ask again.",
            "Don't count on it.", "My reply is no.", "My sources say no.",
            "Outlook not so good.", "Very doubtful."
        ]

        embed = discord.Embed(
            title="🎱 Magic 8Ball",
            color=discord.Color.purple()
        )
        embed.add_field(name="Question", value=question, inline=False)
        embed.add_field(name="Answer", value=random.choice(responses), inline=False)
        await interaction.response.send_message(embed=embed)
    
    @fun_group.command(name="dice", description="Roll a dice")
    @app_commands.describe(sides="Number of sides (default: 6)")
    async def fun_dice(self, interaction: discord.Interaction, sides: int = 6):
        if sides < 2:
            return await interaction.response.send_message("❌ Dice must have at least 2 sides.", ephemeral=True)
        result = random.randint(1, sides)
        await interaction.response.send_message(f"🎲 You rolled a **{result}** (1-{sides})")
    
    @fun_group.command(name="coinflip", description="Flip a coin")
    async def fun_coinflip(self, interaction: discord.Interaction):
        result = random.choice(["Heads", "Tails"])
        await interaction.response.send_message(f"🪙 **{result}**")
    
    @fun_group.command(name="meme", description="Get a random meme")
    async def fun_meme(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://meme-api.com/gimme") as resp:
                    data = await resp.json()
                    embed = discord.Embed(title=data.get("title", "Meme"), color=discord.Color.random())
                    embed.set_image(url=data.get("url"))
                    embed.set_footer(text=f"From r/{data.get('subreddit', 'unknown')} | 👍 {data.get('ups', 0)}")
                    await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Meme error: {e}")
            await interaction.followup.send("❌ Failed to fetch meme. Try again later.")
    
    @fun_group.command(name="rps", description="Play rock-paper-scissors")
    @app_commands.describe(choice="Your choice")
    @app_commands.choices(choice=[
        discord.app_commands.Choice(name="Rock 🪨", value="rock"),
        discord.app_commands.Choice(name="Paper 📄", value="paper"),
        discord.app_commands.Choice(name="Scissors ✂️", value="scissors"),
    ])
    async def fun_rps(self, interaction: discord.Interaction, choice: str):
        bot_choice = random.choice(["rock", "paper", "scissors"])
        emojis = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
        
        if choice == bot_choice:
            result = "It's a tie!"
        elif (choice == "rock" and bot_choice == "scissors") or \
             (choice == "paper" and bot_choice == "rock") or \
             (choice == "scissors" and bot_choice == "paper"):
            result = "You win! 🎉"
        else:
            result = "I win! 😎"
        
        embed = discord.Embed(title="Rock Paper Scissors", color=discord.Color.blue())
        embed.add_field(name="Your Choice", value=f"{emojis[choice]} {choice.title()}", inline=True)
        embed.add_field(name="My Choice", value=f"{emojis[bot_choice]} {bot_choice.title()}", inline=True)
        embed.add_field(name="Result", value=result, inline=False)
        await interaction.response.send_message(embed=embed)


# =========================
# 💰 ECONOMY COG
# =========================

class Economy(commands.Cog, name="economy"):
    economy_group = app_commands.Group(name="economy", description="Economy commands")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.daily_cooldowns = {}

    async def get_balance(self, user_id: int) -> tuple:
        return await get_balance_simple(user_id)
    
    @economy_group.command(name="balance", description="Check your or another user's balance")
    @app_commands.describe(member="Member to check (optional)")
    async def economy_balance(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        wallet, bank = await self.get_balance(target.id)
        embed = discord.Embed(title=f"💰 {target.display_name}'s Balance", color=discord.Color.gold())
        embed.add_field(name="Wallet", value=f"${wallet:,}", inline=True)
        embed.add_field(name="Bank", value=f"${bank:,}", inline=True)
        embed.add_field(name="Total", value=f"${wallet + bank:,}", inline=False)
        await interaction.response.send_message(embed=embed)
    
    @economy_group.command(name="daily", description="Claim your daily reward")
    async def economy_daily(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        now = int(time.time())
        
        if user_id in self.daily_cooldowns:
            remaining = 86400 - (now - self.daily_cooldowns[user_id])
            if remaining > 0:
                hours = remaining // 3600
                minutes = (remaining % 3600) // 60
                return await interaction.response.send_message(
                    f"⏰ You already claimed your daily! Come back in {int(hours)}h {int(minutes)}m.", ephemeral=True)
        
        amount = random.randint(100, 500)
        result = await db.fetchone("SELECT wallet FROM economy WHERE user_id=?", (user_id,))
        if result:
            await db.execute("UPDATE economy SET wallet = wallet + ? WHERE user_id=?", (amount, user_id))
        else:
            await db.execute("INSERT INTO economy (user_id, wallet, bank) VALUES (?, ?, 0)", (user_id, amount))
        await db.commit()
        
        self.daily_cooldowns[user_id] = now
        embed = discord.Embed(title="📅 Daily Reward", description=f"You received **${amount:,}**!", color=discord.Color.green())
        await interaction.response.send_message(embed=embed)
    
    @economy_group.command(name="pay", description="Send money to another user")
    @app_commands.describe(member="Recipient", amount="Amount to send")
    async def economy_pay(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        if amount <= 0:
            return await interaction.response.send_message("❌ Amount must be positive.", ephemeral=True)
        if member.id == interaction.user.id:
            return await interaction.response.send_message("❌ You can't pay yourself.", ephemeral=True)
        
        sender_result = await db.fetchone("SELECT wallet FROM economy WHERE user_id=?", (interaction.user.id,))
        sender_wallet = sender_result[0] if sender_result else 0
        if sender_wallet < amount:
            return await interaction.response.send_message("❌ Insufficient funds in wallet.", ephemeral=True)
        
        await db.execute("UPDATE economy SET wallet = wallet - ? WHERE user_id=?", (amount, interaction.user.id))
        await db.execute("UPDATE economy SET wallet = wallet + ? WHERE user_id=?", (amount, member.id))
        await db.commit()
        
        embed = discord.Embed(title="💸 Payment Sent", color=discord.Color.green())
        embed.add_field(name="From", value=interaction.user.mention, inline=True)
        embed.add_field(name="To", value=member.mention, inline=True)
        embed.add_field(name="Amount", value=f"${amount:,}", inline=False)
        await interaction.response.send_message(embed=embed)
    
    @economy_group.command(name="gamble", description="Gamble your money")
    @app_commands.describe(amount="Amount to gamble")
    async def economy_gamble(self, interaction: discord.Interaction, amount: int):
        if amount <= 0:
            return await interaction.response.send_message("❌ Amount must be positive.", ephemeral=True)
        
        result = await db.fetchone("SELECT wallet FROM economy WHERE user_id=?", (interaction.user.id,))
        wallet = result[0] if result else 0
        if wallet < amount:
            return await interaction.response.send_message("❌ Insufficient funds.", ephemeral=True)
        
        win = random.random() < 0.45
        if win:
            winnings = int(amount * random.uniform(1.5, 3.0))
            await db.execute("UPDATE economy SET wallet = wallet - ? + ? WHERE user_id=?", (amount, winnings, interaction.user.id))
            await db.commit()
            embed = discord.Embed(title="🎰 You Won!", description=f"You gambled ${amount:,} and won **${winnings:,}**!", color=discord.Color.green())
        else:
            await db.execute("UPDATE economy SET wallet = wallet - ? WHERE user_id=?", (amount, interaction.user.id))
            await db.commit()
            embed = discord.Embed(title="🎰 You Lost!", description=f"You gambled ${amount:,} and lost it all.", color=discord.Color.red())
        
        await interaction.response.send_message(embed=embed)
    
    @economy_group.command(name="leaderboard", description="View the economy leaderboard")
    async def economy_leaderboard(self, interaction: discord.Interaction):
        rows = await db.fetchall(
            "SELECT user_id, wallet, bank FROM economy ORDER BY (wallet + bank) DESC LIMIT 10"
        )
        
        embed = discord.Embed(title="🏆 Economy Leaderboard", color=discord.Color.gold())
        
        if not rows:
            embed.description = "No data yet."
        else:
            medals = ["🥇", "🥈", "🥉"]
            for i, (user_id, wallet, bank) in enumerate(rows):
                total = wallet + bank
                user = interaction.guild.get_member(user_id)
                name = user.display_name if user else f"Unknown ({user_id})"
                prefix = medals[i] if i < 3 else f"{i+1}."
                embed.add_field(name=f"{prefix} {name}", value=f"${total:,} (Wallet: ${wallet:,} | Bank: ${bank:,})", inline=False)
        
        await interaction.response.send_message(embed=embed)


# =========================
# 🎉 GIVEAWAY COG (UNIFIED)
# =========================

class Giveaway(commands.Cog, name="giveaway"):
    giveaway_group = app_commands.Group(name="giveaway", description="Giveaway commands")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @giveaway_group.command(name="start", description="Start a giveaway")
    @app_commands.describe(
        prize="The prize to give away",
        duration="Duration in minutes",
        winners="Number of winners (default: 1)"
    )
    async def giveaway_start(self, interaction: discord.Interaction, prize: str, duration: int, winners: int = 1):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
        
        if duration < 1:
            return await interaction.response.send_message("❌ Duration must be at least 1 minute.", ephemeral=True)
        if winners < 1:
            return await interaction.response.send_message("❌ Must have at least 1 winner.", ephemeral=True)
        
        end_time = time.time() + (duration * 60)
        giveaway_id = str(uuid.uuid4())
        
        embed = discord.Embed(title="🎉 Giveaway", color=discord.Color.blue())
        embed.add_field(name="Prize", value=prize, inline=False)
        embed.add_field(name="Entries", value="0", inline=True)
        embed.add_field(name="Winners", value=str(winners), inline=True)
        embed.add_field(name="Ends", value=f"<t:{int(end_time)}:R>", inline=False)
        embed.set_footer(text="Click the button below to enter!")
        
        view = GiveawayView(giveaway_id)
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        
        g_entry = {
            "id": giveaway_id,
            "prize": prize,
            "winners": winners,
            "end_time": end_time,
            "entries": [],
            "channel_id": interaction.channel_id,
            "message_id": msg.id,
            "ended": False
        }
        
        if interaction.guild_id not in active_giveaways:
            active_giveaways[interaction.guild_id] = []
        active_giveaways[interaction.guild_id].append(g_entry)
        
        logger.info(f"Giveaway started: {prize} in {interaction.guild_id}")
    
    @giveaway_group.command(name="reroll", description="Reroll a giveaway winner")
    @app_commands.describe(message_id="ID of the giveaway message")
    async def giveaway_reroll(self, interaction: discord.Interaction, message_id: str):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("❌ You need Manage Server permission.", ephemeral=True)
        
        try:
            msg_id = int(message_id)
            msg = await interaction.channel.fetch_message(msg_id)
        except:
            return await interaction.response.send_message("❌ Message not found.", ephemeral=True)
        
        for guild_id, giveaways in active_giveaways.items():
            for g in giveaways:
                if g["message_id"] == msg_id and g.get("ended"):
                    entries = g["entries"]
                    if not entries:
                        return await interaction.response.send_message("❌ No entries in that giveaway.", ephemeral=True)
                    
                    winner = random.choice(entries)
                    embed = msg.embeds[0]
                    embed.clear_fields()
                    embed.add_field(name="🎉 New Winner", value=f"<@{winner}>", inline=False)
                    embed.add_field(name="Prize", value=g["prize"], inline=False)
                    await msg.edit(embed=embed)
                    
                    return await interaction.response.send_message(f"🎉 New winner: <@{winner}>!")
        
        await interaction.response.send_message("❌ Could not find that giveaway or it hasn't ended yet.", ephemeral=True)


# =========================
# 🎫 TICKET COG
# =========================

class Ticket(commands.Cog, name="ticket"):
    ticket_group = app_commands.Group(name="ticket", description="Ticket commands")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @ticket_group.command(name="setup", description="Set up the ticket panel in this channel")
    async def ticket_setup(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_channels:
            return await interaction.response.send_message("❌ You need Manage Channels permission.", ephemeral=True)
        
        embed = discord.Embed(
            title="🎫 Support Tickets",
            description="Click the button below to create a support ticket.",
            color=discord.Color.blue()
        )
        
        view = TicketPanelView()
        await interaction.response.send_message(embed=embed, view=view)
    
    @ticket_group.command(name="add", description="Add a user to a ticket")
    @app_commands.describe(member="Member to add")
    async def ticket_add(self, interaction: discord.Interaction, member: discord.Member):
        if not interaction.channel.name.startswith("ticket-"):
            return await interaction.response.send_message("❌ This is not a ticket channel.", ephemeral=True)
        
        try:
            await interaction.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
            await interaction.response.send_message(f"✅ Added {member.mention} to this ticket.")
        except Exception as e:
            logger.error(f"Ticket add error: {e}")
            await interaction.response.send_message(f"❌ Error: {e}")
    
    @ticket_group.command(name="remove", description="Remove a user from a ticket")
    @app_commands.describe(member="Member to remove")
    async def ticket_remove(self, interaction: discord.Interaction, member: discord.Member):
        if not interaction.channel.name.startswith("ticket-"):
            return await interaction.response.send_message("❌ This is not a ticket channel.", ephemeral=True)
        
        try:
            await interaction.channel.set_permissions(member, overwrite=None)
            await interaction.response.send_message(f"✅ Removed {member.mention} from this ticket.")
        except Exception as e:
            logger.error(f"Ticket remove error: {e}")
            await interaction.response.send_message(f"❌ Error: {e}")


# =========================
# 📊 LEVELING COG
# =========================

class Leveling(commands.Cog, name="leveling"):
    leveling_group = app_commands.Group(name="leveling", description="Leveling commands")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @leveling_group.command(name="rank", description="Check your or another user's level rank")
    @app_commands.describe(member="Member to check (optional)")
    async def leveling_rank(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        
        row = await db.fetchone(
            "SELECT xp, level, total_messages FROM levels WHERE user_id=? AND guild_id=?",
            (target.id, interaction.guild.id)
        )
        
        if not row:
            return await interaction.response.send_message(f"{target.mention} has no XP yet.", ephemeral=True)
        
        xp, level, total_messages = row
        
        rank_result = await db.fetchone(
            "SELECT COUNT(*) FROM levels WHERE guild_id=? AND (xp + (level * (100 + (level - 1) * 50))) > (SELECT xp + (level * (100 + (level - 1) * 50)) FROM levels WHERE user_id=? AND guild_id=?)",
            (interaction.guild.id, target.id, interaction.guild.id)
        )
        rank = (rank_result[0] if rank_result else 0) + 1
        
        total_users_result = await db.fetchone("SELECT COUNT(*) FROM levels WHERE guild_id=?", (interaction.guild.id,))
        total_users = total_users_result[0] if total_users_result else 0
        
        needed = 100 + (level * 50)
        
        embed = discord.Embed(title=f"📊 {target.display_name}'s Rank", color=discord.Color.blue())
        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(name="Rank", value=f"#{rank}/{total_users}", inline=True)
        embed.add_field(name="XP", value=f"{xp}/{needed}", inline=False)
        embed.add_field(name="Total Messages", value=str(total_messages), inline=True)
        
        progress = min(xp / needed, 1.0) if needed > 0 else 0
        bar_length = 15
        filled = int(progress * bar_length)
        bar = "█" * filled + "░" * (bar_length - filled)
        embed.add_field(name="Progress", value=f"`{bar}` {int(progress * 100)}%", inline=False)
        
        await interaction.response.send_message(embed=embed)
    
    @leveling_group.command(name="leaderboard", description="View the leveling leaderboard")
    async def leveling_leaderboard(self, interaction: discord.Interaction):
        rows = await db.fetchall(
            "SELECT user_id, level, xp FROM levels WHERE guild_id=? ORDER BY level DESC, xp DESC LIMIT 10",
            (interaction.guild.id,)
        )
        
        embed = discord.Embed(title=f"🏆 Leveling Leaderboard - {interaction.guild.name}", color=discord.Color.gold())
        
        if not rows:
            embed.description = "No data yet."
        else:
            medals = ["🥇", "🥈", "🥉"]
            for i, (user_id, level, xp) in enumerate(rows):
                user = interaction.guild.get_member(user_id)
                name = user.display_name if user else f"Unknown ({user_id})"
                prefix = medals[i] if i < 3 else f"{i+1}."
                embed.add_field(name=f"{prefix} {name}", value=f"Level {level} | {xp} XP", inline=False)
        
        await interaction.response.send_message(embed=embed)


# =========================
# 🤖 AI COG
# =========================

class AI(commands.Cog, name="ai"):
    ai_group = app_commands.Group(name="ai", description="AI commands")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.conversation_history = {}
    
    def get_history(self, channel_id: int) -> list:
        if channel_id not in self.conversation_history:
            self.conversation_history[channel_id] = []
        return self.conversation_history[channel_id]
    
    @ai_group.command(name="chat", description="Chat with the AI")
    @app_commands.describe(message="Your message to the AI")
    async def ai_chat(self, interaction: discord.Interaction, message: str):
        await interaction.response.defer()
        
        history = self.get_history(interaction.channel_id)
        history.append({"role": "user", "content": f"[{interaction.user.display_name}] {message}"})
        
        if len(history) > 20:
            history = history[-20:]
            self.conversation_history[interaction.channel_id] = history
        
        try:
            response = await query_ai(message)
            history.append({"role": "assistant", "content": response})
            
            if len(response) > 1900:
                parts = [response[i:i+1900] for i in range(0, len(response), 1900)]
                for part in parts:
                    await interaction.followup.send(part)
            else:
                await interaction.followup.send(response)
        except Exception as e:
            logger.error(f"AI chat error: {e}")
            await interaction.followup.send(f"❌ AI Error: {str(e)}")
    
    @ai_group.command(name="reset", description="Reset the AI conversation history")
    async def ai_reset(self, interaction: discord.Interaction):
        self.conversation_history[interaction.channel_id] = []
        await interaction.response.send_message("✅ Conversation history reset.", ephemeral=True)
    
    @ai_group.command(name="ask", description="Ask the AI a one-off question (no history)")
    @app_commands.describe(question="Your question")
    async def ai_ask(self, interaction: discord.Interaction, question: str):
        await interaction.response.defer()
        try:
            response = await query_ai(question)
            if len(response) > 1900:
                parts = [response[i:i+1900] for i in range(0, len(response), 1900)]
                for part in parts:
                    await interaction.followup.send(part)
            else:
                await interaction.followup.send(response)
        except Exception as e:
            logger.error(f"AI ask error: {e}")
            await interaction.followup.send(f"❌ AI Error: {str(e)}")


# =========================
# 📋 UTILITY COMMANDS
# =========================

class Utility(commands.Cog, name="utility"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @app_commands.command(name="ping", description="Check the bot's latency")
    async def ping(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"🏓 Pong! `{latency}ms`")
    
    @app_commands.command(name="serverinfo", description="Display information about the server")
    async def serverinfo(self, interaction: discord.Interaction):
        guild = interaction.guild
        embed = discord.Embed(title=guild.name, color=discord.Color.blue())
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Owner", value=guild.owner.mention if guild.owner else "Unknown", inline=True)
        embed.add_field(name="Members", value=guild.member_count, inline=True)
        embed.add_field(name="Channels", value=len(guild.channels), inline=True)
        embed.add_field(name="Roles", value=len(guild.roles), inline=True)
        embed.add_field(name="Created", value=f"<t:{int(guild.created_at.timestamp())}:R>", inline=True)
        embed.add_field(name="ID", value=guild.id, inline=True)
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(name="userinfo", description="Display information about a user")
    @app_commands.describe(member="Member to look up (optional)")
    async def userinfo(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        embed = discord.Embed(title=target.display_name, color=target.color or discord.Color.blue())
        if target.avatar:
            embed.set_thumbnail(url=target.avatar.url)
        embed.add_field(name="Username", value=str(target), inline=True)
        embed.add_field(name="ID", value=target.id, inline=True)
        embed.add_field(name="Joined", value=f"<t:{int(target.joined_at.timestamp())}:R>" if target.joined_at else "Unknown", inline=True)
        embed.add_field(name="Created", value=f"<t:{int(target.created_at.timestamp())}:R>", inline=True)
        embed.add_field(name="Top Role", value=target.top_role.mention if target.top_role else "None", inline=True)
        embed.add_field(name="Bot", value="Yes" if target.bot else "No", inline=True)
        await interaction.response.send_message(embed=embed)


# =========================
# 📋 HELP COG
# =========================

class Help(commands.Cog, name="help"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @app_commands.command(name="help", description="Show all available commands")
    async def help_command(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title=f"🤖 {self.bot.user.name} Help",
            description="A multi-purpose Discord bot with moderation, music, economy, and more!",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="🛡️ Moderation",
            value="`/mod clear`, `/mod clearall`, `/mod ban`, `/mod softban`, `/mod kick`, `/mod mute`, `/mod unmute`, `/mod warn`, `/mod clean`",
            inline=False
        )
        embed.add_field(
            name="🎵 Music",
            value="`/music play`, `/music search`, `/music pause`, `/music resume`, `/music skip`, `/music previous`, `/music stop`, `/music queue`, `/music nowplaying`, `/music shuffle`, `/music remove`, `/music loop`, `/music volume`, `/music playlist`",
            inline=False
        )
        embed.add_field(
            name="🎮 Fun",
            value="`/fun 8ball`, `/fun dice`, `/fun coinflip`, `/fun meme`, `/fun rps`",
            inline=False
        )
        embed.add_field(
            name="💰 Economy",
            value="`/economy balance`, `/economy daily`, `/economy pay`, `/economy gamble`, `/economy leaderboard`",
            inline=False
        )
        embed.add_field(
            name="📊 Leveling",
            value="`/leveling rank`, `/leveling leaderboard`",
            inline=False
        )
        embed.add_field(
            name="🎫 Tickets",
            value="`/ticket setup`, `/ticket add`, `/ticket remove`",
            inline=False
        )
        embed.add_field(
            name="🎉 Giveaways",
            value="`/giveaway start`, `/giveaway reroll`",
            inline=False
        )
        embed.add_field(
            name="🤖 AI",
            value="`/ai chat`, `/ai ask`, `/ai reset`",
            inline=False
        )
        embed.add_field(
            name="📋 Utility",
            value="`/serverinfo`, `/userinfo`, `/ping`",
            inline=False
        )
        
        embed.set_footer(text=f"Use /<command> to use any command | {len(self.bot.cogs)} modules loaded")
        await interaction.response.send_message(embed=embed)


# =========================
# ⚠️ ERROR HANDLER
# =========================

class CommandErrorHandler(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error):
        logger.error(f"Command error in {ctx.command}: {error}")
    
    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        if isinstance(error, discord.app_commands.CommandOnCooldown):
            return await interaction.response.send_message(
                f"⏰ Command on cooldown. Try again in {error.retry_after:.0f}s.", ephemeral=True)
        elif isinstance(error, discord.app_commands.MissingPermissions):
            return await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        elif isinstance(error, discord.app_commands.BotMissingPermissions):
            return await interaction.response.send_message("❌ I don't have the required permissions.", ephemeral=True)
        elif isinstance(error, discord.app_commands.CheckFailure):
            return await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        else:
            error_details = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
            logger.error(f"App command error: {error}\n{error_details}")
            try:
                await interaction.response.send_message(f"❌ An error occurred: {str(error)[:100]}", ephemeral=True)
            except:
                try:
                    await interaction.followup.send(f"❌ An error occurred: {str(error)[:100]}", ephemeral=True)
                except:
                    pass


# =========================
# 📋 HISTORY COMMANDS
# =========================

class HistoryCommands(commands.Cog, name="history"):
    history_group = app_commands.Group(name="history", description="View server history")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @history_group.command(name="user", description="View a user's history")
    @app_commands.describe(member="Member to check")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def history_user(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        events = await get_user_history(member.id, interaction.guild.id)
        if not events:
            return await interaction.followup.send("No history found.", ephemeral=True)
        
        embed = discord.Embed(title=f"📜 History for {member.display_name}", color=discord.Color.blue())
        for event_type, details, timestamp in events[:15]:
            embed.add_field(name=event_type, value=f"{details[:100]}\n{timestamp}", inline=False)
        
        await interaction.followup.send(embed=embed)
    
    @history_group.command(name="search", description="Search history")
    @app_commands.describe(query="Search term")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def history_search(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)
        events = await get_history_search(interaction.guild.id, query)
        if not events:
            return await interaction.followup.send("No matching history found.", ephemeral=True)
        
        embed = discord.Embed(title=f"🔍 History Search: {query}", color=discord.Color.blue())
        for event_type, details, timestamp, username, user_id in events[:15]:
            embed.add_field(name=f"{event_type} - {username}", value=f"{details[:100]}\n{timestamp}", inline=False)
        
        await interaction.followup.send(embed=embed)


# =========================
# 🎵 MAIN BOT INITIALIZATION
# =========================

async def main():
    async with bot:
        # Connect database
        await db.connect()
        
        # Register all cogs
        await bot.add_cog(Moderation(bot))
        await bot.add_cog(Music(bot))
        await bot.add_cog(WavelinkEvents(bot))
        await bot.add_cog(Fun(bot))
        await bot.add_cog(Economy(bot))
        await bot.add_cog(Giveaway(bot))
        await bot.add_cog(Ticket(bot))
        await bot.add_cog(Leveling(bot))
        await bot.add_cog(AI(bot))
        await bot.add_cog(Utility(bot))
        await bot.add_cog(Help(bot))
        await bot.add_cog(CommandErrorHandler(bot))
        await bot.add_cog(HistoryCommands(bot))
        
        # Start the bot (sync is handled in setup_hook)
        await bot.start(TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot shut down by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        traceback.print_exc()
