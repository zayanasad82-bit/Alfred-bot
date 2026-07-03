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
from collections import defaultdict
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
OWNER_ROLE_ID = int(os.getenv("OWNER_ROLE_ID", "0"))  # Role ID for owner permissions
OWNER_ROLE_NAME = os.getenv("OWNER_ROLE_NAME", "Owner")  # Fallback role name

# Gemini client
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
MODEL_NAME = "gemini-2.0-flash"

# =========================
# DISCORD BOT SETUP
# =========================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.presences = True

class MyBot(commands.Bot):
    async def setup_hook(self):
        await self.tree.sync()
        logger.info("✅ Slash commands synced")
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
                    description="Owner-only bot control panel with moderation tools.",
                    color=discord.Color.blue(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="🤖 Status", value="🟢 Online", inline=True)
                embed.add_field(name="👑 Owner Role", value=owner_role_mention(), inline=True)
                embed.add_field(name="📊 Commands", value=str(len(self.tree.get_commands())), inline=True)
                embed.set_footer(text="Control Panel • Owner Role Only")

                view = ControlPanelView(self)

                try:
                    message = await channel.fetch_message(message_id)
                    await message.edit(embed=embed, view=view)
                    logger.info(f"✅ Restored control panel in guild {guild_id}")
                except discord.NotFound:
                    new_message = await channel.send(embed=embed, view=view)
                    await save_control_panel(guild_id, channel_id, new_message.id)
                    logger.info(f"✅ Recreated control panel in guild {guild_id}")

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
            # Statistics Tables
            """CREATE TABLE IF NOT EXISTS user_stats (
                user_id INTEGER,
                guild_id INTEGER,
                total_messages INTEGER DEFAULT 0,
                messages_today INTEGER DEFAULT 0,
                messages_week INTEGER DEFAULT 0,
                messages_month INTEGER DEFAULT 0,
                commands_used INTEGER DEFAULT 0,
                attachments_sent INTEGER DEFAULT 0,
                images_sent INTEGER DEFAULT 0,
                gifs_sent INTEGER DEFAULT 0,
                stickers_used INTEGER DEFAULT 0,
                reactions_given INTEGER DEFAULT 0,
                reactions_received INTEGER DEFAULT 0,
                links_shared INTEGER DEFAULT 0,
                mentions_sent INTEGER DEFAULT 0,
                mentions_received INTEGER DEFAULT 0,
                longest_message_length INTEGER DEFAULT 0,
                total_message_length INTEGER DEFAULT 0,
                message_count_with_content INTEGER DEFAULT 0,
                favorite_channel_id INTEGER DEFAULT 0,
                favorite_emoji TEXT DEFAULT '',
                voice_hours REAL DEFAULT 0,
                voice_join_count INTEGER DEFAULT 0,
                longest_vc_session INTEGER DEFAULT 0,
                current_vc_session_start INTEGER DEFAULT 0,
                current_message_streak INTEGER DEFAULT 0,
                longest_message_streak INTEGER DEFAULT 0,
                last_message_time TEXT,
                updated_at TEXT,
                PRIMARY KEY (user_id, guild_id)
            )""",
            """CREATE TABLE IF NOT EXISTS server_stats (
                guild_id INTEGER PRIMARY KEY,
                total_messages INTEGER DEFAULT 0,
                total_vc_hours REAL DEFAULT 0,
                total_commands_executed INTEGER DEFAULT 0,
                most_active_user_id INTEGER DEFAULT 0,
                most_active_channel_id INTEGER DEFAULT 0,
                total_files_uploaded INTEGER DEFAULT 0,
                total_reactions_used INTEGER DEFAULT 0,
                total_moderation_actions INTEGER DEFAULT 0,
                updated_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS channel_stats (
                guild_id INTEGER,
                channel_id INTEGER,
                total_messages INTEGER DEFAULT 0,
                most_active_user_id INTEGER DEFAULT 0,
                total_days_tracked INTEGER DEFAULT 0,
                total_message_length INTEGER DEFAULT 0,
                peak_hour INTEGER DEFAULT 0,
                peak_day TEXT DEFAULT '',
                updated_at TEXT,
                PRIMARY KEY (guild_id, channel_id)
            )""",
            """CREATE TABLE IF NOT EXISTS daily_channel_stats (
                guild_id INTEGER,
                channel_id INTEGER,
                date TEXT,
                message_count INTEGER DEFAULT 0,
                unique_users INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, channel_id, date)
            )""",
            """CREATE TABLE IF NOT EXISTS user_emoji_stats (
                user_id INTEGER,
                guild_id INTEGER,
                emoji TEXT,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, guild_id, emoji)
            )""",
            """CREATE TABLE IF NOT EXISTS daily_user_stats (
                user_id INTEGER,
                guild_id INTEGER,
                date TEXT,
                message_count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, guild_id, date)
            )""",
            """CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT
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
# OWNER CHECK FUNCTIONS - FIXED
# =========================
def has_owner_role(member: discord.Member) -> bool:
    """Check if a member has the Owner role by ID or name."""
    # First check by ID if set
    if OWNER_ROLE_ID:
        role = discord.utils.get(member.roles, id=OWNER_ROLE_ID)
        if role:
            logger.debug(f"User {member.id} has Owner role by ID: {OWNER_ROLE_ID}")
            return True
    
    # Fallback to checking by name
    role = discord.utils.get(member.roles, name=OWNER_ROLE_NAME)
    if role:
        logger.debug(f"User {member.id} has Owner role by name: {OWNER_ROLE_NAME}")
        return True
    
    logger.debug(f"User {member.id} does not have Owner role. Roles: {[r.name for r in member.roles]}")
    return False

def is_owner():
    """Check if the user has the Owner role."""
    async def predicate(interaction: discord.Interaction):
        if not interaction.guild:
            logger.warning("is_owner check called outside a guild")
            return False
        
        member = interaction.guild.get_member(interaction.user.id)
        if not member:
            logger.warning(f"Could not find member {interaction.user.id} in guild {interaction.guild.id}")
            return False
        
        has_role = has_owner_role(member)
        if not has_role:
            logger.info(f"User {interaction.user} ({interaction.user.id}) does not have Owner role")
        
        return has_role
    return app_commands.check(predicate)

def owner_role_mention() -> str:
    """Get the mention string for the owner role."""
    if OWNER_ROLE_ID:
        return f"<@&{OWNER_ROLE_ID}>"
    return f"**{OWNER_ROLE_NAME}**"

# =========================
# STATISTICS HELPER FUNCTIONS
# =========================

async def reset_weekly_monthly_stats():
    """Reset weekly and monthly message counts if needed."""
    today = datetime.now()
    current_week = today.strftime("%Y-%W")
    current_month = today.strftime("%Y-%m")
    
    # Check if we need to reset weekly stats
    result = await db.fetchone(
        "SELECT value FROM bot_state WHERE key = 'current_week'"
    )
    if result:
        stored_week = result[0]
        if stored_week != current_week:
            # Reset weekly counts for all users
            await db.execute(
                "UPDATE user_stats SET messages_week = 0"
            )
            await db.execute(
                "UPDATE bot_state SET value = ? WHERE key = 'current_week'",
                (current_week,)
            )
            await db.commit()
            logger.info(f"Reset weekly stats for week {current_week}")
    else:
        await db.execute(
            "INSERT INTO bot_state (key, value) VALUES ('current_week', ?)",
            (current_week,)
        )
        await db.commit()
    
    # Check if we need to reset monthly stats
    result = await db.fetchone(
        "SELECT value FROM bot_state WHERE key = 'current_month'"
    )
    if result:
        stored_month = result[0]
        if stored_month != current_month:
            # Reset monthly counts for all users
            await db.execute(
                "UPDATE user_stats SET messages_month = 0"
            )
            await db.execute(
                "UPDATE bot_state SET value = ? WHERE key = 'current_month'",
                (current_month,)
            )
            await db.commit()
            logger.info(f"Reset monthly stats for month {current_month}")
    else:
        await db.execute(
            "INSERT INTO bot_state (key, value) VALUES ('current_month', ?)",
            (current_month,)
        )
        await db.commit()

async def initialize_bot_state():
    """Initialize bot state table if it doesn't exist."""
    await db.execute(
        """CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )"""
    )
    await db.commit()
    
    # Initialize week and month tracking
    today = datetime.now()
    current_week = today.strftime("%Y-%W")
    current_month = today.strftime("%Y-%m")
    
    result = await db.fetchone(
        "SELECT value FROM bot_state WHERE key = 'current_week'"
    )
    if not result:
        await db.execute(
            "INSERT INTO bot_state (key, value) VALUES ('current_week', ?)",
            (current_week,)
        )
        await db.commit()
    
    result = await db.fetchone(
        "SELECT value FROM bot_state WHERE key = 'current_month'"
    )
    if not result:
        await db.execute(
            "INSERT INTO bot_state (key, value) VALUES ('current_month', ?)",
            (current_month,)
        )
        await db.commit()

async def update_user_stats(user_id: int, guild_id: int, message: discord.Message = None, is_command: bool = False):
    """Update user statistics for a message or command."""
    try:
        await reset_weekly_monthly_stats()
        
        today = datetime.now().strftime("%Y-%m-%d")
        
        result = await db.fetchone(
            "SELECT * FROM user_stats WHERE user_id=? AND guild_id=?",
            (user_id, guild_id)
        )
        
        if not result:
            await db.execute(
                """INSERT INTO user_stats (
                    user_id, guild_id, total_messages, messages_today, messages_week, messages_month,
                    commands_used, updated_at, last_message_time
                ) VALUES (?, ?, 0, 0, 0, 0, 0, ?, ?)""",
                (user_id, guild_id, datetime.now().isoformat(), datetime.now().isoformat())
            )
            await db.commit()
            result = await db.fetchone(
                "SELECT * FROM user_stats WHERE user_id=? AND guild_id=?",
                (user_id, guild_id)
            )
            if not result:
                logger.error(f"Failed to create user stats for {user_id} in guild {guild_id}")
                return
        
        try:
            current_stats = {
                "total_messages": result[2] if len(result) > 2 else 0,
                "messages_today": result[3] if len(result) > 3 else 0,
                "messages_week": result[4] if len(result) > 4 else 0,
                "messages_month": result[5] if len(result) > 5 else 0,
                "commands_used": result[6] if len(result) > 6 else 0,
                "attachments_sent": result[7] if len(result) > 7 else 0,
                "images_sent": result[8] if len(result) > 8 else 0,
                "gifs_sent": result[9] if len(result) > 9 else 0,
                "stickers_used": result[10] if len(result) > 10 else 0,
                "reactions_given": result[11] if len(result) > 11 else 0,
                "reactions_received": result[12] if len(result) > 12 else 0,
                "links_shared": result[13] if len(result) > 13 else 0,
                "mentions_sent": result[14] if len(result) > 14 else 0,
                "mentions_received": result[15] if len(result) > 15 else 0,
                "longest_message_length": result[16] if len(result) > 16 else 0,
                "total_message_length": result[17] if len(result) > 17 else 0,
                "message_count_with_content": result[18] if len(result) > 18 else 0,
                "favorite_channel_id": result[19] if len(result) > 19 else 0,
                "favorite_emoji": result[20] if len(result) > 20 else "",
                "voice_hours": result[21] if len(result) > 21 else 0.0,
                "voice_join_count": result[22] if len(result) > 22 else 0,
                "longest_vc_session": result[23] if len(result) > 23 else 0,
                "current_vc_session_start": result[24] if len(result) > 24 else 0,
                "current_message_streak": result[25] if len(result) > 25 else 0,
                "longest_message_streak": result[26] if len(result) > 26 else 0,
                "last_message_time": result[27] if len(result) > 27 else None,
            }
        except IndexError as e:
            logger.error(f"Index error parsing user_stats: {e}")
            return
        
        current_streak = current_stats["current_message_streak"]
        if current_stats["last_message_time"]:
            try:
                last_msg_time = datetime.fromisoformat(current_stats["last_message_time"])
                time_diff = (datetime.now() - last_msg_time).total_seconds()
                if time_diff < 3600:
                    current_streak += 1
                else:
                    current_streak = 1
            except (ValueError, TypeError):
                current_streak = 1
        else:
            current_streak = 1
        
        if current_streak > current_stats["longest_message_streak"]:
            longest_streak = current_streak
        else:
            longest_streak = current_stats["longest_message_streak"]
        
        updates = {
            "total_messages": current_stats["total_messages"] + 1,
            "messages_today": current_stats["messages_today"] + 1,
            "messages_week": current_stats["messages_week"] + 1,
            "messages_month": current_stats["messages_month"] + 1,
            "current_message_streak": current_streak,
            "longest_message_streak": longest_streak,
            "last_message_time": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        
        if is_command:
            updates["commands_used"] = current_stats["commands_used"] + 1
        
        if message:
            if message.attachments:
                updates["attachments_sent"] = current_stats["attachments_sent"] + len(message.attachments)
                for att in message.attachments:
                    if att.content_type and att.content_type.startswith("image/"):
                        updates["images_sent"] = current_stats["images_sent"] + 1
                    elif att.content_type and att.content_type == "image/gif":
                        updates["gifs_sent"] = current_stats["gifs_sent"] + 1
            
            if message.stickers:
                updates["stickers_used"] = current_stats["stickers_used"] + len(message.stickers)
            
            link_pattern = r'https?://[^\s]+|www\.[^\s]+'
            if re.search(link_pattern, message.content):
                updates["links_shared"] = current_stats["links_shared"] + 1
            
            if message.mentions:
                updates["mentions_sent"] = current_stats["mentions_sent"] + len(message.mentions)
            
            content_len = len(message.content)
            updates["total_message_length"] = current_stats["total_message_length"] + content_len
            updates["message_count_with_content"] = current_stats["message_count_with_content"] + 1
            if content_len > current_stats["longest_message_length"]:
                updates["longest_message_length"] = content_len
            
            if message.channel.id:
                channel_count_result = await db.fetchone(
                    "SELECT count FROM message_stats WHERE guild_id=? AND channel_id=? AND user_id=? AND date=?",
                    (guild_id, message.channel.id, user_id, today)
                )
                channel_count = channel_count_result[0] if channel_count_result else 1
                
                fav_id = current_stats.get("favorite_channel_id", 0)
                if fav_id:
                    fav_count_result = await db.fetchone(
                        "SELECT count FROM message_stats WHERE guild_id=? AND channel_id=? AND user_id=? AND date=?",
                        (guild_id, fav_id, user_id, today)
                    )
                    fav_count = fav_count_result[0] if fav_count_result else 0
                    if channel_count > fav_count:
                        updates["favorite_channel_id"] = message.channel.id
                else:
                    updates["favorite_channel_id"] = message.channel.id
        
        await db.execute(
            """INSERT INTO daily_user_stats (user_id, guild_id, date, message_count)
               VALUES (?, ?, ?, 1)
               ON CONFLICT(user_id, guild_id, date)
               DO UPDATE SET message_count = message_count + 1""",
            (user_id, guild_id, today)
        )
        
        if updates:
            set_clause = ", ".join([f"{key} = ?" for key in updates.keys()])
            values = list(updates.values())
            values.extend([user_id, guild_id])
            
            await db.execute(
                f"UPDATE user_stats SET {set_clause} WHERE user_id=? AND guild_id=?",
                tuple(values)
            )
            await db.commit()
        
        await update_server_stats(guild_id, message, is_command)
        
        if message:
            await update_channel_stats(guild_id, message.channel.id, user_id, message.content)
            
    except Exception as e:
        logger.error(f"Error updating user stats: {e}")
        logger.error(traceback.format_exc())

async def update_server_stats(guild_id: int, message: discord.Message = None, is_command: bool = False):
    """Update server-wide statistics."""
    try:
        result = await db.fetchone("SELECT * FROM server_stats WHERE guild_id=?", (guild_id,))
        
        if not result:
            await db.execute(
                """INSERT INTO server_stats (
                    guild_id, total_messages, total_vc_hours, total_commands_executed,
                    total_files_uploaded, total_reactions_used, total_moderation_actions,
                    updated_at
                ) VALUES (?, 0, 0, 0, 0, 0, 0, ?)""",
                (guild_id, datetime.now().isoformat())
            )
            await db.commit()
            result = await db.fetchone("SELECT * FROM server_stats WHERE guild_id=?", (guild_id,))
            if not result:
                return
        
        updates = {}
        if message:
            updates["total_messages"] = (result[1] if result[1] is not None else 0) + 1
            if message.attachments:
                updates["total_files_uploaded"] = (result[6] if result[6] is not None else 0) + len(message.attachments)
        
        if is_command:
            updates["total_commands_executed"] = (result[3] if result[3] is not None else 0) + 1
        
        if updates:
            set_clause = ", ".join([f"{key} = ?" for key in updates.keys()])
            values = list(updates.values())
            values.append(datetime.now().isoformat())
            values.append(guild_id)
            await db.execute(
                f"UPDATE server_stats SET {set_clause}, updated_at=? WHERE guild_id=?",
                tuple(values)
            )
            await db.commit()
        
        await update_most_active(guild_id)
        
    except Exception as e:
        logger.error(f"Error updating server stats: {e}")

async def update_channel_stats(guild_id: int, channel_id: int, user_id: int, content: str):
    """Update channel statistics."""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        
        result = await db.fetchone(
            "SELECT * FROM channel_stats WHERE guild_id=? AND channel_id=?",
            (guild_id, channel_id)
        )
        
        if not result:
            await db.execute(
                """INSERT INTO channel_stats (
                    guild_id, channel_id, total_messages, most_active_user_id,
                    total_days_tracked, total_message_length, updated_at
                ) VALUES (?, ?, 0, 0, 0, 0, ?)""",
                (guild_id, channel_id, datetime.now().isoformat())
            )
            await db.commit()
            result = await db.fetchone(
                "SELECT * FROM channel_stats WHERE guild_id=? AND channel_id=?",
                (guild_id, channel_id)
            )
            if not result:
                return
        
        updates = {
            "total_messages": (result[2] if result[2] is not None else 0) + 1,
            "total_message_length": (result[5] if result[5] is not None else 0) + len(content),
            "updated_at": datetime.now().isoformat()
        }
        
        await db.execute(
            """INSERT INTO daily_channel_stats (guild_id, channel_id, date, message_count, unique_users)
               VALUES (?, ?, ?, 1, 1)
               ON CONFLICT(guild_id, channel_id, date)
               DO UPDATE SET message_count = message_count + 1""",
            (guild_id, channel_id, today)
        )
        
        user_count = await db.fetchone(
            "SELECT count FROM message_stats WHERE guild_id=? AND channel_id=? AND user_id=? AND date=?",
            (guild_id, channel_id, user_id, today)
        )
        current_most = result[3] if result[3] is not None else 0
        if current_most:
            current_count = await db.fetchone(
                "SELECT count FROM message_stats WHERE guild_id=? AND channel_id=? AND user_id=? AND date=?",
                (guild_id, channel_id, current_most, today)
            )
            current_count_val = current_count[0] if current_count else 0
            new_count = user_count[0] if user_count else 1
            if new_count > current_count_val:
                updates["most_active_user_id"] = user_id
        else:
            updates["most_active_user_id"] = user_id
        
        hour = datetime.now().hour
        current_peak_hour = result[6] if result[6] is not None else 0
        if hour > current_peak_hour:
            updates["peak_hour"] = hour
        
        day = datetime.now().strftime("%A")
        current_peak_day = result[7] if result[7] is not None else ""
        if day and not current_peak_day:
            updates["peak_day"] = day
        
        day_count = await db.fetchone(
            "SELECT COUNT(DISTINCT date) FROM daily_channel_stats WHERE guild_id=? AND channel_id=?",
            (guild_id, channel_id)
        )
        if day_count and day_count[0] > 0:
            updates["total_days_tracked"] = day_count[0]
        
        set_clause = ", ".join([f"{key} = ?" for key in updates.keys()])
        values = list(updates.values())
        values.extend([guild_id, channel_id])
        
        await db.execute(
            f"UPDATE channel_stats SET {set_clause} WHERE guild_id=? AND channel_id=?",
            tuple(values)
        )
        await db.commit()
        
    except Exception as e:
        logger.error(f"Error updating channel stats: {e}")

async def update_most_active(guild_id: int):
    """Update the most active user and channel in server stats."""
    try:
        user_result = await db.fetchone(
            """SELECT user_id, SUM(message_count) as total
               FROM member_message_counts
               WHERE guild_id=?
               GROUP BY user_id
               ORDER BY total DESC
               LIMIT 1""",
            (guild_id,)
        )
        
        if user_result and user_result[0]:
            await db.execute(
                "UPDATE server_stats SET most_active_user_id=? WHERE guild_id=?",
                (user_result[0], guild_id)
            )
            await db.commit()
        
        channel_result = await db.fetchone(
            """SELECT channel_id, COUNT(*) as total
               FROM message_stats
               WHERE guild_id=?
               GROUP BY channel_id
               ORDER BY total DESC
               LIMIT 1""",
            (guild_id,)
        )
        
        if channel_result and channel_result[0]:
            await db.execute(
                "UPDATE server_stats SET most_active_channel_id=? WHERE guild_id=?",
                (channel_result[0], guild_id)
            )
            await db.commit()
            
    except Exception as e:
        logger.error(f"Error updating most active: {e}")

async def update_voice_stats(user_id: int, guild_id: int, is_join: bool = True, duration: float = 0):
    """Update voice statistics for a user."""
    try:
        result = await db.fetchone(
            "SELECT * FROM user_stats WHERE user_id=? AND guild_id=?",
            (user_id, guild_id)
        )
        
        if not result:
            await db.execute(
                """INSERT INTO user_stats (
                    user_id, guild_id, voice_hours, voice_join_count, updated_at
                ) VALUES (?, ?, 0, 0, ?)""",
                (user_id, guild_id, datetime.now().isoformat())
            )
            await db.commit()
            result = await db.fetchone(
                "SELECT * FROM user_stats WHERE user_id=? AND guild_id=?",
                (user_id, guild_id)
            )
            if not result:
                return
        
        if is_join:
            voice_join_count = (result[22] if len(result) > 22 and result[22] is not None else 0) + 1
            await db.execute(
                """UPDATE user_stats
                   SET voice_join_count = ?, current_vc_session_start = ?, updated_at = ?
                   WHERE user_id = ? AND guild_id = ?""",
                (voice_join_count, int(time.time()), datetime.now().isoformat(), user_id, guild_id)
            )
            await db.commit()
        else:
            session_start = result[24] if len(result) > 24 else 0
            if session_start and session_start > 0:
                session_duration = int(time.time()) - session_start
                if session_duration > 0:
                    hours = session_duration / 3600.0
                    current_hours = result[21] if len(result) > 21 and result[21] is not None else 0.0
                    new_total_hours = current_hours + hours
                    
                    longest = result[23] if len(result) > 23 and result[23] is not None else 0
                    if session_duration > longest:
                        longest = session_duration
                    
                    await db.execute(
                        """UPDATE user_stats
                           SET voice_hours = ?, longest_vc_session = ?, current_vc_session_start = 0, updated_at = ?
                           WHERE user_id = ? AND guild_id = ?""",
                        (new_total_hours, longest, datetime.now().isoformat(), user_id, guild_id)
                    )
                    await db.commit()
                    
                    server_result = await db.fetchone(
                        "SELECT total_vc_hours FROM server_stats WHERE guild_id=?",
                        (guild_id,)
                    )
                    if server_result:
                        current_total = server_result[0] if server_result[0] is not None else 0.0
                        await db.execute(
                            "UPDATE server_stats SET total_vc_hours = ? WHERE guild_id=?",
                            (current_total + hours, guild_id)
                        )
                        await db.commit()
                    
    except Exception as e:
        logger.error(f"Error updating voice stats: {e}")

async def track_reaction(guild_id: int, user_id: int, emoji: str, is_given: bool = True):
    """Track reaction usage."""
    try:
        if is_given:
            result = await db.fetchone(
                "SELECT reactions_given FROM user_stats WHERE user_id=? AND guild_id=?",
                (user_id, guild_id)
            )
            
            if result:
                new_count = (result[0] if result[0] is not None else 0) + 1
                await db.execute(
                    """UPDATE user_stats SET reactions_given = ?, updated_at = ?
                       WHERE user_id=? AND guild_id=?""",
                    (new_count, datetime.now().isoformat(), user_id, guild_id)
                )
            else:
                await db.execute(
                    """INSERT INTO user_stats (user_id, guild_id, reactions_given, updated_at)
                       VALUES (?, ?, 1, ?)""",
                    (user_id, guild_id, datetime.now().isoformat())
                )
            await db.commit()
            
            emoji_str = str(emoji)
            emoji_result = await db.fetchone(
                "SELECT count FROM user_emoji_stats WHERE user_id=? AND guild_id=? AND emoji=?",
                (user_id, guild_id, emoji_str)
            )
            if emoji_result:
                await db.execute(
                    "UPDATE user_emoji_stats SET count = count + 1 WHERE user_id=? AND guild_id=? AND emoji=?",
                    (user_id, guild_id, emoji_str)
                )
            else:
                await db.execute(
                    "INSERT INTO user_emoji_stats (user_id, guild_id, emoji, count) VALUES (?, ?, ?, 1)",
                    (user_id, guild_id, emoji_str)
                )
            await db.commit()
            
            fav_emoji_result = await db.fetchone(
                """SELECT emoji FROM user_emoji_stats
                   WHERE user_id=? AND guild_id=?
                   ORDER BY count DESC LIMIT 1""",
                (user_id, guild_id)
            )
            if fav_emoji_result and fav_emoji_result[0]:
                await db.execute(
                    "UPDATE user_stats SET favorite_emoji = ? WHERE user_id=? AND guild_id=?",
                    (fav_emoji_result[0], user_id, guild_id)
                )
                await db.commit()
        
        server_result = await db.fetchone(
            "SELECT total_reactions_used FROM server_stats WHERE guild_id=?",
            (guild_id,)
        )
        if server_result:
            new_total = (server_result[0] if server_result[0] is not None else 0) + 1
            await db.execute(
                "UPDATE server_stats SET total_reactions_used = ? WHERE guild_id=?",
                (new_total, guild_id)
            )
            await db.commit()
            
    except Exception as e:
        logger.error(f"Error tracking reaction: {e}")

async def track_moderation_action(guild_id: int, action: str):
    """Track moderation actions."""
    try:
        result = await db.fetchone(
            "SELECT total_moderation_actions FROM server_stats WHERE guild_id=?",
            (guild_id,)
        )
        if result:
            new_total = (result[0] if result[0] is not None else 0) + 1
            await db.execute(
                "UPDATE server_stats SET total_moderation_actions = ? WHERE guild_id=?",
                (new_total, guild_id)
            )
        else:
            await db.execute(
                "INSERT INTO server_stats (guild_id, total_moderation_actions) VALUES (?, 1)",
                (guild_id,)
            )
        await db.commit()
    except Exception as e:
        logger.error(f"Error tracking moderation action: {e}")

async def get_user_stats(user_id: int, guild_id: int) -> Dict:
    """Get comprehensive user statistics."""
    try:
        result = await db.fetchone(
            "SELECT * FROM user_stats WHERE user_id=? AND guild_id=?",
            (user_id, guild_id)
        )
        
        if not result:
            return None
        
        return {
            "user_id": result[0],
            "guild_id": result[1],
            "total_messages": result[2] if result[2] is not None else 0,
            "messages_today": result[3] if result[3] is not None else 0,
            "messages_week": result[4] if result[4] is not None else 0,
            "messages_month": result[5] if result[5] is not None else 0,
            "commands_used": result[6] if result[6] is not None else 0,
            "attachments_sent": result[7] if result[7] is not None else 0,
            "images_sent": result[8] if result[8] is not None else 0,
            "gifs_sent": result[9] if result[9] is not None else 0,
            "stickers_used": result[10] if result[10] is not None else 0,
            "reactions_given": result[11] if result[11] is not None else 0,
            "reactions_received": result[12] if result[12] is not None else 0,
            "links_shared": result[13] if result[13] is not None else 0,
            "mentions_sent": result[14] if result[14] is not None else 0,
            "mentions_received": result[15] if result[15] is not None else 0,
            "longest_message_length": result[16] if result[16] is not None else 0,
            "total_message_length": result[17] if result[17] is not None else 0,
            "message_count_with_content": result[18] if result[18] is not None else 0,
            "favorite_channel_id": result[19] if result[19] is not None else 0,
            "favorite_emoji": result[20] if result[20] is not None else "",
            "voice_hours": result[21] if result[21] is not None else 0.0,
            "voice_join_count": result[22] if result[22] is not None else 0,
            "longest_vc_session": result[23] if result[23] is not None else 0,
            "current_vc_session_start": result[24] if result[24] is not None else 0,
            "current_message_streak": result[25] if result[25] is not None else 0,
            "longest_message_streak": result[26] if result[26] is not None else 0,
            "last_message_time": result[27] if len(result) > 27 else None,
            "updated_at": result[28] if len(result) > 28 else None,
        }
    except Exception as e:
        logger.error(f"Error getting user stats: {e}")
        return None

async def get_server_stats(guild_id: int) -> Dict:
    """Get server-wide statistics."""
    try:
        result = await db.fetchone(
            "SELECT * FROM server_stats WHERE guild_id=?",
            (guild_id,)
        )
        
        if not result:
            return None
        
        return {
            "guild_id": result[0],
            "total_messages": result[1] if result[1] is not None else 0,
            "total_vc_hours": result[2] if result[2] is not None else 0.0,
            "total_commands_executed": result[3] if result[3] is not None else 0,
            "most_active_user_id": result[4] if result[4] is not None else 0,
            "most_active_channel_id": result[5] if result[5] is not None else 0,
            "total_files_uploaded": result[6] if result[6] is not None else 0,
            "total_reactions_used": result[7] if result[7] is not None else 0,
            "total_moderation_actions": result[8] if result[8] is not None else 0,
            "updated_at": result[9] if len(result) > 9 else None,
        }
    except Exception as e:
        logger.error(f"Error getting server stats: {e}")
        return None

async def get_channel_stats(guild_id: int, channel_id: int) -> Dict:
    """Get channel statistics."""
    try:
        result = await db.fetchone(
            "SELECT * FROM channel_stats WHERE guild_id=? AND channel_id=?",
            (guild_id, channel_id)
        )
        
        if not result:
            return None
        
        return {
            "guild_id": result[0],
            "channel_id": result[1],
            "total_messages": result[2] if result[2] is not None else 0,
            "most_active_user_id": result[3] if result[3] is not None else 0,
            "total_days_tracked": result[4] if result[4] is not None else 0,
            "total_message_length": result[5] if result[5] is not None else 0,
            "peak_hour": result[6] if result[6] is not None else 0,
            "peak_day": result[7] if result[7] is not None else "",
            "updated_at": result[8] if len(result) > 8 else None,
        }
    except Exception as e:
        logger.error(f"Error getting channel stats: {e}")
        return None

async def get_leaderboard(guild_id: int, stat_type: str, limit: int = 10) -> List:
    """Get leaderboard for a specific stat type."""
    try:
        query = ""
        
        if stat_type == "messages":
            query = """
                SELECT user_id, total_messages
                FROM user_stats
                WHERE guild_id=? AND total_messages > 0
                ORDER BY total_messages DESC
                LIMIT ?
            """
        elif stat_type == "vc_hours":
            query = """
                SELECT user_id, voice_hours
                FROM user_stats
                WHERE guild_id=? AND voice_hours > 0
                ORDER BY voice_hours DESC
                LIMIT ?
            """
        elif stat_type == "commands":
            query = """
                SELECT user_id, commands_used
                FROM user_stats
                WHERE guild_id=? AND commands_used > 0
                ORDER BY commands_used DESC
                LIMIT ?
            """
        elif stat_type == "reactions":
            query = """
                SELECT user_id, reactions_given
                FROM user_stats
                WHERE guild_id=? AND reactions_given > 0
                ORDER BY reactions_given DESC
                LIMIT ?
            """
        elif stat_type == "attachments":
            query = """
                SELECT user_id, attachments_sent
                FROM user_stats
                WHERE guild_id=? AND attachments_sent > 0
                ORDER BY attachments_sent DESC
                LIMIT ?
            """
        elif stat_type == "mentions":
            query = """
                SELECT user_id, mentions_sent
                FROM user_stats
                WHERE guild_id=? AND mentions_sent > 0
                ORDER BY mentions_sent DESC
                LIMIT ?
            """
        elif stat_type == "streak":
            query = """
                SELECT user_id, longest_message_streak
                FROM user_stats
                WHERE guild_id=? AND longest_message_streak > 0
                ORDER BY longest_message_streak DESC
                LIMIT ?
            """
        elif stat_type == "channels":
            query = """
                SELECT channel_id, total_messages
                FROM channel_stats
                WHERE guild_id=? AND total_messages > 0
                ORDER BY total_messages DESC
                LIMIT ?
            """
        else:
            return []
        
        return await db.fetchall(query, (guild_id, limit))
    except Exception as e:
        logger.error(f"Error getting leaderboard: {e}")
        return []

async def get_fun_titles(user_stats: Dict, guild: discord.Guild, member: discord.Member) -> List[str]:
    """Get fun titles for a user based on their statistics."""
    titles = []
    
    try:
        if user_stats["total_messages"] > 1000:
            titles.append("💬 Biggest Yapper")
        elif user_stats["total_messages"] > 500:
            titles.append("🗣️ Professional Chatter")
        elif user_stats["total_messages"] > 100:
            titles.append("📢 Talkative")
        
        if user_stats["voice_hours"] > 100:
            titles.append("🎧 VC Addict")
        elif user_stats["voice_hours"] > 50:
            titles.append("🎙️ VC Enthusiast")
        elif user_stats["voice_hours"] > 10:
            titles.append("🔊 VC Regular")
        
        if user_stats["longest_message_streak"] > 100:
            titles.append("🔥 Streak God")
        elif user_stats["longest_message_streak"] > 50:
            titles.append("⚡ Streak Master")
        elif user_stats["longest_message_streak"] > 20:
            titles.append("📈 Streaker")
        
        if user_stats["attachments_sent"] > 100:
            titles.append("📎 Attachment Dealer")
        elif user_stats["attachments_sent"] > 50:
            titles.append("📦 Attachment Enthusiast")
        
        if user_stats["reactions_given"] > 500:
            titles.append("🎭 Emoji Criminal")
        elif user_stats["reactions_given"] > 200:
            titles.append("😄 Emoji Addict")
        elif user_stats["reactions_given"] > 50:
            titles.append("👍 Reaction Giver")
        
        if user_stats["links_shared"] > 50:
            titles.append("🔗 Link Lord")
        elif user_stats["links_shared"] > 20:
            titles.append("📎 Linker")
        
        if user_stats["commands_used"] > 100:
            titles.append("🎮 Command Master")
        elif user_stats["commands_used"] > 50:
            titles.append("⌨️ Command User")
        
        if user_stats["images_sent"] > 100:
            titles.append("🖼️ Image Lord")
        elif user_stats["images_sent"] > 50:
            titles.append("📸 Image Sharer")
        
        if user_stats["gifs_sent"] > 50:
            titles.append("🎬 GIF Master")
        elif user_stats["gifs_sent"] > 20:
            titles.append("🎞️ GIF Sharer")
        
        if user_stats["stickers_used"] > 50:
            titles.append("🎨 Sticker Lord")
        elif user_stats["stickers_used"] > 20:
            titles.append("🎭 Sticker User")
        
        if user_stats.get("last_message_time"):
            try:
                last_msg = datetime.fromisoformat(user_stats["last_message_time"])
                if 0 <= last_msg.hour < 6:
                    titles.append("🦉 Midnight Goblin")
            except:
                pass
        
        top_messages = await db.fetchone(
            "SELECT COUNT(*) FROM user_stats WHERE guild_id=? AND total_messages > ?",
            (user_stats["guild_id"], user_stats["total_messages"])
        )
        if top_messages and top_messages[0] < 10:
            titles.append("👑 Top Chatter")
        
        top_vc = await db.fetchone(
            "SELECT COUNT(*) FROM user_stats WHERE guild_id=? AND voice_hours > ?",
            (user_stats["guild_id"], user_stats["voice_hours"])
        )
        if top_vc and top_vc[0] < 10 and user_stats["voice_hours"] > 0:
            titles.append("🎵 VC Royalty")
        
        if not titles:
            titles.append("🌱 Newbie")
            
    except Exception as e:
        logger.error(f"Error getting fun titles: {e}")
        titles.append("🌱 Newbie")
    
    return titles

# =========================
# DATABASE HELPER FUNCTIONS
# =========================

async def add_warning(user_id, guild_id, reason, moderator=None):
    await db.execute(
        "INSERT INTO warnings (user_id, guild_id, reason, moderator, timestamp) VALUES (?, ?, ?, ?, ?)",
        (user_id, guild_id, reason, moderator, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    await db.commit()
    await track_moderation_action(guild_id, "warn")

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
    attachments = json.dumps([{"filename": a.filename, "url": a.url} for a in message.attachments])
    await db.execute(
        """INSERT OR REPLACE INTO message_cache (message_id, content, author_id, channel_id, guild_id, timestamp, attachments)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (message.id, message.content[:2000] if message.content else "", message.author.id,
         message.channel.id, message.guild.id, datetime.now().isoformat(), attachments)
    )
    await db.commit()

async def add_snipe(guild_id, channel_id, message_id, content, author_id, attachments=""):
    await db.execute(
        """INSERT INTO snipe_cache (guild_id, channel_id, message_id, content, author_id, timestamp, deleted_at, attachments)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (guild_id, channel_id, message_id, content[:2000] if content else "", author_id,
         datetime.now().isoformat(), datetime.now().isoformat(), attachments)
    )
    await db.commit()
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

async def save_control_panel(guild_id: int, channel_id: int, message_id: int):
    await db.execute(
        """INSERT OR REPLACE INTO control_panel (guild_id, channel_id, message_id, last_updated)
           VALUES (?, ?, ?, ?)""",
        (guild_id, channel_id, message_id, datetime.now().isoformat())
    )
    await db.commit()

async def get_control_panel(guild_id: int):
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
    await db.execute("DELETE FROM control_panel WHERE guild_id=?", (guild_id,))
    await db.commit()

async def log_to_mod_channel(guild, embed):
    config = await get_mod_logs_config(guild.id)
    if not config or not config["enabled"]:
        return
    
    channel = guild.get_channel(config["channel_id"])
    if channel:
        try:
            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Failed to send mod log: {e}")

async def get_balance_simple(user_id: int) -> Tuple[int, int]:
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
    result = await db.fetchone(
        "SELECT COUNT(*) FROM history WHERE guild_id=? AND event_type=? AND timestamp LIKE ?",
        (guild_id, event_type, f"{date}%")
    )
    return result[0] if result else 0

# =========================
# CONSTANTS
# =========================
INVITE_REGEX = r"(discord\.gg/|discordapp\.com/invite/)"

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

# =========================
# GIVEAWAY TRACKING
# =========================
active_giveaways = {}

# =========================
# TICKET VIEWS
# =========================
class TicketCloseView(discord.ui.View):
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
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="🎫 Create Ticket", style=discord.ButtonStyle.primary, custom_id="create_ticket")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        user = interaction.user
        
        existing = discord.utils.get(guild.text_channels, name=f"ticket-{user.name.lower().replace(' ', '-')}")
        if existing:
            return await interaction.response.send_message("❌ You already have an open ticket!", ephemeral=True)
        
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

class GiveawayView(discord.ui.View):
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
    def __init__(self):
        self.user_messages = defaultdict(lambda: defaultdict(list))

    async def check_message(self, message):
        if message.author.bot:
            return
        
        config = await get_automod_config(message.guild.id)
        
        if config["anti_spam"]:
            user_id = message.author.id
            guild_id = message.guild.id
            now = time.time()
            
            self.user_messages[guild_id][user_id] = [
                t for t in self.user_messages[guild_id][user_id]
                if now - t < config["spam_window"]
            ]
            
            self.user_messages[guild_id][user_id].append(now)
            
            if len(self.user_messages[guild_id][user_id]) > config["spam_threshold"]:
                await self.take_action(message, "Spam detected")
                return True
        
        if config["anti_invite"]:
            if re.search(INVITE_REGEX, message.content.lower()):
                await self.take_action(message, "Invite link detected")
                return True
        
        if config["anti_mentions"]:
            mention_count = len(message.mentions) + len(message.role_mentions)
            if mention_count > config["mention_limit"]:
                await self.take_action(message, f"Mass mention detected ({mention_count} mentions)")
                return True
        
        if config["bad_words"] and config["bad_words_list"]:
            bad_words = [w.strip().lower() for w in config["bad_words_list"].split(",") if w.strip()]
            for word in bad_words:
                if word in message.content.lower():
                    await self.take_action(message, f"Bad word detected: {word}")
                    return True
        
        return False
    
    async def take_action(self, message, reason):
        try:
            await message.delete()
            await message.channel.send(
                f"<@{message.author.id}> ⚠️ Message removed: {reason}",
                delete_after=5
            )
            await add_warning(message.author.id, message.guild.id, f"AutoMod: {reason}", "AutoMod")
            await add_history(message.guild.id, message.author.id, str(message.author), "AUTO_MOD", reason)
            
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
# CONTROL PANEL HANDLERS
# =========================

async def handle_refresh_panel(interaction: discord.Interaction):
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
    
    embed = discord.Embed(
        title="🎛️ Bot Control Panel",
        description="Welcome to the bot control panel! Use the buttons below to manage the bot.",
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )
    embed.add_field(name="🤖 Status", value="Bot is online and ready", inline=True)
    embed.add_field(name="👑 Owner Role", value=owner_role_mention(), inline=True)
    embed.add_field(name="📊 Commands", value=f"{len(bot.tree.get_commands())} total", inline=True)
    embed.set_footer(text="Control Panel • Owner Role Only")
    
    view = ControlPanelView(bot)
    new_message = await channel.send(embed=embed, view=view)
    
    await save_control_panel(interaction.guild_id, channel.id, new_message.id)
    await interaction.response.send_message("✅ Control panel refreshed!", ephemeral=True)

async def handle_clear_cache(interaction: discord.Interaction):
    await db.execute("DELETE FROM message_cache")
    await db.execute("DELETE FROM snipe_cache")
    await db.execute("DELETE FROM edit_snipe_cache")
    await db.commit()
    
    await interaction.response.send_message("🗑️ All caches cleared successfully!", ephemeral=True)

async def handle_show_stats(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📊 Bot Statistics",
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )
    embed.add_field(name="🤖 Bot Name", value=bot.user.name, inline=True)
    embed.add_field(name="📈 Guilds", value=len(bot.guilds), inline=True)
    embed.add_field(name="⚙️ Commands", value=len(bot.tree.get_commands()), inline=True)
    embed.add_field(name="⏱️ Uptime", value=str(datetime.now() - START_TIME).split('.')[0], inline=True)
    embed.add_field(name="📊 Total Members", value=sum(g.member_count for g in bot.guilds), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

async def handle_db_status(interaction: discord.Interaction):
    try:
        result = await db.fetchone("SELECT sqlite_version()")
        version = result[0] if result else "Unknown"
        
        tables = ["warnings", "economy", "leveling", "tickets", "giveaways", "polls"]
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

# =========================
# COMPLETE MODERATION CONTROL PANEL VIEWS
# =========================

class ModerationActionModal(discord.ui.Modal, title="Moderation Action"):
    def __init__(self, action_type: str, target: Any = None):
        super().__init__()
        self.action_type = action_type
        self.target = target
        
        self.reason_input = discord.ui.TextInput(
            label="Reason",
            placeholder="Enter the reason for this action...",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500
        )
        self.add_item(self.reason_input)
        
        if action_type in ["timeout", "mute"]:
            self.duration_input = discord.ui.TextInput(
                label="Duration (minutes)",
                placeholder="Enter duration in minutes (e.g., 5, 60)",
                required=True,
                max_length=10
            )
            self.add_item(self.duration_input)
        
        if action_type == "clear":
            self.amount_input = discord.ui.TextInput(
                label="Number of messages to clear",
                placeholder="Enter amount (1-100)",
                required=True,
                max_length=3
            )
            self.add_item(self.amount_input)
        
        if action_type == "slowmode":
            self.slowmode_input = discord.ui.TextInput(
                label="Slowmode (seconds)",
                placeholder="Enter seconds (0-21600)",
                required=True,
                max_length=10
            )
            self.add_item(self.slowmode_input)

    async def on_submit(self, interaction: discord.Interaction):
        reason = self.reason_input.value or "No reason provided"
        guild = interaction.guild
        
        try:
            if self.action_type in ["warn", "add_warning"]:
                await add_warning(self.target.id, guild.id, reason, str(interaction.user))
                await add_history(guild.id, self.target.id, str(self.target), "WARN", reason)
                await track_moderation_action(guild.id, "warn")
                await interaction.response.send_message(f"✅ Warned {self.target.mention} | Reason: {reason}", ephemeral=True)
                
            elif self.action_type == "ban":
                await self.target.ban(reason=reason)
                await add_history(guild.id, self.target.id, str(self.target), "BAN", f"Banned by {interaction.user}: {reason}")
                await track_moderation_action(guild.id, "ban")
                await interaction.response.send_message(f"✅ Banned {self.target.mention} | Reason: {reason}", ephemeral=True)
                
            elif self.action_type == "kick":
                await self.target.kick(reason=reason)
                await add_history(guild.id, self.target.id, str(self.target), "KICK", reason)
                await track_moderation_action(guild.id, "kick")
                await interaction.response.send_message(f"✅ Kicked {self.target.mention} | Reason: {reason}", ephemeral=True)
                
            elif self.action_type in ["timeout", "mute"]:
                try:
                    duration_minutes = int(self.duration_input.value)
                    if duration_minutes <= 0:
                        await interaction.response.send_message("❌ Duration must be positive.", ephemeral=True)
                        return
                    await self.target.timeout(utcnow() + timedelta(minutes=duration_minutes), reason=reason)
                    await add_history(guild.id, self.target.id, str(self.target), "TIMEOUT", f"Timed out for {duration_minutes}min by {interaction.user}: {reason}")
                    await track_moderation_action(guild.id, "timeout")
                    await interaction.response.send_message(f"✅ Timed out {self.target.mention} for {duration_minutes} minutes | Reason: {reason}", ephemeral=True)
                except ValueError:
                    await interaction.response.send_message("❌ Invalid duration. Please enter a number.", ephemeral=True)
                    return
                    
            elif self.action_type == "unmute":
                await self.target.timeout(None)
                await add_history(guild.id, self.target.id, str(self.target), "UNMUTE", f"Unmuted by {interaction.user}")
                await interaction.response.send_message(f"✅ Unmuted {self.target.mention}", ephemeral=True)
                    
            elif self.action_type == "clear":
                try:
                    amount = min(int(self.amount_input.value), 100)
                    deleted = await self.target.purge(limit=amount)
                    await add_history(guild.id, interaction.user.id, str(interaction.user), "CLEAR", f"Cleared {len(deleted)} messages in #{self.target.name}")
                    await interaction.response.send_message(f"🧹 Deleted {len(deleted)} messages from {self.target.mention} | Reason: {reason}", ephemeral=True)
                except ValueError:
                    await interaction.response.send_message("❌ Invalid amount. Please enter a number.", ephemeral=True)
                    return
                    
            elif self.action_type == "thanos_snap":
                total = 0
                while True:
                    deleted = await self.target.purge(limit=100)
                    total += len(deleted)
                    if not deleted:
                        break
                await add_history(guild.id, interaction.user.id, str(interaction.user), "CLEARALL", f"Cleared {total} messages in #{self.target.name}")
                await interaction.response.send_message(f"🧹 Cleared {total} messages from {self.target.mention} | Reason: {reason}", ephemeral=True)
                    
            elif self.action_type == "lock":
                overwrite = self.target.overwrites_for(guild.default_role)
                overwrite.send_messages = False
                await self.target.set_permissions(guild.default_role, overwrite=overwrite)
                await add_history(guild.id, interaction.user.id, str(interaction.user), "LOCK", f"Locked #{self.target.name}: {reason}")
                await interaction.response.send_message(f"🔒 Locked {self.target.mention} | Reason: {reason}", ephemeral=True)
                
            elif self.action_type == "unlock":
                overwrite = self.target.overwrites_for(guild.default_role)
                overwrite.send_messages = None
                await self.target.set_permissions(guild.default_role, overwrite=overwrite)
                await add_history(guild.id, interaction.user.id, str(interaction.user), "UNLOCK", f"Unlocked #{self.target.name}: {reason}")
                await interaction.response.send_message(f"🔓 Unlocked {self.target.mention} | Reason: {reason}", ephemeral=True)
                
            elif self.action_type == "slowmode":
                try:
                    seconds = int(self.slowmode_input.value)
                    if seconds < 0 or seconds > 21600:
                        await interaction.response.send_message("❌ Slowmode must be between 0 and 21600 seconds.", ephemeral=True)
                        return
                    await self.target.edit(slowmode_delay=seconds)
                    await add_history(guild.id, interaction.user.id, str(interaction.user), "SLOWMODE", f"Set slowmode in #{self.target.name} to {seconds}s: {reason}")
                    await interaction.response.send_message(f"⏱️ Slowmode set to {seconds} seconds in {self.target.mention} | Reason: {reason}", ephemeral=True)
                except ValueError:
                    await interaction.response.send_message("❌ Invalid input. Please enter a number.", ephemeral=True)
                    return
                
            elif self.action_type == "hide":
                await self.target.set_permissions(guild.default_role, view_channel=False)
                await add_history(guild.id, interaction.user.id, str(interaction.user), "HIDE", f"Hid #{self.target.name}: {reason}")
                await interaction.response.send_message(f"👁️ Hidden {self.target.mention} | Reason: {reason}", ephemeral=True)
                
            elif self.action_type == "show":
                await self.target.set_permissions(guild.default_role, view_channel=None)
                await add_history(guild.id, interaction.user.id, str(interaction.user), "SHOW", f"Showed #{self.target.name}: {reason}")
                await interaction.response.send_message(f"👁️ Shown {self.target.mention} | Reason: {reason}", ephemeral=True)
                
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to perform this action.", ephemeral=True)
        except Exception as e:
            logger.error(f"Mod action error: {e}")
            await interaction.response.send_message(f"❌ Error: {str(e)[:100]}", ephemeral=True)

class ModerationMemberSelectView(discord.ui.View):
    def __init__(self, action_type: str, guild: discord.Guild, callback_func):
        super().__init__(timeout=60)
        self.action_type = action_type
        self.guild = guild
        
        options = []
        members = [m for m in guild.members if not m.bot and m.id != guild.me.id][:25]
        for member in members:
            options.append(
                discord.SelectOption(
                    label=member.display_name[:25],
                    value=str(member.id),
                    description=f"@{member.name[:20]}",
                    emoji="👤"
                )
            )
        
        if not options:
            options.append(
                discord.SelectOption(
                    label="No members available",
                    value="none",
                    default=True
                )
            )
        
        self.select = discord.ui.Select(
            placeholder=f"Select a member for {action_type.replace('_', ' ').title()}...",
            options=options,
            min_values=1,
            max_values=1
        )
        
        async def select_callback(select_interaction: discord.Interaction):
            if self.select.values[0] == "none":
                return await select_interaction.response.send_message("❌ No members available.", ephemeral=True)
            member_id = int(self.select.values[0])
            member = self.guild.get_member(member_id)
            if member:
                await callback_func(select_interaction, member, self.action_type)
            else:
                await select_interaction.response.send_message("❌ Member not found.", ephemeral=True)
        
        self.select.callback = select_callback
        self.add_item(self.select)
        
        cancel_button = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            custom_id="cancel_mod_action"
        )
        
        async def cancel_callback(button_interaction: discord.Interaction):
            await button_interaction.response.send_message("❌ Action cancelled.", ephemeral=True)
        
        cancel_button.callback = cancel_callback
        self.add_item(cancel_button)

class ModerationChannelSelectView(discord.ui.View):
    def __init__(self, action_type: str, guild: discord.Guild, callback_func, destructive: bool = False):
        super().__init__(timeout=60)
        self.action_type = action_type
        self.guild = guild
        self.destructive = destructive
        self.selected_channel = None
        
        options = []
        channels = [c for c in guild.text_channels][:25]
        for channel in channels:
            options.append(
                discord.SelectOption(
                    label=f"#{channel.name[:25]}",
                    value=str(channel.id),
                    description=f"ID: {channel.id}",
                    emoji="📢"
                )
            )
        
        if not options:
            options.append(
                discord.SelectOption(
                    label="No channels available",
                    value="none",
                    default=True
                )
            )
        
        self.select = discord.ui.Select(
            placeholder=f"Select a channel for {action_type.replace('_', ' ').title()}...",
            options=options,
            min_values=1,
            max_values=1
        )
        
        async def select_callback(select_interaction: discord.Interaction):
            if self.select.values[0] == "none":
                return await select_interaction.response.send_message("❌ No channels available.", ephemeral=True)
            channel_id = int(self.select.values[0])
            channel = self.guild.get_channel(channel_id)
            if channel:
                self.selected_channel = channel
                if self.destructive:
                    await self._show_confirmation(select_interaction, channel)
                else:
                    await callback_func(select_interaction, channel, self.action_type)
            else:
                await select_interaction.response.send_message("❌ Channel not found.", ephemeral=True)
        
        self.select.callback = select_callback
        self.add_item(self.select)
        
        cancel_button = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            custom_id="cancel_mod_action"
        )
        
        async def cancel_callback(button_interaction: discord.Interaction):
            await button_interaction.response.send_message("❌ Action cancelled.", ephemeral=True)
        
        cancel_button.callback = cancel_callback
        self.add_item(cancel_button)
    
    async def _show_confirmation(self, interaction: discord.Interaction, channel: discord.TextChannel):
        action_name = self.action_type.replace('_', ' ').title()
        embed = discord.Embed(
            title=f"⚠️ Confirm {action_name}",
            description=f"Are you sure you want to perform **{action_name}** on {channel.mention}?\n\n"
                       f"This action will **permanently delete all messages** in this channel and cannot be undone!",
            color=discord.Color.red()
        )
        
        view = discord.ui.View()
        
        confirm_button = discord.ui.Button(
            label="✅ Confirm",
            style=discord.ButtonStyle.danger,
            custom_id="confirm_destructive"
        )
        
        async def confirm_callback(button_interaction: discord.Interaction):
            await self._execute_action(button_interaction, channel)
        
        confirm_button.callback = confirm_callback
        view.add_item(confirm_button)
        
        cancel_button = discord.ui.Button(
            label="❌ Cancel",
            style=discord.ButtonStyle.secondary,
            custom_id="cancel_destructive"
        )
        
        async def cancel_callback(button_interaction: discord.Interaction):
            await button_interaction.response.send_message("❌ Action cancelled.", ephemeral=True)
        
        cancel_button.callback = cancel_callback
        view.add_item(cancel_button)
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    async def _execute_action(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if self.action_type in ["thanos_snap", "clear"]:
            modal = ModerationActionModal(self.action_type, channel)
            await interaction.response.send_modal(modal)
        else:
            await interaction.response.send_message("❌ Unknown action.", ephemeral=True)

class ModerationPanelView(discord.ui.View):
    def __init__(self, bot_instance, guild: discord.Guild, return_to_main_callback):
        super().__init__(timeout=None)
        self.bot = bot_instance
        self.guild = guild
        self.return_to_main_callback = return_to_main_callback

        self.add_item(ModerationPanelButton(
            "⚠️ Warn", "warn",
            discord.ButtonStyle.danger, row=0
        ))
        self.add_item(ModerationPanelButton(
            "🔨 Ban", "ban",
            discord.ButtonStyle.danger, row=0
        ))
        self.add_item(ModerationPanelButton(
            "👢 Kick", "kick",
            discord.ButtonStyle.danger, row=0
        ))
        self.add_item(ModerationPanelButton(
            "⏰ Timeout", "timeout",
            discord.ButtonStyle.danger, row=0
        ))

        self.add_item(ModerationPanelButton(
            "🔊 Unmute", "unmute",
            discord.ButtonStyle.success, row=1
        ))
        self.add_item(ModerationPanelButton(
            "🔨 Softban", "softban",
            discord.ButtonStyle.danger, row=1
        ))
        self.add_item(ModerationPanelButton(
            "🔓 Unban", "unban",
            discord.ButtonStyle.success, row=1
        ))
        self.add_item(ModerationPanelButton(
            "📜 History", "history",
            discord.ButtonStyle.secondary, row=1
        ))

        self.add_item(ModerationPanelButton(
            "🧹 Clear", "clear",
            discord.ButtonStyle.secondary, row=2
        ))
        self.add_item(ModerationPanelButton(
            "💥 Thanos Snap", "thanos_snap",
            discord.ButtonStyle.danger, row=2
        ))
        self.add_item(ModerationPanelButton(
            "🔒 Lock", "lock",
            discord.ButtonStyle.danger, row=2
        ))
        self.add_item(ModerationPanelButton(
            "🔓 Unlock", "unlock",
            discord.ButtonStyle.success, row=2
        ))

        self.add_item(ModerationPanelButton(
            "⏱️ Slowmode", "slowmode",
            discord.ButtonStyle.primary, row=3
        ))
        self.add_item(ModerationPanelButton(
            "👁️ Hide", "hide",
            discord.ButtonStyle.secondary, row=3
        ))
        self.add_item(ModerationPanelButton(
            "👁️ Show", "show",
            discord.ButtonStyle.secondary, row=3
        ))
        self.add_item(ModerationPanelButton(
            "🕵️ Snipe", "snipe",
            discord.ButtonStyle.secondary, row=3
        ))
        self.add_item(ModerationPanelButton(
            "✏️ Edit Snipe", "editsnipe",
            discord.ButtonStyle.secondary, row=3
        ))

        self.add_item(ModerationPanelButton(
            "📋 View Warnings", "view_warnings",
            discord.ButtonStyle.secondary, row=4
        ))
        self.add_item(ModerationPanelButton(
            "🗑️ Remove Warning", "remove_warning",
            discord.ButtonStyle.danger, row=4
        ))
        self.add_item(ModerationPanelButton(
            "🧹 Clear Warnings", "clear_warnings",
            discord.ButtonStyle.danger, row=4
        ))
        self.add_item(ModerationPanelButton(
            "🔙 Back", "back_to_main",
            discord.ButtonStyle.secondary, row=4
        ))

    async def button_callback(self, interaction: discord.Interaction, action: str):
        if not has_owner_role(interaction.user):
            return await interaction.response.send_message(
                f"❌ This control panel is restricted to {owner_role_mention()} only.",
                ephemeral=True
            )
        
        if action == "back_to_main":
            await self.return_to_main_callback(interaction)
            return
        
        channel_actions = ["lock", "unlock", "clear", "thanos_snap", "slowmode", "hide", "show"]
        destructive_actions = ["thanos_snap", "clear"]
        
        if action in channel_actions:
            embed = discord.Embed(
                title=f"Select Target Channel for {action.replace('_', ' ').title()}",
                description=f"Choose a channel from the dropdown below.",
                color=discord.Color.blue()
            )
            
            is_destructive = action in destructive_actions
            view = ModerationChannelSelectView(
                action, self.guild,
                self._channel_action_callback,
                destructive=is_destructive
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            return
        
        if action == "snipe":
            await self._handle_snipe(interaction)
            return
        if action == "editsnipe":
            await self._handle_edit_snipe(interaction)
            return
        
        if action == "view_warnings":
            embed = discord.Embed(
                title="📋 View Warnings",
                description="Select a member from the dropdown below to view their warnings.",
                color=discord.Color.blue()
            )
            view = ModerationMemberSelectView(
                action, self.guild,
                self._view_warnings_callback
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            return
        
        if action == "clear_warnings":
            embed = discord.Embed(
                title="🧹 Clear Warnings",
                description="Select a member from the dropdown below to clear all their warnings.",
                color=discord.Color.orange()
            )
            view = ModerationMemberSelectView(
                action, self.guild,
                self._clear_warnings_callback
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            return
        
        if action == "remove_warning":
            await self._handle_remove_warning(interaction)
            return
        
        if action == "history":
            embed = discord.Embed(
                title="📜 Moderation History",
                description="Select a member from the dropdown below to view their history.",
                color=discord.Color.blue()
            )
            view = ModerationMemberSelectView(
                action, self.guild,
                self._history_callback
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            return
        
        if action == "unban":
            await self._handle_unban(interaction)
            return
        
        if action in ["warn", "ban", "kick", "timeout", "unmute", "softban"]:
            embed = discord.Embed(
                title=f"Select Target for {action.replace('_', ' ').title()}",
                description=f"Choose a member from the dropdown below.",
                color=discord.Color.blue()
            )
            view = ModerationMemberSelectView(
                action, self.guild,
                self._member_action_callback
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            return
        
        await interaction.response.send_message(f"❌ Unknown action: {action}", ephemeral=True)

    async def _channel_action_callback(self, interaction: discord.Interaction, channel: discord.TextChannel, action: str):
        modal = ModerationActionModal(action, channel)
        await interaction.response.send_modal(modal)

    async def _member_action_callback(self, interaction: discord.Interaction, member: discord.Member, action: str):
        modal = ModerationActionModal(action, member)
        await interaction.response.send_modal(modal)

    async def _view_warnings_callback(self, interaction: discord.Interaction, member: discord.Member, action: str):
        warnings = await get_warnings(member.id, self.guild.id)
        if not warnings:
            embed = discord.Embed(
                title=f"📋 Warnings for {member.display_name}",
                description="No warnings found.",
                color=discord.Color.green()
            )
        else:
            embed = discord.Embed(
                title=f"📋 Warnings for {member.display_name}",
                color=discord.Color.orange()
            )
            embed.set_footer(text=f"Total: {len(warnings)} warnings")
            for i, (wid, reason, timestamp) in enumerate(warnings[:10], 1):
                embed.add_field(
                    name=f"#{i} (ID: {wid})",
                    value=f"Reason: {reason}\nTime: {timestamp}",
                    inline=False
                )
            if len(warnings) > 10:
                embed.add_field(name="...", value=f"And {len(warnings) - 10} more warnings.", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _clear_warnings_callback(self, interaction: discord.Interaction, member: discord.Member, action: str):
        warnings = await get_warnings(member.id, self.guild.id)
        if not warnings:
            return await interaction.response.send_message(f"⚠️ {member.mention} has no warnings to clear.", ephemeral=True)
        
        await clear_warnings(member.id, self.guild.id)
        await add_history(self.guild.id, member.id, str(member), "WARN_CLEAR", f"All warnings cleared by {interaction.user}")
        
        embed = discord.Embed(
            title="✅ Warnings Cleared",
            description=f"Cleared {len(warnings)} warnings from {member.mention}",
            color=discord.Color.green()
        )
        embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _history_callback(self, interaction: discord.Interaction, member: discord.Member, action: str):
        history = await get_user_history(member.id, self.guild.id, limit=15)
        if not history:
            embed = discord.Embed(
                title=f"📜 History for {member.display_name}",
                description="No history found.",
                color=discord.Color.green()
            )
        else:
            embed = discord.Embed(
                title=f"📜 History for {member.display_name}",
                color=discord.Color.blue()
            )
            embed.set_footer(text=f"Showing {len(history)} events")
            for event_type, details, timestamp in history:
                embed.add_field(
                    name=f"🔹 {event_type}",
                    value=f"{details[:100]}\n{timestamp}",
                    inline=False
                )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _handle_remove_warning(self, interaction: discord.Interaction):
        modal = RemoveWarningModal()
        await interaction.response.send_modal(modal)

    async def _handle_unban(self, interaction: discord.Interaction):
        modal = UnbanModal(self.bot, self.guild)
        await interaction.response.send_modal(modal)

    async def _handle_snipe(self, interaction: discord.Interaction):
        result = await get_snipe(self.guild.id, interaction.channel.id)
        if not result:
            return await interaction.response.send_message("❌ No deleted messages to snipe.", ephemeral=True)
        
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
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _handle_edit_snipe(self, interaction: discord.Interaction):
        result = await get_edit_snipe(self.guild.id, interaction.channel.id)
        if not result:
            return await interaction.response.send_message("❌ No edited messages to snipe.", ephemeral=True)
        
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
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

class RemoveWarningModal(discord.ui.Modal, title="Remove Warning"):
    def __init__(self):
        super().__init__()
        
        self.warning_id_input = discord.ui.TextInput(
            label="Warning ID",
            placeholder="Enter the warning ID to remove",
            required=True,
            max_length=20
        )
        self.add_item(self.warning_id_input)
        
        self.reason_input = discord.ui.TextInput(
            label="Reason for removal",
            placeholder="Why are you removing this warning?",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            warning_id = int(self.warning_id_input.value)
            result = await db.fetchone("SELECT user_id, reason FROM warnings WHERE id=?", (warning_id,))
            if not result:
                return await interaction.response.send_message(f"❌ Warning ID {warning_id} not found.", ephemeral=True)
            
            user_id, old_reason = result
            await remove_warning(warning_id)
            
            embed = discord.Embed(
                title="✅ Warning Removed",
                description=f"Removed warning #{warning_id}",
                color=discord.Color.green()
            )
            embed.add_field(name="User", value=f"<@{user_id}>", inline=True)
            embed.add_field(name="Original Reason", value=old_reason, inline=False)
            if self.reason_input.value:
                embed.add_field(name="Removal Reason", value=self.reason_input.value, inline=False)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except ValueError:
            await interaction.response.send_message("❌ Invalid warning ID. Please enter a number.", ephemeral=True)

class UnbanModal(discord.ui.Modal, title="Unban User"):
    def __init__(self, bot_instance, guild: discord.Guild):
        super().__init__()
        self.bot = bot_instance
        self.guild = guild
        
        self.user_id_input = discord.ui.TextInput(
            label="User ID",
            placeholder="Enter the user ID to unban",
            required=True,
            max_length=20
        )
        self.add_item(self.user_id_input)
        
        self.reason_input = discord.ui.TextInput(
            label="Reason",
            placeholder="Reason for unbanning...",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id = int(self.user_id_input.value)
            user = await self.bot.fetch_user(user_id)
            reason = self.reason_input.value or "No reason provided"
            
            await self.guild.unban(user, reason=reason)
            await add_history(self.guild.id, user_id, str(user), "UNBAN", f"Unbanned by {interaction.user}: {reason}")
            
            embed = discord.Embed(
                title="🔓 User Unbanned",
                color=discord.Color.green()
            )
            embed.add_field(name="User", value=str(user), inline=True)
            embed.add_field(name="Reason", value=reason, inline=False)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except ValueError:
            await interaction.response.send_message("❌ Invalid user ID.", ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message("❌ User not found or not banned.", ephemeral=True)
        except Exception as e:
            logger.error(f"Unban error: {e}")
            await interaction.response.send_message(f"❌ Error: {str(e)[:100]}", ephemeral=True)

class ModerationPanelButton(discord.ui.Button):
    def __init__(self, label: str, action: str, style: discord.ButtonStyle, row: int = 0):
        super().__init__(
            label=label,
            custom_id=f"mod_{action}",
            style=style,
            row=row
        )
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        if hasattr(self.view, 'button_callback'):
            await self.view.button_callback(interaction, self.action)

# =========================
# MAIN CONTROL PANEL VIEW
# =========================

class ControlPanelButton(discord.ui.Button):
    def __init__(
        self,
        label: str,
        custom_id: str,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        row: int = 0,
        emoji: str = None
    ):
        super().__init__(
            label=label,
            custom_id=custom_id,
            style=style,
            row=row,
            emoji=emoji
        )

    async def callback(self, interaction: discord.Interaction):
        if not has_owner_role(interaction.user):
            return await interaction.response.send_message(
                f"❌ This control panel is restricted to {owner_role_mention()} only.",
                ephemeral=True
            )

        if self.custom_id == "open_moderation":
            embed = discord.Embed(
                title="🛡️ Moderation Control Panel",
                description="Complete moderation dashboard with all moderation commands.\n\n"
                           "**👤 User Actions:** Warn, Ban, Kick, Timeout, Unmute, Softban, Unban\n"
                           "**📢 Channel Actions:** Clear, Thanos Snap, Lock, Unlock, Slowmode, Hide, Show\n"
                           "**⚠️ Warning Management:** View Warnings, Remove Warning, Clear Warnings\n"
                           "**📋 Info:** History, Snipe, Edit Snipe",
                color=discord.Color.red(),
                timestamp=datetime.now()
            )
            embed.set_footer(text="Moderation Panel • Owner Role Only • All actions are logged")
            
            view = ModerationPanelView(
                self.view.bot,
                interaction.guild,
                self.view.return_to_main_panel
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            return

        command_map = {
            "show_stats": handle_show_stats,
            "clear_cache": handle_clear_cache,
            "refresh_panel": handle_refresh_panel,
            "db_status": handle_db_status,
            "db_optimize": handle_db_optimize,
            "db_stats": handle_db_stats,
            "toggle_modules": handle_toggle_modules,
            "maintenance_mode": handle_maintenance,
            "restart_bot": handle_restart_bot,
        }
        
        handler = command_map.get(self.custom_id)
        if handler:
            await handler(interaction)
        else:
            await interaction.response.send_message(
                f"✅ Executed: {self.label}",
                ephemeral=True
            )

async def handle_db_optimize(interaction: discord.Interaction):
    await interaction.response.send_message("🔄 Optimizing database...", ephemeral=True)
    
    try:
        await db.execute("VACUUM")
        await db.execute("ANALYZE")
        await db.commit()
        
        await interaction.edit_original_response(content="✅ Database optimized successfully!")
    except Exception as e:
        await interaction.edit_original_response(content=f"❌ Optimization failed: {str(e)}")

async def handle_db_stats(interaction: discord.Interaction):
    try:
        size = os.path.getsize("moderation.db")
        size_mb = size / (1024 * 1024)
        
        embed = discord.Embed(
            title="📊 Database Statistics",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        embed.add_field(name="📦 Database Size", value=f"{size_mb:.2f} MB", inline=True)
        embed.add_field(name="📂 Database File", value="moderation.db", inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Error getting stats: {str(e)}", ephemeral=True)

async def handle_toggle_modules(interaction: discord.Interaction):
    cogs = list(bot.cogs.keys())
    
    embed = discord.Embed(
        title="🔄 Module Management",
        description="Click the buttons below to toggle modules on/off. (Coming soon!)",
        color=discord.Color.blue()
    )
    
    module_status = ""
    for cog in cogs:
        module_status += f"✅ {cog}\n"
    
    embed.add_field(name="📦 Loaded Modules", value=module_status or "No modules loaded", inline=False)
    embed.add_field(name="ℹ️ Note", value="Module toggling will be available in a future update.", inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

async def handle_maintenance(interaction: discord.Interaction):
    await interaction.response.send_message(
        "🔧 Maintenance mode is not implemented yet.\n"
        "To restart the bot, use the Restart Bot button or redeploy on Railway.",
        ephemeral=True
    )

async def handle_restart_bot(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🔄 Restarting Bot",
        description="The bot is restarting... This may take a few seconds.",
        color=discord.Color.orange()
    )
    await interaction.response.send_message(embed=embed)
    
    config = await get_control_panel(interaction.guild_id)
    if config:
        channel = interaction.guild.get_channel(config["channel_id"])
        if channel:
            try:
                old_message = await channel.fetch_message(config["message_id"])
                embed = discord.Embed(
                    title="🔄 Bot Restarting...",
                    description="The bot is currently restarting. Please wait a moment.",
                    color=discord.Color.orange(),
                    timestamp=datetime.now()
                )
                await old_message.edit(embed=embed, view=None)
            except:
                pass
    
    os._exit(0)

class ControlPanelView(discord.ui.View):
    def __init__(self, bot_instance):
        super().__init__(timeout=None)
        self.bot = bot_instance
        
        self.add_item(ControlPanelButton(
            "📊 Stats",
            "show_stats",
            discord.ButtonStyle.success,
            row=0
        ))
        
        self.add_item(ControlPanelButton(
            "🧹 Cache",
            "clear_cache",
            discord.ButtonStyle.danger,
            row=0
        ))
        
        self.add_item(ControlPanelButton(
            "🔄 Refresh",
            "refresh_panel",
            discord.ButtonStyle.primary,
            row=0
        ))
        
        self.add_item(ControlPanelButton(
            "💾 DB Status",
            "db_status",
            discord.ButtonStyle.secondary,
            row=1
        ))
        
        self.add_item(ControlPanelButton(
            "📊 DB Stats",
            "db_stats",
            discord.ButtonStyle.secondary,
            row=1
        ))
        
        self.add_item(ControlPanelButton(
            "🔄 DB Optimize",
            "db_optimize",
            discord.ButtonStyle.primary,
            row=1
        ))
        
        self.add_item(ControlPanelButton(
            "⚙️ Modules",
            "toggle_modules",
            discord.ButtonStyle.secondary,
            row=2
        ))
        
        self.add_item(ControlPanelButton(
            "🔧 Maintenance",
            "maintenance_mode",
            discord.ButtonStyle.danger,
            row=2
        ))
        
        self.add_item(ControlPanelButton(
            "🔄 Restart",
            "restart_bot",
            discord.ButtonStyle.danger,
            row=2
        ))
        
        self.add_item(ControlPanelButton(
            "🛡️ Moderation",
            "open_moderation",
            discord.ButtonStyle.primary,
            row=3,
            emoji="🛡️"
        ))

    async def return_to_main_panel(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎛️ Bot Control Panel",
            description="Welcome to the bot control panel! Use the buttons below to manage the bot.",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        embed.add_field(name="🤖 Status", value="Bot is online and ready", inline=True)
        embed.add_field(name="👑 Owner Role", value=owner_role_mention(), inline=True)
        embed.add_field(name="📊 Commands", value=f"{len(self.bot.tree.get_commands())} total", inline=True)
        embed.set_footer(text="Control Panel • Owner Role Only")
        
        await interaction.response.edit_original_response(embed=embed, view=self)

# =========================
# BOT EVENTS
# =========================
welcome_configs = {}

@bot.event
async def on_member_join(member):
    try:
        await add_history(member.guild.id, member.id, str(member), "JOIN", "Joined the server")
        
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
        
    except Exception as e:
        logger.error(f"on_member_join error: {e}")

@bot.event
async def on_member_remove(member):
    try:
        await add_history(member.guild.id, member.id, str(member), "LEAVE", "Left the server")
        
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
        await cache_message(message)
        
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
async def on_voice_state_update(member, before, after):
    try:
        if before.channel != after.channel:
            if after.channel:
                await add_history(member.guild.id, member.id, str(member), "VOICE_JOIN", f"Joined {after.channel.name}")
                await update_voice_stats(member.id, member.guild.id, is_join=True)
                
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
                await update_voice_stats(member.id, member.guild.id, is_join=False)
                
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
async def on_reaction_add(reaction, user):
    if user.bot:
        return
    
    try:
        if reaction.message.guild:
            await track_reaction(reaction.message.guild.id, user.id, reaction.emoji, is_given=True)
            
            if reaction.message.author.id != user.id:
                await track_reaction(reaction.message.guild.id, reaction.message.author.id, reaction.emoji, is_given=False)
                
                result = await db.fetchone(
                    "SELECT reactions_received FROM user_stats WHERE user_id=? AND guild_id=?",
                    (reaction.message.author.id, reaction.message.guild.id)
                )
                if result:
                    new_count = (result[0] if result[0] is not None else 0) + 1
                    await db.execute(
                        "UPDATE user_stats SET reactions_received = ?, updated_at = ? WHERE user_id=? AND guild_id=?",
                        (new_count, datetime.now().isoformat(), reaction.message.author.id, reaction.message.guild.id)
                    )
                else:
                    await db.execute(
                        """INSERT INTO user_stats (user_id, guild_id, reactions_received, updated_at)
                           VALUES (?, ?, 1, ?)""",
                        (reaction.message.author.id, reaction.message.guild.id, datetime.now().isoformat())
                    )
                await db.commit()
    except Exception as e:
        logger.error(f"on_reaction_add error: {e}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    try:
        if message.guild:
            await log_message(message.guild.id, message.channel.id, message.author.id, message.content)
            await increment_message_count(message.author.id, message.guild.id)
            await automod.check_message(message)
            
            await update_user_stats(message.author.id, message.guild.id, message)
            
            new_level = await add_xp(message.author.id, message.guild.id)
            if new_level:
                await message.channel.send(f"🎉 {message.author.mention} leveled up to **Level {new_level}**!")
                await add_history(message.guild.id, message.author.id, str(message.author), "LEVEL_UP", f"Reached level {new_level}")
            
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

@bot.event
async def on_ready():
    global _tasks_started

    if _tasks_started:
        return

    _tasks_started = True

    await initialize_bot_state()
    
    logger.info(f"🤖 Logged in as {bot.user}")
    logger.info(f"   Servers: {len(bot.guilds)}")
    logger.info(f"   Commands: {len(bot.tree.get_commands())}")
    logger.info(f"   Owner Role: {OWNER_ROLE_NAME} (ID: {OWNER_ROLE_ID if OWNER_ROLE_ID else 'Not set'}")

# =========================
# 🛡️ MODERATION COG - ACTIONS
# =========================
class ModerationActions(commands.Cog, name="moderation_actions"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    mod_group = app_commands.Group(name="mod", description="Moderation actions - Owner Role Only")
    
    @mod_group.command(name="clear", description="Delete messages")
    @is_owner()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def mod_clear(self, interaction: discord.Interaction, amount: int):
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(limit=amount)
            await interaction.followup.send(f"🧹 Deleted {len(deleted)} messages", ephemeral=True)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "CLEAR", f"Cleared {len(deleted)} messages in #{interaction.channel.name}")
            await track_moderation_action(interaction.guild.id, "clear")
            
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

    @mod_group.command(name="thanos_snap", description="Delete all messages in channel")
    @is_owner()
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
            await track_moderation_action(interaction.guild.id, "clearall")
            
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
    @is_owner()
    @app_commands.checks.has_permissions(ban_members=True)
    async def mod_ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and not has_owner_role(interaction.user):
                return await interaction.followup.send("❌ You cannot ban this member (role hierarchy).", ephemeral=True)
            
            await member.ban(reason=reason)
            await add_history(interaction.guild.id, member.id, str(member), "BAN", f"Banned by {interaction.user}: {reason}")
            await track_moderation_action(interaction.guild.id, "ban")
            
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
    @is_owner()
    @app_commands.checks.has_permissions(ban_members=True)
    async def mod_softban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "Softban"):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and not has_owner_role(interaction.user):
                return await interaction.followup.send("❌ You cannot softban this member (role hierarchy).", ephemeral=True)
            
            await member.ban(reason=reason)
            await asyncio.sleep(1)
            await interaction.guild.unban(member, reason="Softban complete")
            await add_history(interaction.guild.id, member.id, str(member), "SOFTBAN", f"Softbanned by {interaction.user}: {reason}")
            await track_moderation_action(interaction.guild.id, "softban")
            
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
    @is_owner()
    @app_commands.checks.has_permissions(ban_members=True)
    async def mod_unban(self, interaction: discord.Interaction, user_id: str, reason: str = "No reason"):
        await interaction.response.defer(ephemeral=True)
        try:
            user = await self.bot.fetch_user(int(user_id))
            await interaction.guild.unban(user, reason=reason)
            await add_history(interaction.guild.id, int(user_id), str(user), "UNBAN", f"Unbanned by {interaction.user}: {reason}")
            await track_moderation_action(interaction.guild.id, "unban")
            
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
    @is_owner()
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mod_mute(self, interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "No reason"):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and not has_owner_role(interaction.user):
                return await interaction.followup.send("❌ You cannot mute this member (role hierarchy).", ephemeral=True)
            
            await member.timeout(utcnow() + timedelta(minutes=minutes), reason=reason)
            await add_history(interaction.guild.id, member.id, str(member), "MUTE", f"Muted for {minutes}min by {interaction.user}: {reason}")
            await track_moderation_action(interaction.guild.id, "mute")
            
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
    @is_owner()
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
    @is_owner()
    @app_commands.checks.has_permissions(kick_members=True)
    async def mod_kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and not has_owner_role(interaction.user):
                return await interaction.followup.send("❌ You cannot kick this member (role hierarchy).", ephemeral=True)
            
            await member.kick(reason=reason)
            await add_history(interaction.guild.id, member.id, str(member), "KICK", reason)
            await track_moderation_action(interaction.guild.id, "kick")
            
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
    @is_owner()
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mod_warn(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and not has_owner_role(interaction.user):
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
    @is_owner()
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
    @is_owner()
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
    @is_owner()
    @app_commands.checks.has_permissions(manage_channels=True)
    async def mod_lock(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        await interaction.response.defer(ephemeral=True)
        try:
            channel = channel or interaction.channel
            overwrite = channel.overwrites_for(interaction.guild.default_role)
            overwrite.send_messages = False
            await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "LOCK", f"Locked #{channel.name}")
            await track_moderation_action(interaction.guild.id, "lock")
            
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
    @is_owner()
    @app_commands.checks.has_permissions(manage_channels=True)
    async def mod_unlock(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        await interaction.response.defer(ephemeral=True)
        try:
            channel = channel or interaction.channel
            overwrite = channel.overwrites_for(interaction.guild.default_role)
            overwrite.send_messages = None
            await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "UNLOCK", f"Unlocked #{channel.name}")
            await track_moderation_action(interaction.guild.id, "unlock")
            
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
    @is_owner()
    @app_commands.checks.has_permissions(manage_channels=True)
    async def mod_slowmode(self, interaction: discord.Interaction, seconds: int, channel: discord.TextChannel = None):
        await interaction.response.defer(ephemeral=True)
        try:
            channel = channel or interaction.channel
            if seconds < 0 or seconds > 21600:
                return await interaction.followup.send("❌ Slowmode must be between 0 and 21600 seconds.", ephemeral=True)
            await channel.edit(slowmode_delay=seconds)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "SLOWMODE", f"Set slowmode in #{channel.name} to {seconds}s")
            await track_moderation_action(interaction.guild.id, "slowmode")
            
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
    @is_owner()
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

    @mod_group.command(name="snipe", description="Show the last deleted message")
    @is_owner()
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
    @is_owner()
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

    @mod_group.command(name="hide", description="Hide a channel from @everyone")
    @is_owner()
    @app_commands.checks.has_permissions(manage_channels=True)
    async def mod_hide(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        await interaction.response.defer(ephemeral=True)
        try:
            channel = channel or interaction.channel
            await channel.set_permissions(interaction.guild.default_role, view_channel=False)
            await track_moderation_action(interaction.guild.id, "hide")
            
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
    @is_owner()
    @app_commands.checks.has_permissions(manage_channels=True)
    async def mod_show(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        await interaction.response.defer(ephemeral=True)
        try:
            channel = channel or interaction.channel
            await channel.set_permissions(interaction.guild.default_role, view_channel=None)
            await track_moderation_action(interaction.guild.id, "show")
            
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

# =========================
# 🎭 ROLE MANAGEMENT COG
# =========================
class RoleManagement(commands.Cog, name="role_management"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    role_group = app_commands.Group(name="role", description="Role management - Owner Role Only")
    
    @role_group.command(name="nick", description="Change a member's nickname")
    @is_owner()
    @app_commands.checks.has_permissions(manage_nicknames=True)
    async def role_nick(self, interaction: discord.Interaction, member: discord.Member, nickname: str):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and not has_owner_role(interaction.user):
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
    @is_owner()
    @app_commands.checks.has_permissions(manage_nicknames=True)
    async def role_resetnick(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and not has_owner_role(interaction.user):
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
    @is_owner()
    @app_commands.checks.has_permissions(manage_roles=True)
    async def role_give(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role):
        await interaction.response.defer(ephemeral=True)
        try:
            if role >= interaction.user.top_role and not has_owner_role(interaction.user):
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
    @is_owner()
    @app_commands.checks.has_permissions(manage_roles=True)
    async def role_remove(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role):
        await interaction.response.defer(ephemeral=True)
        try:
            if role >= interaction.user.top_role and not has_owner_role(interaction.user):
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
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    voice_group = app_commands.Group(name="voice", description="Voice moderation - Owner Role Only")
    
    @voice_group.command(name="kick", description="Disconnect a member from voice")
    @is_owner()
    @app_commands.checks.has_permissions(move_members=True)
    async def voice_kick(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and not has_owner_role(interaction.user):
                return await interaction.followup.send("❌ You cannot voice kick this member (role hierarchy).", ephemeral=True)
            if not member.voice or not member.voice.channel:
                return await interaction.followup.send(f"❌ {member.display_name} is not in a voice channel.", ephemeral=True)
            
            channel_name = member.voice.channel.name
            await member.move_to(None, reason=f"Voice kicked by {interaction.user}")
            await add_history(interaction.guild.id, member.id, str(member), "VOICE_KICK", f"Disconnected from voice by {interaction.user}")
            await track_moderation_action(interaction.guild.id, "voice_kick")
            
            embed = discord.Embed(title="🔊 User Voice Kicked", color=discord.Color.orange())
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Channel", value=channel_name, inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Voice kick error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @voice_group.command(name="move", description="Move a member to another voice channel")
    @is_owner()
    @app_commands.checks.has_permissions(move_members=True)
    async def voice_move(self, interaction: discord.Interaction, member: discord.Member, channel: discord.VoiceChannel):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and not has_owner_role(interaction.user):
                return await interaction.followup.send("❌ You cannot move this member (role hierarchy).", ephemeral=True)
            if not member.voice or not member.voice.channel:
                return await interaction.followup.send(f"❌ {member.display_name} is not in a voice channel.", ephemeral=True)
            
            old_channel = member.voice.channel
            await member.move_to(channel, reason=f"Moved by {interaction.user}")
            await add_history(interaction.guild.id, member.id, str(member), "VOICE_MOVE", f"Moved from {old_channel.name} to {channel.name} by {interaction.user}")
            await track_moderation_action(interaction.guild.id, "voice_move")
            
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
    @is_owner()
    @app_commands.checks.has_permissions(deafen_members=True)
    async def voice_deafen(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and not has_owner_role(interaction.user):
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
    @is_owner()
    @app_commands.checks.has_permissions(deafen_members=True)
    async def voice_undeafen(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and not has_owner_role(interaction.user):
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
    @is_owner()
    @app_commands.checks.has_permissions(mute_members=True)
    async def voice_mute(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and not has_owner_role(interaction.user):
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
    @is_owner()
    @app_commands.checks.has_permissions(mute_members=True)
    async def voice_unmute(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and not has_owner_role(interaction.user):
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
# 🧹 CHANNEL MANAGEMENT COG
# =========================
class ChannelManagement(commands.Cog, name="channel_management"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    purge_group = app_commands.Group(name="purge", description="Message purging - Owner Role Only")
    
    @purge_group.command(name="user", description="Delete messages from a specific user")
    @is_owner()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge_user(self, interaction: discord.Interaction, member: discord.Member, amount: int = 50):
        await interaction.response.defer(ephemeral=True)
        try:
            amount = min(amount, 100)
            def check(msg):
                return msg.author.id == member.id
            deleted = await interaction.channel.purge(limit=amount, check=check)
            await add_history(interaction.guild.id, member.id, str(member), "PURGE_USER", f"Purged {len(deleted)} messages by {interaction.user}")
            await track_moderation_action(interaction.guild.id, "purge_user")
            
            embed = discord.Embed(title="🧹 User Messages Purged", color=discord.Color.orange())
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Messages Deleted", value=str(len(deleted)), inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Purge user error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @purge_group.command(name="bots", description="Delete messages from bots")
    @is_owner()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge_bots(self, interaction: discord.Interaction, amount: int = 50):
        await interaction.response.defer(ephemeral=True)
        try:
            amount = min(amount, 100)
            def check(msg):
                return msg.author.bot
            deleted = await interaction.channel.purge(limit=amount, check=check)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "PURGE_BOTS", f"Purged {len(deleted)} bot messages")
            await track_moderation_action(interaction.guild.id, "purge_bots")
            
            embed = discord.Embed(title="🤖 Bot Messages Purged", color=discord.Color.orange())
            embed.add_field(name="Messages Deleted", value=str(len(deleted)), inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Purge bots error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @purge_group.command(name="images", description="Delete messages with images")
    @is_owner()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge_images(self, interaction: discord.Interaction, amount: int = 50):
        await interaction.response.defer(ephemeral=True)
        try:
            amount = min(amount, 100)
            def check(msg):
                return any(a.content_type and a.content_type.startswith("image/") for a in msg.attachments)
            deleted = await interaction.channel.purge(limit=amount, check=check)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "PURGE_IMAGES", f"Purged {len(deleted)} images")
            await track_moderation_action(interaction.guild.id, "purge_images")
            
            embed = discord.Embed(title="🖼️ Images Purged", color=discord.Color.orange())
            embed.add_field(name="Messages Deleted", value=str(len(deleted)), inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Purge images error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @purge_group.command(name="attachments", description="Delete messages with attachments")
    @is_owner()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge_attachments(self, interaction: discord.Interaction, amount: int = 50):
        await interaction.response.defer(ephemeral=True)
        try:
            amount = min(amount, 100)
            def check(msg):
                return len(msg.attachments) > 0
            deleted = await interaction.channel.purge(limit=amount, check=check)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "PURGE_ATTACHMENTS", f"Purged {len(deleted)} attachments")
            await track_moderation_action(interaction.guild.id, "purge_attachments")
            
            embed = discord.Embed(title="📎 Attachments Purged", color=discord.Color.orange())
            embed.add_field(name="Messages Deleted", value=str(len(deleted)), inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Purge attachments error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @purge_group.command(name="embeds", description="Delete messages with embeds")
    @is_owner()
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge_embeds(self, interaction: discord.Interaction, amount: int = 50):
        await interaction.response.defer(ephemeral=True)
        try:
            amount = min(amount, 100)
            def check(msg):
                return len(msg.embeds) > 0
            deleted = await interaction.channel.purge(limit=amount, check=check)
            await add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "PURGE_EMBEDS", f"Purged {len(deleted)} embeds")
            await track_moderation_action(interaction.guild.id, "purge_embeds")
            
            embed = discord.Embed(title="📦 Embeds Purged", color=discord.Color.orange())
            embed.add_field(name="Messages Deleted", value=str(len(deleted)), inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Purge embeds error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @purge_group.command(name="contains", description="Delete messages containing specific text")
    @is_owner()
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
            await track_moderation_action(interaction.guild.id, "purge_contains")
            
            embed = discord.Embed(title="🔍 Messages Purged", color=discord.Color.orange())
            embed.add_field(name="Contains", value=text, inline=True)
            embed.add_field(name="Messages Deleted", value=str(len(deleted)), inline=True)
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Purge contains error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

    @purge_group.command(name="links", description="Delete messages containing links")
    @is_owner()
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
            await track_moderation_action(interaction.guild.id, "purge_links")
            
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
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    info_group = app_commands.Group(name="info", description="Server information - Owner Role Only")
    
    @info_group.command(name="server", description="Display detailed server information")
    @is_owner()
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
    @is_owner()
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
    @is_owner()
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
    @is_owner()
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
    @is_owner()
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
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    msg_group = app_commands.Group(name="msg", description="Message commands - Owner Role Only")
    
    @msg_group.command(name="announce", description="Send an announcement embed")
    @is_owner()
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
    @is_owner()
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
    @is_owner()
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
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    logs_group = app_commands.Group(name="logs", description="Mod logs configuration - Owner Role Only")
    
    @logs_group.command(name="set", description="Set the mod logs channel")
    @is_owner()
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
    @is_owner()
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
    @is_owner()
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
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    automod_group = app_commands.Group(name="automod", description="AutoModeration configuration - Owner Role Only")
    
    @automod_group.command(name="antispam", description="Configure anti-spam protection")
    @is_owner()
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
    @is_owner()
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
    @is_owner()
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
    @is_owner()
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
    @is_owner()
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
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    warn_group = app_commands.Group(name="warn", description="Warning management - Owner Role Only")
    
    @warn_group.command(name="add", description="Warn a member")
    @is_owner()
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn_add(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        await interaction.response.defer(ephemeral=True)
        try:
            if member.top_role >= interaction.user.top_role and not has_owner_role(interaction.user):
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
    @is_owner()
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
    @is_owner()
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
    @is_owner()
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.describe(warning_id="ID of the warning to remove")
    async def warn_remove(self, interaction: discord.Interaction, warning_id: int):
        await interaction.response.defer(ephemeral=True)
        try:
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
# 🎮 FUN COG (PUBLIC - NO OWNER CHECK)
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
# 💰 ECONOMY COG (PUBLIC - NO OWNER CHECK)
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
        result = await db.fetchone("SELECT balance FROM economy WHERE user_id=?", (user_id,))
        if result:
            await db.execute("UPDATE economy SET balance = balance + ? WHERE user_id=?", (amount, user_id))
        else:
            await db.execute("INSERT INTO economy (user_id, balance, bank) VALUES (?, ?, 0)", (user_id, amount))
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
        sender_result = await db.fetchone("SELECT balance FROM economy WHERE user_id=?", (interaction.user.id,))
        sender_balance = sender_result[0] if sender_result else 0
        if sender_balance < amount:
            return await interaction.response.send_message("❌ Insufficient funds in wallet.", ephemeral=True)
        await db.execute("UPDATE economy SET balance = balance - ? WHERE user_id=?", (amount, interaction.user.id))
        await db.execute("UPDATE economy SET balance = balance + ? WHERE user_id=?", (amount, member.id))
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
        result = await db.fetchone("SELECT balance FROM economy WHERE user_id=?", (interaction.user.id,))
        balance = result[0] if result else 0
        if balance < amount:
            return await interaction.response.send_message("❌ Insufficient funds.", ephemeral=True)
        win = random.random() < 0.45
        if win:
            winnings = int(amount * random.uniform(1.5, 3.0))
            await db.execute("UPDATE economy SET balance = balance - ? + ? WHERE user_id=?", (amount, winnings, interaction.user.id))
            await db.commit()
            embed = discord.Embed(title="🎰 You Won!", description=f"You gambled ${amount:,} and won **${winnings:,}**!", color=discord.Color.green())
        else:
            await db.execute("UPDATE economy SET balance = balance - ? WHERE user_id=?", (amount, interaction.user.id))
            await db.commit()
            embed = discord.Embed(title="🎰 You Lost!", description=f"You gambled ${amount:,} and lost it all.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed)
    
    @economy_group.command(name="leaderboard", description="View the economy leaderboard")
    async def economy_leaderboard(self, interaction: discord.Interaction):
        rows = await db.fetchall(
            "SELECT user_id, balance, bank FROM economy ORDER BY (balance + bank) DESC LIMIT 10"
        )
        embed = discord.Embed(title="🏆 Economy Leaderboard", color=discord.Color.gold())
        if not rows:
            embed.description = "No data yet."
        else:
            medals = ["🥇", "🥈", "🥉"]
            for i, (user_id, balance, bank) in enumerate(rows):
                total = balance + bank
                user = interaction.guild.get_member(user_id)
                name = user.display_name if user else f"Unknown ({user_id})"
                prefix = medals[i] if i < 3 else f"{i+1}."
                embed.add_field(name=f"{prefix} {name}", value=f"${total:,} (Wallet: ${balance:,} | Bank: ${bank:,})", inline=False)
        await interaction.response.send_message(embed=embed)

# =========================
# 🎉 GIVEAWAY COG (PUBLIC - NO OWNER CHECK)
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
# 🎫 TICKET COG (PUBLIC - NO OWNER CHECK)
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
# 📊 LEVELING COG (PUBLIC - NO OWNER CHECK)
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
# 🤖 AI COG (PUBLIC - NO OWNER CHECK)
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
# 📊 STATISTICS COG (OWNER ROLE ONLY)
# =========================
class Statistics(commands.Cog, name="statistics"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    stats_group = app_commands.Group(name="stats", description="Advanced statistics - Owner Role Only")
    
    @stats_group.command(name="user", description="View detailed statistics for a user")
    @is_owner()
    @app_commands.describe(member="Member to view statistics for")
    async def stats_user(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer()
        
        stats = await get_user_stats(member.id, interaction.guild.id)
        if not stats:
            return await interaction.followup.send(f"📊 No statistics recorded for {member.mention} yet. Stats begin tracking after the feature is added.")
        
        guild = interaction.guild
        
        fav_channel = None
        if stats["favorite_channel_id"]:
            fav_channel = guild.get_channel(stats["favorite_channel_id"])
        
        avg_length = 0
        if stats["message_count_with_content"] > 0:
            avg_length = stats["total_message_length"] / stats["message_count_with_content"]
        
        titles = await get_fun_titles(stats, guild, member)
        
        embed = discord.Embed(
            title=f"📊 Statistics for {member.display_name}",
            color=member.color or discord.Color.blue(),
            timestamp=datetime.now()
        )
        if member.avatar:
            embed.set_thumbnail(url=member.avatar.url)
        
        embed.add_field(
            name="💬 Messages",
            value=f"Total: **{stats['total_messages']:,}**\n"
                  f"Today: {stats['messages_today']:,}\n"
                  f"This Week: {stats['messages_week']:,}\n"
                  f"This Month: {stats['messages_month']:,}",
            inline=True
        )
        
        embed.add_field(
            name="📏 Message Length",
            value=f"Longest: **{stats['longest_message_length']}** chars\n"
                  f"Average: **{avg_length:.1f}** chars",
            inline=True
        )
        
        embed.add_field(
            name="🔥 Streaks",
            value=f"Current: **{stats['current_message_streak']}**\n"
                  f"Longest: **{stats['longest_message_streak']}**",
            inline=True
        )
        
        embed.add_field(
            name="🎭 Reactions",
            value=f"Given: **{stats['reactions_given']:,}**\n"
                  f"Received: **{stats['reactions_received']:,}**",
            inline=True
        )
        
        embed.add_field(
            name="📎 Attachments",
            value=f"Total: **{stats['attachments_sent']:,}**\n"
                  f"Images: {stats['images_sent']:,}\n"
                  f"GIFs: {stats['gifs_sent']:,}",
            inline=True
        )
        
        embed.add_field(
            name="🎨 Stickers & Links",
            value=f"Stickers: **{stats['stickers_used']:,}**\n"
                  f"Links Shared: **{stats['links_shared']:,}**",
            inline=True
        )
        
        embed.add_field(
            name="🔗 Mentions",
            value=f"Sent: **{stats['mentions_sent']:,}**\n"
                  f"Received: **{stats['mentions_received']:,}**",
            inline=True
        )
        
        embed.add_field(
            name="🎙️ Voice",
            value=f"Hours: **{stats['voice_hours']:.1f}**\n"
                  f"Joins: {stats['voice_join_count']:,}\n"
                  f"Longest Session: **{self._format_duration(stats['longest_vc_session'])}**",
            inline=True
        )
        
        embed.add_field(
            name="⚙️ Commands",
            value=f"**{stats['commands_used']:,}**",
            inline=True
        )
        
        fav_channel_name = fav_channel.mention if fav_channel else "None"
        embed.add_field(
            name="⭐ Favorites",
            value=f"Channel: {fav_channel_name}\n"
                  f"Emoji: {stats['favorite_emoji'] or 'None'}",
            inline=True
        )
        
        if titles:
            embed.add_field(
                name="🏅 Titles",
                value="\n".join(titles),
                inline=False
            )
        
        embed.set_footer(text=f"Updated: {stats['updated_at']}")
        await interaction.followup.send(embed=embed)
    
    @stats_group.command(name="server", description="View server-wide statistics")
    @is_owner()
    async def stats_server(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        stats = await get_server_stats(interaction.guild.id)
        if not stats:
            return await interaction.followup.send("📊 No server statistics available yet. Stats begin tracking after the feature is added.")
        
        guild = interaction.guild
        
        most_active_user = None
        if stats["most_active_user_id"]:
            most_active_user = guild.get_member(stats["most_active_user_id"])
        
        most_active_channel = None
        if stats["most_active_channel_id"]:
            most_active_channel = guild.get_channel(stats["most_active_channel_id"])
        
        embed = discord.Embed(
            title=f"📊 Server Statistics - {guild.name}",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        
        embed.add_field(
            name="💬 Messages",
            value=f"**{stats['total_messages']:,}** total messages",
            inline=True
        )
        
        embed.add_field(
            name="🎙️ Voice",
            value=f"**{stats['total_vc_hours']:.1f}** total hours",
            inline=True
        )
        
        embed.add_field(
            name="⚙️ Commands",
            value=f"**{stats['total_commands_executed']:,}** executed",
            inline=True
        )
        
        embed.add_field(
            name="📎 Files",
            value=f"**{stats['total_files_uploaded']:,}** uploaded",
            inline=True
        )
        
        embed.add_field(
            name="🎭 Reactions",
            value=f"**{stats['total_reactions_used']:,}** used",
            inline=True
        )
        
        embed.add_field(
            name="🛡️ Moderation",
            value=f"**{stats['total_moderation_actions']:,}** actions",
            inline=True
        )
        
        embed.add_field(
            name="👑 Most Active User",
            value=most_active_user.mention if most_active_user else "None",
            inline=True
        )
        
        embed.add_field(
            name="📢 Most Active Channel",
            value=most_active_channel.mention if most_active_channel else "None",
            inline=True
        )
        
        embed.set_footer(text=f"Updated: {stats['updated_at']}")
        await interaction.followup.send(embed=embed)
    
    @stats_group.command(name="channel", description="View channel statistics")
    @is_owner()
    @app_commands.describe(channel="Channel to view statistics for")
    async def stats_channel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        await interaction.response.defer()
        
        target = channel or interaction.channel
        stats = await get_channel_stats(interaction.guild.id, target.id)
        if not stats:
            return await interaction.followup.send(f"📊 No statistics recorded for {target.mention} yet.")
        
        most_active_user = None
        if stats["most_active_user_id"]:
            most_active_user = interaction.guild.get_member(stats["most_active_user_id"])
        
        avg_daily = 0
        if stats["total_days_tracked"] > 0:
            avg_daily = stats["total_messages"] / stats["total_days_tracked"]
        
        avg_length = 0
        if stats["total_messages"] > 0:
            avg_length = stats["total_message_length"] / stats["total_messages"]
        
        embed = discord.Embed(
            title=f"📊 Channel Statistics - #{target.name}",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        embed.add_field(
            name="💬 Total Messages",
            value=f"**{stats['total_messages']:,}**",
            inline=True
        )
        
        embed.add_field(
            name="📅 Days Tracked",
            value=f"**{stats['total_days_tracked']}**",
            inline=True
        )
        
        embed.add_field(
            name="📊 Average Daily",
            value=f"**{avg_daily:.1f}** messages/day",
            inline=True
        )
        
        embed.add_field(
            name="📏 Average Length",
            value=f"**{avg_length:.1f}** chars",
            inline=True
        )
        
        embed.add_field(
            name="👑 Most Active User",
            value=most_active_user.mention if most_active_user else "None",
            inline=True
        )
        
        embed.add_field(
            name="⏰ Peak Hour",
            value=f"**{stats['peak_hour']}:00**" if stats["peak_hour"] else "None",
            inline=True
        )
        
        if stats["peak_day"]:
            embed.add_field(
                name="📅 Peak Day",
                value=f"**{stats['peak_day']}**",
                inline=True
            )
        
        embed.set_footer(text=f"Updated: {stats['updated_at']}")
        await interaction.followup.send(embed=embed)
    
    @stats_group.command(name="leaderboard", description="View leaderboards for various stats")
    @is_owner()
    @app_commands.describe(
        stat_type="Type of leaderboard to view",
        limit="Number of entries to show (max 25)"
    )
    @app_commands.choices(stat_type=[
        discord.app_commands.Choice(name="Messages", value="messages"),
        discord.app_commands.Choice(name="Voice Hours", value="vc_hours"),
        discord.app_commands.Choice(name="Commands", value="commands"),
        discord.app_commands.Choice(name="Reactions", value="reactions"),
        discord.app_commands.Choice(name="Attachments", value="attachments"),
        discord.app_commands.Choice(name="Mentions", value="mentions"),
        discord.app_commands.Choice(name="Streaks", value="streak"),
        discord.app_commands.Choice(name="Channels", value="channels"),
    ])
    async def stats_leaderboard(self, interaction: discord.Interaction, stat_type: str, limit: int = 10):
        await interaction.response.defer()
        
        limit = min(limit, 25)
        results = await get_leaderboard(interaction.guild.id, stat_type, limit)
        
        if not results:
            return await interaction.followup.send(f"📊 No data available for {stat_type} leaderboard yet.")
        
        stat_names = {
            "messages": "Total Messages",
            "vc_hours": "Voice Hours",
            "commands": "Commands Used",
            "reactions": "Reactions Given",
            "attachments": "Attachments Sent",
            "mentions": "Mentions Sent",
            "streak": "Longest Streak",
            "channels": "Channel Messages"
        }
        
        stat_name = stat_names.get(stat_type, stat_type.title())
        embed = discord.Embed(
            title=f"🏆 {stat_name} Leaderboard",
            color=discord.Color.gold(),
            timestamp=datetime.now()
        )
        
        medals = ["🥇", "🥈", "🥉"]
        description = ""
        
        for i, result in enumerate(results):
            user_id = result[0]
            value = result[1]
            
            member = interaction.guild.get_member(user_id)
            name = member.display_name if member else f"Unknown ({user_id})"
            prefix = medals[i] if i < 3 else f"{i+1}."
            
            if stat_type == "vc_hours":
                display_value = f"{value:.1f} hours"
            elif stat_type == "channels":
                channel = interaction.guild.get_channel(user_id)
                name = f"#{channel.name}" if channel else f"Unknown ({user_id})"
                display_value = f"{value:,} messages"
            elif stat_type == "streak":
                display_value = f"{value} messages"
            else:
                display_value = f"{value:,}"
            
            description += f"{prefix} **{name}** - {display_value}\n"
        
        embed.description = description
        embed.set_footer(text=f"Showing top {len(results)} • {interaction.guild.name}")
        await interaction.followup.send(embed=embed)
    
    @stats_group.command(name="titles", description="View fun titles for a user")
    @is_owner()
    @app_commands.describe(member="Member to view titles for")
    async def stats_titles(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer()
        
        stats = await get_user_stats(member.id, interaction.guild.id)
        if not stats:
            return await interaction.followup.send(f"📊 No statistics recorded for {member.mention} yet.")
        
        titles = await get_fun_titles(stats, interaction.guild, member)
        
        embed = discord.Embed(
            title=f"🏅 Titles for {member.display_name}",
            color=member.color or discord.Color.blue(),
            timestamp=datetime.now()
        )
        if member.avatar:
            embed.set_thumbnail(url=member.avatar.url)
        
        if titles:
            embed.description = "\n".join([f"• {title}" for title in titles])
        else:
            embed.description = "No titles earned yet. Keep chatting!"
        
        await interaction.followup.send(embed=embed)
    
    @stats_group.command(name="reset", description="Reset statistics for a user (Owner Role Only)")
    @is_owner()
    @app_commands.describe(
        member="Member to reset statistics for",
        confirm="Type 'CONFIRM' to confirm reset"
    )
    async def stats_reset(self, interaction: discord.Interaction, member: discord.Member, confirm: str):
        if confirm != "CONFIRM":
            return await interaction.response.send_message(
                "⚠️ To confirm reset, type `CONFIRM` in the confirm parameter.",
                ephemeral=True
            )
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            await db.execute(
                "DELETE FROM user_stats WHERE user_id=? AND guild_id=?",
                (member.id, interaction.guild.id)
            )
            await db.execute(
                "DELETE FROM daily_user_stats WHERE user_id=? AND guild_id=?",
                (member.id, interaction.guild.id)
            )
            await db.execute(
                "DELETE FROM user_emoji_stats WHERE user_id=? AND guild_id=?",
                (member.id, interaction.guild.id)
            )
            await db.commit()
            
            embed = discord.Embed(
                title="✅ Statistics Reset",
                description=f"All statistics for {member.mention} have been reset.",
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Stats reset error: {e}")
            await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)
    
    def _format_duration(self, seconds: int) -> str:
        if seconds <= 0:
            return "0s"
        
        minutes = seconds // 60
        hours = minutes // 60
        
        if hours > 0:
            minutes = minutes % 60
            return f"{hours}h {minutes}m"
        elif minutes > 0:
            return f"{minutes}m"
        else:
            return f"{seconds}s"

# =========================
# 📋 UTILITY COMMANDS (PUBLIC - NO OWNER CHECK)
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
# 📋 VOICE COMMANDS (PUBLIC - NO OWNER CHECK)
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
# 📋 HELP COG (PUBLIC - NO OWNER CHECK)
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
            name="🛡️ Moderation (Owner Role Only)",
            value="`/mod clear`, `/mod thanos_snap`, `/mod ban`, `/mod softban`, `/mod unban`, `/mod kick`, `/mod mute`, `/mod unmute`, `/mod warn`, `/mod warnings`, `/mod clean`, `/mod lock`, `/mod unlock`, `/mod slowmode`, `/mod history`, `/mod hide`, `/mod show`, `/mod snipe`, `/mod editsnipe`\n"
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
            name="📊 Statistics (Owner Role Only)",
            value="`/stats user`, `/stats server`, `/stats channel`, `/stats leaderboard`, `/stats titles`, `/stats reset`",
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
    async def on_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        if isinstance(error, discord.app_commands.CommandOnCooldown):
            return await interaction.response.send_message(f"⏰ Command on cooldown. Try again in {error.retry_after:.0f}s.", ephemeral=True)
        elif isinstance(error, discord.app_commands.MissingPermissions):
            return await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        elif isinstance(error, discord.app_commands.BotMissingPermissions):
            return await interaction.response.send_message("❌ I don't have the required permissions.", ephemeral=True)
        elif isinstance(error, discord.app_commands.CheckFailure):
            # Give a more helpful message for owner role check failures
            return await interaction.response.send_message(
                f"❌ This command is restricted to {owner_role_mention()} only.\n"
                f"Please make sure you have the '{OWNER_ROLE_NAME}' role.",
                ephemeral=True
            )
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
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @app_commands.command(name="controlpanelset", description="Set up the control panel in the current channel")
    @is_owner()
    async def controlpanelset(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        try:
            existing = await get_control_panel(interaction.guild_id)
            if existing:
                try:
                    old_channel = interaction.guild.get_channel(existing["channel_id"])
                    if old_channel:
                        old_message = await old_channel.fetch_message(existing["message_id"])
                        await old_message.delete()
                except:
                    pass
                await delete_control_panel(interaction.guild_id)
            
            embed = discord.Embed(
                title="🎛️ Bot Control Panel",
                description="Welcome to the bot control panel! Use the buttons and dropdowns below to manage the bot.\n\n"
                           "**🛡️ Moderation Panel** - Click the Moderation button for moderation tools.",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            embed.add_field(name="🤖 Status", value="Bot is online and ready", inline=True)
            embed.add_field(name="👑 Owner Role", value=owner_role_mention(), inline=True)
            embed.add_field(name="📊 Commands", value=f"{len(self.bot.tree.get_commands())} total", inline=True)
            embed.set_footer(text="Control Panel • Owner Role Only")
            
            view = ControlPanelView(self.bot)
            message = await interaction.channel.send(embed=embed, view=view)
            
            await save_control_panel(interaction.guild_id, interaction.channel_id, message.id)
            
            await interaction.followup.send(
                f"✅ Control panel has been set up in {interaction.channel.mention}!",
                ephemeral=True
            )
            
        except Exception as e:
            logger.error(f"Control panel setup error: {e}")
            await interaction.followup.send(f"❌ Failed to set up control panel: {str(e)}", ephemeral=True)
    
    @app_commands.command(name="controlpanelrefresh", description="Refresh the control panel")
    @is_owner()
    async def controlpanelrefresh(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await handle_refresh_panel(interaction)
    
    @app_commands.command(name="controlpanelremove", description="Remove the control panel")
    @is_owner()
    async def controlpanelremove(self, interaction: discord.Interaction):
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
    @is_owner()
    @app_commands.describe(channel="Channel to move the control panel to")
    async def controlpanelmove(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        
        config = await get_control_panel(interaction.guild_id)
        if not config:
            await interaction.followup.send("❌ No control panel configured in this server.", ephemeral=True)
            return
        
        try:
            old_channel = interaction.guild.get_channel(config["channel_id"])
            if old_channel:
                try:
                    old_message = await old_channel.fetch_message(config["message_id"])
                    await old_message.delete()
                except:
                    pass
            
            embed = discord.Embed(
                title="🎛️ Bot Control Panel",
                description="Welcome to the bot control panel! Use the buttons and dropdowns below to manage the bot.\n\n"
                           "**🛡️ Moderation Panel** - Click the Moderation button for moderation tools.",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            embed.add_field(name="🤖 Status", value="Bot is online and ready", inline=True)
            embed.add_field(name="👑 Owner Role", value=owner_role_mention(), inline=True)
            embed.add_field(name="📊 Commands", value=f"{len(self.bot.tree.get_commands())} total", inline=True)
            embed.set_footer(text="Control Panel • Owner Role Only")
            
            view = ControlPanelView(self.bot)
            message = await channel.send(embed=embed, view=view)
            
            await save_control_panel(interaction.guild_id, channel.id, message.id)
            
            await interaction.followup.send(
                f"✅ Control panel moved to {channel.mention}!",
                ephemeral=True
            )
            
        except Exception as e:
            logger.error(f"Control panel move error: {e}")
            await interaction.followup.send(f"❌ Failed to move control panel: {str(e)}", ephemeral=True)

# =========================
# AI RESPONSE FUNCTION
# =========================
async def get_ai_response(prompt: str) -> str:
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
# MAIN BOT INITIALIZATION
# =========================
async def main():
    async with bot:
        await db.connect()
        await initialize_bot_state()
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
        await bot.add_cog(Statistics(bot))
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
