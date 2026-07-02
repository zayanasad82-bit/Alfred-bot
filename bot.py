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
import time
import uuid
import logging
import traceback
from collections import defaultdict, deque
from typing import Optional, List, Dict, Any, Tuple

from google import genai
from pypdf import PdfReader
from docx import Document
from discord.utils import utcnow
import aiohttp



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

# Gemini client
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
MODEL_NAME = "gemini-2.5-flash"

# =========================
# DISCORD BOT SETUP
# =========================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.presences = True
intents.voice_states = True

class MyBot(commands.Bot):
    async def setup_hook(self):
        # Sync slash commands
        await self.tree.sync()
        logger.info("✅ Slash commands synced")

        # Restore control panel if the method exists
        if hasattr(self, "restore_control_panel"):
            try:
                await self.restore_control_panel()
                logger.info("✅ Control panel restored")
            except Exception as e:
                logger.error(f"Control panel restoration failed: {e}")

bot = MyBot(
    command_prefix="!",
    intents=intents,
    application_id=1513901589570523216
)

START_TIME = datetime.now()
_tasks_started = False

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
                guild_id INTEGER, user_id INTEGER,
                channel_id INTEGER, status TEXT DEFAULT 'open', created_at TEXT,
                PRIMARY KEY (guild_id, channel_id)
            )""",
            """CREATE TABLE IF NOT EXISTS giveaways (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER, channel_id INTEGER,
                prize TEXT, winner_count INTEGER,
                end_time TEXT, host_id INTEGER, message_id INTEGER
            )""",
            """CREATE TABLE IF NOT EXISTS reaction_roles (
                guild_id INTEGER, channel_id INTEGER,
                message_id INTEGER, emoji TEXT, role_id INTEGER,
                PRIMARY KEY (guild_id, message_id, emoji)
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
                guild_id INTEGER, name TEXT, url TEXT, added_by INTEGER,
                PRIMARY KEY (guild_id, name)
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
                guild_id INTEGER, user_id INTEGER, username TEXT,
                event_type TEXT, details TEXT, timestamp TEXT,
                PRIMARY KEY (guild_id, user_id, event_type, timestamp)
            )""",
            """CREATE TABLE IF NOT EXISTS daily_summaries (
                guild_id INTEGER, date TEXT, summary TEXT,
                total_messages INTEGER, most_active_user_id INTEGER,
                top_topic TEXT, generated_at TEXT,
                PRIMARY KEY (guild_id, date)
            )""",
            """CREATE TABLE IF NOT EXISTS message_stats (
                guild_id INTEGER, channel_id INTEGER, user_id INTEGER,
                date TEXT, count INTEGER DEFAULT 0, topics TEXT,
                PRIMARY KEY (guild_id, channel_id, user_id, date)
            )""",
            """CREATE TABLE IF NOT EXISTS ai_conversations (
                guild_id INTEGER, channel_id INTEGER, user_id INTEGER,
                role TEXT, content TEXT, timestamp TEXT,
                PRIMARY KEY (guild_id, user_id, timestamp)
            )""",
            """CREATE TABLE IF NOT EXISTS ai_memories (
                guild_id INTEGER, user_id INTEGER, key TEXT, value TEXT,
                importance INTEGER DEFAULT 1, created_at TEXT, last_accessed TEXT,
                PRIMARY KEY (guild_id, user_id, key)
            )""",
            """CREATE TABLE IF NOT EXISTS ai_personality (
                guild_id INTEGER, user_id INTEGER,
                trait TEXT, value TEXT,
                PRIMARY KEY (guild_id, user_id, trait)
            )""",
            """CREATE TABLE IF NOT EXISTS mod_logs_config (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                enabled BOOLEAN DEFAULT 1,
                log_deletes BOOLEAN DEFAULT 1,
                log_edits BOOLEAN DEFAULT 1,
                log_joins BOOLEAN DEFAULT 1,
                log_leaves BOOLEAN DEFAULT 1,
                log_bans BOOLEAN DEFAULT 1,
                log_kicks BOOLEAN DEFAULT 1,
                log_timeouts BOOLEAN DEFAULT 1,
                log_voice BOOLEAN DEFAULT 1,
                log_roles BOOLEAN DEFAULT 1
            )""",
            """CREATE TABLE IF NOT EXISTS automod_config (
                guild_id INTEGER PRIMARY KEY,
                anti_spam BOOLEAN DEFAULT 0,
                anti_invite BOOLEAN DEFAULT 0,
                anti_mentions BOOLEAN DEFAULT 0,
                bad_words BOOLEAN DEFAULT 0,
                spam_threshold INTEGER DEFAULT 5,
                spam_window INTEGER DEFAULT 5,
                mention_limit INTEGER DEFAULT 5,
                bad_words_list TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS message_cache (
                message_id INTEGER PRIMARY KEY,
                content TEXT, author_id INTEGER, channel_id INTEGER,
                guild_id INTEGER, timestamp TEXT, attachments TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS snipe_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER, channel_id INTEGER,
                message_id INTEGER, content TEXT, author_id INTEGER,
                timestamp TEXT, deleted_at TEXT, attachments TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS edit_snipe_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER, channel_id INTEGER,
                message_id INTEGER, old_content TEXT, new_content TEXT,
                author_id INTEGER, timestamp TEXT, edited_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS member_message_counts (
                user_id INTEGER, guild_id INTEGER,
                message_count INTEGER DEFAULT 0,
                last_message_time TEXT,
                PRIMARY KEY (user_id, guild_id)
            )""",
            """CREATE TABLE IF NOT EXISTS control_panel (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                message_id INTEGER,
                last_updated TEXT
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
# DATABASE HELPER FUNCTIONS
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

async def clear_warnings(user_id, guild_id):
    await db.execute("DELETE FROM warnings WHERE user_id=? AND guild_id=?", (user_id, guild_id))
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

async def cache_message(message):
    """Cache a message for snipe/delete logging."""
    attachments = json.dumps([{"filename": a.filename, "url": a.url} for a in message.attachments])
    await db.execute(
        """INSERT OR REPLACE INTO message_cache (message_id, content, author_id, channel_id, guild_id, timestamp, attachments)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (message.id, message.content[:2000] if message.content else "", message.author.id,
         message.channel.id, message.guild.id, datetime.now().isoformat(), attachments)
    )
    await db.commit()

async def get_cached_message(message_id):
    result = await db.fetchone(
        "SELECT content, author_id, channel_id, guild_id, timestamp, attachments FROM message_cache WHERE message_id=?",
        (message_id,)
    )
    return result

async def add_snipe(guild_id, channel_id, message_id, content, author_id, attachments=""):
    await db.execute(
        """INSERT INTO snipe_cache (guild_id, channel_id, message_id, content, author_id, timestamp, deleted_at, attachments)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (guild_id, channel_id, message_id, content[:2000] if content else "", author_id,
         datetime.now().isoformat(), datetime.now().isoformat(), attachments)
    )
    await db.commit()
    # Keep only last 100 snipes per guild
    await db.execute(
        "DELETE FROM snipe_cache WHERE id NOT IN (SELECT id FROM snipe_cache WHERE guild_id=? ORDER BY id DESC LIMIT 100)",
        (guild_id,)
    )
    await db.commit()

async def add_edit_snipe(guild_id, channel_id, message_id, old_content, new_content, author_id):
    await db.execute(
        """INSERT INTO edit_snipe_cache (guild_id, channel_id, message_id, old_content, new_content, author_id, timestamp, edited_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (guild_id, channel_id, message_id, old_content[:2000] if old_content else "",
         new_content[:2000] if new_content else "", author_id,
         datetime.now().isoformat(), datetime.now().isoformat())
    )
    await db.commit()
    # Keep only last 100 edit snipes per guild
    await db.execute(
        "DELETE FROM edit_snipe_cache WHERE id NOT IN (SELECT id FROM edit_snipe_cache WHERE guild_id=? ORDER BY id DESC LIMIT 100)",
        (guild_id,)
    )
    await db.commit()

async def get_snipe(guild_id, channel_id):
    result = await db.fetchone(
        """SELECT content, author_id, timestamp, message_id, attachments FROM snipe_cache
           WHERE guild_id=? AND channel_id=? ORDER BY id DESC LIMIT 1""",
        (guild_id, channel_id)
    )
    return result

async def get_edit_snipe(guild_id, channel_id):
    result = await db.fetchone(
        """SELECT old_content, new_content, author_id, timestamp, message_id FROM edit_snipe_cache
           WHERE guild_id=? AND channel_id=? ORDER BY id DESC LIMIT 1""",
        (guild_id, channel_id)
    )
    return result

async def get_mod_logs_config(guild_id):
    result = await db.fetchone("SELECT * FROM mod_logs_config WHERE guild_id=?", (guild_id,))
    if not result:
        return None
    return {
        "channel_id": result[1],
        "enabled": bool(result[2]),
        "log_deletes": bool(result[3]),
        "log_edits": bool(result[4]),
        "log_joins": bool(result[5]),
        "log_leaves": bool(result[6]),
        "log_bans": bool(result[7]),
        "log_kicks": bool(result[8]),
        "log_timeouts": bool(result[9]),
        "log_voice": bool(result[10]),
        "log_roles": bool(result[11])
    }

async def set_mod_logs_config(guild_id, channel_id, **kwargs):
    existing = await get_mod_logs_config(guild_id)
    if existing:
        await db.execute(
            """UPDATE mod_logs_config SET channel_id=?, enabled=?, log_deletes=?, log_edits=?,
               log_joins=?, log_leaves=?, log_bans=?, log_kicks=?, log_timeouts=?, log_voice=?, log_roles=?
               WHERE guild_id=?""",
            (channel_id, kwargs.get("enabled", True), kwargs.get("log_deletes", True),
             kwargs.get("log_edits", True), kwargs.get("log_joins", True),
             kwargs.get("log_leaves", True), kwargs.get("log_bans", True),
             kwargs.get("log_kicks", True), kwargs.get("log_timeouts", True),
             kwargs.get("log_voice", True), kwargs.get("log_roles", True), guild_id)
        )
    else:
        await db.execute(
            """INSERT INTO mod_logs_config (guild_id, channel_id, enabled, log_deletes, log_edits,
               log_joins, log_leaves, log_bans, log_kicks, log_timeouts, log_voice, log_roles)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (guild_id, channel_id, kwargs.get("enabled", True), kwargs.get("log_deletes", True),
             kwargs.get("log_edits", True), kwargs.get("log_joins", True),
             kwargs.get("log_leaves", True), kwargs.get("log_bans", True),
             kwargs.get("log_kicks", True), kwargs.get("log_timeouts", True),
             kwargs.get("log_voice", True), kwargs.get("log_roles", True))
        )
    await db.commit()

async def get_automod_config(guild_id):
    result = await db.fetchone("SELECT * FROM automod_config WHERE guild_id=?", (guild_id,))
    if not result:
        return {
            "anti_spam": False,
            "anti_invite": False,
            "anti_mentions": False,
            "bad_words": False,
            "spam_threshold": 5,
            "spam_window": 5,
            "mention_limit": 5,
            "bad_words_list": ""
        }
    return {
        "anti_spam": bool(result[1]),
        "anti_invite": bool(result[2]),
        "anti_mentions": bool(result[3]),
        "bad_words": bool(result[4]),
        "spam_threshold": result[5],
        "spam_window": result[6],
        "mention_limit": result[7],
        "bad_words_list": result[8] or ""
    }

async def set_automod_config(guild_id, **kwargs):
    existing = await get_automod_config(guild_id)
    if existing:
        await db.execute(
            """UPDATE automod_config SET anti_spam=?, anti_invite=?, anti_mentions=?,
               bad_words=?, spam_threshold=?, spam_window=?, mention_limit=?, bad_words_list=?
               WHERE guild_id=?""",
            (kwargs.get("anti_spam", False), kwargs.get("anti_invite", False),
             kwargs.get("anti_mentions", False), kwargs.get("bad_words", False),
             kwargs.get("spam_threshold", 5), kwargs.get("spam_window", 5),
             kwargs.get("mention_limit", 5), kwargs.get("bad_words_list", ""), guild_id)
        )
    else:
        await db.execute(
            """INSERT INTO automod_config (guild_id, anti_spam, anti_invite, anti_mentions,
               bad_words, spam_threshold, spam_window, mention_limit, bad_words_list)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (guild_id, kwargs.get("anti_spam", False), kwargs.get("anti_invite", False),
             kwargs.get("anti_mentions", False), kwargs.get("bad_words", False),
             kwargs.get("spam_threshold", 5), kwargs.get("spam_window", 5),
             kwargs.get("mention_limit", 5), kwargs.get("bad_words_list", ""))
        )
    await db.commit()

async def increment_message_count(user_id, guild_id):
    await db.execute(
        """INSERT INTO member_message_counts (user_id, guild_id, message_count, last_message_time)
           VALUES (?, ?, 1, ?)
           ON CONFLICT(user_id, guild_id)
           DO UPDATE SET message_count = message_count + 1, last_message_time = excluded.last_message_time""",
        (user_id, guild_id, datetime.now().isoformat())
    )
    await db.commit()

async def get_message_count(user_id, guild_id, window_seconds=5):
    result = await db.fetchone(
        """SELECT message_count FROM member_message_counts
           WHERE user_id=? AND guild_id=? AND last_message_time > datetime(?, '-' || ? || ' seconds')""",
        (user_id, guild_id, datetime.now().isoformat(), window_seconds)
    )
    return result[0] if result else 0

# =========================
# CONTROL PANEL DATABASE FUNCTIONS
# =========================

async def save_control_panel(guild_id: int, channel_id: int, message_id: int):
    """Save control panel configuration to database."""
    await db.execute(
        """INSERT OR REPLACE INTO control_panel (guild_id, channel_id, message_id, last_updated)
           VALUES (?, ?, ?, ?)""",
        (guild_id, channel_id, message_id, datetime.now().isoformat())
    )
    await db.commit()

async def get_control_panel(guild_id: int):
    """Get control panel configuration from database."""
    result = await db.fetchone(
        "SELECT channel_id, message_id, last_updated FROM control_panel WHERE guild_id=?",
        (guild_id,)
    )
    if result:
        return {
            "channel_id": result[0],
            "message_id": result[1],
            "last_updated": result[2]
        }
    return None

async def delete_control_panel(guild_id: int):
    """Delete control panel configuration from database."""
    await db.execute("DELETE FROM control_panel WHERE guild_id=?", (guild_id,))
    await db.commit()

# =========================
# MOD LOGS FUNCTIONS
# =========================

async def log_to_mod_channel(guild, embed):
    """Send an embed to the configured mod-logs channel."""
    config = await get_mod_logs_config(guild.id)
    if not config or not config["enabled"]:
        return
    
    channel = guild.get_channel(config["channel_id"])
    if channel:
        try:
            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Failed to send mod log: {e}")

async def log_event(guild, event_type, title, description, color=discord.Color.blue(), fields=None, thumbnail=None):
    """Create and send a moderation log embed."""
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"Event ID: {event_type} • {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline if inline is not None else False)
    
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    
    await log_to_mod_channel(guild, embed)

# =========================
# AI MEMORY FUNCTIONS
# =========================

async def get_memory(user_id: int) -> Tuple[Optional[str], Optional[str]]:
    """Get user's memory from database."""
    result = await db.fetchone("SELECT user_name, bot_name FROM memory WHERE user_id=?", (user_id,))
    if result:
        return result[0], result[1]
    return None, None

async def save_memory(user_id: int, user_name: str = None, bot_name: str = None):
    """Save or update user's memory."""
    existing = await db.fetchone("SELECT user_name, bot_name FROM memory WHERE user_id=?", (user_id,))
    if existing:
        current_user, current_bot = existing
        new_user = user_name if user_name is not None else current_user
        new_bot = bot_name if bot_name is not None else current_bot
        await db.execute(
            "UPDATE memory SET user_name=?, bot_name=? WHERE user_id=?",
            (new_user, new_bot, user_id)
        )
    else:
        await db.execute(
            "INSERT INTO memory (user_id, user_name, bot_name) VALUES (?, ?, ?)",
            (user_id, user_name or "User", bot_name or "AI Bot")
        )
    await db.commit()

async def get_ai_memories(guild_id: int, user_id: int, limit: int = 20):
    """Get AI memories for a user."""
    return await db.fetchall(
        """SELECT key, value, importance, created_at, last_accessed 
           FROM ai_memories 
           WHERE guild_id=? AND user_id=? 
           ORDER BY importance DESC, last_accessed DESC 
           LIMIT ?""",
        (guild_id, user_id, limit)
    )

async def save_ai_memory(guild_id: int, user_id: int, key: str, value: str, importance: int = 1):
    """Save or update an AI memory."""
    await db.execute(
        """INSERT INTO ai_memories (guild_id, user_id, key, value, importance, created_at, last_accessed)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(guild_id, user_id, key) 
           DO UPDATE SET value=?, importance=?, last_accessed=?""",
        (guild_id, user_id, key, value, importance, datetime.now().isoformat(), datetime.now().isoformat(),
         value, importance, datetime.now().isoformat())
    )
    await db.commit()

async def get_user_personality(guild_id: int, user_id: int):
    """Get user personality traits."""
    return await db.fetchall(
        "SELECT trait, value FROM ai_personality WHERE guild_id=? AND user_id=?",
        (guild_id, user_id)
    )

async def save_user_personality(guild_id: int, user_id: int, trait: str, value: str):
    """Save a personality trait for a user."""
    await db.execute(
        """INSERT INTO ai_personality (guild_id, user_id, trait, value)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(guild_id, user_id, trait)
           DO UPDATE SET value=?""",
        (guild_id, user_id, trait, value, value)
    )
    await db.commit()

async def get_recent_conversation(guild_id: int, channel_id: int, limit: int = 10):
    """Get recent conversation history."""
    return await db.fetchall(
        """SELECT role, content, timestamp, user_id 
           FROM ai_conversations 
           WHERE guild_id=? AND channel_id=? 
           ORDER BY id DESC LIMIT ?""",
        (guild_id, channel_id, limit)
    )

async def save_conversation(guild_id: int, channel_id: int, user_id: int, role: str, content: str):
    """Save a conversation message."""
    await db.execute(
        """INSERT INTO ai_conversations (guild_id, channel_id, user_id, role, content, timestamp)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (guild_id, channel_id, user_id, role, content[:1000], datetime.now().isoformat())
    )
    await db.commit()

async def extract_memory_facts(guild_id: int, user_id: int, content: str):
    """Extract and store memory facts from user messages."""
    # Simple pattern-based memory extraction
    patterns = {
        "name": r"(?:my name is|call me|I'm|i am)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        "age": r"(?:i am|I'm|age)\s+(\d+)\s+(?:years old|yo|y/o)",
        "birthday": r"(?:my birthday is|born on)\s+([A-Z][a-z]+\s+\d{1,2}(?:st|nd|rd|th)?)",
        "location": r"(?:i (?:live|am) (?:in|from)|location is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        "job": r"(?:my job is|i work (?:as|in)|profession|occupation)\s+(?:a|an)?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        "favorite": r"(?:my favorite|i love|i like)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        "preferred_name": r"(?:call me|prefer|preferred name is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
    }
    
    content_lower = content.lower()
    for key, pattern in patterns.items():
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            importance = 2 if key in ["name", "preferred_name"] else 1
            await save_ai_memory(guild_id, user_id, key, value, importance)
            
            # Also save as personality trait
            if key in ["name", "age", "location", "job", "preferred_name"]:
                trait_name = "preferred_name" if key == "name" else key
                await save_user_personality(guild_id, user_id, trait_name, value)

async def get_ai_response(prompt: str) -> str:
    """Get a response from Google Gemini."""
    try:
        if not client:
            return "⚠️ GEMINI_API_KEY is missing. Please set the GEMINI_API_KEY environment variable."

        response = await asyncio.to_thread(
            lambda: client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt
            )
        )

        return response.text.strip()

    except Exception as e:
        logger.error(f"AI response error: {e}")
        return f"⚠️ AI error: {str(e)}"

# =========================
# AI CONTEXT BUILDING
# =========================
async def build_ai_context(
    guild_id: int,
    channel_id: int,
    user_id: int,
    username: str,
    message_content: str,
    conversation_history: List[Dict] = None
) -> Tuple[str, Dict]:
    """Build comprehensive AI context with memory, personality, and history."""
    user_name = username
    bot_name = "AI Bot"
    memories = []
    traits = []
    
    # Get AI memories
    try:
        ai_memories = await get_ai_memories(guild_id, user_id, limit=15)
        for key, value, importance, created_at, last_accessed in ai_memories:
            memories.append({"key": key, "value": value, "importance": importance})
            if key == "preferred_name":
                user_name = value
    except Exception as e:
        logger.error(f"Error getting AI memories: {e}")
    
    # Get personality traits
    try:
        traits_data = await get_user_personality(guild_id, user_id)
        traits = [{"trait": t[0], "value": t[1]} for t in traits_data]
    except Exception as e:
        logger.error(f"Error getting personality: {e}")
    
    # Get level
    level = 0
    try:
        result = await db.fetchone(
            "SELECT level FROM leveling WHERE user_id=? AND guild_id=?",
            (user_id, guild_id)
        )
        if result:
            level = result[0]
    except Exception:
        pass
    
    # Get balance
    try:
        bal_result = await db.fetchone(
            "SELECT balance, bank FROM economy WHERE user_id=? AND guild_id=?",
            (user_id, guild_id)
        )
        if bal_result:
            wallet, bank = bal_result
        else:
            wallet, bank = 0, 0
    except Exception:
        wallet, bank = 0, 0
    
    # Extract memory facts from current message
    try:
        await extract_memory_facts(guild_id, user_id, message_content)
    except Exception as e:
        logger.error(f"Memory extraction error: {e}")
    
    # Build context
    context_parts = [
        f"User: {user_name}",
        f"Bot: {bot_name}",
        f"Level: {level}",
        f"Wallet: ${wallet:,}",
        f"Bank: ${bank:,}"
    ]
    
    # Add personality traits
    if traits:
        trait_str = " | ".join(f"{t['trait']}: {t['value']}" for t in traits[:5])
        context_parts.append(f"Personality: {trait_str}")
    
    # Add important memories
    important_memories = [m for m in memories if m["importance"] >= 2]
    if important_memories:
        memory_str = "; ".join(f"{m['key']}: {m['value']}" for m in important_memories[:5])
        context_parts.append(f"Memories: {memory_str}")
    
    # Add conversation history
    if conversation_history:
        recent = conversation_history[-10:] if len(conversation_history) > 10 else conversation_history
        history_lines = []
        for entry in recent:
            role = "User" if entry.get("role") == "user" else "Bot"
            content = entry.get("content", "")[:200]
            if content:
                history_lines.append(f"{role}: {content}")
        if history_lines:
            context_parts.append("Recent conversation:\n" + "\n".join(history_lines))
    
    context = "\n".join(context_parts)
    
    # Return context and metadata
    metadata = {
        "user_name": user_name,
        "bot_name": bot_name,
        "memories": memories,
        "traits": traits,
        "level": level,
        "wallet": wallet,
        "bank": bank
    }
    
    return context, metadata

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

async def get_balance_simple(user_id: int) -> Tuple[int, int]:
    """Get user's balance from database."""
    result = await db.fetchone(
        "SELECT balance, bank FROM economy WHERE user_id=?",
        (user_id,)
    )
    if result:
        return result[0], result[1]
    await db.execute(
        "INSERT INTO economy (user_id, balance, bank) VALUES (?, 0, 0)",
        (user_id,)
    )
    await db.commit()
    return 0, 0

async def count_events_for_date(guild_id: int, date: str, event_type: str) -> int:
    """Count events of a specific type for a date."""
    result = await db.fetchone(
        "SELECT COUNT(*) FROM history WHERE guild_id=? AND event_type=? AND timestamp LIKE ?",
        (guild_id, event_type, f"{date}%")
    )
    return result[0] if result else 0

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
# CONSTANTS
# =========================
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
# DAILY SUMMARY TASK
# =========================
@tasks.loop(hours=24)
async def generate_daily_summary():
    """Generate and post daily server summary."""
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    for guild in bot.guilds:
        try:
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
            total_msgs = result[0] if result and result[0] else 0

            # Find most active user
            most_active_name = "No one"
            most_active_user_id = 0

            result = await db.fetchone(
                """
                SELECT user_id, count
                FROM message_stats
                WHERE guild_id=? AND date=?
                ORDER BY count DESC
                LIMIT 1
                """,
                (guild.id, yesterday)
            )

            if result:
                most_active_user_id = result[0]
                member = guild.get_member(most_active_user_id)

                if member:
                    most_active_name = member.display_name
                else:
                    most_active_name = f"User ID {most_active_user_id}"

            summary = (
                f"📊 **Daily Summary - {yesterday}**\n"
                f"📝 Total Messages: {total_msgs:,}\n"
                f"👋 Joins: {joins} | 🚪 Leaves: {leaves}\n"
                f"⚠️ Warnings: {warns} | 🗑️ Deleted: {deletes}\n"
                f"👢 Kicks: {kicks} | 🔨 Bans: {bans}\n"
                f"🏆 Most Active: {most_active_name}"
            )

            await db.execute(
                """
                INSERT INTO daily_summaries
                (
                    guild_id,
                    date,
                    summary,
                    total_messages,
                    most_active_user_id,
                    top_topic,
                    generated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild.id,
                    yesterday,
                    summary,
                    total_msgs,
                    most_active_user_id,
                    "",
                    datetime.now().isoformat()
                )
            )

            await db.commit()

            channel = discord.utils.get(guild.text_channels, name="mod-logs")
            if channel and total_msgs > 0:
                await channel.send(summary)

        except Exception as e:
            logger.error(f"Error generating daily summary for {guild.id}: {e}")

# =========================
# CONSOLIDATE MEMORIES TASK
# =========================
@tasks.loop(hours=6)
async def consolidate_memories():
    """Periodically consolidate and clean up AI memories."""
    try:
        # Delete old low-importance memories
        await db.execute(
            """DELETE FROM ai_memories 
               WHERE importance <= 1 
               AND last_accessed < datetime('now', '-30 days')"""
        )
        await db.commit()
        logger.info("🧠 Consolidated AI memories")
    except Exception as e:
        logger.error(f"Memory consolidation error: {e}")

# =========================
# CHECK BIRTHDAYS TASK
# =========================
@tasks.loop(hours=24)
async def check_birthdays():
    """Check for birthdays and send announcements."""
    today = datetime.now().strftime("%m-%d")
    
    for guild in bot.guilds:
        try:
            results = await db.fetchall(
                "SELECT user_id FROM birthdays WHERE guild_id=? AND date LIKE ?",
                (guild.id, f"%{today}%")
            )
            
            for (user_id,) in results:
                member = guild.get_member(user_id)
                if member:
                    channel = discord.utils.get(guild.text_channels, name="general")
                    if not channel:
                        channel = guild.system_channel
                    if channel:
                        await channel.send(f"🎂 Happy Birthday {member.mention}! 🎉")
        except Exception as e:
            logger.error(f"Birthday check error for {guild.id}: {e}")

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
# GIVEAWAY VIEWS
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
# AUTOMODERATION SYSTEM
# =========================

class AutoMod:
    """Handles automoderation features."""
    
    def __init__(self):
        self.user_messages = defaultdict(lambda: defaultdict(list))  # guild_id -> user_id -> list of timestamps

    async def check_message(self, message):
        """Check a message against automod rules."""
        if message.author.bot:
            return
        
        config = await get_automod_config(message.guild.id)
        
        # Anti-spam
        if config["anti_spam"]:
            user_id = message.author.id
            guild_id = message.guild.id
            now = time.time()
            
            # Clean old messages
            self.user_messages[guild_id][user_id] = [
                t for t in self.user_messages[guild_id][user_id]
                if now - t < config["spam_window"]
            ]
            
            self.user_messages[guild_id][user_id].append(now)
            
            if len(self.user_messages[guild_id][user_id]) > config["spam_threshold"]:
                await self.take_action(message, "Spam detected")
                return True
        
        # Anti-invite
        if config["anti_invite"]:
            if re.search(INVITE_REGEX, message.content.lower()):
                await self.take_action(message, "Invite link detected")
                return True
        
        # Anti-mass-mentions
        if config["anti_mentions"]:
            mention_count = len(message.mentions) + len(message.role_mentions)
            if mention_count > config["mention_limit"]:
                await self.take_action(message, f"Mass mention detected ({mention_count} mentions)")
                return True
        
        # Bad words filter
        if config["bad_words"] and config["bad_words_list"]:
            bad_words = [w.strip().lower() for w in config["bad_words_list"].split(",") if w.strip()]
            for word in bad_words:
                if word in message.content.lower():
                    await self.take_action(message, f"Bad word detected: {word}")
                    return True
        
        return False
    
    async def take_action(self, message, reason):
        """Take action on a violating message."""
        try:
            await message.delete()
            await message.channel.send(
                f"<@{message.author.id}> ⚠️ Message removed: {reason}",
                delete_after=5
            )
            await add_warning(message.author.id, message.guild.id, f"AutoMod: {reason}", "AutoMod")
            await add_history(message.guild.id, message.author.id, str(message.author), "AUTO_MOD", reason)
            
            # Log to mod logs
            embed = discord.Embed(
                title="🛡️ AutoMod Action",
                description=f"Action taken against {message.author.mention}",
                color=discord.Color.red(),
                timestamp=datetime.now()
            )
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.add_field(name="Channel", value=message.channel.mention, inline=True)
            embed.add_field(name="Message", value=message.content[:200] if message.content else "[No content]", inline=False)
            await log_to_mod_channel(message.guild, embed)
            
        except Exception as e:
            logger.error(f"AutoMod action error: {e}")

automod = AutoMod()

# =========================
# CONTROL PANEL VIEWS
# =========================

class ControlPanelButton(discord.ui.Button):
    """Custom button for the control panel."""
    
    def __init__(self, label: str, custom_id: str, style: discord.ButtonStyle = discord.ButtonStyle.secondary):
        super().__init__(label=label, style=style, custom_id=custom_id)
    
    async def callback(self, interaction: discord.Interaction):
        """Handle button press."""
        view = self.view
        if not hasattr(view, 'bot'):
            return await interaction.response.send_message("❌ Bot instance not found.", ephemeral=True)
        
        # Check if user is owner
        if interaction.user.id != view.owner_id:
            return await interaction.response.send_message("❌ This control panel is owner-only.", ephemeral=True)
        
        command = self.custom_id
        
        # Handle different commands
        if command == "warn_user":
            await interaction.response.send_message("⚠️ Use `/mod warn` to warn a user.", ephemeral=True)
        elif command == "ban_user":
            await interaction.response.send_message("🔨 Use `/mod ban` to ban a user.", ephemeral=True)
        elif command == "kick_user":
            await interaction.response.send_message("👢 Use `/mod kick` to kick a user.", ephemeral=True)
        elif command == "mute_user":
            await interaction.response.send_message("🔇 Use `/mod mute` to mute a user.", ephemeral=True)
        elif command == "unmute_user":
            await interaction.response.send_message("🔊 Use `/mod unmute` to unmute a user.", ephemeral=True)
        elif command == "timeout_user":
            await interaction.response.send_message("⏰ Use `/mod timeout` to timeout a user.", ephemeral=True)
        elif command == "clear_messages":
            await interaction.response.send_message("🗑️ Use `/mod clear` to clear messages.", ephemeral=True)
        elif command == "lock_channel":
            await interaction.response.send_message("🔒 Use `/mod lock` to lock a channel.", ephemeral=True)
        elif command == "unlock_channel":
            await interaction.response.send_message("🔓 Use `/mod unlock` to unlock a channel.", ephemeral=True)
        elif command == "set_slowmode":
            await interaction.response.send_message("⏱️ Use `/mod slowmode` to set slowmode.", ephemeral=True)
        elif command == "reset_ai":
            await interaction.response.send_message("🧠 AI conversation history reset.", ephemeral=True)
        elif command == "view_memories":
            await handle_view_memories(interaction)
        elif command == "clear_memories":
            await handle_clear_memories(interaction)
        elif command == "chat_history":
            await handle_chat_history(interaction)
        elif command == "show_stats":
            await handle_show_stats(interaction)
        elif command == "refresh_panel":
            await handle_refresh_panel(interaction)
        elif command == "clear_cache":
            await handle_clear_cache(interaction)
        elif command == "db_status":
            await handle_db_status(interaction)
        elif command == "db_optimize":
            await handle_db_optimize(interaction)
        elif command == "db_stats":
            await handle_db_stats(interaction)
        elif command == "toggle_modules":
            await handle_toggle_modules(interaction)
        elif command == "maintenance_mode":
            await handle_maintenance(interaction)
        elif command == "restart_bot":
            await handle_restart_bot(interaction)
        else:
            await interaction.response.send_message(f"⚠️ Unknown command: {command}", ephemeral=True)

class ControlPanelDropdown(discord.ui.Select):
    """Custom dropdown for the control panel."""
    
    def __init__(self, bot_instance, placeholder: str, options: List[discord.SelectOption]):
        super().__init__(placeholder=placeholder, options=options)
        self.bot = bot_instance
        self.owner_id = OWNER_ID
    
    async def callback(self, interaction: discord.Interaction):
        """Handle dropdown selection."""
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("❌ This control panel is owner-only.", ephemeral=True)
        
        value = self.values[0]
        
        # Route to appropriate handler based on dropdown
        if self.placeholder == "Moderation":
            if value == "warn":
                await interaction.response.send_message("⚠️ Use `/mod warn` to warn a user.", ephemeral=True)
            elif value == "ban":
                await interaction.response.send_message("🔨 Use `/mod ban` to ban a user.", ephemeral=True)
            elif value == "kick":
                await interaction.response.send_message("👢 Use `/mod kick` to kick a user.", ephemeral=True)
            elif value == "mute":
                await interaction.response.send_message("🔇 Use `/mod mute` to mute a user.", ephemeral=True)
            elif value == "unmute":
                await interaction.response.send_message("🔊 Use `/mod unmute` to unmute a user.", ephemeral=True)
            elif value == "timeout":
                await interaction.response.send_message("⏰ Use `/mod timeout` to timeout a user.", ephemeral=True)
        elif self.placeholder == "AI Actions":
            if value == "reset":
                await interaction.response.send_message("🧠 AI conversation history reset.", ephemeral=True)
            elif value == "view_memories":
                await handle_view_memories(interaction)
            elif value == "clear_memories":
                await handle_clear_memories(interaction)
            elif value == "chat_history":
                await handle_chat_history(interaction)
        elif self.placeholder == "Quick Actions":
            if value == "stats":
                await handle_show_stats(interaction)
            elif value == "clear_cache":
                await handle_clear_cache(interaction)
            elif value == "db_status":
                await handle_db_status(interaction)
            elif value == "toggle_modules":
                await handle_toggle_modules(interaction)

class ControlPanelView(discord.ui.View):
    """Main control panel view with all interactive components."""
    
    def __init__(self, bot_instance, owner_id: int):
        super().__init__(timeout=None)
        self.bot = bot_instance
        self.owner_id = owner_id
        self._setup_components()
    
    def _setup_components(self):
        """Set up all control panel components."""
        # Row 1: Moderation Tools
        row1 = discord.ui.Row()
        row1.add_item(ControlPanelButton("⚠️ Warn", "warn_user", discord.ButtonStyle.warning))
        row1.add_item(ControlPanelButton("🔨 Ban", "ban_user", discord.ButtonStyle.danger))
        row1.add_item(ControlPanelButton("👢 Kick", "kick_user", discord.ButtonStyle.danger))
        row1.add_item(ControlPanelButton("🔇 Mute", "mute_user", discord.ButtonStyle.secondary))
        
        # Row 2: More Moderation
        row2 = discord.ui.Row()
        row2.add_item(ControlPanelButton("🔊 Unmute", "unmute_user", discord.ButtonStyle.success))
        row2.add_item(ControlPanelButton("⏰ Timeout", "timeout_user", discord.ButtonStyle.warning))
        row2.add_item(ControlPanelButton("🗑️ Clear", "clear_messages", discord.ButtonStyle.primary))
        row2.add_item(ControlPanelButton("🔒 Lock", "lock_channel", discord.ButtonStyle.danger))
        
        # Row 3: Channel Controls
        row3 = discord.ui.Row()
        row3.add_item(ControlPanelButton("🔓 Unlock", "unlock_channel", discord.ButtonStyle.success))
        row3.add_item(ControlPanelButton("⏱️ Slowmode", "set_slowmode", discord.ButtonStyle.secondary))
        row3.add_item(ControlPanelButton("🧠 Reset AI", "reset_ai", discord.ButtonStyle.danger))
        row3.add_item(ControlPanelButton("📚 View Memories", "view_memories", discord.ButtonStyle.primary))
        
        # Row 4: AI & System
        row4 = discord.ui.Row()
        row4.add_item(ControlPanelButton("🧹 Clear Memories", "clear_memories", discord.ButtonStyle.danger))
        row4.add_item(ControlPanelButton("💬 Chat History", "chat_history", discord.ButtonStyle.secondary))
        row4.add_item(ControlPanelButton("📊 Stats", "show_stats", discord.ButtonStyle.primary))
        row4.add_item(ControlPanelButton("🔄 Refresh", "refresh_panel", discord.ButtonStyle.secondary))
        
        # Row 5: Database & Cache
        row5 = discord.ui.Row()
        row5.add_item(ControlPanelButton("🗑️ Clear Cache", "clear_cache", discord.ButtonStyle.danger))
        row5.add_item(ControlPanelButton("💾 DB Status", "db_status", discord.ButtonStyle.primary))
        row5.add_item(ControlPanelButton("🔄 DB Optimize", "db_optimize", discord.ButtonStyle.secondary))
        row5.add_item(ControlPanelButton("📊 DB Stats", "db_stats", discord.ButtonStyle.primary))
        
        # Row 6: System Controls
        row6 = discord.ui.Row()
        row6.add_item(ControlPanelButton("🔄 Toggle Modules", "toggle_modules", discord.ButtonStyle.secondary))
        row6.add_item(ControlPanelButton("🔧 Maintenance", "maintenance_mode", discord.ButtonStyle.danger))
        row6.add_item(ControlPanelButton("🔄 Restart Bot", "restart_bot", discord.ButtonStyle.danger))
        
        # Add all rows
        self.add_row(row1)
        self.add_row(row2)
        self.add_row(row3)
        self.add_row(row4)
        self.add_row(row5)
        self.add_row(row6)
        
        # Add dropdown menus
        dropdown_row = discord.ui.Row()
        dropdown_row.add_item(ControlPanelDropdown(self.bot, "Moderation", [
            discord.SelectOption(label="Warn", value="warn", emoji="⚠️"),
            discord.SelectOption(label="Ban", value="ban", emoji="🔨"),
            discord.SelectOption(label="Kick", value="kick", emoji="👢"),
            discord.SelectOption(label="Mute", value="mute", emoji="🔇"),
            discord.SelectOption(label="Unmute", value="unmute", emoji="🔊"),
            discord.SelectOption(label="Timeout", value="timeout", emoji="⏰"),
        ]))
        
        dropdown_row.add_item(ControlPanelDropdown(self.bot, "AI Actions", [
            discord.SelectOption(label="Reset AI", value="reset", emoji="🧠"),
            discord.SelectOption(label="View Memories", value="view_memories", emoji="📚"),
            discord.SelectOption(label="Clear Memories", value="clear_memories", emoji="🧹"),
            discord.SelectOption(label="Chat History", value="chat_history", emoji="💬"),
        ]))
        
        dropdown_row.add_item(ControlPanelDropdown(self.bot, "Quick Actions", [
            discord.SelectOption(label="Show Stats", value="stats", emoji="📊"),
            discord.SelectOption(label="Clear Cache", value="clear_cache", emoji="🗑️"),
            discord.SelectOption(label="Database Status", value="db_status", emoji="💾"),
            discord.SelectOption(label="Toggle Modules", value="toggle_modules", emoji="🔄"),
        ]))
        
        self.add_row(dropdown_row)

# =========================
# CONTROL PANEL HANDLERS
# =========================

async def handle_view_memories(interaction: discord.Interaction):
    """Handle view memories command."""
    memories = await get_ai_memories(interaction.guild_id, interaction.user.id, limit=20)
    if not memories:
        await interaction.response.send_message("📚 No memories found for you.", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="📚 Your AI Memories",
        description="Here are the things I remember about you:",
        color=discord.Color.blue()
    )
    
    for key, value, importance, created_at, last_accessed in memories[:10]:
        embed.add_field(
            name=f"{key} (Importance: {importance})",
            value=f"{value[:100]}\n*Last accessed: {last_accessed[:10]}*",
            inline=False
        )
    
    if len(memories) > 10:
        embed.set_footer(text=f"Showing 10 of {len(memories)} memories")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

async def handle_clear_memories(interaction: discord.Interaction):
    """Handle clear memories command."""
    await db.execute(
        "DELETE FROM ai_memories WHERE guild_id=? AND user_id=?",
        (interaction.guild_id, interaction.user.id)
    )
    await db.commit()
    await interaction.response.send_message("🧹 All your AI memories have been cleared.", ephemeral=True)

async def handle_chat_history(interaction: discord.Interaction):
    """Handle chat history command."""
    history = await get_recent_conversation(interaction.guild_id, interaction.channel_id, limit=10)
    if not history:
        await interaction.response.send_message("💬 No recent chat history in this channel.", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="💬 Recent Chat History",
        color=discord.Color.blue()
    )
    
    for role, content, timestamp, user_id in history[:10]:
        user = interaction.guild.get_member(user_id)
        name = user.display_name if user else f"User {user_id}"
        embed.add_field(
            name=f"{name} ({role})",
            value=f"{content[:200]}...",
            inline=False
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

async def handle_show_stats(interaction: discord.Interaction):
    """Handle show stats command."""
    # Get bot stats
    total_members = sum(guild.member_count for guild in bot.guilds)
    total_guilds = len(bot.guilds)
    total_commands = len(bot.tree.get_commands())
    uptime = datetime.now() - START_TIME
    
    # Get database stats
    warnings_count = await db.fetchone("SELECT COUNT(*) FROM warnings")
    warnings_count = warnings_count[0] if warnings_count else 0
    
    memories_count = await db.fetchone("SELECT COUNT(*) FROM ai_memories")
    memories_count = memories_count[0] if memories_count else 0
    
    economy_count = await db.fetchone("SELECT COUNT(*) FROM economy")
    economy_count = economy_count[0] if economy_count else 0
    
    embed = discord.Embed(
        title="📊 Bot Statistics",
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )
    embed.add_field(name="🤖 Bot Name", value=bot.user.name, inline=True)
    embed.add_field(name="🆔 Bot ID", value=bot.user.id, inline=True)
    embed.add_field(name="📈 Guilds", value=total_guilds, inline=True)
    embed.add_field(name="👥 Total Members", value=total_members, inline=True)
    embed.add_field(name="⚙️ Commands", value=total_commands, inline=True)
    embed.add_field(name="⏱️ Uptime", value=str(uptime).split('.')[0], inline=True)
    embed.add_field(name="⚠️ Warnings", value=warnings_count, inline=True)
    embed.add_field(name="🧠 AI Memories", value=memories_count, inline=True)
    embed.add_field(name="💰 Economy Users", value=economy_count, inline=True)
    embed.set_footer(text=f"Owner: <@{OWNER_ID}>")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

async def handle_refresh_panel(interaction: discord.Interaction):
    """Handle refresh panel command."""
    config = await get_control_panel(interaction.guild_id)
    if not config:
        await interaction.response.send_message("❌ No control panel configured in this server.", ephemeral=True)
        return
    
    channel = interaction.guild.get_channel(config["channel_id"])
    if not channel:
        await interaction.response.send_message("❌ Configured channel not found.", ephemeral=True)
        return
    
    try:
        old_message = await channel.fetch_message(config["message_id"])
        await old_message.delete()
    except:
        pass
    
    # Create new panel
    embed = discord.Embed(
        title="🎛️ Bot Control Panel",
        description="Welcome to the bot control panel! Use the buttons and dropdowns below to manage the bot.",
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )
    embed.add_field(name="🤖 Status", value="Bot is online and ready", inline=True)
    embed.add_field(name="👑 Owner", value=f"<@{OWNER_ID}>", inline=True)
    embed.add_field(name="📊 Commands", value=f"{len(bot.tree.get_commands())} total", inline=True)
    embed.set_footer(text="Control Panel • Owner Only")
    
    view = ControlPanelView(bot, OWNER_ID)
    new_message = await channel.send(embed=embed, view=view)
    
    await save_control_panel(interaction.guild_id, channel.id, new_message.id)
    await interaction.response.send_message("✅ Control panel refreshed!", ephemeral=True)

async def handle_clear_cache(interaction: discord.Interaction):
    """Handle clear cache command."""
    await db.execute("DELETE FROM message_cache")
    await db.execute("DELETE FROM snipe_cache")
    await db.execute("DELETE FROM edit_snipe_cache")
    await db.commit()
    
    await interaction.response.send_message("🗑️ All caches cleared successfully!", ephemeral=True)

async def handle_db_status(interaction: discord.Interaction):
    """Handle database status command."""
    try:
        # Check if database is accessible
        result = await db.fetchone("SELECT sqlite_version()")
        version = result[0] if result else "Unknown"
        
        # Get table counts
        tables = ["warnings", "ai_memories", "economy", "leveling", "tickets", "giveaways", "polls"]
        table_stats = {}
        
        for table in tables:
            count = await db.fetchone(f"SELECT COUNT(*) FROM {table}")
            table_stats[table] = count[0] if count else 0
        
        embed = discord.Embed(
            title="💾 Database Status",
            color=discord.Color.green(),
            timestamp=datetime.now()
        )
        embed.add_field(name="🔌 Connection", value="✅ Connected", inline=True)
        embed.add_field(name="📦 SQLite Version", value=version, inline=True)
        embed.add_field(name="📊 Tables", value=len(tables), inline=True)
        
        stats_text = "\n".join([f"**{table}**: {count}" for table, count in table_stats.items()])
        embed.add_field(name="📈 Table Statistics", value=stats_text, inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Database error: {str(e)}", ephemeral=True)

async def handle_db_optimize(interaction: discord.Interaction):
    """Handle database optimize command."""
    await interaction.response.send_message("🔄 Optimizing database...", ephemeral=True)
    
    try:
        await db.execute("VACUUM")
        await db.execute("ANALYZE")
        await db.commit()
        
        await interaction.edit_original_response(content="✅ Database optimized successfully!")
    except Exception as e:
        await interaction.edit_original_response(content=f"❌ Optimization failed: {str(e)}")

async def handle_db_stats(interaction: discord.Interaction):
    """Handle database statistics command."""
    try:
        # Get database file size
        size = os.path.getsize("moderation.db")
        size_mb = size / (1024 * 1024)
        
        # Get more detailed stats
        embed = discord.Embed(
            title="📊 Database Statistics",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        embed.add_field(name="📦 Database Size", value=f"{size_mb:.2f} MB", inline=True)
        embed.add_field(name="📂 Database File", value="moderation.db", inline=True)
        embed.add_field(name="🔢 Total Tables", value="20+", inline=True)
        
        # Get memory usage stats
        memory_usage = await db.fetchone("PRAGMA memory_used")
        if memory_usage:
            embed.add_field(name="🧠 Memory Used", value=f"{memory_usage[0] / 1024:.2f} KB", inline=True)
        
        # Get cache stats
        cache_size = await db.fetchone("PRAGMA cache_size")
        if cache_size:
            embed.add_field(name="💾 Cache Size", value=f"{cache_size[0]} pages", inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Error getting stats: {str(e)}", ephemeral=True)

async def handle_toggle_modules(interaction: discord.Interaction):
    """Handle toggle modules command."""
    # Get all cog names
    cogs = list(bot.cogs.keys())
    
    embed = discord.Embed(
        title="🔄 Module Management",
        description="Click the buttons below to toggle modules on/off. (Coming soon!)",
        color=discord.Color.blue()
    )
    
    # Show current modules
    module_status = ""
    for cog in cogs:
        status = "✅"  # All modules are enabled by default
        module_status += f"{status} {cog}\n"
    
    embed.add_field(name="📦 Loaded Modules", value=module_status or "No modules loaded", inline=False)
    embed.add_field(name="ℹ️ Note", value="Module toggling will be available in a future update. For now, all modules are active.", inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

async def handle_maintenance(interaction: discord.Interaction):
    """Handle maintenance mode command."""
    await interaction.response.send_message(
        "🔧 Maintenance mode is not implemented yet.\n"
        "To restart the bot, use the Restart Bot button or redeploy on Railway.",
        ephemeral=True
    )

async def handle_restart_bot(interaction: discord.Interaction):
    """Handle restart bot command."""
    embed = discord.Embed(
        title="🔄 Restarting Bot",
        description="The bot is restarting... This may take a few seconds.",
        color=discord.Color.orange()
    )
    await interaction.response.send_message(embed=embed)
    
    # Save control panel before restart
    config = await get_control_panel(interaction.guild_id)
    if config:
        channel = interaction.guild.get_channel(config["channel_id"])
        if channel:
            try:
                old_message = await channel.fetch_message(config["message_id"])
                # Keep the message, just update it
                embed = discord.Embed(
                    title="🔄 Bot Restarting...",
                    description="The bot is currently restarting. Please wait a moment.",
                    color=discord.Color.orange(),
                    timestamp=datetime.now()
                )
                await old_message.edit(embed=embed, view=None)
            except:
                pass
    
    # Restart the bot
    os._exit(0)

# =========================
# BOT EVENTS
# =========================

@bot.event
async def on_member_join(member):
    try:
        await add_history(member.guild.id, member.id, str(member), "JOIN", "Joined the server")
        
        # Log to mod channel
        embed = discord.Embed(
            title="👋 Member Joined",
            description=f"{member.mention} has joined the server",
            color=discord.Color.green(),
            timestamp=datetime.now()
        )
        embed.add_field(name="User", value=str(member), inline=True)
        embed.add_field(name="ID", value=member.id, inline=True)
        embed.add_field(name="Account Created", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
        if member.avatar:
            embed.set_thumbnail(url=member.avatar.url)
        await log_to_mod_channel(member.guild, embed)
        
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
        
        # Log to mod channel
        embed = discord.Embed(
            title="👋 Member Left",
            description=f"{member.mention} has left the server",
            color=discord.Color.orange(),
            timestamp=datetime.now()
        )
        embed.add_field(name="User", value=str(member), inline=True)
        embed.add_field(name="ID", value=member.id, inline=True)
        if member.avatar:
            embed.set_thumbnail(url=member.avatar.url)
        await log_to_mod_channel(member.guild, embed)
        
    except Exception as e:
        logger.error(f"on_member_remove error: {e}")

@bot.event
async def on_message_delete(message):
    if message.author.bot:
        return
    
    try:
        # Cache the message for snipe
        await cache_message(message)
        
        # Add to snipe cache
        attachments = json.dumps([{"filename": a.filename, "url": a.url} for a in message.attachments])
        await add_snipe(
            message.guild.id, message.channel.id, message.id,
            message.content or "[No content]", message.author.id, attachments
        )
        
        await add_history(
            message.guild.id,
            message.author.id,
            str(message.author),
            "DELETE",
            message.content[:500] if message.content else "[Attachment/Embed]"
        )
        
        # Log deletion to mod channel
        config = await get_mod_logs_config(message.guild.id)
        if config and config["enabled"] and config["log_deletes"]:
            embed = discord.Embed(
                title="🗑️ Message Deleted",
                color=discord.Color.red(),
                timestamp=datetime.now()
            )
            embed.add_field(name="Author", value=f"{message.author.mention} ({message.author})", inline=True)
            embed.add_field(name="Channel", value=message.channel.mention, inline=True)
            embed.add_field(name="Message ID", value=message.id, inline=True)
            
            if message.content:
                embed.add_field(name="Content", value=message.content[:1000] or "[Empty]", inline=False)
            elif message.attachments:
                embed.add_field(name="Attachments", value=", ".join([a.filename for a in message.attachments[:5]]), inline=False)
            else:
                embed.add_field(name="Content", value="[No content]", inline=False)
            
            await log_to_mod_channel(message.guild, embed)
            
    except Exception as e:
        logger.error(f"on_message_delete error: {e}")

@bot.event
async def on_message_edit(before, after):
    if before.author.bot:
        return
    if before.content == after.content:
        return
    
    try:
        # Add to edit snipe cache
        await add_edit_snipe(
            before.guild.id, before.channel.id, before.id,
            before.content or "", after.content or "", before.author.id
        )
        
        await add_history(
            before.guild.id,
            before.author.id,
            str(before.author),
            "EDIT",
            f"{before.content[:200]} -> {after.content[:200]}"
        )
        
        # Log edit to mod channel
        config = await get_mod_logs_config(before.guild.id)
        if config and config["enabled"] and config["log_edits"]:
            embed = discord.Embed(
                title="📝 Message Edited",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            embed.add_field(name="Author", value=f"{before.author.mention} ({before.author})", inline=True)
            embed.add_field(name="Channel", value=before.channel.mention, inline=True)
            embed.add_field(name="Message ID", value=before.id, inline=True)
            embed.add_field(name="Before", value=before.content[:500] or "[Empty]", inline=False)
            embed.add_field(name="After", value=after.content[:500] or "[Empty]", inline=False)
            
            await log_to_mod_channel(before.guild, embed)
            
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
                
                # Log to mod channel
                config = await get_mod_logs_config(member.guild.id)
                if config and config["enabled"] and config["log_voice"]:
                    embed = discord.Embed(
                        title="🔊 Voice Join",
                        description=f"{member.mention} joined voice channel",
                        color=discord.Color.green(),
                        timestamp=datetime.now()
                    )
                    embed.add_field(name="Channel", value=after.channel.mention, inline=True)
                    embed.add_field(name="User", value=str(member), inline=True)
                    await log_to_mod_channel(member.guild, embed)
                    
            elif before.channel:
                await add_history(member.guild.id, member.id, str(member), "VOICE_LEAVE", f"Left {before.channel.name}")
                
                # Log to mod channel
                config = await get_mod_logs_config(member.guild.id)
                if config and config["enabled"] and config["log_voice"]:
                    embed = discord.Embed(
                        title="🔇 Voice Leave",
                        description=f"{member.mention} left voice channel",
                        color=discord.Color.orange(),
                        timestamp=datetime.now()
                    )
                    embed.add_field(name="Channel", value=before.channel.mention, inline=True)
                    embed.add_field(name="User", value=str(member), inline=True)
                    await log_to_mod_channel(member.guild, embed)
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
            
            # Log to mod channel
            config = await get_mod_logs_config(after.guild.id)
            if config and config["enabled"] and config["log_roles"]:
                embed = discord.Embed(
                    title="📝 Nickname Changed",
                    color=discord.Color.blue(),
                    timestamp=datetime.now()
                )
                embed.add_field(name="User", value=f"{after.mention} ({after})", inline=True)
                embed.add_field(name="Before", value=before.nick or before.name, inline=True)
                embed.add_field(name="After", value=after.nick or after.name, inline=True)
                await log_to_mod_channel(after.guild, embed)
        
        added_roles = set(after.roles) - set(before.roles)
        removed_roles = set(before.roles) - set(after.roles)
        
        for role in added_roles:
            if role.name != "@everyone":
                await add_history(after.guild.id, after.id, str(after), "ROLE_ADD", f"Role added: {role.name}")
                
                # Log to mod channel
                config = await get_mod_logs_config(after.guild.id)
                if config and config["enabled"] and config["log_roles"]:
                    embed = discord.Embed(
                        title="🎭 Role Added",
                        color=discord.Color.green(),
                        timestamp=datetime.now()
                    )
                    embed.add_field(name="User", value=f"{after.mention} ({after})", inline=True)
                    embed.add_field(name="Role", value=role.mention, inline=True)
                    await log_to_mod_channel(after.guild, embed)
        
        for role in removed_roles:
            if role.name != "@everyone":
                await add_history(after.guild.id, after.id, str(after), "ROLE_REMOVE", f"Role removed: {role.name}")
                
                # Log to mod channel
                config = await get_mod_logs_config(after.guild.id)
                if config and config["enabled"] and config["log_roles"]:
                    embed = discord.Embed(
                        title="🎭 Role Removed",
                        color=discord.Color.orange(),
                        timestamp=datetime.now()
                    )
                    embed.add_field(name="User", value=f"{after.mention} ({after})", inline=True)
                    embed.add_field(name="Role", value=role.mention, inline=True)
                    await log_to_mod_channel(after.guild, embed)
    except Exception as e:
        logger.error(f"on_member_update error: {e}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    try:
        if message.guild:
            await log_message(message.guild.id, message.channel.id, message.author.id, message.content)
            await increment_message_count(message.author.id, message.guild.id)
            
            # Run automod checks
            await automod.check_message(message)
        
        # =========================
        # DM AI
        # =========================
        if isinstance(message.channel, discord.DMChannel):
            await handle_dm_message(message)
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

async def handle_dm_message(message):
    """Handle DM messages with AI."""
    if message.author.id != OWNER_ID:
        await message.channel.send("👋 Only the owner can use AI in DMs.")
        return
    
    user_id = message.author.id
    
    # Get or create memory
    mem = await get_memory(user_id)
    if not mem or not mem[0]:
        await save_memory(user_id, user_name=message.author.name, bot_name="AI Bot")
    
    user_name, bot_name = await get_memory(user_id) or ("User", "AI Bot")
    
    # Extract memory facts
    await extract_memory_facts(0, user_id, message.content)
    
    # Handle name changes
    content_lower = message.content.lower()
    name_match = re.search(r"my name is (.+)", content_lower)
    if name_match:
        await save_memory(user_id, user_name=name_match.group(1).strip().title())
        user_name = name_match.group(1).strip().title()
    
    bot_name_match = re.search(r"your name is (.+)", content_lower)
    if bot_name_match:
        await save_memory(user_id, bot_name=bot_name_match.group(1).strip().title())
        bot_name = bot_name_match.group(1).strip().title()
    
    # Get AI memories
    memories = await get_ai_memories(0, user_id, limit=15)
    
    # Build prompt
    memory_lines = []
    if memories:
        for key, value, importance, _, _ in memories:
            if importance >= 2:
                memory_lines.append(f"- {key}: {value}")
    
    memory_context = ""
    if memory_lines:
        memory_context = "Things I remember:\n" + "\n".join(memory_lines) + "\n"
    
    prompt = f"""You are a Discord AI bot with persistent memory.

User: {user_name}
Bot: {bot_name}

{memory_context}

Respond naturally and conversationally. If the user mentions something new, remember it."""
    
    try:
        # Handle attachments
        if message.attachments:
            reply = await handle_dm_attachment(message, prompt)
        else:
            reply = await get_ai_response(prompt)
        
        await save_conversation(0, 0, user_id, "user", message.content)
        await save_conversation(0, 0, user_id, "assistant", reply)
        
        # Send reply in chunks
        while len(reply) > 1900:
            await message.channel.send(reply[:1900])
            reply = reply[1900:]
        await message.channel.send(reply)
        
    except Exception as e:
        logger.error(f"DM AI error: {e}")
        await message.channel.send(f"⚠️ AI error: {str(e)}")

async def handle_dm_attachment(message, prompt):
    """Handle attachments in DMs."""
    attachment = message.attachments[0]
    
    # Image analysis with Gemini
    if attachment.content_type and attachment.content_type.startswith("image/"):
        if client:
            img = await attachment.read()
            uploaded = client.files.upload(file=img, config={"mime_type": attachment.content_type})
            response = client.models.generate_content(
                model=MODEL_NAME, 
                contents=[prompt, uploaded]
            )
            return response.text
        else:
            return "⚠️ GEMINI_API_KEY is missing. Please set the GEMINI_API_KEY environment variable."
    
    # PDF handling
    elif attachment.filename.endswith(".pdf"):
        pdf_data = await attachment.read()
        pdf = PdfReader(io.BytesIO(pdf_data))
        text = ""
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"
        return await get_ai_response(f"{prompt}\n\nPDF content:\n{text[:3000]}")
    
    # DOCX handling
    elif attachment.filename.endswith(".docx"):
        doc_data = await attachment.read()
        doc = Document(io.BytesIO(doc_data))
        text = "\n".join(p.text for p in doc.paragraphs)
        return await get_ai_response(f"{prompt}\n\nDOCX content:\n{text[:3000]}")
    
    # Text file handling
    elif attachment.filename.endswith(".txt"):
        txt = await attachment.read()
        text = txt.decode("utf-8", errors="ignore")
        return await get_ai_response(f"{prompt}\n\nTXT content:\n{text[:3000]}")
    
    # Default
    else:
        return await get_ai_response(f"{prompt}\n\nThe user sent a file: {attachment.filename}")

@bot.event
async def on_ready():
    global _tasks_started

    if _tasks_started:
        return

    _tasks_started = True

    logger.info(f"🤖 Logged in as {bot.user}")

    try:
        if not generate_daily_summary.is_running():
            generate_daily_summary.start()
            logger.info("✅ Started daily summary task")

        if not consolidate_memories.is_running():
            consolidate_memories.start()
            logger.info("✅ Started memory consolidation task")

        if not check_birthdays.is_running():
            check_birthdays.start()
            logger.info("✅ Started birthday check task")

    except Exception as e:
        logger.error(f"Task startup error: {e}")

    logger.info("✅ Bot is fully online and tasks are running!")
    logger.info(f"   Servers: {len(bot.guilds)}")
    logger.info(f"   Commands: {len(bot.tree.get_commands())}")

# =========================
# CONTROL PANEL RESTORATION
# =========================

@bot.command(name="restore_panel")
@commands.is_owner()
async def restore_panel_command(ctx):
    """Manually restore the control panel."""
    await ctx.send("🔄 Attempting to restore control panel...")

    if hasattr(bot, "restore_control_panel"):
        await bot.restore_control_panel()
        await ctx.send("✅ Control panel restoration complete!")
    else:
        await ctx.send("❌ restore_control_panel() method not found in bot class.")


# Put this INSIDE your MyBot class
class MyBot(commands.Bot):

    async def setup_hook(self):
        # Your existing setup code here

        # Restore control panel on startup
        await self.restore_control_panel()

    async def restore_control_panel(self):
        """Restore control panel after bot restart."""
        try:
            results = await db.fetchall(
                "SELECT guild_id, channel_id, message_id FROM control_panel"
            )

            for guild_id, channel_id, message_id in results:

                guild = self.get_guild(guild_id)
                if guild is None:
                    continue

                channel = guild.get_channel(channel_id)
                if channel is None:
                    continue

                embed = discord.Embed(
                    title="🎛️ Bot Control Panel",
                    description="Owner-only bot control panel.",
                    color=discord.Color.blue(),
                    timestamp=datetime.utcnow()
                )

                embed.add_field(
                    name="🤖 Status",
                    value="🟢 Online",
                    inline=True
                )

                embed.add_field(
                    name="👑 Owner",
                    value=f"<@{OWNER_ID}>",
                    inline=True
                )

                embed.add_field(
                    name="📊 Commands",
                    value=str(len(self.tree.get_commands())),
                    inline=True
                )

                embed.set_footer(
                    text="Control Panel • Owner Only"
                )

                view = ControlPanelView(self, OWNER_ID)

                try:
                    message = await channel.fetch_message(message_id)

                    await message.edit(
                        embed=embed,
                        view=view
                    )

                    logger.info(
                        f"✅ Restored control panel in guild {guild_id}"
                    )

                except discord.NotFound:
                    new_message = await channel.send(
                        embed=embed,
                        view=view
                    )

                    await save_control_panel(
                        guild_id,
                        channel_id,
                        new_message.id
                    )

                    logger.info(
                        f"✅ Recreated control panel in guild {guild_id}"
                    )

        except Exception as e:
            logger.error(
                f"Control panel restoration failed: {e}"
            )
            
# =========================
# 🛡️ MODERATION COG - ACTIONS
# =========================

class ModerationActions(commands.Cog, name="moderation_actions"):
    """Moderation action commands (ban, kick, mute, warn, etc.)."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    mod_group = app_commands.Group(name="mod", description="Moderation actions - Owner Only")
    
    @mod_group.command(name="clear", description="Delete messages")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def mod_clear(self, interaction: discord.Interaction, amount: int):
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(limit=amount)
            await interaction.followup.send(f"🧹 Deleted {len(deleted)} messages", ephemeral=True)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "CLEAR", f"Cleared {len(deleted)} messages in #{interaction.channel.name}")
            
            embed = discord.Embed(
                title="🧹 Channel Cleared",
                description=f"{len(deleted)} messages deleted in {interaction.channel.mention}",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await log_to_mod_channel(interaction.guild, embed)
        except Exception as e:
            logger.error(f"Clear error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @mod_group.command(name="thanos_snap", description="Boom Bazooka, Channel Gone")
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
            await interaction.followup.send(f"🧹 Cleared {total} messages", ephemeral=True)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "CLEARALL", f"Cleared {total} messages in #{interaction.channel.name}")
            
            embed = discord.Embed(
                title="🧹 Channel Wiped",
                description=f"All messages deleted in {interaction.channel.mention}",
                color=discord.Color.red(),
                timestamp=datetime.now()
            )
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            embed.add_field(name="Total Deleted", value=str(total), inline=True)
            await log_to_mod_channel(interaction.guild, embed)
        except Exception as e:
            logger.error(f"Clearall error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @mod_group.command(name="ban", description="Ban member")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(ban_members=True)
    async def mod_ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner.id:
                return await interaction.followup.send("❌ You cannot ban this member (role hierarchy).", ephemeral=True)
            
            await member.ban(reason=reason)
            await add_history(interaction.guild.id, member.id, str(member), "BAN", f"Banned by {interaction.user}: {reason}")
            
            embed = discord.Embed(title="🔨 User Banned", color=discord.Color.red())
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            embed.add_field(name="Reason", value=reason, inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            log_embed = discord.Embed(
                title="🔨 Member Banned",
                color=discord.Color.red(),
                timestamp=datetime.now()
            )
            log_embed.add_field(name="User", value=f"{member.mention} ({member})", inline=True)
            log_embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Reason", value=reason, inline=False)
            if member.avatar:
                log_embed.set_thumbnail(url=member.avatar.url)
            await log_to_mod_channel(interaction.guild, log_embed)
            
        except Exception as e:
            logger.error(f"Ban error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @mod_group.command(name="softban", description="Ban and unban user")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(ban_members=True)
    async def mod_softban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "Softban"):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner.id:
                return await interaction.followup.send("❌ You cannot softban this member (role hierarchy).", ephemeral=True)
            
            await member.ban(reason=reason)
            await asyncio.sleep(1)
            await interaction.guild.unban(member, reason="Softban complete")
            await add_history(interaction.guild.id, member.id, str(member), "SOFTBAN", f"Softbanned by {interaction.user}: {reason}")
            
            embed = discord.Embed(title="🧹 User Softbanned", color=discord.Color.orange())
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            embed.add_field(name="Reason", value=reason, inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            log_embed = discord.Embed(
                title="🧹 Member Softbanned",
                color=discord.Color.orange(),
                timestamp=datetime.now()
            )
            log_embed.add_field(name="User", value=f"{member.mention} ({member})", inline=True)
            log_embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Reason", value=reason, inline=False)
            await log_to_mod_channel(interaction.guild, log_embed)
            
        except Exception as e:
            logger.error(f"Softban error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @mod_group.command(name="unban", description="Unban a user by ID")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(ban_members=True)
    async def mod_unban(self, interaction: discord.Interaction, user_id: str, reason: str = "No reason"):
        await interaction.response.defer(ephemeral=True)
        try:
            user = await self.bot.fetch_user(int(user_id))
            await interaction.guild.unban(user, reason=reason)
            await add_history(interaction.guild.id, int(user_id), str(user), "UNBAN", f"Unbanned by {interaction.user}: {reason}")
            
            embed = discord.Embed(title="🔓 User Unbanned", color=discord.Color.green())
            embed.add_field(name="User", value=user.mention if user else user_id, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            embed.add_field(name="Reason", value=reason, inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            log_embed = discord.Embed(
                title="🔓 Member Unbanned",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            log_embed.add_field(name="User", value=str(user), inline=True)
            log_embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Reason", value=reason, inline=False)
            await log_to_mod_channel(interaction.guild, log_embed)
            
        except ValueError:
            await interaction.followup.send("❌ Invalid user ID.", ephemeral=True)
        except discord.NotFound:
            await interaction.followup.send("❌ User not found or not banned.", ephemeral=True)
        except Exception as e:
            logger.error(f"Unban error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @mod_group.command(name="mute", description="Timeout member")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mod_mute(self, interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "No reason"):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner.id:
                return await interaction.followup.send("❌ You cannot mute this member (role hierarchy).", ephemeral=True)
            
            await member.timeout(utcnow() + timedelta(minutes=minutes), reason=reason)
            await add_history(interaction.guild.id, member.id, str(member), "MUTE", f"Muted for {minutes}min by {interaction.user}: {reason}")
            
            embed = discord.Embed(title="🔇 User Muted", color=discord.Color.orange())
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            embed.add_field(name="Duration", value=f"{minutes} minutes", inline=True)
            embed.add_field(name="Reason", value=reason, inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            log_embed = discord.Embed(
                title="🔇 Member Timed Out",
                color=discord.Color.orange(),
                timestamp=datetime.now()
            )
            log_embed.add_field(name="User", value=f"{member.mention} ({member})", inline=True)
            log_embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Duration", value=f"{minutes} minutes", inline=True)
            log_embed.add_field(name="Reason", value=reason, inline=False)
            await log_to_mod_channel(interaction.guild, log_embed)
            
        except Exception as e:
            logger.error(f"Mute error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @mod_group.command(name="unmute", description="Remove timeout")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mod_unmute(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        try:
            await member.timeout(None)
            await add_history(interaction.guild.id, member.id, str(member), "UNMUTE", f"Unmuted by {interaction.user}")
            
            embed = discord.Embed(title="🔊 User Unmuted", color=discord.Color.green())
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            log_embed = discord.Embed(
                title="🔊 Member Unmuted",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            log_embed.add_field(name="User", value=f"{member.mention} ({member})", inline=True)
            log_embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await log_to_mod_channel(interaction.guild, log_embed)
            
        except Exception as e:
            logger.error(f"Unmute error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @mod_group.command(name="kick", description="Kick a member")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(kick_members=True)
    async def mod_kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner.id:
                return await interaction.followup.send("❌ You cannot kick this member (role hierarchy).", ephemeral=True)
            
            await member.kick(reason=reason)
            await add_history(interaction.guild.id, member.id, str(member), "KICK", reason)
            
            embed = discord.Embed(title="👢 User Kicked", color=discord.Color.red())
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            embed.add_field(name="Reason", value=reason, inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            log_embed = discord.Embed(
                title="👢 Member Kicked",
                color=discord.Color.red(),
                timestamp=datetime.now()
            )
            log_embed.add_field(name="User", value=f"{member.mention} ({member})", inline=True)
            log_embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Reason", value=reason, inline=False)
            await log_to_mod_channel(interaction.guild, log_embed)
            
        except Exception as e:
            logger.error(f"Kick error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @mod_group.command(name="warn", description="Warn a member")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mod_warn(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner.id:
                return await interaction.followup.send("❌ You cannot warn this member (role hierarchy).", ephemeral=True)
            
            await add_warning(member.id, interaction.guild.id, reason, str(interaction.user))
            await add_history(interaction.guild.id, member.id, str(member), "WARN", reason)
            
            try:
                dm_embed = discord.Embed(title=f"You were warned in {interaction.guild.name}", description=f"Reason: {reason}", color=discord.Color.orange())
                await member.send(embed=dm_embed)
            except:
                pass
            
            embed = discord.Embed(title="⚠️ User Warned", color=discord.Color.orange())
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            embed.add_field(name="Reason", value=reason, inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            log_embed = discord.Embed(
                title="⚠️ Member Warned",
                color=discord.Color.orange(),
                timestamp=datetime.now()
            )
            log_embed.add_field(name="User", value=f"{member.mention} ({member})", inline=True)
            log_embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Reason", value=reason, inline=False)
            await log_to_mod_channel(interaction.guild, log_embed)
            
        except Exception as e:
            logger.error(f"Warn error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @mod_group.command(name="warnings", description="View a member's warnings")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mod_warnings(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        try:
            warnings = await get_warnings(member.id, interaction.guild.id)
            if not warnings:
                embed = discord.Embed(title=f"📋 Warnings for {member.display_name}", description="No warnings found.", color=discord.Color.green())
                return await interaction.followup.send(embed=embed, ephemeral=True)
            
            embed = discord.Embed(title=f"📋 Warnings for {member.display_name}", color=discord.Color.orange())
            embed.set_footer(text=f"Total: {len(warnings)} warnings")
            for i, (wid, reason, timestamp) in enumerate(warnings[:10], 1):
                embed.add_field(name=f"#{i}", value=f"Reason: {reason}\nTime: {timestamp}", inline=False)
            if len(warnings) > 10:
                embed.add_field(name="...", value=f"And {len(warnings) - 10} more warnings.", inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"Warnings error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @mod_group.command(name="clean", description="Delete messages (optional user filter)")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def mod_clean(self, interaction: discord.Interaction, amount: int, member: discord.Member = None):
        await interaction.response.defer(ephemeral=True)
        try:
            amount = min(amount, 100)
            def check(msg):
                return True if member is None else msg.author.id == member.id
            deleted = await interaction.channel.purge(limit=amount, check=check)
            
            embed = discord.Embed(title="🧹 Messages Deleted", color=discord.Color.green())
            embed.add_field(name="Amount", value=str(len(deleted)), inline=True)
            if member:
                embed.add_field(name="Filter", value=member.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "CLEAN", f"Cleaned {len(deleted)} messages in #{interaction.channel.name}")
            
        except Exception as e:
            logger.error(f"Clean error: {e}")
            await interaction.followup.send("❌ Failed to delete messages.", ephemeral=True)

    @mod_group.command(name="lock", description="Lock a channel")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def mod_lock(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        await interaction.response.defer(ephemeral=True)
        try:
            channel = channel or interaction.channel
            overwrite = channel.overwrites_for(interaction.guild.default_role)
            overwrite.send_messages = False
            await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "LOCK", f"Locked #{channel.name}")
            
            embed = discord.Embed(title="🔒 Channel Locked", color=discord.Color.red())
            embed.add_field(name="Channel", value=channel.mention, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=False)
            
            log_embed = discord.Embed(
                title="🔒 Channel Locked",
                color=discord.Color.red(),
                timestamp=datetime.now()
            )
            log_embed.add_field(name="Channel", value=channel.mention, inline=True)
            log_embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await log_to_mod_channel(interaction.guild, log_embed)
            
        except Exception as e:
            logger.error(f"Lock error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @mod_group.command(name="unlock", description="Unlock a channel")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def mod_unlock(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        await interaction.response.defer(ephemeral=True)
        try:
            channel = channel or interaction.channel
            overwrite = channel.overwrites_for(interaction.guild.default_role)
            overwrite.send_messages = None
            await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "UNLOCK", f"Unlocked #{channel.name}")
            
            embed = discord.Embed(title="🔓 Channel Unlocked", color=discord.Color.green())
            embed.add_field(name="Channel", value=channel.mention, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=False)
            
            log_embed = discord.Embed(
                title="🔓 Channel Unlocked",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            log_embed.add_field(name="Channel", value=channel.mention, inline=True)
            log_embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await log_to_mod_channel(interaction.guild, log_embed)
            
        except Exception as e:
            logger.error(f"Unlock error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @mod_group.command(name="slowmode", description="Set channel slowmode")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def mod_slowmode(self, interaction: discord.Interaction, seconds: int, channel: discord.TextChannel = None):
        await interaction.response.defer(ephemeral=True)
        try:
            channel = channel or interaction.channel
            if seconds < 0 or seconds > 21600:
                return await interaction.followup.send("❌ Slowmode must be between 0 and 21600 seconds.", ephemeral=True)
            await channel.edit(slowmode_delay=seconds)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "SLOWMODE", f"Set slowmode in #{channel.name} to {seconds}s")
            
            embed = discord.Embed(title="⏱️ Slowmode Set", color=discord.Color.blue())
            embed.add_field(name="Channel", value=channel.mention, inline=True)
            embed.add_field(name="Slowmode", value=f"{seconds} seconds", inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            log_embed = discord.Embed(
                title="⏱️ Slowmode Updated",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            log_embed.add_field(name="Channel", value=channel.mention, inline=True)
            log_embed.add_field(name="Slowmode", value=f"{seconds} seconds", inline=True)
            log_embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await log_to_mod_channel(interaction.guild, log_embed)
            
        except Exception as e:
            logger.error(f"Slowmode error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @mod_group.command(name="history", description="Show moderation history of a member")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mod_history(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        try:
            history = await get_user_history(member.id, interaction.guild.id, limit=15)
            if not history:
                embed = discord.Embed(title=f"📜 History for {member.display_name}", description="No moderation history found.", color=discord.Color.green())
                return await interaction.followup.send(embed=embed, ephemeral=True)
            
            embed = discord.Embed(title=f"📜 Moderation History for {member.display_name}", color=discord.Color.blue())
            embed.set_footer(text=f"Showing {len(history)} events")
            for event_type, details, timestamp in history:
                embed.add_field(name=f"🔹 {event_type}", value=f"{details[:100]}\n<{timestamp}>", inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"History error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @mod_group.command(name="timeout", description="Timeout a member (alias for mute)")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mod_timeout(self, interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "No reason"):
        await self.mod_mute(interaction, member, minutes, reason)

    @mod_group.command(name="untimeout", description="Remove timeout from a member (alias for unmute)")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mod_untimeout(self, interaction: discord.Interaction, member: discord.Member):
        await self.mod_unmute(interaction, member)

    @mod_group.command(name="hide", description="Hide a channel from @everyone")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def mod_hide(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        await interaction.response.defer(ephemeral=True)
        try:
            channel = channel or interaction.channel
            await channel.set_permissions(interaction.guild.default_role, view_channel=False)
            
            embed = discord.Embed(title="👁️ Channel Hidden", color=discord.Color.orange())
            embed.add_field(name="Channel", value=channel.mention, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            log_embed = discord.Embed(
                title="👁️ Channel Hidden",
                color=discord.Color.orange(),
                timestamp=datetime.now()
            )
            log_embed.add_field(name="Channel", value=channel.mention, inline=True)
            log_embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await log_to_mod_channel(interaction.guild, log_embed)
            
        except Exception as e:
            logger.error(f"Hide error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @mod_group.command(name="show", description="Show a channel to @everyone")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def mod_show(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        await interaction.response.defer(ephemeral=True)
        try:
            channel = channel or interaction.channel
            await channel.set_permissions(interaction.guild.default_role, view_channel=None)
            
            embed = discord.Embed(title="👁️ Channel Shown", color=discord.Color.green())
            embed.add_field(name="Channel", value=channel.mention, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            log_embed = discord.Embed(
                title="👁️ Channel Shown",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            log_embed.add_field(name="Channel", value=channel.mention, inline=True)
            log_embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await log_to_mod_channel(interaction.guild, log_embed)
            
        except Exception as e:
            logger.error(f"Show error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @mod_group.command(name="snipe", description="Show the last deleted message")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def mod_snipe(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            result = await get_snipe(interaction.guild.id, interaction.channel.id)
            if not result:
                return await interaction.followup.send("❌ No deleted messages to snipe.", ephemeral=True)
            
            content, author_id, timestamp, message_id, attachments = result
            user = await self.bot.fetch_user(author_id)
            
            embed = discord.Embed(
                title="🕵️ Snipe - Deleted Message",
                color=discord.Color.purple(),
                timestamp=datetime.fromisoformat(timestamp) if timestamp else datetime.now()
            )
            embed.add_field(name="Author", value=f"{user.mention if user else author_id}", inline=True)
            embed.add_field(name="Channel", value=interaction.channel.mention, inline=True)
            
            if content:
                embed.add_field(name="Content", value=content[:1000] or "[Empty]", inline=False)
            
            if attachments:
                try:
                    attach_list = json.loads(attachments)
                    if attach_list:
                        embed.add_field(name="Attachments", value=", ".join([a.get("filename", "Unknown") for a in attach_list[:5]]), inline=False)
                except:
                    pass
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Snipe error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @mod_group.command(name="editsnipe", description="Show the last edited message")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def mod_editsnipe(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            result = await get_edit_snipe(interaction.guild.id, interaction.channel.id)
            if not result:
                return await interaction.followup.send("❌ No edited messages to snipe.", ephemeral=True)
            
            old_content, new_content, author_id, timestamp, message_id = result
            user = await self.bot.fetch_user(author_id)
            
            embed = discord.Embed(
                title="🕵️ Edit Snipe - Edited Message",
                color=discord.Color.blue(),
                timestamp=datetime.fromisoformat(timestamp) if timestamp else datetime.now()
            )
            embed.add_field(name="Author", value=f"{user.mention if user else author_id}", inline=True)
            embed.add_field(name="Channel", value=interaction.channel.mention, inline=True)
            embed.add_field(name="Before", value=old_content[:500] or "[Empty]", inline=False)
            embed.add_field(name="After", value=new_content[:500] or "[Empty]", inline=False)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Edit snipe error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


# =========================
# 🎭 ROLE MANAGEMENT COG
# =========================

class RoleManagement(commands.Cog, name="role_management"):
    """Role management commands."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    role_group = app_commands.Group(name="role", description="Role management - Owner Only")
    
    @role_group.command(name="nick", description="Change a member's nickname")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_nicknames=True)
    async def role_nick(self, interaction: discord.Interaction, member: discord.Member, nickname: str):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner.id:
                return await interaction.followup.send("❌ You cannot change this member's nickname (role hierarchy).", ephemeral=True)
            
            old_nick = member.nick or member.name
            await member.edit(nick=nickname, reason=f"Changed by {interaction.user}")
            await add_history(interaction.guild.id, member.id, str(member), "NICK_CHANGE", f"{old_nick} -> {nickname} by {interaction.user}")
            
            embed = discord.Embed(title="✏️ Nickname Changed", color=discord.Color.blue())
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Old Nickname", value=old_nick, inline=True)
            embed.add_field(name="New Nickname", value=nickname, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to change this member's nickname.", ephemeral=True)
        except Exception as e:
            logger.error(f"Nick error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @role_group.command(name="resetnick", description="Remove a member's nickname")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_nicknames=True)
    async def role_resetnick(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner.id:
                return await interaction.followup.send("❌ You cannot reset this member's nickname (role hierarchy).", ephemeral=True)
            
            old_nick = member.nick or member.name
            await member.edit(nick=None, reason=f"Reset by {interaction.user}")
            await add_history(interaction.guild.id, member.id, str(member), "NICK_RESET", f"{old_nick} -> {member.name} by {interaction.user}")
            
            embed = discord.Embed(title="🔄 Nickname Reset", color=discord.Color.blue())
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Previous Nickname", value=old_nick, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to reset this member's nickname.", ephemeral=True)
        except Exception as e:
            logger.error(f"Resetnick error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @role_group.command(name="give", description="Give a role to a member")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_roles=True)
    async def role_give(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role):
        await interaction.response.defer(ephemeral=True)
        try:
            if role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner.id:
                return await interaction.followup.send("❌ You cannot give this role (role hierarchy).", ephemeral=True)
            if role in member.roles:
                return await interaction.followup.send(f"❌ {member.mention} already has the {role.name} role.", ephemeral=True)
            
            await member.add_roles(role, reason=f"Added by {interaction.user}")
            await add_history(interaction.guild.id, member.id, str(member), "ROLE_ADD", f"Role {role.name} added by {interaction.user}")
            
            embed = discord.Embed(title="🎭 Role Given", color=discord.Color.green())
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Role", value=role.mention, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to give this role.", ephemeral=True)
        except Exception as e:
            logger.error(f"Give role error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @role_group.command(name="remove", description="Remove a role from a member")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_roles=True)
    async def role_remove(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role):
        await interaction.response.defer(ephemeral=True)
        try:
            if role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner.id:
                return await interaction.followup.send("❌ You cannot remove this role (role hierarchy).", ephemeral=True)
            if role not in member.roles:
                return await interaction.followup.send(f"❌ {member.mention} doesn't have the {role.name} role.", ephemeral=True)
            
            await member.remove_roles(role, reason=f"Removed by {interaction.user}")
            await add_history(interaction.guild.id, member.id, str(member), "ROLE_REMOVE", f"Role {role.name} removed by {interaction.user}")
            
            embed = discord.Embed(title="🎭 Role Removed", color=discord.Color.orange())
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Role", value=role.mention, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to remove this role.", ephemeral=True)
        except Exception as e:
            logger.error(f"Remove role error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


# =========================
# 🔊 VOICE MODERATION COG
# =========================

class VoiceModeration(commands.Cog, name="voice_moderation"):
    """Voice moderation commands."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    voice_group = app_commands.Group(name="voice", description="Voice moderation - Owner Only")
    
    @voice_group.command(name="kick", description="Disconnect a member from voice")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(move_members=True)
    async def voice_kick(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner.id:
                return await interaction.followup.send("❌ You cannot voice kick this member (role hierarchy).", ephemeral=True)
            if not member.voice or not member.voice.channel:
                return await interaction.followup.send(f"❌ {member.display_name} is not in a voice channel.", ephemeral=True)
            
            channel_name = member.voice.channel.name
            await member.move_to(None, reason=f"Voice kicked by {interaction.user}")
            await add_history(interaction.guild.id, member.id, str(member), "VOICE_KICK", f"Disconnected from voice by {interaction.user}")
            
            embed = discord.Embed(title="🔊 User Voice Kicked", color=discord.Color.orange())
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Channel", value=channel_name, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Voice kick error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @voice_group.command(name="move", description="Move a member to another voice channel")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(move_members=True)
    async def voice_move(self, interaction: discord.Interaction, member: discord.Member, channel: discord.VoiceChannel):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner.id:
                return await interaction.followup.send("❌ You cannot move this member (role hierarchy).", ephemeral=True)
            if not member.voice or not member.voice.channel:
                return await interaction.followup.send(f"❌ {member.display_name} is not in a voice channel.", ephemeral=True)
            
            old_channel = member.voice.channel
            await member.move_to(channel, reason=f"Moved by {interaction.user}")
            await add_history(interaction.guild.id, member.id, str(member), "VOICE_MOVE", f"Moved from {old_channel.name} to {channel.name} by {interaction.user}")
            
            embed = discord.Embed(title="🔊 User Moved", color=discord.Color.blue())
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="From", value=old_channel.mention, inline=True)
            embed.add_field(name="To", value=channel.mention, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Voice move error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @voice_group.command(name="deafen", description="Deafen a member in voice")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(deafen_members=True)
    async def voice_deafen(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner.id:
                return await interaction.followup.send("❌ You cannot deafen this member (role hierarchy).", ephemeral=True)
            if not member.voice or not member.voice.channel:
                return await interaction.followup.send(f"❌ {member.display_name} is not in a voice channel.", ephemeral=True)
            
            await member.edit(deafen=True, reason=f"Deafened by {interaction.user}")
            await add_history(interaction.guild.id, member.id, str(member), "DEAFEN", f"Deafened by {interaction.user}")
            
            embed = discord.Embed(title="🔇 User Deafened", color=discord.Color.orange())
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Deafen error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @voice_group.command(name="undeafen", description="Undeafen a member in voice")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(deafen_members=True)
    async def voice_undeafen(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner.id:
                return await interaction.followup.send("❌ You cannot undeafen this member (role hierarchy).", ephemeral=True)
            if not member.voice or not member.voice.channel:
                return await interaction.followup.send(f"❌ {member.display_name} is not in a voice channel.", ephemeral=True)
            
            await member.edit(deafen=False, reason=f"Undeafened by {interaction.user}")
            await add_history(interaction.guild.id, member.id, str(member), "UNDEAFEN", f"Undeafened by {interaction.user}")
            
            embed = discord.Embed(title="🔊 User Undeafened", color=discord.Color.green())
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Undeafen error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @voice_group.command(name="mute", description="Mute a member in voice")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(mute_members=True)
    async def voice_mute(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner.id:
                return await interaction.followup.send("❌ You cannot mute this member in voice (role hierarchy).", ephemeral=True)
            if not member.voice or not member.voice.channel:
                return await interaction.followup.send(f"❌ {member.display_name} is not in a voice channel.", ephemeral=True)
            
            await member.edit(mute=True, reason=f"Muted in voice by {interaction.user}")
            await add_history(interaction.guild.id, member.id, str(member), "VOICE_MUTE", f"Muted in voice by {interaction.user}")
            
            embed = discord.Embed(title="🔇 User Voice Muted", color=discord.Color.orange())
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Voice mute error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @voice_group.command(name="unmute", description="Unmute a member in voice")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(mute_members=True)
    async def voice_unmute(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner.id:
                return await interaction.followup.send("❌ You cannot unmute this member in voice (role hierarchy).", ephemeral=True)
            if not member.voice or not member.voice.channel:
                return await interaction.followup.send(f"❌ {member.display_name} is not in a voice channel.", ephemeral=True)
            
            await member.edit(mute=False, reason=f"Unmuted in voice by {interaction.user}")
            await add_history(interaction.guild.id, member.id, str(member), "VOICE_UNMUTE", f"Unmuted in voice by {interaction.user}")
            
            embed = discord.Embed(title="🔊 User Voice Unmuted", color=discord.Color.green())
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Voice unmute error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


# =========================
# 🧹 CHANNEL MANAGEMENT COG (Purge Commands)
# =========================

class ChannelManagement(commands.Cog, name="channel_management"):
    """Channel management and purge commands."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    purge_group = app_commands.Group(name="purge", description="Message purging - Owner Only")
    
    @purge_group.command(name="user", description="Delete messages from a specific user")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge_user(self, interaction: discord.Interaction, member: discord.Member, amount: int = 50):
        await interaction.response.defer(ephemeral=True)
        try:
            amount = min(amount, 100)
            def check(msg):
                return msg.author.id == member.id
            deleted = await interaction.channel.purge(limit=amount, check=check)
            await add_history(interaction.guild.id, member.id, str(member), "PURGE_USER", f"Purged {len(deleted)} messages by {interaction.user}")
            
            embed = discord.Embed(title="🧹 User Messages Purged", color=discord.Color.orange())
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Messages Deleted", value=str(len(deleted)), inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Purge user error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @purge_group.command(name="bots", description="Delete messages from bots")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge_bots(self, interaction: discord.Interaction, amount: int = 50):
        await interaction.response.defer(ephemeral=True)
        try:
            amount = min(amount, 100)
            def check(msg):
                return msg.author.bot
            deleted = await interaction.channel.purge(limit=amount, check=check)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "PURGE_BOTS", f"Purged {len(deleted)} bot messages")
            
            embed = discord.Embed(title="🤖 Bot Messages Purged", color=discord.Color.orange())
            embed.add_field(name="Messages Deleted", value=str(len(deleted)), inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Purge bots error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @purge_group.command(name="images", description="Delete messages with images")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge_images(self, interaction: discord.Interaction, amount: int = 50):
        await interaction.response.defer(ephemeral=True)
        try:
            amount = min(amount, 100)
            def check(msg):
                return any(a.content_type and a.content_type.startswith("image/") for a in msg.attachments)
            deleted = await interaction.channel.purge(limit=amount, check=check)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "PURGE_IMAGES", f"Purged {len(deleted)} images")
            
            embed = discord.Embed(title="🖼️ Images Purged", color=discord.Color.orange())
            embed.add_field(name="Messages Deleted", value=str(len(deleted)), inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Purge images error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @purge_group.command(name="attachments", description="Delete messages with attachments")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge_attachments(self, interaction: discord.Interaction, amount: int = 50):
        await interaction.response.defer(ephemeral=True)
        try:
            amount = min(amount, 100)
            def check(msg):
                return len(msg.attachments) > 0
            deleted = await interaction.channel.purge(limit=amount, check=check)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "PURGE_ATTACHMENTS", f"Purged {len(deleted)} attachments")
            
            embed = discord.Embed(title="📎 Attachments Purged", color=discord.Color.orange())
            embed.add_field(name="Messages Deleted", value=str(len(deleted)), inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Purge attachments error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @purge_group.command(name="embeds", description="Delete messages with embeds")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge_embeds(self, interaction: discord.Interaction, amount: int = 50):
        await interaction.response.defer(ephemeral=True)
        try:
            amount = min(amount, 100)
            def check(msg):
                return len(msg.embeds) > 0
            deleted = await interaction.channel.purge(limit=amount, check=check)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "PURGE_EMBEDS", f"Purged {len(deleted)} embeds")
            
            embed = discord.Embed(title="📦 Embeds Purged", color=discord.Color.orange())
            embed.add_field(name="Messages Deleted", value=str(len(deleted)), inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Purge embeds error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @purge_group.command(name="contains", description="Delete messages containing specific text")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.describe(text="Text to search for", amount="Number of messages to check (max 100)")
    async def purge_contains(self, interaction: discord.Interaction, text: str, amount: int = 50):
        await interaction.response.defer(ephemeral=True)
        try:
            amount = min(amount, 100)
            def check(msg):
                return text.lower() in msg.content.lower()
            deleted = await interaction.channel.purge(limit=amount, check=check)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "PURGE_CONTAINS", f"Purged {len(deleted)} messages containing '{text}'")
            
            embed = discord.Embed(title="🔍 Messages Purged", color=discord.Color.orange())
            embed.add_field(name="Contains", value=text, inline=True)
            embed.add_field(name="Messages Deleted", value=str(len(deleted)), inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Purge contains error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @purge_group.command(name="links", description="Delete messages containing links")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge_links(self, interaction: discord.Interaction, amount: int = 50):
        await interaction.response.defer(ephemeral=True)
        try:
            amount = min(amount, 100)
            link_regex = r"https?://[^\s]+|www\.[^\s]+"
            def check(msg):
                return re.search(link_regex, msg.content.lower()) is not None
            deleted = await interaction.channel.purge(limit=amount, check=check)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "PURGE_LINKS", f"Purged {len(deleted)} messages with links")
            
            embed = discord.Embed(title="🔗 Links Purged", color=discord.Color.orange())
            embed.add_field(name="Messages Deleted", value=str(len(deleted)), inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Purge links error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


# =========================
# 📊 INFORMATION COG
# =========================

class Information(commands.Cog, name="information"):
    """Information and utility commands."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    info_group = app_commands.Group(name="info", description="Server information - Owner Only")
    
    @info_group.command(name="server", description="Display detailed server information")
    @app_commands.check(owner_check)
    async def info_server(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            guild = interaction.guild
            embed = discord.Embed(title=f"📋 {guild.name}", color=discord.Color.blue())
            if guild.icon:
                embed.set_thumbnail(url=guild.icon.url)
            embed.add_field(name="👑 Owner", value=guild.owner.mention if guild.owner else "Unknown", inline=True)
            embed.add_field(name="👥 Members", value=guild.member_count, inline=True)
            embed.add_field(name="💬 Channels", value=len(guild.channels), inline=True)
            embed.add_field(name="🎭 Roles", value=len(guild.roles), inline=True)
            embed.add_field(name="📅 Created", value=f"<t:{int(guild.created_at.timestamp())}:R>", inline=True)
            embed.add_field(name="🆔 ID", value=guild.id, inline=True)
            embed.add_field(name="🔊 Voice Channels", value=len(guild.voice_channels), inline=True)
            embed.add_field(name="📝 Text Channels", value=len(guild.text_channels), inline=True)
            embed.add_field(name="📈 Boost Level", value=guild.premium_tier, inline=True)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Server info error: {e}")
            await interaction.followup.send(f"❌ Error: {e}")

    @info_group.command(name="user", description="Display detailed user information")
    @app_commands.check(owner_check)
    @app_commands.describe(member="Member to look up (optional)")
    async def info_user(self, interaction: discord.Interaction, member: discord.Member = None):
        await interaction.response.defer()
        try:
            target = member or interaction.user
            embed = discord.Embed(title=f"👤 {target.display_name}", color=target.color or discord.Color.blue())
            if target.avatar:
                embed.set_thumbnail(url=target.avatar.url)
            embed.add_field(name="Username", value=str(target), inline=True)
            embed.add_field(name="🆔 ID", value=target.id, inline=True)
            if target.joined_at:
                embed.add_field(name="📅 Joined", value=f"<t:{int(target.joined_at.timestamp())}:R>", inline=True)
            embed.add_field(name="📅 Created", value=f"<t:{int(target.created_at.timestamp())}:R>", inline=True)
            embed.add_field(name="🎭 Top Role", value=target.top_role.mention if target.top_role else "None", inline=True)
            embed.add_field(name="🤖 Bot", value="✅ Yes" if target.bot else "❌ No", inline=True)
            if len(target.roles) > 1:
                roles = [r.mention for r in target.roles[1:]][:10]
                embed.add_field(name="🎭 Roles", value=" ".join(roles) if roles else "None", inline=False)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"User info error: {e}")
            await interaction.followup.send(f"❌ Error: {e}")

    @info_group.command(name="role", description="Display role information")
    @app_commands.check(owner_check)
    @app_commands.describe(role="Role to look up")
    async def info_role(self, interaction: discord.Interaction, role: discord.Role):
        await interaction.response.defer()
        try:
            embed = discord.Embed(title=f"🎭 {role.name}", color=role.color)
            embed.add_field(name="🆔 ID", value=role.id, inline=True)
            embed.add_field(name="📅 Created", value=f"<t:{int(role.created_at.timestamp())}:R>", inline=True)
            embed.add_field(name="👥 Members", value=len(role.members), inline=True)
            embed.add_field(name="🎨 Color", value=str(role.color), inline=True)
            embed.add_field(name="🔝 Position", value=role.position, inline=True)
            embed.add_field(name="🔒 Mentionable", value="✅ Yes" if role.mentionable else "❌ No", inline=True)
            embed.add_field(name="🔐 Hoist", value="✅ Yes" if role.hoist else "❌ No", inline=True)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Role info error: {e}")
            await interaction.followup.send(f"❌ Error: {e}")

    @info_group.command(name="channel", description="Display channel information")
    @app_commands.check(owner_check)
    @app_commands.describe(channel="Channel to look up (optional)")
    async def info_channel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        await interaction.response.defer()
        try:
            target = channel or interaction.channel
            embed = discord.Embed(title=f"📢 #{target.name}", color=discord.Color.blue())
            embed.add_field(name="🆔 ID", value=target.id, inline=True)
            embed.add_field(name="📅 Created", value=f"<t:{int(target.created_at.timestamp())}:R>", inline=True)
            embed.add_field(name="📝 Type", value=str(target.type).title(), inline=True)
            embed.add_field(name="🔒 Slowmode", value=f"{target.slowmode_delay}s" if target.slowmode_delay > 0 else "Off", inline=True)
            if isinstance(target, discord.TextChannel):
                embed.add_field(name="👥 Topic", value=target.topic or "None", inline=False)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Channel info error: {e}")
            await interaction.followup.send(f"❌ Error: {e}")

    @info_group.command(name="avatar", description="Display a user's avatar")
    @app_commands.check(owner_check)
    @app_commands.describe(member="Member to look up (optional)")
    async def info_avatar(self, interaction: discord.Interaction, member: discord.Member = None):
        await interaction.response.defer()
        try:
            target = member or interaction.user
            embed = discord.Embed(title=f"🖼️ {target.display_name}'s Avatar", color=discord.Color.blue())
            if target.avatar:
                embed.set_image(url=target.avatar.url)
                embed.add_field(name="Link", value=f"[Click here]({target.avatar.url})", inline=False)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error(f"Avatar error: {e}")
            await interaction.followup.send(f"❌ Error: {e}")


# =========================
# 💬 MESSAGE COMMANDS COG
# =========================

class MessageCommands(commands.Cog, name="message_commands"):
    """Message-related commands."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    msg_group = app_commands.Group(name="msg", description="Message commands - Owner Only")
    
    @msg_group.command(name="announce", description="Send an announcement embed")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.describe(title="Announcement title", description="Announcement description", color="Color hex code (e.g., #FF0000)")
    async def msg_announce(self, interaction: discord.Interaction, title: str, description: str, color: str = "#00FF00"):
        await interaction.response.defer(ephemeral=True)
        try:
            color_int = int(color.replace("#", ""), 16) if color.startswith("#") else int(color, 16)
            embed = discord.Embed(title=f"📢 {title}", description=description, color=color_int)
            embed.set_footer(text=f"Announced by {interaction.user.display_name}", icon_url=interaction.user.avatar.url if interaction.user.avatar else None)
            embed.timestamp = datetime.now()
            await interaction.channel.send(embed=embed)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "ANNOUNCE", f"Announcement: {title}")
            
            embed_response = discord.Embed(title="✅ Announcement Sent", color=discord.Color.green())
            embed_response.add_field(name="Title", value=title, inline=True)
            embed_response.add_field(name="Channel", value=interaction.channel.mention, inline=True)
            await interaction.followup.send(embed=embed_response, ephemeral=True)
            
        except ValueError:
            await interaction.followup.send("❌ Invalid color format. Use hex like #FF0000 or a number.", ephemeral=True)
        except Exception as e:
            logger.error(f"Announce error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @msg_group.command(name="say", description="Make the bot send a message")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.describe(message="Message to send")
    async def msg_say(self, interaction: discord.Interaction, message: str):
        await interaction.response.defer(ephemeral=True)
        try:
            await interaction.channel.send(message)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "SAY", f"Sent message: {message[:100]}")
            
            embed = discord.Embed(title="✅ Message Sent", color=discord.Color.green())
            embed.add_field(name="Channel", value=interaction.channel.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Say error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @msg_group.command(name="poll", description="Create a poll")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.describe(question="Poll question", option1="First option", option2="Second option", option3="Third option (optional)", option4="Fourth option (optional)", option5="Fifth option (optional)")
    async def msg_poll(self, interaction: discord.Interaction, question: str, option1: str, option2: str,
                        option3: str = None, option4: str = None, option5: str = None):
        await interaction.response.defer(ephemeral=True)
        try:
            options = [option1, option2]
            if option3:
                options.append(option3)
            if option4:
                options.append(option4)
            if option5:
                options.append(option5)
            if len(options) > 10:
                return await interaction.followup.send("❌ Maximum 10 options allowed.", ephemeral=True)
            
            poll_id = random.randint(100000, 999999)
            votes_data = {str(i): [] for i in range(len(options))}
            await db.execute(
                "INSERT INTO polls (id, guild_id, channel_id, question, options, votes) VALUES (?, ?, ?, ?, ?, ?)",
                (poll_id, interaction.guild.id, interaction.channel.id, question, json.dumps(options), json.dumps(votes_data))
            )
            await db.commit()
            
            embed = discord.Embed(title=f"📊 Poll: {question}", color=discord.Color.blue())
            emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
            for i, option in enumerate(options):
                embed.add_field(name=f"{emojis[i]} {option}", value="0 votes", inline=False)
            embed.set_footer(text=f"Poll ID: {poll_id} | React with the buttons below to vote")
            
            view = PollView(poll_id, options)
            await interaction.channel.send(embed=embed, view=view)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "POLL", f"Created poll: {question}")
            
            embed_response = discord.Embed(title="✅ Poll Created", color=discord.Color.green())
            embed_response.add_field(name="Question", value=question, inline=True)
            embed_response.add_field(name="Options", value=str(len(options)), inline=True)
            await interaction.followup.send(embed=embed_response, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Poll error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


# =========================
# 📋 LOGS CONFIGURATION COG
# =========================

class LogsConfig(commands.Cog, name="logs_config"):
    """Logs configuration commands."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    logs_group = app_commands.Group(name="logs", description="Mod logs configuration - Owner Only")
    
    @logs_group.command(name="set", description="Set the mod logs channel")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(channel="Channel for moderation logs")
    async def logs_set(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        try:
            await set_mod_logs_config(interaction.guild.id, channel.id, enabled=True)
            
            embed = discord.Embed(
                title="✅ Mod Logs Configured",
                description=f"Moderation logs will be sent to {channel.mention}",
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            # Send test log
            test_embed = discord.Embed(
                title="📋 Mod Logs Test",
                description="This is a test message to confirm logs are working.",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            await log_to_mod_channel(interaction.guild, test_embed)
            
        except Exception as e:
            logger.error(f"Logs set error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @logs_group.command(name="disable", description="Disable mod logs")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(administrator=True)
    async def logs_disable(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            await set_mod_logs_config(interaction.guild.id, 0, enabled=False)
            
            embed = discord.Embed(
                title="✅ Mod Logs Disabled",
                description="Moderation logs have been disabled.",
                color=discord.Color.orange()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Logs disable error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @logs_group.command(name="status", description="Show mod logs configuration")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(administrator=True)
    async def logs_status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            config = await get_mod_logs_config(interaction.guild.id)
            if not config or not config["enabled"]:
                return await interaction.followup.send("❌ Mod logs are not configured or disabled.", ephemeral=True)
            
            channel = interaction.guild.get_channel(config["channel_id"])
            channel_name = channel.mention if channel else "Unknown (deleted)"
            
            embed = discord.Embed(
                title="📋 Mod Logs Configuration",
                color=discord.Color.blue()
            )
            embed.add_field(name="Channel", value=channel_name, inline=True)
            embed.add_field(name="Enabled", value="✅ Yes" if config["enabled"] else "❌ No", inline=True)
            embed.add_field(name="Message Deletes", value="✅" if config["log_deletes"] else "❌", inline=True)
            embed.add_field(name="Message Edits", value="✅" if config["log_edits"] else "❌", inline=True)
            embed.add_field(name="Member Joins", value="✅" if config["log_joins"] else "❌", inline=True)
            embed.add_field(name="Member Leaves", value="✅" if config["log_leaves"] else "❌", inline=True)
            embed.add_field(name="Bans", value="✅" if config["log_bans"] else "❌", inline=True)
            embed.add_field(name="Kicks", value="✅" if config["log_kicks"] else "❌", inline=True)
            embed.add_field(name="Timeouts", value="✅" if config["log_timeouts"] else "❌", inline=True)
            embed.add_field(name="Voice", value="✅" if config["log_voice"] else "❌", inline=True)
            embed.add_field(name="Role Updates", value="✅" if config["log_roles"] else "❌", inline=True)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Logs status error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


# =========================
# 🛡️ AUTOMOD CONFIGURATION COG
# =========================

class AutoModConfig(commands.Cog, name="automod_config"):
    """AutoMod configuration commands."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    automod_group = app_commands.Group(name="automod", description="AutoModeration configuration - Owner Only")
    
    @automod_group.command(name="antispam", description="Configure anti-spam protection")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        enabled="Enable/disable anti-spam",
        threshold="Number of messages in the window",
        window="Time window in seconds"
    )
    async def automod_antispam(self, interaction: discord.Interaction, enabled: bool, threshold: int = 5, window: int = 5):
        await interaction.response.defer(ephemeral=True)
        try:
            await set_automod_config(interaction.guild.id, anti_spam=enabled, spam_threshold=threshold, spam_window=window)
            
            embed = discord.Embed(
                title="🛡️ Anti-Spam Configuration",
                description=f"Anti-spam is now {'✅ ENABLED' if enabled else '❌ DISABLED'}",
                color=discord.Color.green() if enabled else discord.Color.red()
            )
            embed.add_field(name="Threshold", value=f"{threshold} messages", inline=True)
            embed.add_field(name="Window", value=f"{window} seconds", inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"AutoMod antispam error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @automod_group.command(name="antiinvite", description="Configure anti-invite link protection")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(enabled="Enable/disable anti-invite")
    async def automod_antiinvite(self, interaction: discord.Interaction, enabled: bool):
        await interaction.response.defer(ephemeral=True)
        try:
            await set_automod_config(interaction.guild.id, anti_invite=enabled)
            
            embed = discord.Embed(
                title="🛡️ Anti-Invite Configuration",
                description=f"Anti-invite is now {'✅ ENABLED' if enabled else '❌ DISABLED'}",
                color=discord.Color.green() if enabled else discord.Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"AutoMod antiinvite error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @automod_group.command(name="antimentions", description="Configure anti-mass-mention protection")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        enabled="Enable/disable anti-mass-mentions",
        limit="Maximum mentions allowed per message"
    )
    async def automod_antimentions(self, interaction: discord.Interaction, enabled: bool, limit: int = 5):
        await interaction.response.defer(ephemeral=True)
        try:
            await set_automod_config(interaction.guild.id, anti_mentions=enabled, mention_limit=limit)
            
            embed = discord.Embed(
                title="🛡️ Anti-Mass-Mention Configuration",
                description=f"Anti-mass-mentions is now {'✅ ENABLED' if enabled else '❌ DISABLED'}",
                color=discord.Color.green() if enabled else discord.Color.red()
            )
            embed.add_field(name="Mention Limit", value=str(limit), inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"AutoMod antimentions error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @automod_group.command(name="badwords", description="Configure bad words filter")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        enabled="Enable/disable bad words filter",
        words="Comma-separated list of bad words"
    )
    async def automod_badwords(self, interaction: discord.Interaction, enabled: bool, words: str = ""):
        await interaction.response.defer(ephemeral=True)
        try:
            await set_automod_config(interaction.guild.id, bad_words=enabled, bad_words_list=words)
            
            embed = discord.Embed(
                title="🛡️ Bad Words Filter Configuration",
                description=f"Bad words filter is now {'✅ ENABLED' if enabled else '❌ DISABLED'}",
                color=discord.Color.green() if enabled else discord.Color.red()
            )
            if words:
                word_list = [w.strip() for w in words.split(",") if w.strip()]
                embed.add_field(name="Filtered Words", value=", ".join(word_list[:20]) + (f" and {len(word_list)-20} more" if len(word_list) > 20 else ""), inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"AutoMod badwords error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @automod_group.command(name="status", description="Show automod configuration")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(administrator=True)
    async def automod_status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            config = await get_automod_config(interaction.guild.id)
            
            embed = discord.Embed(
                title="🛡️ AutoMod Configuration",
                color=discord.Color.blue()
            )
            embed.add_field(name="Anti-Spam", value="✅ Enabled" if config["anti_spam"] else "❌ Disabled", inline=True)
            embed.add_field(name="Anti-Invite", value="✅ Enabled" if config["anti_invite"] else "❌ Disabled", inline=True)
            embed.add_field(name="Anti-Mass-Mentions", value="✅ Enabled" if config["anti_mentions"] else "❌ Disabled", inline=True)
            embed.add_field(name="Bad Words Filter", value="✅ Enabled" if config["bad_words"] else "❌ Disabled", inline=True)
            
            if config["anti_spam"]:
                embed.add_field(name="Spam Threshold", value=f"{config['spam_threshold']} messages", inline=True)
                embed.add_field(name="Spam Window", value=f"{config['spam_window']} seconds", inline=True)
            if config["anti_mentions"]:
                embed.add_field(name="Mention Limit", value=str(config["mention_limit"]), inline=True)
            if config["bad_words"] and config["bad_words_list"]:
                words = [w.strip() for w in config["bad_words_list"].split(",") if w.strip()]
                embed.add_field(name="Filtered Words", value=", ".join(words[:20]) + (f" and {len(words)-20} more" if len(words) > 20 else ""), inline=False)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"AutoMod status error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


# =========================
# ⚠️ WARNING MANAGEMENT COG
# =========================

class WarningManagement(commands.Cog, name="warning_management"):
    """Warning management commands."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    warn_group = app_commands.Group(name="warn", description="Warning management - Owner Only")
    
    @warn_group.command(name="add", description="Warn a member")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn_add(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner.id:
                return await interaction.followup.send("❌ You cannot warn this member (role hierarchy).", ephemeral=True)
            
            await add_warning(member.id, interaction.guild.id, reason, str(interaction.user))
            await add_history(interaction.guild.id, member.id, str(member), "WARN", reason)
            
            try:
                dm_embed = discord.Embed(title=f"You were warned in {interaction.guild.name}", description=f"Reason: {reason}", color=discord.Color.orange())
                await member.send(embed=dm_embed)
            except:
                pass
            
            embed = discord.Embed(title="⚠️ User Warned", color=discord.Color.orange())
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            embed.add_field(name="Reason", value=reason, inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            log_embed = discord.Embed(
                title="⚠️ Member Warned",
                color=discord.Color.orange(),
                timestamp=datetime.now()
            )
            log_embed.add_field(name="User", value=f"{member.mention} ({member})", inline=True)
            log_embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Reason", value=reason, inline=False)
            await log_to_mod_channel(interaction.guild, log_embed)
            
        except Exception as e:
            logger.error(f"Warn add error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @warn_group.command(name="list", description="View a member's warnings")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn_list(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        try:
            warnings = await get_warnings(member.id, interaction.guild.id)
            if not warnings:
                embed = discord.Embed(title=f"📋 Warnings for {member.display_name}", description="No warnings found.", color=discord.Color.green())
                return await interaction.followup.send(embed=embed, ephemeral=True)
            
            embed = discord.Embed(title=f"📋 Warnings for {member.display_name}", color=discord.Color.orange())
            embed.set_footer(text=f"Total: {len(warnings)} warnings")
            for i, (wid, reason, timestamp) in enumerate(warnings[:10], 1):
                embed.add_field(name=f"#{i}", value=f"Reason: {reason}\nTime: {timestamp}", inline=False)
            if len(warnings) > 10:
                embed.add_field(name="...", value=f"And {len(warnings) - 10} more warnings.", inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"Warn list error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @warn_group.command(name="clear", description="Clear all warnings from a member")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn_clear(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        try:
            warnings = await get_warnings(member.id, interaction.guild.id)
            if not warnings:
                return await interaction.followup.send(f"⚠️ {member.mention} has no warnings to clear.", ephemeral=True)
            
            await clear_warnings(member.id, interaction.guild.id)
            await add_history(interaction.guild.id, member.id, str(member), "WARN_CLEAR", f"All warnings cleared by {interaction.user}")
            
            embed = discord.Embed(
                title="✅ Warnings Cleared",
                description=f"Cleared {len(warnings)} warnings from {member.mention}",
                color=discord.Color.green()
            )
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            log_embed = discord.Embed(
                title="⚠️ Warnings Cleared",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            log_embed.add_field(name="User", value=f"{member.mention} ({member})", inline=True)
            log_embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            log_embed.add_field(name="Warnings Cleared", value=str(len(warnings)), inline=True)
            await log_to_mod_channel(interaction.guild, log_embed)
            
        except Exception as e:
            logger.error(f"Warn clear error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @warn_group.command(name="remove", description="Remove a specific warning by ID")
    @app_commands.check(owner_check)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.describe(warning_id="ID of the warning to remove")
    async def warn_remove(self, interaction: discord.Interaction, warning_id: int):
        await interaction.response.defer(ephemeral=True)
        try:
            # Check if warning exists
            result = await db.fetchone("SELECT user_id, reason FROM warnings WHERE id=?", (warning_id,))
            if not result:
                return await interaction.followup.send(f"❌ Warning ID {warning_id} not found.", ephemeral=True)
            
            user_id, reason = result
            await remove_warning(warning_id)
            
            embed = discord.Embed(
                title="✅ Warning Removed",
                description=f"Removed warning #{warning_id}",
                color=discord.Color.green()
            )
            embed.add_field(name="User", value=f"<@{user_id}>", inline=True)
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Warn remove error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


# =========================
# 🎮 FUN COG (NO OWNER CHECK - PUBLIC)
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
        embed = discord.Embed(title="🎱 Magic 8Ball", color=discord.Color.purple())
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
# 💰 ECONOMY COG (NO OWNER CHECK - PUBLIC)
# =========================

class Economy(commands.Cog, name="economy"):
    economy_group = app_commands.Group(name="economy", description="Economy commands")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.daily_cooldowns = {}

    @economy_group.command(name="balance", description="Check your or another user's balance")
    @app_commands.describe(member="Member to check (optional)")
    async def economy_balance(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        wallet, bank = await get_balance_simple(target.id)
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
# 🎉 GIVEAWAY COG (NO OWNER CHECK - PUBLIC)
# =========================

class Giveaway(commands.Cog, name="giveaway"):
    giveaway_group = app_commands.Group(name="giveaway", description="Giveaway commands")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @giveaway_group.command(name="start", description="Start a giveaway")
    @app_commands.describe(prize="The prize to give away", duration="Duration in minutes", winners="Number of winners (default: 1)")
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
            "id": giveaway_id, "prize": prize, "winners": winners,
            "end_time": end_time, "entries": [],
            "channel_id": interaction.channel_id, "message_id": msg.id, "ended": False
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
# 🎫 TICKET COG (NO OWNER CHECK - PUBLIC)
# =========================

class Ticket(commands.Cog, name="ticket"):
    ticket_group = app_commands.Group(name="ticket", description="Ticket commands")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @ticket_group.command(name="setup", description="Set up the ticket panel in this channel")
    async def ticket_setup(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_channels:
            return await interaction.response.send_message("❌ You need Manage Channels permission.", ephemeral=True)
        embed = discord.Embed(title="🎫 Support Tickets", description="Click the button below to create a support ticket.", color=discord.Color.blue())
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
# 📊 LEVELING COG (NO OWNER CHECK - PUBLIC)
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
            "SELECT xp, level FROM leveling WHERE user_id=? AND guild_id=?",
            (target.id, interaction.guild.id)
        )
        if not row:
            return await interaction.response.send_message(f"{target.mention} has no XP yet.", ephemeral=True)
        xp, level = row
        rank_result = await db.fetchone(
            "SELECT COUNT(*) FROM leveling WHERE guild_id=? AND (xp + (level * 100)) > (SELECT xp + (level * 100) FROM leveling WHERE user_id=? AND guild_id=?)",
            (interaction.guild.id, target.id, interaction.guild.id)
        )
        rank = (rank_result[0] if rank_result else 0) + 1
        total_users_result = await db.fetchone("SELECT COUNT(*) FROM leveling WHERE guild_id=?", (interaction.guild.id,))
        total_users = total_users_result[0] if total_users_result else 0
        needed = (level + 1) * 100
        embed = discord.Embed(title=f"📊 {target.display_name}'s Rank", color=discord.Color.blue())
        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(name="Rank", value=f"#{rank}/{total_users}", inline=True)
        embed.add_field(name="XP", value=f"{xp}/{needed}", inline=False)
        progress = min(xp / needed, 1.0) if needed > 0 else 0
        bar_length = 15
        filled = int(progress * bar_length)
        bar = "█" * filled + "░" * (bar_length - filled)
        embed.add_field(name="Progress", value=f"`{bar}` {int(progress * 100)}%", inline=False)
        await interaction.response.send_message(embed=embed)
    
    @leveling_group.command(name="leaderboard", description="View the leveling leaderboard")
    async def leveling_leaderboard(self, interaction: discord.Interaction):
        rows = await db.fetchall(
            "SELECT user_id, level, xp FROM leveling WHERE guild_id=? ORDER BY level DESC, xp DESC LIMIT 10",
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
# 🤖 AI COG (NO OWNER CHECK - PUBLIC)
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
            response = await get_ai_response(message)
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
            response = await get_ai_response(question)
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
# 📋 UTILITY COMMANDS (NO OWNER CHECK - PUBLIC)
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
# VOICE COMMANDS (NO OWNER CHECK - PUBLIC)
# =========================

class VoiceCommands(commands.Cog, name="voice_commands"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @app_commands.command(name="join", description="Make the bot join your voice channel")
    async def join(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ You must be in a voice channel first.", ephemeral=True)
        channel = interaction.user.voice.channel
        if interaction.guild.voice_client:
            if interaction.guild.voice_client.channel.id == channel.id:
                return await interaction.response.send_message("✅ I'm already in that voice channel.", ephemeral=True)
            await interaction.guild.voice_client.move_to(channel)
            await interaction.response.send_message(f"✅ Moved to {channel.mention}")
        else:
            await channel.connect()
            await interaction.response.send_message(f"✅ Joined {channel.mention}")
    
    @app_commands.command(name="leave", description="Make the bot leave the voice channel")
    async def leave(self, interaction: discord.Interaction):
        voice_client = interaction.guild.voice_client
        if not voice_client:
            return await interaction.response.send_message("❌ I'm not in a voice channel.", ephemeral=True)
        await voice_client.disconnect(force=True)
        await interaction.response.send_message("👋 Disconnected from voice channel.")


# =========================
# 📋 HELP COG (NO OWNER CHECK - PUBLIC)
# =========================

class Help(commands.Cog, name="help"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @app_commands.command(name="help", description="Show all available commands")
    async def help_command(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title=f"🤖 {self.bot.user.name} Help",
            description="A multi-purpose Discord bot with moderation, economy, and more!",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="🛡️ Moderation (Owner Only)",
            value="`/mod clear`, `/mod thanos_snap`, `/mod ban`, `/mod softban`, `/mod unban`, `/mod kick`, `/mod mute`, `/mod unmute`, `/mod warn`, `/mod warnings`, `/mod clean`, `/mod lock`, `/mod unlock`, `/mod slowmode`, `/mod history`, `/mod timeout`, `/mod untimeout`, `/mod hide`, `/mod show`, `/mod snipe`, `/mod editsnipe`\n"
                  "`/role nick`, `/role resetnick`, `/role give`, `/role remove`\n"
                  "`/voice kick`, `/voice move`, `/voice deafen`, `/voice undeafen`, `/voice mute`, `/voice unmute`\n"
                  "`/purge user`, `/purge bots`, `/purge images`, `/purge attachments`, `/purge embeds`, `/purge contains`, `/purge links`\n"
                  "`/info server`, `/info user`, `/info role`, `/info channel`, `/info avatar`\n"
                  "`/msg announce`, `/msg say`, `/msg poll`\n"
                  "`/logs set`, `/logs disable`, `/logs status`\n"
                  "`/automod antispam`, `/automod antiinvite`, `/automod antimentions`, `/automod badwords`, `/automod status`\n"
                  "`/warn add`, `/warn list`, `/warn clear`, `/warn remove`",
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
            value="`/serverinfo`, `/userinfo`, `/ping`, `/join`, `/leave`",
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
            return await interaction.response.send_message(f"⏰ Command on cooldown. Try again in {error.retry_after:.0f}s.", ephemeral=True)
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
# CONTROL PANEL SLASH COMMAND
# =========================

class ControlPanelCommands(commands.Cog, name="control_panel"):
    """Control panel management commands."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @app_commands.command(name="controlpanelset", description="Set up the control panel in the current channel")
    @app_commands.check(owner_check)
    async def controlpanelset(self, interaction: discord.Interaction):
        """Set up the control panel in the current channel."""
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Check if control panel already exists
            existing = await get_control_panel(interaction.guild_id)
            if existing:
                # Delete old panel
                try:
                    old_channel = interaction.guild.get_channel(existing["channel_id"])
                    if old_channel:
                        old_message = await old_channel.fetch_message(existing["message_id"])
                        await old_message.delete()
                except:
                    pass
                await delete_control_panel(interaction.guild_id)
            
            # Create new control panel
            embed = discord.Embed(
                title="🎛️ Bot Control Panel",
                description="Welcome to the bot control panel! Use the buttons and dropdowns below to manage the bot.",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            embed.add_field(name="🤖 Status", value="Bot is online and ready", inline=True)
            embed.add_field(name="👑 Owner", value=f"<@{OWNER_ID}>", inline=True)
            embed.add_field(name="📊 Commands", value=f"{len(self.bot.tree.get_commands())} total", inline=True)
            embed.set_footer(text="Control Panel • Owner Only")
            
            view = ControlPanelView(self.bot, OWNER_ID)
            message = await interaction.channel.send(embed=embed, view=view)
            
            # Save to database
            await save_control_panel(interaction.guild_id, interaction.channel_id, message.id)
            
            await interaction.followup.send(
                f"✅ Control panel has been set up in {interaction.channel.mention}!",
                ephemeral=True
            )
            
        except Exception as e:
            logger.error(f"Control panel setup error: {e}")
            await interaction.followup.send(f"❌ Failed to set up control panel: {str(e)}", ephemeral=True)
    
    @app_commands.command(name="controlpanelrefresh", description="Refresh the control panel")
    @app_commands.check(owner_check)
    async def controlpanelrefresh(self, interaction: discord.Interaction):
        """Refresh the control panel."""
        await interaction.response.defer(ephemeral=True)
        await handle_refresh_panel(interaction)
    
    @app_commands.command(name="controlpanelremove", description="Remove the control panel")
    @app_commands.check(owner_check)
    async def controlpanelremove(self, interaction: discord.Interaction):
        """Remove the control panel."""
        await interaction.response.defer(ephemeral=True)
        
        config = await get_control_panel(interaction.guild_id)
        if not config:
            await interaction.followup.send("❌ No control panel configured in this server.", ephemeral=True)
            return
        
        try:
            channel = interaction.guild.get_channel(config["channel_id"])
            if channel:
                try:
                    message = await channel.fetch_message(config["message_id"])
                    await message.delete()
                except:
                    pass
            
            await delete_control_panel(interaction.guild_id)
            await interaction.followup.send("✅ Control panel removed successfully!", ephemeral=True)
            
        except Exception as e:
            logger.error(f"Control panel remove error: {e}")
            await interaction.followup.send(f"❌ Failed to remove control panel: {str(e)}", ephemeral=True)
    
    @app_commands.command(name="controlpanelmove", description="Move the control panel to another channel")
    @app_commands.check(owner_check)
    @app_commands.describe(channel="Channel to move the control panel to")
    async def controlpanelmove(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Move the control panel to another channel."""
        await interaction.response.defer(ephemeral=True)
        
        config = await get_control_panel(interaction.guild_id)
        if not config:
            await interaction.followup.send("❌ No control panel configured in this server.", ephemeral=True)
            return
        
        try:
            # Delete old panel
            old_channel = interaction.guild.get_channel(config["channel_id"])
            if old_channel:
                try:
                    old_message = await old_channel.fetch_message(config["message_id"])
                    await old_message.delete()
                except:
                    pass
            
            # Create new panel in new channel
            embed = discord.Embed(
                title="🎛️ Bot Control Panel",
                description="Welcome to the bot control panel! Use the buttons and dropdowns below to manage the bot.",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            embed.add_field(name="🤖 Status", value="Bot is online and ready", inline=True)
            embed.add_field(name="👑 Owner", value=f"<@{OWNER_ID}>", inline=True)
            embed.add_field(name="📊 Commands", value=f"{len(self.bot.tree.get_commands())} total", inline=True)
            embed.set_footer(text="Control Panel • Owner Only")
            
            view = ControlPanelView(self.bot, OWNER_ID)
            message = await channel.send(embed=embed, view=view)
            
            # Update database
            await save_control_panel(interaction.guild_id, channel.id, message.id)
            
            await interaction.followup.send(
                f"✅ Control panel moved to {channel.mention}!",
                ephemeral=True
            )
            
        except Exception as e:
            logger.error(f"Control panel move error: {e}")
            await interaction.followup.send(f"❌ Failed to move control panel: {str(e)}", ephemeral=True)


# =========================
# MAIN BOT INITIALIZATION
# =========================

async def main():
    async with bot:
        await db.connect()
        await bot.add_cog(ModerationActions(bot))
        await bot.add_cog(RoleManagement(bot))
        await bot.add_cog(VoiceModeration(bot))
        await bot.add_cog(ChannelManagement(bot))
        await bot.add_cog(Information(bot))
        await bot.add_cog(MessageCommands(bot))
        await bot.add_cog(LogsConfig(bot))
        await bot.add_cog(AutoModConfig(bot))
        await bot.add_cog(WarningManagement(bot))
        await bot.add_cog(Fun(bot))
        await bot.add_cog(Economy(bot))
        await bot.add_cog(Giveaway(bot))
        await bot.add_cog(Ticket(bot))
        await bot.add_cog(Leveling(bot))
        await bot.add_cog(AI(bot))
        await bot.add_cog(Utility(bot))
        await bot.add_cog(VoiceCommands(bot))
        await bot.add_cog(Help(bot))
        await bot.add_cog(CommandErrorHandler(bot))
        await bot.add_cog(HistoryCommands(bot))
        await bot.add_cog(ControlPanelCommands(bot))
        await bot.start(TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot shut down by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        traceback.print_exc()
