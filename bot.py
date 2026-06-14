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
from collections import defaultdict, deque
from typing import Optional, List

from google import genai
from pypdf import PdfReader
from docx import Document
from discord.utils import utcnow
from discord.ext import commands

# =========================
# 🔥 MUSIC SYSTEM IMPORTS (REPLACED with wavelink)
# =========================
import wavelink
import urllib.parse
import urllib.request
import math

TOKEN = os.getenv("TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID = int(os.getenv("OWNER_ID"))

# Lavalink configuration
LAVALINK_HOST = os.getenv("LAVALINK_HOST", "localhost")
LAVALINK_PORT = int(os.getenv("LAVALINK_PORT", "2333"))
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")

client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_NAME = "gemini-3.5-flash"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.presences = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

START_TIME = datetime.now()

# =========================
# DATABASE
# =========================
conn = sqlite3.connect("moderation.db")
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    guild_id INTEGER,
    reason TEXT,
    moderator TEXT,
    timestamp TEXT
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS memory (
    user_id INTEGER PRIMARY KEY,
    user_name TEXT,
    bot_name TEXT
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    user_id INTEGER,
    channel_id INTEGER,
    status TEXT DEFAULT 'open',
    created_at TEXT
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS giveaways (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    channel_id INTEGER,
    prize TEXT,
    winner_count INTEGER,
    end_time TEXT,
    host_id INTEGER,
    message_id INTEGER
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS reaction_roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    channel_id INTEGER,
    message_id INTEGER,
    emoji TEXT,
    role_id INTEGER
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS leveling (
    user_id INTEGER,
    guild_id INTEGER,
    xp INTEGER DEFAULT 0,
    level INTEGER DEFAULT 1,
    PRIMARY KEY (user_id, guild_id)
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS economy (
    user_id INTEGER,
    guild_id INTEGER,
    balance INTEGER DEFAULT 0,
    bank INTEGER DEFAULT 0,
    daily_streak INTEGER DEFAULT 0,
    last_daily TEXT,
    PRIMARY KEY (user_id, guild_id)
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS custom_commands (
    guild_id INTEGER,
    name TEXT,
    response TEXT,
    PRIMARY KEY (guild_id, name)
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    name TEXT,
    url TEXT,
    added_by INTEGER
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS birthdays (
    user_id INTEGER PRIMARY KEY,
    guild_id INTEGER,
    date TEXT,
    year INTEGER
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS polls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    channel_id INTEGER,
    question TEXT,
    options TEXT,
    votes TEXT
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    user_id INTEGER,
    username TEXT,
    event_type TEXT,
    details TEXT,
    timestamp TEXT
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS daily_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    date TEXT,
    summary TEXT,
    total_messages INTEGER,
    most_active_user_id INTEGER,
    top_topic TEXT,
    generated_at TEXT
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS message_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    channel_id INTEGER,
    user_id INTEGER,
    date TEXT,
    count INTEGER DEFAULT 0,
    topics TEXT
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS ai_conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    channel_id INTEGER,
    user_id INTEGER,
    role TEXT,
    content TEXT,
    timestamp TEXT
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS ai_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    user_id INTEGER,
    key TEXT,
    value TEXT,
    importance INTEGER DEFAULT 1,
    created_at TEXT,
    last_accessed TEXT
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS ai_personality (
    guild_id INTEGER,
    user_id INTEGER,
    trait TEXT,
    value TEXT,
    PRIMARY KEY (guild_id, user_id, trait)
)
""")

# =========================
# 🎵 MUSIC SYSTEM TABLES
# =========================
c.execute("""
CREATE TABLE IF NOT EXISTS music_favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    guild_id INTEGER,
    title TEXT,
    url TEXT,
    added_at TEXT
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS music_playlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER,
    name TEXT,
    user_id INTEGER,
    created_at TEXT
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS music_playlist_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id INTEGER,
    title TEXT,
    url TEXT,
    position INTEGER,
    added_at TEXT
)
""")

conn.commit()

# =========================
# HISTORY FUNCTIONS
# =========================
def add_history(guild_id, user_id, username, event_type, details):
    c.execute("""
    INSERT INTO history (guild_id, user_id, username, event_type, details, timestamp)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (
        guild_id,
        user_id,
        username,
        event_type,
        details,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))
    conn.commit()

def get_user_history(user_id, guild_id, limit=20):
    c.execute("""
    SELECT event_type, details, timestamp
    FROM history
    WHERE user_id=? AND guild_id=?
    ORDER BY id DESC
    LIMIT ?
    """, (user_id, guild_id, limit))
    return c.fetchall()

def get_guild_history_by_date(guild_id, date_str, limit=50):
    c.execute("""
    SELECT event_type, details, timestamp, username, user_id
    FROM history
    WHERE guild_id=? AND timestamp LIKE ?
    ORDER BY id DESC
    LIMIT ?
    """, (guild_id, f"{date_str}%", limit))
    return c.fetchall()

def get_guild_history_by_type(guild_id, event_type, limit=20):
    c.execute("""
    SELECT event_type, details, timestamp, username, user_id
    FROM history
    WHERE guild_id=? AND event_type=?
    ORDER BY id DESC
    LIMIT ?
    """, (guild_id, event_type, limit))
    return c.fetchall()

def get_history_search(guild_id, search_term, limit=20):
    c.execute("""
    SELECT event_type, details, timestamp, username, user_id
    FROM history
    WHERE guild_id=? AND (details LIKE ? OR username LIKE ?)
    ORDER BY id DESC
    LIMIT ?
    """, (guild_id, f"%{search_term}%", f"%{search_term}%", limit))
    return c.fetchall()

# =========================
# MESSAGE STATS FUNCTIONS
# =========================
def log_message(guild_id, channel_id, user_id, content):
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("""
    INSERT INTO message_stats (guild_id, channel_id, user_id, date, count, topics)
    VALUES (?, ?, ?, ?, 1, ?)
    ON CONFLICT(guild_id, channel_id, user_id, date)
    DO UPDATE SET count = count + 1
    """, (guild_id, channel_id, user_id, today, ""))
    conn.commit()

def get_channel_stats(guild_id, channel_id, days=7):
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    c.execute("""
    SELECT date, SUM(count) as total
    FROM message_stats
    WHERE guild_id=? AND channel_id=? AND date BETWEEN ? AND ?
    GROUP BY date
    ORDER BY date
    """, (guild_id, channel_id, start_date, end_date))
    return c.fetchall()

def get_top_channels(guild_id, days=7):
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    c.execute("""
    SELECT channel_id, SUM(count) as total
    FROM message_stats
    WHERE guild_id=? AND date BETWEEN ? AND ?
    GROUP BY channel_id
    ORDER BY total DESC
    LIMIT 5
    """, (guild_id, start_date, end_date))
    return c.fetchall()

def get_most_active_user(guild_id, days=7):
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    c.execute("""
    SELECT user_id, SUM(count) as total
    FROM message_stats
    WHERE guild_id=? AND date BETWEEN ? AND ?
    GROUP BY user_id
    ORDER BY total DESC
    LIMIT 1
    """, (guild_id, start_date, end_date))
    return c.fetchone()

def get_busiest_day(guild_id):
    c.execute("""
    SELECT date, SUM(count) as total
    FROM message_stats
    WHERE guild_id=?
    GROUP BY date
    ORDER BY total DESC
    LIMIT 1
    """, (guild_id,))
    return c.fetchone()

def get_topic_for_date(guild_id, date_str, channel_id=None):
    if channel_id:
        c.execute("""
        SELECT details FROM history
        WHERE guild_id=? AND timestamp LIKE ? AND event_type IN ('MESSAGE', 'TOPIC')
        ORDER BY id DESC LIMIT 20
        """, (guild_id, f"{date_str}%"))
    else:
        c.execute("""
        SELECT details FROM history
        WHERE guild_id=? AND timestamp LIKE ? AND event_type IN ('MESSAGE', 'TOPIC')
        ORDER BY id DESC LIMIT 50
        """, (guild_id, f"{date_str}%"))
    return c.fetchall()

def count_events_for_date(guild_id, date_str, event_type):
    c.execute("""
    SELECT COUNT(*) FROM history
    WHERE guild_id=? AND timestamp LIKE ? AND event_type=?
    """, (guild_id, f"{date_str}%", event_type))
    return c.fetchone()[0]

# =========================
# MEMORY FUNCTIONS
# =========================
def get_memory(user_id):
    c.execute("SELECT user_name, bot_name FROM memory WHERE user_id=?", (user_id,))
    return c.fetchone()

def save_memory(user_id, user_name=None, bot_name=None):
    existing = get_memory(user_id)
    if existing:
        user_name = user_name or existing[0]
        bot_name = bot_name or existing[1]
        c.execute("UPDATE memory SET user_name=?, bot_name=? WHERE user_id=?", (user_name, bot_name, user_id))
    else:
        c.execute("INSERT INTO memory (user_id, user_name, bot_name) VALUES (?, ?, ?)", (user_id, user_name, bot_name))
    conn.commit()

def save_ai_memory(guild_id, user_id, key, value, importance=1):
    c.execute("""
    INSERT INTO ai_memories (guild_id, user_id, key, value, importance, created_at, last_accessed)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(guild_id, user_id, key)
    DO UPDATE SET value=excluded.value, importance=excluded.importance, last_accessed=excluded.last_accessed
    """, (guild_id, user_id, key, value, importance, datetime.now().isoformat(), datetime.now().isoformat()))
    conn.commit()

def get_ai_memories(guild_id, user_id, limit=20):
    c.execute("""
    SELECT key, value, importance, created_at, last_accessed
    FROM ai_memories
    WHERE guild_id=? AND user_id=?
    ORDER BY importance DESC, last_accessed DESC
    LIMIT ?
    """, (guild_id, user_id, limit))
    memories = c.fetchall()
    c.execute("""
    UPDATE ai_memories SET last_accessed=? WHERE guild_id=? AND user_id=?
    """, (datetime.now().isoformat(), guild_id, user_id))
    conn.commit()
    return memories

def get_all_guild_memories(guild_id, limit=50):
    c.execute("""
    SELECT user_id, key, value, importance
    FROM ai_memories
    WHERE guild_id=?
    ORDER BY importance DESC, last_accessed DESC
    LIMIT ?
    """, (guild_id, limit))
    return c.fetchall()

def save_conversation(guild_id, channel_id, user_id, role, content):
    c.execute("""
    INSERT INTO ai_conversations (guild_id, channel_id, user_id, role, content, timestamp)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (guild_id, channel_id, user_id, role, content[:1000], datetime.now().isoformat()))
    conn.commit()

def get_recent_conversation(guild_id, channel_id, user_id=None, limit=15):
    if user_id:
        c.execute("""
        SELECT role, content, timestamp, user_id
        FROM ai_conversations
        WHERE guild_id=? AND channel_id=? AND user_id=?
        ORDER BY id DESC LIMIT ?
        """, (guild_id, channel_id, user_id, limit))
    else:
        c.execute("""
        SELECT role, content, timestamp, user_id
        FROM ai_conversations
        WHERE guild_id=? AND channel_id=?
        ORDER BY id DESC LIMIT ?
        """, (guild_id, channel_id, limit))
    return list(reversed(c.fetchall()))

def save_personality_trait(guild_id, user_id, trait, value):
    c.execute("""
    INSERT INTO ai_personality (guild_id, user_id, trait, value)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(guild_id, user_id, trait)
    DO UPDATE SET value=excluded.value
    """, (guild_id, user_id, trait, value))
    conn.commit()

def get_user_personality(guild_id, user_id):
    c.execute("""
    SELECT trait, value FROM ai_personality
    WHERE guild_id=? AND user_id=?
    """, (guild_id, user_id))
    return c.fetchall()

async def build_ai_context(guild_id, channel_id, user_id, username, message_content):
    mem = get_memory(user_id)
    user_name = mem[0] if mem else username
    bot_name = mem[1] if mem else "AI Bot"
    
    ai_memories = get_ai_memories(guild_id, user_id)
    traits = get_user_personality(guild_id, user_id)
    recent_msgs = get_recent_conversation(guild_id, channel_id, limit=10)
    user_events = get_user_history(user_id, guild_id, limit=5)
    
    c.execute("SELECT level FROM leveling WHERE user_id=? AND guild_id=?", (user_id, guild_id))
    level_row = c.fetchone()
    level = level_row[0] if level_row else 0
    
    bal = get_balance(user_id, guild_id)
    
    await extract_memory_facts(guild_id, user_id, message_content)
    
    context_parts = []
    context_parts.append(f"User's name: {user_name}")
    context_parts.append(f"Bot's name: {bot_name}")
    context_parts.append(f"Server: {bot.get_guild(guild_id).name if bot.get_guild(guild_id) else 'Unknown'}")
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
            save_ai_memory(guild_id, user_id, key, m.group(1).title(), importance=5)
            break
    
    age_m = re.search(r"i(?:')?m (\d+) (?:years old|yr old|yo)", msg_lower)
    if age_m:
        save_ai_memory(guild_id, user_id, "age", age_m.group(1), importance=4)
    
    loc_m = re.search(r"i(?:')?m (?:from|in) (\w+(?:\s+\w+)?)", msg_lower)
    if loc_m:
        save_ai_memory(guild_id, user_id, "location", loc_m.group(1).title(), importance=3)
    
    hobby_patterns = [
        (r"i (?:like|love|enjoy) (\w+(?: \w+)?)", "hobby"),
        (r"my (?:hobby|favorite) (?:is|are) (\w+(?: \w+)?)", "hobby"),
        (r"i (?:play|code|draw|write|read|game|stream) (\w+(?: \w+)?)", "interest"),
    ]
    for pattern, key in hobby_patterns:
        m = re.search(pattern, msg_lower)
        if m:
            save_ai_memory(guild_id, user_id, key, m.group(1).title(), importance=2)
    
    mood_m = re.search(r"i(?:')?m (?:feeling|so|very|really) (\w+)", msg_lower)
    if mood_m:
        save_ai_memory(guild_id, user_id, "current_mood", mood_m.group(1), importance=1)
        save_personality_trait(guild_id, user_id, "recent_mood", mood_m.group(1))
    
    pref_patterns = [
        (r"i (?:don't|do not) like (\w+(?: \w+)?)", "dislikes"),
        (r"i love (\w+(?: \w+)?)", "likes"),
    ]
    for pattern, default_key in pref_patterns:
        m = re.search(pattern, msg_lower)
        if m:
            save_ai_memory(guild_id, user_id, default_key, m.group(1).title(), importance=2)
    
    work_m = re.search(r"i (?:work|study) (?:at|in|as) (\w+(?: \w+)?)", msg_lower)
    if work_m:
        save_ai_memory(guild_id, user_id, "occupation", work_m.group(1).title(), importance=3)

_ai_response_cache = {}
_ai_rate_limit = defaultdict(float)

async def get_ai_response(prompt, temperature=0.7, max_retries=2):
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
                config={
                    "temperature": temperature,
                    "max_output_tokens": 500,
                }
            )
            reply = response.text
            
            _ai_response_cache[cache_key] = (datetime.now(), reply)
            
            if len(_ai_response_cache) > 100:
                oldest_key = min(_ai_response_cache.keys(), key=lambda k: _ai_response_cache[k][0])
                del _ai_response_cache[oldest_key]
            
            return reply
            
        except Exception as e:
            last_error = e
            await asyncio.sleep(0.5 * (attempt + 1))
    
    return f"⚠️ AI Error: {last_error}"

async def summarize_conversation(conversation_text):
    if len(conversation_text) < 500:
        return conversation_text
    
    prompt = f"""Summarize this conversation concisely, keeping key facts, preferences, and topics discussed:

{conversation_text}

Summary:"""
    
    try:
        response = client.models.generate_content(model=MODEL_NAME, contents=prompt)
        return response.text[:500]
    except Exception as e:
        return conversation_text[-500:]

def add_warning(user_id, guild_id, reason, moderator=None):
    c.execute("INSERT INTO warnings (user_id, guild_id, reason, moderator, timestamp) VALUES (?, ?, ?, ?, ?)",
              (user_id, guild_id, reason, moderator, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()

def get_warnings(user_id, guild_id):
    c.execute("SELECT id, reason, timestamp FROM warnings WHERE user_id=? AND guild_id=?", (user_id, guild_id))
    return c.fetchall()

def remove_warning(warning_id):
    c.execute("DELETE FROM warnings WHERE id=?", (warning_id,))
    conn.commit()

# =========================
# LEVELING SYSTEM
# =========================
XP_COOLDOWN = {}

async def add_xp(user_id, guild_id):
    if guild_id is None:
        return
    key = f"{user_id}-{guild_id}"
    now = datetime.now()
    if key in XP_COOLDOWN:
        if (now - XP_COOLDOWN[key]).seconds < 60:
            return
    XP_COOLDOWN[key] = now
    
    xp_gain = random.randint(15, 25)
    
    c.execute("SELECT xp, level FROM leveling WHERE user_id=? AND guild_id=?", (user_id, guild_id))
    result = c.fetchone()
    
    if result:
        xp, level = result
        xp += xp_gain
        xp_needed = level * 100
        if xp >= xp_needed:
            level += 1
            xp = 0
            c.execute("UPDATE leveling SET xp=?, level=? WHERE user_id=? AND guild_id=?", (xp, level, user_id, guild_id))
            conn.commit()
            return level
        else:
            c.execute("UPDATE leveling SET xp=? WHERE user_id=? AND guild_id=?", (xp, user_id, guild_id))
    else:
        c.execute("INSERT INTO leveling (user_id, guild_id, xp, level) VALUES (?, ?, ?, 1)", (user_id, guild_id, xp_gain))
    
    conn.commit()
    return None

# =========================
# ECONOMY SYSTEM
# =========================
def get_balance(user_id, guild_id):
    c.execute("SELECT balance, bank FROM economy WHERE user_id=? AND guild_id=?", (user_id, guild_id))
    result = c.fetchone()
    if result:
        return {'wallet': result[0], 'bank': result[1]}
    c.execute("INSERT INTO economy (user_id, guild_id, balance, bank) VALUES (?, ?, 0, 0)", (user_id, guild_id))
    conn.commit()
    return {'wallet': 0, 'bank': 0}

def update_balance(user_id, guild_id, amount, account='wallet'):
    c.execute("UPDATE economy SET " + account + " = " + account + " + ? WHERE user_id=? AND guild_id=?", (amount, user_id, guild_id))
    conn.commit()

# =========================
# LOG SYSTEM
# =========================
async def log(guild, text):
    channel = discord.utils.get(guild.text_channels, name="mod-logs")
    if channel:
        await channel.send(f"📜 {text}")

async def log_to_channel(guild, embed):
    channel = discord.utils.get(guild.text_channels, name="mod-logs")
    if channel:
        await channel.send(embed=embed)

# =========================
# DM MEMORY
# =========================
dm_memory = {}

BAD_WORDS = ["badword1", "badword2"]
INVITE_REGEX = r"(discord\.gg/|discordapp\.com/invite/)"

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
# TICKET SYSTEM
# =========================
ticket_configs = {}

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="🎫 Create Ticket", style=discord.ButtonStyle.green, custom_id="create_ticket")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        user = interaction.user
        
        c.execute("SELECT channel_id FROM tickets WHERE guild_id=? AND user_id=? AND status='open'", (guild.id, user.id))
        existing = c.fetchone()
        if existing:
            channel = guild.get_channel(existing[0])
            if channel:
                await interaction.response.send_message(f"You already have an open ticket: {channel.mention}", ephemeral=True)
                return
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        }
        
        staff_roles = [role for role in guild.roles if role.permissions.administrator or role.permissions.manage_channels]
        for role in staff_roles[:5]:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        
        channel = await guild.create_text_channel(f"ticket-{user.name}", overwrites=overwrites, category=interaction.channel.category)
        
        c.execute("INSERT INTO tickets (guild_id, user_id, channel_id, status, created_at) VALUES (?, ?, ?, 'open', ?)",
                  (guild.id, user.id, channel.id, datetime.now().isoformat()))
        conn.commit()
        
        add_history(guild.id, user.id, str(user), "TICKET_CREATE", f"Created ticket #{channel.name}")
        
        embed = discord.Embed(title="🎫 New Ticket", description=f"Ticket created by {user.mention}\nPlease describe your issue.", color=discord.Color.green())
        await channel.send(embed=embed, view=TicketCloseView(user.id))
        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)

class TicketCloseView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=None)
        self.user_id = user_id
    
    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.red, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only the ticket creator or an admin can close this.", ephemeral=True)
            return
        
        await interaction.response.send_message("Closing ticket in 5 seconds...")
        await asyncio.sleep(5)
        
        c.execute("UPDATE tickets SET status='closed' WHERE channel_id=?", (interaction.channel.id,))
        conn.commit()
        
        await interaction.channel.delete()

# =========================
# GIVEAWAY SYSTEM
# =========================
class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id, end_time, winner_count):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        self.end_time = end_time
        self.winner_count = winner_count
        self.entries = []
    
    @discord.ui.button(label="🎉 Enter Giveaway", style=discord.ButtonStyle.blurple, custom_id="enter_giveaway")
    async def enter_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.entries:
            await interaction.response.send_message("You're already entered!", ephemeral=True)
            return
        
        self.entries.append(interaction.user.id)
        await interaction.response.send_message("✅ You've entered the giveaway!", ephemeral=True)

# =========================
# POLL SYSTEM
# =========================
class PollView(discord.ui.View):
    def __init__(self, poll_id, options):
        super().__init__(timeout=None)
        self.poll_id = poll_id
        self.votes = {i: [] for i in range(len(options))}
        
        for i, option in enumerate(options):
            button = discord.ui.Button(label=f"{self._get_emoji(i)} {option}", style=discord.ButtonStyle.secondary, custom_id=f"poll_vote_{poll_id}_{i}")
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
            
            c.execute("SELECT votes FROM polls WHERE id=?", (self.poll_id,))
            result = c.fetchone()
            if result:
                votes_data = json.loads(result[0])
                for idx in votes_data:
                    if user_id in votes_data[idx]:
                        votes_data[idx].remove(user_id)
                votes_data[option_index].append(user_id)
                c.execute("UPDATE polls SET votes=? WHERE id=?", (json.dumps(votes_data), self.poll_id))
                conn.commit()
            
            await interaction.response.send_message(f"✅ You voted for option {option_index + 1}!", ephemeral=True)
        
        return callback

# =========================
# 🎯 BACKGROUND TASKS
# =========================
@tasks.loop(minutes=1)
async def check_giveaways():
    now = datetime.now()
    c.execute("SELECT * FROM giveaways WHERE end_time < ?", (now.isoformat(),))
    ended = c.fetchall()
    for gw in ended:
        g_id, guild_id, channel_id, prize, winner_count, end_time, host_id, message_id = gw
        channel = bot.get_channel(channel_id)
        if channel:
            try:
                message = await channel.fetch_message(message_id)
                if message:
                    reactions = message.reactions
                    all_users = []
                    for reaction in reactions:
                        async for user in reaction.users():
                            if user != bot.user:
                                all_users.append(user)
                    
                    if all_users and winner_count > 0:
                        winners = random.sample(all_users, min(winner_count, len(all_users)))
                        winner_mentions = ", ".join(w.mention for w in winners)
                        await channel.send(f"🎉 **Giveaway Ended!**\nPrize: {prize}\nWinners: {winner_mentions}")
                        
                        for winner in winners:
                            try:
                                await winner.send(f"🎉 You won **{prize}** in {channel.guild.name}!")
                                add_history(guild_id, winner.id, str(winner), "GIVEAWAY_WIN", f"Won {prize}")
                            except Exception as e:
                                pass
                    else:
                        await channel.send(f"❌ Giveaway ended but no one entered for **{prize}**")
            except Exception as e:
                pass
        
        c.execute("DELETE FROM giveaways WHERE id=?", (g_id,))
        conn.commit()

@tasks.loop(minutes=5)
async def check_birthdays():
    today = datetime.now().strftime("%m-%d")
    c.execute("SELECT user_id, guild_id FROM birthdays WHERE date=?", (today,))
    results = c.fetchall()
    for user_id, guild_id in results:
        guild = bot.get_guild(guild_id)
        if guild:
            channel = discord.utils.get(guild.text_channels, name="general")
            if channel:
                await channel.send(f"🎂 Happy Birthday <@{user_id}>! 🎉")
                add_history(guild_id, user_id, str(user_id), "BIRTHDAY", "Birthday announced")

@tasks.loop(hours=24)
async def generate_daily_summary():
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    for guild in bot.guilds:
        busiest = get_busiest_day(guild.id)
        most_active = get_most_active_user(guild.id)
        
        joins = count_events_for_date(guild.id, yesterday, "JOIN")
        leaves = count_events_for_date(guild.id, yesterday, "LEAVE")
        warns = count_events_for_date(guild.id, yesterday, "WARN")
        deletes = count_events_for_date(guild.id, yesterday, "DELETE")
        kicks = count_events_for_date(guild.id, yesterday, "KICK")
        bans = count_events_for_date(guild.id, yesterday, "BAN")
        
        c.execute("""
        SELECT SUM(count) FROM message_stats
        WHERE guild_id=? AND date=?
        """, (guild.id, yesterday))
        total_msgs = c.fetchone()[0] or 0
        
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
        
        c.execute("""
        INSERT INTO daily_summaries (guild_id, date, summary, total_messages, most_active_user_id, top_topic, generated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (guild.id, yesterday, summary, total_msgs, most_active[0] if most_active else 0, "", datetime.now().isoformat()))
        conn.commit()
        
        channel = discord.utils.get(guild.text_channels, name="mod-logs")
        if channel and total_msgs > 0:
            await channel.send(summary)

@tasks.loop(hours=6)
async def consolidate_memories():
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    c.execute("""
    DELETE FROM ai_memories
    WHERE importance <= 1 AND last_accessed < ?
    """, (cutoff,))
    deleted = c.rowcount
    conn.commit()
    if deleted > 0:
        print(f"🧹 Consolidated {deleted} old memories")

# =========================
# 🎵 MUSIC BACKGROUND TASKS - UPDATED for wavelink
# =========================
@tasks.loop(minutes=5)
async def clean_inactive_players():
    for guild_id, player in list(music_players.items()):
        try:
            vc = player.voice_client
            if not vc or not vc.is_connected():
                continue

            channel = vc.channel
            if channel and len(channel.members) == 1 and channel.members[0].id == bot.user.id:
                if not vc.is_playing() and not vc.is_paused():
                    await player.disconnect_voice()
                    music_players.pop(guild_id, None)

        except Exception as e:
            print(f"[clean task error] {e}")

# =========================
# 🎵 LAVALINK / WAVELINK MUSIC SYSTEM (REPLACED yt-dlp)
# =========================

URL_REGEX = re.compile(r"https?://(?:www\.)?(?:youtube\.com|youtu\.be|soundcloud\.com|spotify\.com)/\S+")

class LavalinkVoiceClient(discord.VoiceProtocol):
    """Custom VoiceProtocol that connects discord voice to Lavalink via wavelink."""
    
    def __init__(self, client: discord.Client, channel: discord.abc.Connectable):
        self.client = client
        self.channel = channel
        self._voice_server_update_data = {}
    
    async def on_voice_server_update(self, data):
        self._voice_server_update_data.update(data)
        wavelink_node = wavelink.NodePool.get_node()
        if wavelink_node:
            await wavelink_node.update_voice_state(self.channel.guild.id, self.channel.id, self._voice_server_update_data)
    
    async def on_voice_state_update(self, data):
        wavelink_node = wavelink.NodePool.get_node()
        if wavelink_node:
            await wavelink_node.update_voice_state(self.channel.guild.id, self.channel.id, data)
    
    async def connect(self, *, timeout: float = 20.0, reconnect: bool = False, self_deaf: bool = False, self_mute: bool = False) -> None:
        guild = self.channel.guild
        ws = guild._state.ws
        await ws.voice_state(str(guild.id), str(self.channel.id), self_deaf=self_deaf, self_mute=self_mute)
    
    async def disconnect(self, *, force: bool = False) -> None:
        guild = self.channel.guild
        ws = guild._state.ws
        await ws.voice_state(str(guild.id), None)
        
        wavelink_node = wavelink.NodePool.get_node()
        if wavelink_node:
            await wavelink_node.update_voice_state(guild.id, None, {})
        
        self.cleanup()


class MusicPlayer:
    """Manages music playback for a single guild using Lavalink/wavelink."""
    
    def __init__(self, bot: commands.Bot, guild_id: int):
        self.bot = bot
        self.guild_id = guild_id
        self.queue = asyncio.Queue()
        self.queue_history = []
        self.current = None  # wavelink.Track object
        self.voice_client = None  # wavelink.Player
        self.loop_mode = "none"
        self.volume = 0.5
        self.is_paused = False
        self._task = None
        self.text_channel = None

    async def connect_voice(self, channel: discord.VoiceChannel) -> bool:
        """Connect to a voice channel using Lavalink."""
        try:
            guild = channel.guild
            
            # Try to get existing wavelink player for this guild
            vc = guild.voice_client
            
            if vc and hasattr(vc, 'is_connected') and vc.is_connected():
                if vc.channel.id != channel.id:
                    await vc.move_to(channel)
                self.voice_client = vc
                return True
            
            # Connect using LavalinkVoiceClient
            vc = await channel.connect(cls=LavalinkVoiceClient)
            # Get the wavelink Player for this guild
            self.voice_client = guild.voice_client
            return True

        except Exception as e:
            print(f"Voice connect error: {e}")
            return False

    async def disconnect_voice(self):
        """Disconnect from voice channel."""
        if self.voice_client:
            try:
                await self.voice_client.disconnect()
            except:
                pass

        self.voice_client = None
        self.queue = asyncio.Queue()
        self.queue_history.clear()
        self.current = None
        self.is_paused = False
        self.loop_mode = "none"
        self._task = None

    async def add_to_queue(self, track):
        """Add a wavelink.Track to the queue."""
        await self.queue.put(track)

        if not self.is_playing() and not self.is_paused:
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._playback_loop())

    async def _playback_loop(self):
        """Main playback loop - pulls from queue and plays via Lavalink."""
        while True:
            try:
                track = await self.queue.get()
            except Exception:
                break

            if not track:
                continue

            self.current = track
            self.queue_history.append(track)
            if len(self.queue_history) > 50:
                self.queue_history.pop(0)

            await self._play_track(track)

            # Wait for track to finish
            while self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
                await asyncio.sleep(1)

            if self.loop_mode == "one":
                await self.queue.put(track)
            
            if self.queue.empty() and self.loop_mode != "all":
                self.current = None
                break

    async def _play_track(self, track):
        """Play a wavelink.Track via Lavalink."""
        if not self.voice_client:
            return

        try:
            self.is_paused = False
            await self.voice_client.play(track)
        except Exception as e:
            print(f"Play error: {e}")

    def is_playing(self):
        return self.voice_client and self.voice_client.is_playing()

    def pause(self):
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.pause()
            self.is_paused = True

    def resume(self):
        if self.voice_client and self.voice_client.is_paused():
            self.voice_client.resume()
            self.is_paused = False

    def stop(self):
        if self.voice_client:
            self.voice_client.stop()

        self.queue = asyncio.Queue()
        self.current = None
        self.is_paused = False

    def skip(self) -> bool:
        if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
            self.voice_client.stop()
            return True
        return False

    def previous(self) -> bool:
        if len(self.queue_history) >= 2:
            self.queue_history.pop()
            prev = self.queue_history.pop()
            old_queue = []
            while not self.queue.empty():
                try:
                    old_queue.append(self.queue.get_nowait())
                except:
                    break
            for item in old_queue:
                self.queue.put_nowait(item)
            self.queue.put_nowait(prev)

            if self.voice_client:
                self.voice_client.stop()
            return True
        return False

    def shuffle(self):
        items = []
        while not self.queue.empty():
            try:
                items.append(self.queue.get_nowait())
            except:
                break

        random.shuffle(items)

        self.queue = asyncio.Queue()
        for item in items:
            self.queue.put_nowait(item)

    def clear_queue(self):
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except:
                break
        self.queue = asyncio.Queue()

    def remove_from_queue(self, index: int) -> Optional[dict]:
        items = []
        while not self.queue.empty():
            try:
                items.append(self.queue.get_nowait())
            except:
                break

        if index < 1 or index > len(items):
            return None

        removed = items.pop(index - 1)

        self.queue = asyncio.Queue()
        for item in items:
            self.queue.put_nowait(item)

        return {
            'title': removed.title if hasattr(removed, 'title') else 'Unknown',
            'url': str(removed.uri) if hasattr(removed, 'uri') else '',
            'duration': removed.duration if hasattr(removed, 'duration') else 0,
        }

    def get_queue_list(self) -> list:
        items = []
        while not self.queue.empty():
            try:
                items.append(self.queue.get_nowait())
            except:
                break

        self.queue = asyncio.Queue()
        for item in items:
            self.queue.put_nowait(item)

        return items

    def set_volume(self, vol: float):
        self.volume = max(0.0, min(1.0, vol))
        if self.voice_client:
            try:
                self.voice_client.set_volume(int(self.volume * 100))
            except:
                pass

    @staticmethod
    def _format_duration(seconds: int) -> str:
        if not seconds:
            return "Live"
        h, remainder = divmod(seconds, 3600)
        m, s = divmod(remainder, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"


# Guild music players cache
music_players: dict[int, MusicPlayer] = {}


def get_music_player(guild_id: int) -> MusicPlayer:
    if guild_id not in music_players:
        music_players[guild_id] = MusicPlayer(bot, guild_id)
    return music_players[guild_id]


# =========================
# LAVALINK NODE CONNECTION
# =========================
@bot.event
async def on_wavelink_node_ready(node: wavelink.Node):
    print(f"✅ Lavalink node {node.identifier} ready!")

@bot.event
async def on_wavelink_track_end(player, track, reason):
    """Handle track end events."""
    pass  # The playback loop handles this

# =========================
# EVENTS
# =========================
@bot.event
async def on_member_join(member):
    add_history(member.guild.id, member.id, str(member), "JOIN", "Joined the server")
    
    config = welcome_configs.get(member.guild.id)
    if config:
        channel = member.guild.get_channel(config.get('channel_id'))
        if channel:
            msg = config.get('message', "Welcome {user} to {server}!").format(user=member.mention, server=member.guild.name)
            await channel.send(msg)
    
    c.execute("SELECT role_id FROM reaction_roles WHERE guild_id=? AND emoji='AUTO_ROLE'", (member.guild.id,))
    roles = c.fetchall()
    for (role_id,) in roles:
        role = member.guild.get_role(role_id)
        if role:
            await member.add_roles(role)
            add_history(member.guild.id, member.id, str(member), "ROLE_ADD", f"Auto-assigned role: {role.name}")

@bot.event
async def on_member_remove(member):
    add_history(member.guild.id, member.id, str(member), "LEAVE", "Left the server")
    await log(member.guild, f"👋 {member} left the server")

@bot.event
async def on_message_delete(message):
    if message.author.bot:
        return
    add_history(
        message.guild.id,
        message.author.id,
        str(message.author),
        "DELETE",
        message.content[:500] if message.content else "[Attachment/Embed]"
    )

@bot.event
async def on_message_edit(before, after):
    if before.author.bot:
        return
    if before.content == after.content:
        return
    add_history(
        before.guild.id,
        before.author.id,
        str(before.author),
        "EDIT",
        f"{before.content[:200]} -> {after.content[:200]}"
    )

@bot.event
async def on_guild_channel_create(channel):
    add_history(
        channel.guild.id,
        0,
        "System",
        "CHANNEL_CREATE",
        f"Channel created: #{channel.name} ({channel.type})"
    )

@bot.event
async def on_guild_channel_delete(channel):
    add_history(
        channel.guild.id,
        0,
        "System",
        "CHANNEL_DELETE",
        f"Channel deleted: #{channel.name}"
    )

@bot.event
async def on_voice_state_update(member, before, after):
    if before.channel != after.channel:
        if after.channel:
            add_history(member.guild.id, member.id, str(member), "VOICE_JOIN", f"Joined {after.channel.name}")
            await log(member.guild, f"🔊 {member} joined {after.channel.name}")
        elif before.channel:
            add_history(member.guild.id, member.id, str(member), "VOICE_LEAVE", f"Left {before.channel.name}")
            await log(member.guild, f"🔇 {member} left {before.channel.name}")

@bot.event
async def on_member_update(before, after):
    if before.nick != after.nick:
        add_history(
            after.guild.id,
            after.id,
            str(after),
            "NICK_CHANGE",
            f"{before.nick or before.name} -> {after.nick or after.name}"
        )
    
    added_roles = set(after.roles) - set(before.roles)
    removed_roles = set(before.roles) - set(after.roles)
    
    for role in added_roles:
        if role.name != "@everyone":
            add_history(after.guild.id, after.id, str(after), "ROLE_ADD", f"Role added: {role.name}")
    
    for role in removed_roles:
        if role.name != "@everyone":
            add_history(after.guild.id, after.id, str(after), "ROLE_REMOVE", f"Role removed: {role.name}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    if message.guild:
        log_message(message.guild.id, message.channel.id, message.author.id, message.content)
    
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
        
        mem = get_memory(user_id)
        if not mem:
            save_memory(user_id, user_name=message.author.name, bot_name="AI Bot")
        
        dm_memory[key] += f"User: {message.content}\n"
        
        await extract_memory_facts(0, user_id, message.content)
        
        msg = message.content.lower()
        m1 = re.search(r"my name is (.+)", msg)
        if m1:
            save_memory(user_id, user_name=m1.group(1).strip().title())
        
        m2 = re.search(r"your name is (.+)", msg)
        if m2:
            save_memory(user_id, bot_name=m2.group(1).strip().title())
        
        mem = get_memory(user_id)
        user_name, bot_name = mem if mem else (None, None)
        
        ai_mems = get_ai_memories(0, user_id, limit=15)
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
                    img = await attachment.read()
                    uploaded = client.files.upload(file=img, config={"mime_type": attachment.content_type})
                    response = client.models.generate_content(model=MODEL_NAME, contents=[prompt, uploaded])
                    reply = response.text
                
                elif attachment.filename.endswith(".pdf"):
                    pdf_data = await attachment.read()
                    pdf = PdfReader(io.BytesIO(pdf_data))
                    text = ""
                    for page in pdf.pages:
                        t = page.extract_text()
                        if t:
                            text += t + "\n"
                    response = client.models.generate_content(model=MODEL_NAME, contents=f"{prompt}\nPDF:\n{text}")
                    reply = response.text
                
                elif attachment.filename.endswith(".docx"):
                    doc_data = await attachment.read()
                    doc = Document(io.BytesIO(doc_data))
                    text = "\n".join(p.text for p in doc.paragraphs)
                    response = client.models.generate_content(model=MODEL_NAME, contents=f"{prompt}\nDOCX:\n{text}")
                    reply = response.text
                
                elif attachment.filename.endswith(".txt"):
                    txt = await attachment.read()
                    text = txt.decode("utf-8", errors="ignore")
                    response = client.models.generate_content(model=MODEL_NAME, contents=f"{prompt}\nTXT:\n{text}")
                    reply = response.text
                
                else:
                    reply = await get_ai_response(prompt)
            
            else:
                reply = await get_ai_response(prompt)
        
        except Exception as e:
            reply = f"⚠️ AI error: {e}"
        
        save_conversation(0, 0, user_id, "user", message.content)
        save_conversation(0, 0, user_id, "assistant", reply)
        
        dm_memory[key] += f"Bot: {reply}\n"
        dm_memory[key] = dm_memory[key][-8000:]
        
        while len(reply) > 1900:
            await message.channel.send(reply[:1900])
            reply = reply[1900:]
        
        await message.channel.send(reply)
        return
    
    # =========================
    # LEVELING SYSTEM
    # =========================
    if message.guild:
        new_level = await add_xp(message.author.id, message.guild.id)
        if new_level:
            await message.channel.send(f"🎉 {message.author.mention} leveled up to **Level {new_level}**!")
            add_history(message.guild.id, message.author.id, str(message.author), "LEVEL_UP", f"Reached level {new_level}")
        
        for word in BAD_WORDS:
            if word in message.content.lower():
                await message.delete()
                await message.channel.send(f"{message.author.mention} Watch your language!", delete_after=5)
                add_warning(message.author.id, message.guild.id, f"Bad word: {word}", "AutoMod")
                add_history(message.guild.id, message.author.id, str(message.author), "AUTO_MOD", f"Bad word filter: {word}")
                break
        
        if re.search(INVITE_REGEX, message.content.lower()):
            await message.delete()
            await message.channel.send(f"{message.author.mention} No invite links!", delete_after=5)
            add_warning(message.author.id, message.guild.id, "Invite link", "AutoMod")
            add_history(message.guild.id, message.author.id, str(message.author), "AUTO_MOD", "Invite link filtered")
        
        if message.content.startswith('?'):
            cmd_name = message.content[1:].lower().split()[0]
            c.execute("SELECT response FROM custom_commands WHERE guild_id=? AND name=?", (message.guild.id, cmd_name))
            result = c.fetchone()
            if result:
                await message.channel.send(result[0])
    
    await bot.process_commands(message)

# =========================
# on_ready with duplicate task prevention + Lavalink connect
# =========================
_tasks_started = False

@bot.event
async def on_ready():
    global _tasks_started
    if not _tasks_started:
        _tasks_started = True
        
        # Connect to Lavalink
        try:
            await wavelink.NodePool.create_node(
                bot=bot,
                host=LAVALINK_HOST,
                port=LAVALINK_PORT,
                password=LAVALINK_PASSWORD,
                identifier="default-node",
                region="us_central",
            )
            print("✅ Connected to Lavalink node!")
        except Exception as e:
            print(f"❌ Failed to connect to Lavalink: {e}")
        
        check_giveaways.start()
        check_birthdays.start()
        generate_daily_summary.start()
        consolidate_memories.start()
        clean_inactive_players.start()
    
    await bot.tree.sync()
    print(f"✅ ULTIMATE BOT ONLINE: {bot.user}")
    print(f"   Servers: {len(bot.guilds)}")
    print(f"   Commands: {len(bot.tree.get_commands())}")

# =========================
# WELCOME SYSTEM
# =========================
welcome_configs = {}

# =========================
# 🎮 MODERATION COMMANDS
# =========================

# Create a moderation command group
mod_group = app_commands.Group(name="mod", description="Moderation commands")

@mod_group.command(name="clear", description="Delete messages")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(manage_messages=True)
async def mod_clear(interaction: discord.Interaction, amount: int):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"🧹 Deleted {len(deleted)} messages", ephemeral=True)
    await log(interaction.guild, f"CLEAR | {len(deleted)}")

@mod_group.command(name="clearall", description="Wipe channel")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(manage_messages=True)
async def mod_clearall(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    total = 0
    while True:
        deleted = await interaction.channel.purge(limit=100)
        total += len(deleted)
        if not deleted:
            break
    await interaction.followup.send(f"Cleared {total}", ephemeral=True)
    await log(interaction.guild, f"CLEARALL | {total}")

@mod_group.command(name="ban", description="Ban member")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(ban_members=True)
async def mod_ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await interaction.response.defer()
    await member.ban(reason=reason)
    add_history(interaction.guild.id, member.id, str(member), "BAN", f"Banned by {interaction.user}: {reason}")
    await interaction.followup.send(f"🔨 Banned {member}")
    await log(interaction.guild, f"BAN | {member} | {reason}")

@mod_group.command(name="softban", description="Ban and immediately unban to clear messages")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(ban_members=True)
async def mod_softban(interaction: discord.Interaction, member: discord.Member, reason: str = "Softban"):
    await interaction.response.defer()
    await member.ban(reason=reason)
    await member.unban(reason="Softban complete")
    add_history(interaction.guild.id, member.id, str(member), "SOFTBAN", f"Softbanned by {interaction.user}: {reason}")
    await interaction.followup.send(f"🧹 Softbanned {member}")
    await log(interaction.guild, f"SOFTBAN | {member}")

@mod_group.command(name="mute", description="Timeout member")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(moderate_members=True)
async def mod_mute(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "No reason"):
    await interaction.response.defer()
    await member.timeout(utcnow() + timedelta(minutes=minutes), reason=reason)
    add_history(interaction.guild.id, member.id, str(member), "MUTE", f"Muted for {minutes}min by {interaction.user}: {reason}")
    await interaction.followup.send(f"🔇 Muted {member.mention} for {minutes} minutes")
    await log(interaction.guild, f"MUTE | {member} | {minutes}min | {reason}")

@mod_group.command(name="unmute", description="Unmute member")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(moderate_members=True)
async def mod_unmute(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer()
    await member.timeout(None)
    add_history(interaction.guild.id, member.id, str(member), "UNMUTE", f"Unmuted by {interaction.user}")
    await interaction.followup.send(f"🔊 Unmuted {member.mention}")
    await log(interaction.guild, f"UNMUTE | {member}")

@mod_group.command(name="warn", description="Warn a member")
@app_commands.describe(member="Member to warn", reason="Reason for the warning")
async def mod_warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
        await interaction.response.defer(ephemeral=True)
        
        if not interaction.user.guild_permissions.moderate_members:
            return await interaction.followup.send("❌ You don't have permission to warn members.", ephemeral=True)
        
        if member.top_role >= interaction.user.top_role:
            return await interaction.followup.send("❌ You cannot warn this member (role hierarchy).", ephemeral=True)
        
        # Add warning to database
        add_warning(member.id, interaction.guild.id, reason, str(interaction.user))
        add_history(interaction.guild.id, member.id, str(member), "WARN", f"Warned by {interaction.user}: {reason}")
        
        # Try to DM the user
        try:
            dm_embed = discord.Embed(
                title=f"You were warned in {interaction.guild.name}",
                description=f"**Reason:** {reason}",
                color=discord.Color.orange()
            )
            await member.send(embed=dm_embed)
        except:
            pass
        
        # Channel confirmation embed
        embed = discord.Embed(title="⚠️ Warned User", color=discord.Color.orange())
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text=f"User ID: {member.id}")
        
        await interaction.followup.send(embed=embed)

@mod_group.command(name="kick", description="Kick a member from the server")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(kick_members=True)
@app_commands.describe(member="Member to kick", reason="Reason for kicking")
async def mod_kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
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

        add_history(
            interaction.guild.id,
            member.id,
            str(member),
            "KICK",
            f"Kicked by {interaction.user}: {reason}"
        )

        await log(
            interaction.guild,
            f"KICK | {member} | {reason}"
        )

    except discord.Forbidden:
        await interaction.followup.send(
            "❌ I don't have permission to kick that member.",
            ephemeral=True
        )

@mod_group.command(name="clean", description="Clean a number of messages from a channel")
@app_commands.describe(amount="Number of messages to delete", member="Only delete messages from this member (optional)")
async def mod_clean(interaction: discord.Interaction, amount: int, member: discord.Member = None):
    await interaction.response.defer(ephemeral=True)
    
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.followup.send("❌ You don't have permission to manage messages.", ephemeral=True)
    
    amount = min(amount, 100)
    
    def check(msg):
        return True if member is None else msg.author.id == member.id
    
    try:
        deleted = await interaction.channel.purge(limit=amount, check=check)
        await interaction.followup.send(f"✅ Deleted {len(deleted)} messages.", ephemeral=True)
    except:
        await interaction.followup.send("❌ Failed to delete messages.", ephemeral=True)

# ─── MUSIC COG ────────────────────────────────────────────────────────────────

class MusicControls(discord.ui.View):
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
        self.queue = {}  # guild_id: list of wavelink.Track
        self.current = {}  # guild_id: wavelink.Track
        self.history = {}  # guild_id: list of wavelink.Track (for previous)
        self.loop = {}  # guild_id: bool
        self.volume = {}  # guild_id: int
    
    def get_queue(self, guild_id: int) -> list:
        if guild_id not in self.queue:
            self.queue[guild_id] = []
        return self.queue[guild_id]
    
    def get_history(self, guild_id: int) -> list:
        if guild_id not in self.history:
            self.history[guild_id] = []
        return self.history[guild_id]
    
    async def shuffle_queue(self, guild_id: int):
        q = self.get_queue(guild_id)
        random.shuffle(q)
    
    async def ensure_voice(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ You must be in a voice channel first.", ephemeral=True)
            return False
        return True

    music_group = app_commands.Group(
    name="music",
    description="Music commands"
)
    
    @music_group.command(name="play", description="Play a song from a query or URL")
    @app_commands.describe(query="Song name or URL to play")
    async def music_play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        
        if not await self.ensure_voice(interaction):
            return
        
        voice_channel = interaction.user.voice.channel
        guild = interaction.guild
        
        try:
            player = await get_music_player(guild, voice_channel)
            
            # Search for tracks
            tracks = await wavelink.Playable.search(query)
            if not tracks:
                return await interaction.followup.send("❌ No results found.")
            
            track = tracks[0]
            
            if isinstance(track, wavelink.Playlist):
                playlist_tracks = list(tracks)
                queue = self.get_queue(guild.id)
                queue.extend(playlist_tracks[1:])
                
                if not player.playing:
                    self.current[guild.id] = playlist_tracks[0]
                    await player.play(playlist_tracks[0])
                    embed = discord.Embed(title="▶️ Now Playing", color=discord.Color.green())
                    embed.add_field(name="Title", value=playlist_tracks[0].title, inline=False)
                    embed.add_field(name="Duration", value=format_duration(playlist_tracks[0].length), inline=True)
                    embed.add_field(name="Tracks in Playlist", value=len(playlist_tracks), inline=True)
                    view = MusicControls(self, playlist_tracks[0])
                    await interaction.followup.send(embed=embed, view=view)
                else:
                    embed = discord.Embed(title="📋 Added to Queue", color=discord.Color.blue())
                    embed.add_field(name="Playlist", value=track.name, inline=False)
                    embed.add_field(name="Tracks Added", value=len(playlist_tracks[1:]) + 1, inline=True)
                    await interaction.followup.send(embed=embed)
            else:
                queue = self.get_queue(guild.id)
                
                if not player.playing:
                    self.current[guild.id] = track
                    await player.play(track)
                    embed = discord.Embed(title="▶️ Now Playing", color=discord.Color.green())
                    embed.add_field(name="Title", value=track.title, inline=False)
                    embed.add_field(name="Artist", value=track.author, inline=True)
                    embed.add_field(name="Duration", value=format_duration(track.length), inline=True)
                    embed.set_thumbnail(url=track.artwork)
                    view = MusicControls(self, track)
                    await interaction.followup.send(embed=embed, view=view)
                else:
                    queue.append(track)
                    position = len(queue)
                    embed = discord.Embed(title="📋 Added to Queue", color=discord.Color.blue())
                    embed.add_field(name="Title", value=track.title, inline=False)
                    embed.add_field(name="Position", value=f"#{position}", inline=True)
                    embed.add_field(name="Duration", value=format_duration(track.length), inline=True)
                    await interaction.followup.send(embed=embed)
        
        except Exception as e:
            await interaction.followup.send(f"❌ An error occurred: {str(e)}")
    
    @music_group.command(name="search", description="Search for a song and select from results")
    @app_commands.describe(query="Search query")
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
                    value=f"Artist: {track.author} | Duration: {format_duration(track.length)}",
                    inline=False
                )
            
            class SearchSelect(discord.ui.View):
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
                        
                        voice_channel = interaction.user.voice.channel
                        guild = interaction.guild
                        player = await get_music_player(guild, voice_channel)
                        
                        queue = self.music_cog.get_queue(guild.id)
                        
                        if not player.playing:
                            self.music_cog.current[guild.id] = track
                            await player.play(track)
                            embed = discord.Embed(title="▶️ Now Playing", color=discord.Color.green())
                            embed.add_field(name="Title", value=track.title, inline=False)
                            embed.add_field(name="Artist", value=track.author, inline=True)
                            embed.add_field(name="Duration", value=format_duration(track.length), inline=True)
                            embed.set_thumbnail(url=track.artwork)
                            view = MusicControls(self.music_cog, track)
                            await interaction.followup.send(embed=embed, view=view)
                        else:
                            queue.append(track)
                            await interaction.followup.send(f"✅ Added **{track.title}** to the queue (position #{len(queue)})")
                        
                        await interaction.message.delete()
                    
                    select.callback = select_callback
                    self.add_item(select)
            
            view = SearchSelect(tracks, self)
            await interaction.followup.send(embed=embed, view=view)
        
        except Exception as e:
            await interaction.followup.send(f"❌ An error occurred: {str(e)}")
    
    @music_group.command(name="pause", description="Pause the current track")
    async def music_pause(self, interaction: discord.Interaction):
        await interaction.response.defer()
        player = interaction.guild.voice_client
        if not player or not isinstance(player, wavelink.Player) or not player.playing:
            return await interaction.followup.send("❌ Nothing is playing.")
        if player.paused:
            return await interaction.followup.send("⚠️ Already paused.")
        await player.pause()
        await interaction.followup.send("⏸️ Paused")
    
    @music_group.command(name="resume", description="Resume the current track")
    async def music_resume(self, interaction: discord.Interaction):
        await interaction.response.defer()
        player = interaction.guild.voice_client
        if not player or not isinstance(player, wavelink.Player):
            return await interaction.followup.send("❌ Not connected.")
        if not player.paused:
            return await interaction.followup.send("⚠️ Already playing.")
        await player.resume()
        await interaction.followup.send("▶️ Resumed")
    
    @music_group.command(name="skip", description="Skip the current track")
    async def music_skip(self, interaction: discord.Interaction):
        await interaction.response.defer()
        player = interaction.guild.voice_client
        if not player or not isinstance(player, wavelink.Player) or not player.playing:
            return await interaction.followup.send("❌ Nothing to skip.")
        
        # Save to history before skipping
        if interaction.guild.id in self.current:
            history = self.get_history(interaction.guild.id)
            history.append(self.current[interaction.guild.id])
            if len(history) > 20:
                history.pop(0)
        
        await player.stop()
        await interaction.followup.send("⏭️ Skipped")
    
    @music_group.command(name="stop", description="Stop playback and clear the queue")
    async def music_stop(self, interaction: discord.Interaction):
        await interaction.response.defer()
        player = interaction.guild.voice_client
        if not player or not isinstance(player, wavelink.Player):
            return await interaction.followup.send("❌ Not connected.")
        
        player.queue.clear()
        self.queue[interaction.guild.id] = []
        self.current[interaction.guild.id] = None
        await player.stop()
        await player.disconnect()
        await interaction.followup.send("⏹️ Stopped and disconnected")
    
    @music_group.command(name="queue", description="View the current music queue")
    async def music_queue(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        queue = self.get_queue(interaction.guild.id)
        current = self.current.get(interaction.guild.id)
        
        embed = discord.Embed(title="📋 Music Queue", color=discord.Color.blue())
        
        if current:
            embed.add_field(
                name="▶️ Now Playing",
                value=f"**{current.title}** by {current.author} [`{format_duration(current.length)}`]",
                inline=False
            )
        
        if not queue:
            embed.description = "No songs in queue."
        else:
            total_tracks = len(queue)
            total_duration = sum(t.length for t in queue if hasattr(t, 'length'))
            
            embed.set_footer(text=f"Total: {total_tracks} tracks | Duration: {format_duration(total_duration)}")
            
            # Show first 15 tracks
            show = queue[:15]
            for i, track in enumerate(show, 1):
                embed.add_field(
                    name=f"{i}. {track.title}",
                    value=f"{track.author} [`{format_duration(track.length)}`]",
                    inline=False
                )
            
            if len(queue) > 15:
                embed.add_field(name="...", value=f"And {len(queue) - 15} more tracks", inline=False)
        
        await interaction.followup.send(embed=embed)
    
    @music_group.command(name="nowplaying", description="Show the currently playing track")
    async def music_nowplaying(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        player = interaction.guild.voice_client
        current = self.current.get(interaction.guild.id)
        
        if not current or not player or not isinstance(player, wavelink.Player) or not player.playing:
            return await interaction.followup.send("❌ Nothing is currently playing.")
        
        embed = discord.Embed(title="▶️ Now Playing", color=discord.Color.green())
        embed.add_field(name="Title", value=current.title, inline=False)
        embed.add_field(name="Artist", value=current.author, inline=True)
        
        if hasattr(player, 'position') and hasattr(current, 'length'):
            elapsed = player.position
            total = current.length
            progress = min(elapsed / total, 1.0) if total > 0 else 0
            bar_length = 20
            filled = int(progress * bar_length)
            bar = "█" * filled + "░" * (bar_length - filled)
            embed.add_field(name="Progress", value=f"`{format_duration(elapsed)}` {bar} `{format_duration(total)}`", inline=False)
        
        embed.set_thumbnail(url=current.artwork)
        view = MusicControls(self, current)
        await interaction.followup.send(embed=embed, view=view)
    
    @music_group.command(name="shuffle", description="Shuffle the music queue")
    async def music_shuffle(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.shuffle_queue(interaction.guild.id)
        await interaction.followup.send("🔀 Queue shuffled!")
    
    @music_group.command(name="remove", description="Remove a track from the queue by position")
    @app_commands.describe(position="Position of the track to remove")
    async def music_remove(self, interaction: discord.Interaction, position: int):
        await interaction.response.defer()
        queue = self.get_queue(interaction.guild.id)
        
        if position < 1 or position > len(queue):
            return await interaction.followup.send(f"❌ Invalid position. Queue has {len(queue)} tracks.")
        
        removed = queue.pop(position - 1)
        await interaction.followup.send(f"✅ Removed **{removed.title}** from the queue.")
    
    @music_group.command(name="previous", description="Play the previous track")
    async def music_previous(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        if not await self.ensure_voice(interaction):
            return
        
        history = self.get_history(interaction.guild.id)
        if not history:
            return await interaction.followup.send("❌ No previous track.")
        
        voice_channel = interaction.user.voice.channel
        guild = interaction.guild
        player = await get_music_player(guild, voice_channel)
        
        prev_track = history.pop()
        
        # Push current to front of queue
        current = self.current.get(guild.id)
        if current:
            queue = self.get_queue(guild.id)
            queue.insert(0, current)
        
        self.current[guild.id] = prev_track
        await player.play(prev_track)
        
        embed = discord.Embed(title="⏮️ Playing Previous Track", color=discord.Color.green())
        embed.add_field(name="Title", value=prev_track.title, inline=False)
        embed.add_field(name="Artist", value=prev_track.author, inline=True)
        embed.add_field(name="Duration", value=format_duration(prev_track.length), inline=True)
        embed.set_thumbnail(url=prev_track.artwork)
        view = MusicControls(self, prev_track)
        await interaction.followup.send(embed=embed, view=view)
    
    @music_group.command(name="loop", description="Toggle looping for the current track")
    async def music_loop(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild_id = interaction.guild.id
        self.loop[guild_id] = not self.loop.get(guild_id, False)
        state = "enabled" if self.loop[guild_id] else "disabled"
        await interaction.followup.send(f"🔁 Loop {state}")
    
    @music_group.command(name="volume", description="Set the player volume")
    @app_commands.describe(volume="Volume level (0-100)")
    async def music_volume(self, interaction: discord.Interaction, volume: int):
        await interaction.response.defer()
        volume = max(0, min(100, volume))
        
        player = interaction.guild.voice_client
        if player and isinstance(player, wavelink.Player):
            await player.set_volume(volume)
        
        self.volume[interaction.guild.id] = volume
        await interaction.followup.send(f"🔊 Volume set to {volume}%")
    
    @music_group.command(name="playlist", description="Load and play a playlist from a URL")
    @app_commands.describe(url="Playlist URL")
    async def music_playlist(self, interaction: discord.Interaction, url: str):
        await interaction.response.defer()
        
        if not await self.ensure_voice(interaction):
            return
        
        voice_channel = interaction.user.voice.channel
        guild = interaction.guild
        
        try:
            player = await get_music_player(guild, voice_channel)
            tracks = await wavelink.Playable.search(url)
            
            if not tracks:
                return await interaction.followup.send("❌ No tracks found in playlist.")
            
            if isinstance(tracks, wavelink.Playlist):
                playlist_tracks = list(tracks)
                queue = self.get_queue(guild.id)
                
                if not player.playing:
                    first = playlist_tracks[0]
                    self.current[guild.id] = first
                    await player.play(first)
                    queue.extend(playlist_tracks[1:])
                    embed = discord.Embed(title="▶️ Now Playing", color=discord.Color.green())
                    embed.add_field(name="Title", value=first.title, inline=False)
                    embed.add_field(name="Playlist", value=tracks.name, inline=True)
                    embed.add_field(name="Tracks Added", value=len(playlist_tracks), inline=True)
                    view = MusicControls(self, first)
                    await interaction.followup.send(embed=embed, view=view)
                else:
                    queue.extend(playlist_tracks)
                    embed = discord.Embed(title="📋 Added to Queue", color=discord.Color.blue())
                    embed.add_field(name="Playlist", value=tracks.name, inline=False)
                    embed.add_field(name="Tracks Added", value=len(playlist_tracks), inline=True)
                    await interaction.followup.send(embed=embed)
            else:
                # Single track result from playlist command
                queue = self.get_queue(guild.id)
                if not player.playing:
                    self.current[guild.id] = tracks[0]
                    await player.play(tracks[0])
                    embed = discord.Embed(title="▶️ Now Playing", color=discord.Color.green())
                    embed.add_field(name="Title", value=tracks[0].title, inline=False)
                    view = MusicControls(self, tracks[0])
                    await interaction.followup.send(embed=embed, view=view)
                else:
                    queue.append(tracks[0])
                    await interaction.followup.send(f"✅ Added **{tracks[0].title}** to the queue.")
        
        except Exception as e:
            await interaction.followup.send(f"❌ Error: {str(e)}")


# ─── EVENTS COG ───────────────────────────────────────────────────────────────

class Events(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @commands.Cog.listener()
    async def on_ready(self):
        print(f"✅ Logged in as {self.bot.user}")
        print(f"🌐 Connected to {len(self.bot.guilds)} guild(s)")
        
        # Connect to Lavalink
        try:
            lava_node: wavelink.Node = wavelink.Node(
                uri="http://localhost:2333",
                password="youshallnotpass"
            )
            await wavelink.Pool.connect(client=self.bot, nodes=[lava_node])
            print("✅ Connected to Lavalink")
        except Exception as e:
            print(f"⚠️ Failed to connect to Lavalink: {e}")
            print("Music features will be unavailable. Start Lavalink server at http://localhost:2333")
    
    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload):
        guild = self.bot.get_guild(payload.player.guild.id)
        if not guild:
            return
        
        music_cog = self.bot.get_cog("Music")
        if music_cog:
            track = payload.track
            music_cog.current[guild.id] = track
    
    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        guild = self.bot.get_guild(payload.player.guild.id)
        if not guild:
            return
        
        music_cog = self.bot.get_cog("Music")
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
    async def on_message(self, message):
        if message.author.bot:
            return
        
        # XP system
        if message.guild:
            await self.handle_xp(message)
        
        # Auto-response to "good morning/night" etc.
        content = message.content.lower()
        if any(greeting in content for greeting in ["good morning", "gm", "good night", "gn", "good afternoon", "good evening"]):
            async with message.channel.typing():
                response = await query_ai(f"The user said: '{message.content}'. Respond naturally to their greeting in a friendly way. Keep it short.")
                await message.reply(response, mention_author=False)
    
    async def handle_xp(self, message):
        user_id = message.author.id
        guild_id = message.guild.id
        now = int(time.time())
        
        cur = conn.cursor()
        
        # Check cooldown (60 seconds)
        cur.execute("SELECT last_message FROM levels WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
        row = cur.fetchone()
        
        if row:
            if now - row[0] < 60:
                return
            xp_gain = random.randint(10, 25)
            cur.execute("UPDATE levels SET xp = xp + ?, total_messages = total_messages + 1, last_message = ? WHERE user_id = ? AND guild_id = ?",
                       (xp_gain, now, user_id, guild_id))
        else:
            xp_gain = random.randint(10, 25)
            cur.execute("INSERT INTO levels (user_id, guild_id, xp, level, total_messages, last_message) VALUES (?, ?, ?, 1, 1, ?)",
                       (user_id, guild_id, xp_gain, now))
        
        conn.commit()
        
        # Check level up
        cur.execute("SELECT xp, level FROM levels WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
        row = cur.fetchone()
        if row:
            xp, level = row
            needed = 100 + (level * 50)
            if xp >= needed:
                new_level = level + 1
                cur.execute("UPDATE levels SET xp = 0, level = ? WHERE user_id = ? AND guild_id = ?", (new_level, user_id, guild_id))
                conn.commit()
                
                # Level up message
                embed = discord.Embed(
                    title="🎉 Level Up!",
                    description=f"{message.author.mention} reached **level {new_level}**!",
                    color=discord.Color.gold()
                )
                await message.channel.send(embed=embed)


# ─── FUN COG ─────────────────────────────────────────────────────────────────

class Fun(commands.Cog, name="fun"):
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
            "Don't count on it.", "My reply is no.", "My sources say no.", "Outlook not so good.", "Very doubtful."
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
        except:
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


# ─── ECONOMY COG ─────────────────────────────────────────────────────────────

class Economy(commands.Cog, name="economy"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.daily_cooldowns = {}
    
    def get_balance(self, user_id: int) -> tuple:
        cur = conn.cursor()
        cur.execute("SELECT wallet, bank FROM economy WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        if row:
            return row
        cur.execute("INSERT INTO economy (user_id, wallet, bank) VALUES (?, 0, 0)", (user_id,))
        conn.commit()
        return (0, 0)
    
    @economy_group.command(name="balance", description="Check your or another user's balance")
    @app_commands.describe(member="Member to check (optional)")
    async def economy_balance(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        wallet, bank = self.get_balance(target.id)
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
        cur = conn.cursor()
        cur.execute("UPDATE economy SET wallet = wallet + ? WHERE user_id = ?", (amount, user_id))
        if cur.rowcount == 0:
            cur.execute("INSERT INTO economy (user_id, wallet, bank) VALUES (?, ?, 0)", (user_id, amount))
        conn.commit()
        
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
        
        sender_wallet, _ = self.get_balance(interaction.user.id)
        if sender_wallet < amount:
            return await interaction.response.send_message("❌ Insufficient funds in wallet.", ephemeral=True)
        
        cur = conn.cursor()
        cur.execute("UPDATE economy SET wallet = wallet - ? WHERE user_id = ?", (amount, interaction.user.id))
        cur.execute("UPDATE economy SET wallet = wallet + ? WHERE user_id = ?", (amount, member.id))
        if cur.rowcount == 0:
            cur.execute("INSERT INTO economy (user_id, wallet, bank) VALUES (?, ?, 0)", (member.id, amount))
        conn.commit()
        
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
        
        wallet, _ = self.get_balance(interaction.user.id)
        if wallet < amount:
            return await interaction.response.send_message("❌ Insufficient funds.", ephemeral=True)
        
        win = random.random() < 0.45
        if win:
            winnings = int(amount * random.uniform(1.5, 3.0))
            cur = conn.cursor()
            cur.execute("UPDATE economy SET wallet = wallet - ? + ? WHERE user_id = ?", (amount, winnings, interaction.user.id))
            conn.commit()
            embed = discord.Embed(title="🎰 You Won!", description=f"You gambled ${amount:,} and won **${winnings:,}**!", color=discord.Color.green())
        else:
            cur = conn.cursor()
            cur.execute("UPDATE economy SET wallet = wallet - ? WHERE user_id = ?", (amount, interaction.user.id))
            conn.commit()
            embed = discord.Embed(title="🎰 You Lost!", description=f"You gambled ${amount:,} and lost it all.", color=discord.Color.red())
        
        await interaction.response.send_message(embed=embed)
    
    @economy_group.command(name="leaderboard", description="View the economy leaderboard")
    async def economy_leaderboard(self, interaction: discord.Interaction):
        cur = conn.cursor()
        cur.execute("SELECT user_id, wallet, bank FROM economy ORDER BY (wallet + bank) DESC LIMIT 10")
        rows = cur.fetchall()
        
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


# ─── GIVEAWAY COG ─────────────────────────────────────────────────────────────

# Store active giveaways: guild_id -> list of giveaway dicts
active_giveaways = {}

class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: str):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
    
    @discord.ui.button(label="🎉 Enter Giveaway", style=discord.ButtonStyle.primary, custom_id=f"giveaway_enter")
    async def enter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for guild_id, giveaways in active_giveaways.items():
            for g in giveaways:
                if g["id"] == self.giveaway_id:
                    if interaction.user.id in g["entries"]:
                        return await interaction.response.send_message("⚠️ You already entered!", ephemeral=True)
                    g["entries"].append(interaction.user.id)
                    
                    # Update embed
                    embed = interaction.message.embeds[0]
                    embed.set_field_at(0, name="Entries", value=str(len(g["entries"])), inline=True)
                    await interaction.message.edit(embed=embed)
                    
                    return await interaction.response.send_message("✅ You entered the giveaway!", ephemeral=True)
        
        await interaction.response.send_message("❌ This giveaway has ended.", ephemeral=True)


class Giveaway(commands.Cog, name="giveaway"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.loop.create_task(self.check_giveaways())
    
    async def check_giveaways(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            now = time.time()
            for guild_id, giveaways in list(active_giveaways.items()):
                for g in giveaways[:]:
                    if now >= g["end_time"] and not g.get("ended"):
                        g["ended"] = True
                        await self.end_giveaway(guild_id, g)
            await asyncio.sleep(30)
    
    async def end_giveaway(self, guild_id: int, g: dict):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        
        channel = guild.get_channel(g["channel_id"])
        if not channel:
            return
        
        try:
            msg = await channel.fetch_message(g["message_id"])
        except:
            return
        
        entries = g["entries"]
        if not entries:
            embed = msg.embeds[0]
            embed.title = "🎉 Giveaway Ended - No Winners"
            embed.color = discord.Color.red()
            await msg.edit(embed=embed, view=None)
            return
        
        winners_count = min(g["winners"], len(entries))
        winners = random.sample(entries, winners_count)
        
        embed = msg.embeds[0]
        embed.title = "🎉 Giveaway Ended"
        embed.color = discord.Color.red()
        embed.clear_fields()
        embed.add_field(name="Winner(s)", value=", ".join(f"<@{w}>" for w in winners), inline=False)
        embed.add_field(name="Prize", value=g["prize"], inline=False)
        await msg.edit(embed=embed, view=None)
        
        await channel.send(f"🎉 Congratulations {' '.join(f'<@{w}>' for w in winners)}! You won **{g['prize']}**!")
        
        # Remove from active
        for guild_id2, giveaways in active_giveaways.items():
            if g in giveaways:
                giveaways.remove(g)
    
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
        
        # Find the giveaway
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


# ─── TICKET COG ──────────────────────────────────────────────────────────────

class TicketView(discord.ui.View):
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
        
        # Add admin role if exists
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
                overwrites=overwrites,
                category=None
            )
            
            embed = discord.Embed(
                title="🎫 Ticket Created",
                description=f"Hello {user.mention}! Support will be with you shortly.\n\nType your issue below.",
                color=discord.Color.green()
            )
            
            close_view = TicketCloseView()
            await channel.send(embed=embed, view=close_view)
            await interaction.response.send_message(f"✅ Ticket created: {channel.mention}", ephemeral=True)
        except:
            await interaction.response.send_message("❌ Failed to create ticket. Check my permissions.", ephemeral=True)


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
        await interaction.channel.delete()


class Ticket(commands.Cog, name="ticket"):
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
        
        view = TicketView()
        await interaction.response.send_message(embed=embed, view=view)
    
    @ticket_group.command(name="add", description="Add a user to a ticket")
    @app_commands.describe(member="Member to add")
    async def ticket_add(self, interaction: discord.Interaction, member: discord.Member):
        if not interaction.channel.name.startswith("ticket-"):
            return await interaction.response.send_message("❌ This is not a ticket channel.", ephemeral=True)
        
        await interaction.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
        await interaction.response.send_message(f"✅ Added {member.mention} to this ticket.")
    
    @ticket_group.command(name="remove", description="Remove a user from a ticket")
    @app_commands.describe(member="Member to remove")
    async def ticket_remove(self, interaction: discord.Interaction, member: discord.Member):
        if not interaction.channel.name.startswith("ticket-"):
            return await interaction.response.send_message("❌ This is not a ticket channel.", ephemeral=True)
        
        await interaction.channel.set_permissions(member, overwrite=None)
        await interaction.response.send_message(f"✅ Removed {member.mention} from this ticket.")


# ─── LEVELING COG ────────────────────────────────────────────────────────────

class Leveling(commands.Cog, name="leveling"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @leveling_group.command(name="rank", description="Check your or another user's level rank")
    @app_commands.describe(member="Member to check (optional)")
    async def leveling_rank(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        
        cur = conn.cursor()
        cur.execute("SELECT xp, level, total_messages FROM levels WHERE user_id = ? AND guild_id = ?", (target.id, interaction.guild.id))
        row = cur.fetchone()
        
        if not row:
            return await interaction.response.send_message(f"{target.mention} has no XP yet.", ephemeral=True)
        
        xp, level, total_messages = row
        
        # Get rank
        cur.execute("SELECT COUNT(*) FROM levels WHERE guild_id = ? AND (xp + (level * (100 + (level - 1) * 50))) > ?",
                   (interaction.guild.id, xp + (level * (100 + (level - 1) * 50))))
        rank_row = cur.fetchone()
        rank = (rank_row[0] if rank_row else 0) + 1
        
        # Get total users
        cur.execute("SELECT COUNT(*) FROM levels WHERE guild_id = ?", (interaction.guild.id,))
        total_users = cur.fetchone()[0]
        
        needed = 100 + (level * 50)
        
        embed = discord.Embed(title=f"📊 {target.display_name}'s Rank", color=discord.Color.blue())
        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(name="Rank", value=f"#{rank}/{total_users}", inline=True)
        embed.add_field(name="XP", value=f"{xp}/{needed}", inline=False)
        embed.add_field(name="Total Messages", value=str(total_messages), inline=True)
        
        # Progress bar
        progress = min(xp / needed, 1.0) if needed > 0 else 0
        bar_length = 15
        filled = int(progress * bar_length)
        bar = "█" * filled + "░" * (bar_length - filled)
        embed.add_field(name="Progress", value=f"`{bar}` {int(progress * 100)}%", inline=False)
        
        await interaction.response.send_message(embed=embed)
    
    @leveling_group.command(name="leaderboard", description="View the leveling leaderboard")
    async def leveling_leaderboard(self, interaction: discord.Interaction):
        cur = conn.cursor()
        cur.execute("SELECT user_id, level, xp FROM levels WHERE guild_id = ? ORDER BY level DESC, xp DESC LIMIT 10", (interaction.guild.id,))
        rows = cur.fetchall()
        
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


# ─── AI COG ──────────────────────────────────────────────────────────────────

class AI(commands.Cog, name="ai"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ai_client = OpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL)
        self.conversation_history = {}  # channel_id or user_id -> list of messages
    
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
        
        # Keep history manageable
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
            await interaction.followup.send(f"❌ AI Error: {str(e)}")


# ─── HELP COG ────────────────────────────────────────────────────────────────

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
            value="`/mod ban`, `/mod kick`, `/mod warn`, `/mod mute`, `/mod unmute`, `/mod clean`, `/mod softban`",
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


# ─── COMMAND GROUPS (definitions) ───────────────────────────────────────────

# These were used earlier; the actual commands are in cogs above.
# The groups are defined here for the tree to register properly.
# Note: The actual command implementations are in their respective cogs.

class GroupCog(commands.Cog):
    """Container cog for command groups that don't need their own cog."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot


# ─── ERROR HANDLER ───────────────────────────────────────────────────────────

class CommandErrorHandler(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error):
        print(f"Command error: {error}")
    
    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        if isinstance(error, discord.app_commands.CommandOnCooldown):
            return await interaction.response.send_message(f"⏰ Command on cooldown. Try again in {error.retry_after:.0f}s.", ephemeral=True)
        elif isinstance(error, discord.app_commands.MissingPermissions):
            return await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        elif isinstance(error, discord.app_commands.BotMissingPermissions):
            return await interaction.response.send_message("❌ I don't have the required permissions.", ephemeral=True)
        else:
            print(f"App command error: {error}")
            try:
                await interaction.response.send_message(f"❌ An error occurred: {str(error)}", ephemeral=True)
            except:
                try:
                    await interaction.followup.send(f"❌ An error occurred: {str(error)}", ephemeral=True)
                except:
                    pass


# ─── HELPER FUNCTIONS ───────────────────────────────────────────────────────

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
                    return f"AI service returned status {resp.status}"
    except Exception as e:
        return f"AI error: {str(e)}"


def format_duration(ms: int) -> str:
    """Format milliseconds to a time string."""
    seconds = ms // 1000
    minutes, secs = divmod(seconds, 60)
    hours, mins = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


# ─── BOT INITIALIZATION ──────────────────────────────────────────────────────

async def main():
    async with bot:
        await bot.add_cog(Moderation(bot))
        await bot.add_cog(Music(bot))
        await bot.add_cog(Events(bot))
        await bot.add_cog(Fun(bot))
        await bot.add_cog(Economy(bot))
        await bot.add_cog(Giveaway(bot))
        await bot.add_cog(Ticket(bot))
        await bot.add_cog(Leveling(bot))
        await bot.add_cog(AI(bot))
        await bot.add_cog(Help(bot))
        await bot.add_cog(GroupCog(bot))
        await bot.add_cog(CommandErrorHandler(bot))
        
        await bot.tree.sync()
        print("✅ Slash commands synced")
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
