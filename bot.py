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

# =========================
# 🔥 MUSIC SYSTEM IMPORTS
# =========================
import yt_dlp
import urllib.parse
import urllib.request
import math
import functools
from discord import PCMVolumeTransformer, FFmpegPCMAudio

TOKEN = os.getenv("TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID = int(os.getenv("OWNER_ID"))

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
# 🎵 MUSIC BACKGROUND TASKS
# =========================
@tasks.loop(minutes=5)
async def clean_inactive_players():
    for guild_id, player in list(music_players.items()):
        if player.voice_client and player.voice_client.is_connected():
            channel = player.voice_client.channel
            if channel and len(channel.members) == 1 and channel.members[0].id == bot.user.id:
                if not player.is_playing() and not player.is_paused:
                    await player.disconnect_voice()
                    if guild_id in music_players:
                        del music_players[guild_id]

# =========================
# 🎵 MUSIC SYSTEM - YT-DLP CONFIGURATION
# =========================

yt_dlp.utils.bug_reports_message = lambda: ''

YTDLP_OPTIONS = {
    'format': 'bestaudio/best',
    'extractaudio': True,
    'audioformat': 'mp3',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'extract_flat': False,
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn -dn',
}

URL_REGEX = re.compile(r'https?://')


class YTDLSource(PCMVolumeTransformer):
    def __init__(self, source: discord.FFmpegPCMAudio, *, data: dict, volume: float = 0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title', 'Unknown')
        self.url = data.get('webpage_url', '')
        self.duration = data.get('duration', 0)
        self.thumbnail = data.get('thumbnail', '')
        self.channel = data.get('channel', '')
        self.channel_url = data.get('channel_url', '')
        self.uploader = data.get('uploader', '')
        self.views = data.get('view_count', 0)
        self.upload_date = data.get('upload_date', '')

    @classmethod
    async def from_url(cls, url: str, *, loop: asyncio.AbstractEventLoop = None, stream: bool = True):
        loop = loop or asyncio.get_event_loop()

        if not URL_REGEX.match(url):
            url = f'ytsearch:{url}'

        partial = functools.partial(cls._extract_info, url, stream)
        data = await loop.run_in_executor(None, partial)

        if data is None:
            raise ValueError("Could not find any matching song.")

        if 'entries' in data:
            data = data['entries'][0]

        filename = data['url'] if stream else yt_dlp.YoutubeDL(YTDLP_OPTIONS).prepare_filename(data)

        audio_source = discord.FFmpegPCMAudio(filename, **FFMPEG_OPTIONS)
        return cls(audio_source, data=data)

    @classmethod
    async def from_playlist(cls, url: str, *, loop: asyncio.AbstractEventLoop = None):
        loop = loop or asyncio.get_event_loop()

        opts = YTDLP_OPTIONS.copy()
        opts['noplaylist'] = False
        opts['extract_flat'] = True

        partial = functools.partial(cls._extract_info, url, True, opts)
        data = await loop.run_in_executor(None, partial)

        if data is None:
            raise ValueError("Could not find any matching playlist.")

        tracks = []
        playlist_title = data.get('title', 'Unknown Playlist')

        if 'entries' in data:
            for entry in data['entries']:
                if entry:
                    tracks.append({
                        'title': entry.get('title', 'Unknown'),
                        'url': entry.get('url') or entry.get('webpage_url', ''),
                        'duration': entry.get('duration', 0),
                        'thumbnail': entry.get('thumbnail', ''),
                        'channel': entry.get('channel', ''),
                        'uploader': entry.get('uploader', ''),
                    })

        return playlist_title, tracks

    @staticmethod
    def _extract_info(url: str, stream: bool, custom_opts: dict = None):
        opts = YTDLP_OPTIONS.copy()
        if custom_opts:
            opts.update(custom_opts)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                # If it's not a real URL, treat it as a YouTube search
                if not url.startswith("http"):
                    url = f"ytsearch1:{url}"

                return ydl.extract_info(url, download=not stream)

        except Exception as e:
            print(f"yt-dlp extract error: {e}")
            return None
        
    @classmethod
    async def search_results(cls, query: str, *, loop: asyncio.AbstractEventLoop = None, max_results: int = 5):
        loop = loop or asyncio.get_event_loop()
        
        search_url = f'ytsearch{max_results}:{query}'
        opts = YTDLP_OPTIONS.copy()
        opts['noplaylist'] = True
        opts['extract_flat'] = True
        
        partial = functools.partial(cls._extract_info, search_url, True, opts)
        data = await loop.run_in_executor(None, partial)
        
        results = []
        if data and 'entries' in data:
            for entry in data['entries']:
                if entry:
                    results.append({
                        'title': entry.get('title', 'Unknown'),
                        'url': f"https://www.youtube.com/watch?v={entry.get('id', '')}",
                        'duration': entry.get('duration', 0),
                        'thumbnail': entry.get('thumbnail', ''),
                        'channel': entry.get('channel', ''),
                        'uploader': entry.get('uploader', ''),
                    })
        return results


class MusicPlayer:
    def __init__(self, bot: commands.Bot, guild_id: int):
        self.bot = bot
        self.guild_id = guild_id
        self.queue = asyncio.Queue()
        self.queue_history = []
        self.current = None
        self.voice_client = None
        self.loop_mode = 'none'
        self.volume = 0.5
        self.is_paused = False
        self.now_playing_message = None
        self._play_next_lock = asyncio.Lock()
        self._task = None
    
    async def connect_voice(self, channel: discord.VoiceChannel) -> bool:
        try:
            if self.voice_client and self.voice_client.is_connected():
                if self.voice_client.channel.id != channel.id:
                    await self.voice_client.move_to(channel)
                return True
            
            self.voice_client = await channel.connect(timeout=20.0)
            return True
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Voice connect error: {repr(e)}")
            return False
    
    async def disconnect_voice(self):
        if self.voice_client and self.voice_client.is_connected():
            self.queue = asyncio.Queue()
            self.queue_history.clear()
            self.current = None
            self.is_paused = False
            self.loop_mode = 'none'
            if self._task:
                self._task.cancel()
                self._task = None
            await self.voice_client.disconnect(force=True)
            self.voice_client = None
    
    async def add_to_queue(self, track_data: dict, at_front: bool = False):
        if at_front:
            old_queue = []
            while not self.queue.empty():
                old_queue.append(await self.queue.get())
            self.queue.put_nowait(track_data)
            for item in old_queue:
                self.queue.put_nowait(item)
        else:
            await self.queue.put(track_data)
        
        if not self.is_playing() and not self.is_paused:
            if self._task is None or self._task.done():
                await self.start_playback()
    
    async def start_playback(self):
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._playback_loop())
    
    async def _playback_loop(self):
        try:
            while True:
                if self.loop_mode == 'none':
                    self.current = await self.queue.get()
                elif self.loop_mode == 'one':
                    pass
                elif self.loop_mode == 'all':
                    await self.queue.put(self.current)
                    self.current = await self.queue.get()
                
                if self.current:
                    self.queue_history.append(self.current)
                    if len(self.queue_history) > 50:
                        self.queue_history.pop(0)
                    
                    await self._play_track(self.current)
                
                while self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
                    await asyncio.sleep(1)
                
                if self.loop_mode == 'one' and self.current:
                    continue
                
                if self.queue.empty() and self.loop_mode != 'all':
                    self.current = None
                    break
        
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Playback loop error: {e}")
    
    async def _play_track(self, track_data: dict):
        if not self.voice_client or not self.voice_client.is_connected():
            return
        
        try:
            source = await YTDLSource.from_url(track_data['url'])
            source.volume = self.volume
            
            self.current = track_data
            self.is_paused = False
            
            self.voice_client.play(source, after=lambda e: print(f"Player error: {e}") if e else None)
            
            await self._update_now_playing()
        
        except Exception as e:
            print(f"Play error: {e}")
    
    async def _update_now_playing(self):
        if not self.current or not self.now_playing_message:
            return
        
        try:
            embed = self._create_now_playing_embed()
            await self.now_playing_message.edit(embed=embed)
        except Exception as e:
            pass
    
    def _create_now_playing_embed(self) -> discord.Embed:
        if not self.current:
            return discord.Embed(title="🎵 No track playing", color=discord.Color.blue())
        
        track = self.current
        duration_str = self._format_duration(track.get('duration', 0))
        
        embed = discord.Embed(
            title="🎵 Now Playing",
            description=f"[{track.get('title', 'Unknown')}]({track.get('url', '')})",
            color=discord.Color.green()
        )
        
        if track.get('thumbnail'):
            embed.set_thumbnail(url=track['thumbnail'])
        
        embed.add_field(name="Duration", value=duration_str, inline=True)
        embed.add_field(name="Uploader", value=track.get('uploader', 'Unknown'), inline=True)
        
        queue_size = self.queue.qsize()
        embed.add_field(name="Queue", value=f"{queue_size} songs" if queue_size > 0 else "Empty", inline=True)
        
        loop_icons = {'none': '➡️', 'one': '🔂', 'all': '🔁'}
        embed.set_footer(text=f"Volume: {int(self.volume * 100)}% | Loop: {loop_icons.get(self.loop_mode, '➡️')}")
        
        return embed
    
    def is_playing(self) -> bool:
        return self.voice_client is not None and self.voice_client.is_playing()
    
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
        
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except Exception as e:
                break
        
        self.queue = asyncio.Queue()
        self.queue_history.clear()
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
                old_queue.append(self.queue.get_nowait())
            self.queue.put_nowait(prev)
            for item in old_queue:
                self.queue.put_nowait(item)
            
            if self.voice_client:
                self.voice_client.stop()
            return True
        return False
    
    def shuffle(self):
        items = []
        while not self.queue.empty():
            try:
                items.append(self.queue.get_nowait())
            except Exception as e:
                break
        
        random.shuffle(items)
        
        self.queue = asyncio.Queue()
        for item in items:
            self.queue.put_nowait(item)
    
    def clear_queue(self):
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except Exception as e:
                break
        self.queue = asyncio.Queue()
    
    def remove_from_queue(self, index: int) -> Optional[dict]:
        items = []
        while not self.queue.empty():
            try:
                items.append(self.queue.get_nowait())
            except Exception as e:
                break
        
        if index < 1 or index > len(items):
            return None
        
        removed = items.pop(index - 1)
        
        self.queue = asyncio.Queue()
        for item in items:
            self.queue.put_nowait(item)
        
        return removed
    
    def get_queue_list(self) -> list:
        items = []
        while not self.queue.empty():
            try:
                items.append(self.queue.get_nowait())
            except Exception as e:
                break
        
        self.queue = asyncio.Queue()
        for item in items:
            self.queue.put_nowait(item)
        
        return items
    
    def set_volume(self, vol: float):
        self.volume = max(0.0, min(1.0, vol))
        if self.voice_client and self.voice_client.source:
            if hasattr(self.voice_client.source, 'volume'):
                self.voice_client.source.volume = self.volume
    
    @staticmethod
    def _format_duration(seconds: int) -> str:
        if not seconds:
            return "Live"
        h, remainder = divmod(seconds, 3600)
        m, s = divmod(remainder, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"


music_players: dict[int, MusicPlayer] = {}


def get_music_player(guild_id: int) -> MusicPlayer:
    if guild_id not in music_players:
        music_players[guild_id] = MusicPlayer(bot, guild_id)
    return music_players[guild_id]


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
# on_ready with duplicate task prevention
# =========================
_tasks_started = False

@bot.event
async def on_ready():
    global _tasks_started
    if not _tasks_started:
        _tasks_started = True
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

@mod_group.command(name="kick", description="Kick member")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(kick_members=True)
async def mod_kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await interaction.response.defer()
    await member.kick(reason=reason)
    add_history(interaction.guild.id, member.id, str(member), "KICK", f"Kicked by {interaction.user}: {reason}")
    await interaction.followup.send(f"👢 Kicked {member}")
    await log(interaction.guild, f"KICK | {member} | {reason}")

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
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(moderate_members=True)
async def mod_warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    await interaction.response.defer()
    add_warning(member.id, interaction.guild.id, reason, str(interaction.user))
    add_history(interaction.guild.id, member.id, str(member), "WARN", f"Warned by {interaction.user}: {reason}")
    
    try:
        embed = discord.Embed(title="⚠️ You have been warned", color=discord.Color.orange(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="Server", value=interaction.guild.name, inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text=f"Moderator: {interaction.user}")
        await member.send(embed=embed)
    except discord.Forbidden:
        pass
    
    embed = discord.Embed(title="⚠️ Warning Issued", color=discord.Color.red())
    embed.add_field(name="User", value=member.mention, inline=True)
    embed.add_field(name="Reason", value=reason, inline=True)
    embed.set_footer(text=f"By {interaction.user}")
    await interaction.followup.send(embed=embed)
    await log(interaction.guild, f"WARN | {member} | {reason}")

@mod_group.command(name="warnings", description="Show warnings for a member")
async def mod_warnings(interaction: discord.Interaction, member: discord.Member):
    warns = get_warnings(member.id, interaction.guild.id)
    if not warns:
        await interaction.response.send_message("✅ No warnings.")
        return
    
    await interaction.response.defer()
    embed = discord.Embed(title=f"⚠️ Warnings for {member.display_name}", color=discord.Color.orange())
    for warn_id, reason, timestamp in warns:
        embed.add_field(name=f"Warning #{warn_id}", value=f"Reason: {reason}\nDate: {timestamp}", inline=False)
    await interaction.followup.send(embed=embed)

@mod_group.command(name="unwarn", description="Remove a specific warning")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(moderate_members=True)
async def mod_unwarn(interaction: discord.Interaction, warning_id: int):
    remove_warning(warning_id)
    await interaction.response.send_message(f"✅ Removed warning #{warning_id}")

@mod_group.command(name="clearwarns", description="Remove all warnings from a user")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(moderate_members=True)
async def mod_clearwarns(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer()
    c.execute("DELETE FROM warnings WHERE user_id=? AND guild_id=?", (member.id, interaction.guild.id))
    conn.commit()
    add_history(interaction.guild.id, member.id, str(member), "CLEAR_WARNS", f"All warnings cleared by {interaction.user}")
    await interaction.followup.send(f"🧹 Removed all warnings for {member.mention}")
    await log(interaction.guild, f"CLEARWARNS | {member}")

@mod_group.command(name="lock", description="Lock a channel")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(manage_channels=True)
async def mod_lock(interaction: discord.Interaction):
    overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = False
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "CHANNEL_LOCK", f"Locked #{interaction.channel.name}")
    await interaction.response.send_message("🔒 Channel locked.")

@mod_group.command(name="unlock", description="Unlock a channel")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(manage_channels=True)
async def mod_unlock(interaction: discord.Interaction):
    overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = None
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "CHANNEL_UNLOCK", f"Unlocked #{interaction.channel.name}")
    await interaction.response.send_message("🔓 Channel unlocked.")

@mod_group.command(name="slowmode", description="Set slowmode delay")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(manage_channels=True)
async def mod_slowmode(interaction: discord.Interaction, seconds: int):
    await interaction.channel.edit(slowmode_delay=seconds)
    add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "SLOWMODE", f"Set slowmode to {seconds}s in #{interaction.channel.name}")
    await interaction.response.send_message(f"🐌 Slowmode set to {seconds} seconds.")

@mod_group.command(name="massban", description="Ban multiple users at once")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(ban_members=True)
async def mod_massban(interaction: discord.Interaction, members: str, reason: str = "Mass ban"):
    await interaction.response.defer()
    ids = re.findall(r'\d+', members)
    banned = 0
    for uid in ids[:10]:
        try:
            await interaction.guild.ban(discord.Object(id=int(uid)), reason=reason)
            add_history(interaction.guild.id, int(uid), f"User({uid})", "BAN", f"Mass banned by {interaction.user}: {reason}")
            banned += 1
        except Exception as e:
            pass
    await interaction.followup.send(f"🔨 Banned {banned} users")
    await log(interaction.guild, f"MASSBAN | {banned} users")

# ===== 🕰️ HISTORY COMMANDS =====
history_group = app_commands.Group(name="history", description="History and analytics commands")

@history_group.command(name="user", description="Show user history in this server")
async def history_user(interaction: discord.Interaction, member: discord.Member):
    rows = get_user_history(member.id, interaction.guild.id, 25)
    
    if not rows:
        await interaction.response.send_message(f"📭 No history found for {member.mention}.")
        return
    
    await interaction.response.defer()
    embed = discord.Embed(
        title=f"🕰️ History: {member.display_name}",
        description=f"Last 25 events for {member.mention}",
        color=discord.Color.blue()
    )
    
    for event, details, time in rows[:25]:
        emoji_map = {
            "JOIN": "👋", "LEAVE": "👋", "WARN": "⚠️", "KICK": "👢", "BAN": "🔨",
            "MUTE": "🔇", "UNMUTE": "🔊", "NICK_CHANGE": "✏️", "ROLE_ADD": "➕",
            "ROLE_REMOVE": "➖", "DELETE": "🗑️", "EDIT": "📝", "LEVEL_UP": "⬆️",
            "VOICE_JOIN": "🔊", "VOICE_LEAVE": "🔇", "SOFTBAN": "🧹",
            "TICKET_CREATE": "🎫", "GIVEAWAY_WIN": "🎉", "BIRTHDAY": "🎂",
            "AUTO_MOD": "🤖", "CLEAR_WARNS": "🧹"
        }
        emoji = emoji_map.get(event, "📌")
        embed.add_field(
            name=f"{emoji} {event}",
            value=f"{details[:100]}{'...' if len(details) > 100 else ''}\n{time}",
            inline=False
        )
    
    await interaction.followup.send(embed=embed)

@history_group.command(name="server", description="Show server-wide analytics and summary")
async def history_server(interaction: discord.Interaction, days: int = 7):
    await interaction.response.defer()
    busiest = get_busiest_day(interaction.guild.id)
    
    most_active = get_most_active_user(interaction.guild.id, days)
    most_active_name = "No data"
    if most_active:
        user = interaction.guild.get_member(most_active[0])
        if user:
            most_active_name = user.display_name
    
    top_channels = get_top_channels(interaction.guild.id, days)
    channel_text = "\n".join([
        f"<#{cid}>: {count:,} msgs"
        for cid, count in top_channels
    ]) or "No data"
    
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    joins = count_events_for_date(interaction.guild.id, datetime.now().strftime("%Y-%m-%d"), "JOIN")
    leaves = count_events_for_date(interaction.guild.id, datetime.now().strftime("%Y-%m-%d"), "LEAVE")
    warns = count_events_for_date(interaction.guild.id, datetime.now().strftime("%Y-%m-%d"), "WARN")
    
    c.execute("""
    SELECT summary FROM daily_summaries
    WHERE guild_id=? AND date=?
    ORDER BY id DESC LIMIT 1
    """, (interaction.guild.id, yesterday))
    summary_row = c.fetchone()
    
    embed = discord.Embed(
        title=f"📊 {interaction.guild.name} Analytics",
        description=f"Last {days} days summary",
        color=discord.Color.gold()
    )
    
    if busiest:
        embed.add_field(name="🚀 Busiest Day Ever", value=f"{busiest[0]} ({busiest[1]:,} msgs)", inline=False)
    
    embed.add_field(name="🏆 Most Active User", value=most_active_name, inline=True)
    embed.add_field(name="📊 Today's Activity", value=f"👋 {joins} joins | 👋 {leaves} leaves | ⚠️ {warns} warns", inline=False)
    embed.add_field(name="📈 Top Channels", value=channel_text, inline=False)
    
    if summary_row:
        embed.add_field(name="📋 Yesterday's Summary", value=summary_row[0][:500], inline=False)
    
    await interaction.followup.send(embed=embed)

@history_group.command(name="rewind", description="🕰️ See what happened on a specific date")
async def history_rewind(interaction: discord.Interaction, date: str):
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        await interaction.response.send_message("❌ Invalid date format. Use YYYY-MM-DD (e.g., 2026-06-10)")
        return
    
    await interaction.response.defer()
    
    events = get_guild_history_by_date(interaction.guild.id, date, 50)
    
    if not events:
        await interaction.followup.send(f"📭 Nothing recorded for {date}.")
        return
    
    event_counts = {}
    for event, _, _, _, _ in events:
        event_counts[event] = event_counts.get(event, 0) + 1
    
    c.execute("""
    SELECT SUM(count) FROM message_stats
    WHERE guild_id=? AND date=?
    """, (interaction.guild.id, date))
    total_msgs = c.fetchone()[0] or 0
    
    c.execute("""
    SELECT user_id, SUM(count) as total
    FROM message_stats
    WHERE guild_id=? AND date=?
    GROUP BY user_id
    ORDER BY total DESC
    LIMIT 1
    """, (interaction.guild.id, date))
    top_user_row = c.fetchone()
    top_user_name = "Unknown"
    if top_user_row:
        user = interaction.guild.get_member(top_user_row[0])
        if user:
            top_user_name = user.display_name
        else:
            top_user_name = f"User({top_user_row[0]})"
    
    embed = discord.Embed(
        title=f"🕰️ Time Machine: {date}",
        description=f"What happened on this day in {interaction.guild.name}",
        color=discord.Color.dark_gold()
    )
    
    summary_lines = []
    if total_msgs > 0:
        summary_lines.append(f"📝 **{total_msgs:,}** messages sent")
    if event_counts.get("JOIN", 0) > 0:
        summary_lines.append(f"👋 **{event_counts['JOIN']}** members joined")
    if event_counts.get("LEAVE", 0) > 0:
        summary_lines.append(f"👋 **{event_counts['LEAVE']}** members left")
    if event_counts.get("WARN", 0) > 0:
        summary_lines.append(f"⚠️ **{event_counts['WARN']}** warnings issued")
    if event_counts.get("KICK", 0) > 0:
        summary_lines.append(f"👢 **{event_counts['KICK']}** kicks")
    if event_counts.get("BAN", 0) > 0:
        summary_lines.append(f"🔨 **{event_counts['BAN']}** bans")
    if event_counts.get("CHANNEL_CREATE", 0) > 0:
        summary_lines.append(f"📁 **{event_counts['CHANNEL_CREATE']}** channels created")
    if event_counts.get("CHANNEL_DELETE", 0) > 0:
        summary_lines.append(f"🗑️ **{event_counts['CHANNEL_DELETE']}** channels deleted")
    if event_counts.get("MUTE", 0) > 0:
        summary_lines.append(f"🔇 **{event_counts['MUTE']}** mutes")
    if event_counts.get("LEVEL_UP", 0) > 0:
        summary_lines.append(f"⬆️ **{event_counts['LEVEL_UP']}** level ups")
    if top_user_row and total_msgs > 0:
        summary_lines.append(f"🏆 **Most active:** {top_user_name} ({top_user_row[1]:,} msgs)")
    
    embed.add_field(name="📊 Summary", value="\n".join(summary_lines) or "No significant events", inline=False)
    
    timeline = []
    for event, details, time, username, user_id in events[:20]:
        emoji_map = {
            "JOIN": "👋", "LEAVE": "👋", "WARN": "⚠️", "KICK": "👢", "BAN": "🔨",
            "MUTE": "🔇", "UNMUTE": "🔊", "NICK_CHANGE": "✏️", "ROLE_ADD": "➕",
            "ROLE_REMOVE": "➖", "DELETE": "🗑️", "EDIT": "📝", "LEVEL_UP": "⬆️",
            "VOICE_JOIN": "🔊", "VOICE_LEAVE": "🔇", "SOFTBAN": "🧹",
            "CHANNEL_CREATE": "📁", "CHANNEL_DELETE": "🗑️", "CHANNEL_LOCK": "🔒",
            "CHANNEL_UNLOCK": "🔓", "SLOWMODE": "🐌"
        }
        emoji = emoji_map.get(event, "📌")
        time_only = time.split(" ")[1][:5] if " " in time else time
        timeline.append(f"**{time_only}** {emoji} **{username}**: {details[:80]}")
    
    if timeline:
        embed.add_field(name="📋 Event Timeline", value="\n".join(timeline[:15]), inline=False)
    
    embed.set_footer(text=f"Total events: {len(events)}")
    
    await interaction.followup.send(embed=embed)

# ===== FUN COMMANDS =====
fun_group = app_commands.Group(name="fun", description="Fun commands")

@fun_group.command(name="guess", description="Guess a number between 1 and 10")
async def fun_guess(interaction: discord.Interaction, number: int):
    secret = random.randint(1, 10)
    if number == secret:
        await interaction.response.send_message(f"🎉 You won! I picked {secret}")
    else:
        await interaction.response.send_message(f"❌ Wrong! I picked {secret}")

@fun_group.command(name="coinflip", description="Flip a coin")
async def fun_coinflip(interaction: discord.Interaction):
    result = random.choice(["Heads", "Tails"])
    embed = discord.Embed(title="🪙 Coin Flip", description=f"**{result}**", color=discord.Color.gold())
    await interaction.response.send_message(embed=embed)

@fun_group.command(name="dice", description="Roll a dice")
async def fun_dice(interaction: discord.Interaction, sides: int = 6):
    result = random.randint(1, sides)
    await interaction.response.send_message(f"🎲 You rolled a **{result}** (1-{sides})")

@fun_group.command(name="meme", description="Get a random meme")
async def fun_meme(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://meme-api.com/gimme") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    embed = discord.Embed(title=data.get("title", "Meme"), color=discord.Color.random())
                    embed.set_image(url=data["url"])
                    embed.set_footer(text=f"👍 {data.get('ups', 0)} | r/{data.get('subreddit', 'unknown')}")
                    await interaction.followup.send(embed=embed)
                else:
                    await interaction.followup.send("❌ Couldn't fetch a meme right now.")
    except Exception as e:
        await interaction.followup.send("❌ Meme API unavailable.")

# ===== UTILITY COMMANDS =====
@bot.tree.command(name="avatar", description="Show user avatar")
async def avatar(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    embed = discord.Embed(title=f"{member.name}'s Avatar", color=discord.Color.blue())
    embed.set_image(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="banner", description="Show user banner")
async def banner(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    user = await bot.fetch_user(member.id)
    if not user.banner:
        await interaction.response.send_message("❌ No banner found.")
        return
    embed = discord.Embed(title=f"{member.name}'s Banner", color=discord.Color.purple())
    embed.set_image(url=user.banner.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="userinfo", description="Get user information")
async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    roles = [role.mention for role in member.roles if role != interaction.guild.default_role]
    
    await interaction.response.defer()
    embed = discord.Embed(title=f"User Info - {member.display_name}", color=member.color, timestamp=datetime.now())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID", value=member.id, inline=True)
    embed.add_field(name="Joined", value=member.joined_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name="Registered", value=member.created_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name=f"Roles [{len(roles)}]", value=" ".join(roles[:10]) or "None", inline=False)
    embed.add_field(name="Top Role", value=member.top_role.mention, inline=True)
    embed.add_field(name="Bot?", value="Yes" if member.bot else "No", inline=True)
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="serverinfo", description="Get server information")
async def serverinfo(interaction: discord.Interaction):
    guild = interaction.guild
    embed = discord.Embed(title=f"Server Info - {guild.name}", color=discord.Color.gold())
    embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
    embed.add_field(name="ID", value=guild.id, inline=True)
    embed.add_field(name="Owner", value=guild.owner.mention, inline=True)
    embed.add_field(name="Members", value=guild.member_count, inline=True)
    embed.add_field(name="Channels", value=len(guild.channels), inline=True)
    embed.add_field(name="Roles", value=len(guild.roles), inline=True)
    embed.add_field(name="Boosts", value=guild.premium_subscription_count, inline=True)
    embed.add_field(name="Created", value=guild.created_at.strftime("%Y-%m-%d"), inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="poll", description="Create a poll")
@app_commands.check(owner_check)
async def create_poll(interaction: discord.Interaction, question: str, option1: str, option2: str, option3: str = None, option4: str = None, option5: str = None):
    options = [opt for opt in [option1, option2, option3, option4, option5] if opt]
    
    c.execute("INSERT INTO polls (guild_id, channel_id, question, options, votes) VALUES (?, ?, ?, ?, ?)",
              (interaction.guild.id, interaction.channel.id, question, json.dumps(options), json.dumps({i: [] for i in range(len(options))})))
    poll_id = c.lastrowid
    conn.commit()
    
    embed = discord.Embed(title="📊 Poll", description=question, color=discord.Color.blue())
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for i, option in enumerate(options):
        embed.add_field(name=f"{emojis[i]} Option {i+1}", value=option, inline=False)
    
    view = PollView(poll_id, options)
    await interaction.response.send_message(embed=embed, view=view)

# ===== TICKET COMMANDS =====
ticket_group = app_commands.Group(name="ticket", description="Ticket system commands")

@ticket_group.command(name="setup", description="Setup ticket system in current channel")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(administrator=True)
async def ticket_setup(interaction: discord.Interaction):
    embed = discord.Embed(title="🎫 Support Tickets", description="Click below to create a ticket", color=discord.Color.green())
    await interaction.channel.send(embed=embed, view=TicketView())
    add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "TICKET_SETUP", "Ticket system configured")
    await interaction.response.send_message("✅ Ticket system setup complete!", ephemeral=True)

@ticket_group.command(name="add", description="Add user to current ticket")
@app_commands.check(owner_check)
async def ticket_add(interaction: discord.Interaction, member: discord.Member):
    c.execute("SELECT * FROM tickets WHERE channel_id=? AND status='open'", (interaction.channel.id,))
    ticket = c.fetchone()
    if not ticket:
        await interaction.response.send_message("❌ This isn't an open ticket channel.")
        return
    await interaction.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
    await interaction.response.send_message(f"✅ Added {member.mention} to ticket")

@ticket_group.command(name="remove", description="Remove user from current ticket")
@app_commands.check(owner_check)
async def ticket_remove(interaction: discord.Interaction, member: discord.Member):
    c.execute("SELECT * FROM tickets WHERE channel_id=? AND status='open'", (interaction.channel.id,))
    ticket = c.fetchone()
    if not ticket:
        await interaction.response.send_message("❌ This isn't an open ticket channel.")
        return
    if member.id == ticket[2]:
        await interaction.response.send_message("❌ Cannot remove the ticket creator.")
        return
    await interaction.channel.set_permissions(member, overwrite=None)
    await interaction.response.send_message(f"✅ Removed {member.mention} from ticket")

# ===== LEVELING COMMANDS =====
level_group = app_commands.Group(name="level", description="Leveling system commands")

@level_group.command(name="rank", description="Check your rank or another user's")
async def level_rank(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    c.execute("SELECT xp, level FROM leveling WHERE user_id=? AND guild_id=?", (member.id, interaction.guild.id))
    result = c.fetchone()
    
    if not result:
        await interaction.response.send_message(f"{member.mention} has no XP yet. Start chatting!")
        return
    
    xp, level = result
    xp_needed = level * 100
    
    c.execute("SELECT user_id, level, xp FROM leveling WHERE guild_id=? ORDER BY level DESC, xp DESC", (interaction.guild.id,))
    all_users = c.fetchall()
    rank_pos = 1
    for uid, l, x in all_users:
        if uid == member.id:
            break
        rank_pos += 1
    
    embed = discord.Embed(title=f"📊 {member.display_name}'s Rank", color=discord.Color.gold())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Level", value=level, inline=True)
    embed.add_field(name="XP", value=f"{xp}/{xp_needed}", inline=True)
    embed.add_field(name="Rank", value=f"#{rank_pos}/{len(all_users)}", inline=True)
    
    progress = int((xp / xp_needed) * 10)
    bar = "🟩" * progress + "⬜" * (10 - progress)
    embed.add_field(name="Progress", value=bar, inline=False)
    
    await interaction.response.send_message(embed=embed)

@level_group.command(name="leaderboard", description="Show server level leaderboard")
async def level_leaderboard(interaction: discord.Interaction):
    c.execute("SELECT user_id, level, xp FROM leveling WHERE guild_id=? ORDER BY level DESC, xp DESC LIMIT 10", (interaction.guild.id,))
    top_users = c.fetchall()
    
    if not top_users:
        await interaction.response.send_message("No one has XP yet!")
        return
    
    embed = discord.Embed(title=f"🏆 {interaction.guild.name} Leaderboard", color=discord.Color.gold())
    
    medals = ["🥇", "🥈", "🥉"]
    for i, (user_id, level, xp) in enumerate(top_users):
        user = interaction.guild.get_member(user_id)
        name = user.display_name if user else f"Unknown ({user_id})"
        medal = medals[i] if i < 3 else f"#{i+1}"
        embed.add_field(name=f"{medal} {name}", value=f"Level {level} | XP: {xp}", inline=False)
    
    await interaction.response.send_message(embed=embed)

# ===== ECONOMY COMMANDS =====
eco_group = app_commands.Group(name="economy", description="Economy system commands")

@eco_group.command(name="balance", description="Check your balance")
async def eco_balance(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    bal = get_balance(member.id, interaction.guild.id)
    
    embed = discord.Embed(title=f"💰 {member.display_name}'s Balance", color=discord.Color.green())
    embed.add_field(name="Wallet", value=f"${bal['wallet']:,}", inline=True)
    embed.add_field(name="Bank", value=f"${bal['bank']:,}", inline=True)
    embed.add_field(name="Total", value=f"${bal['wallet'] + bal['bank']:,}", inline=True)
    await interaction.response.send_message(embed=embed)

@eco_group.command(name="daily", description="Claim daily reward")
async def eco_daily(interaction: discord.Interaction):
    user_id = interaction.user.id
    guild_id = interaction.guild.id
    
    c.execute("SELECT daily_streak, last_daily FROM economy WHERE user_id=? AND guild_id=?", (user_id, guild_id))
    result = c.fetchone()
    
    if result:
        streak, last_daily_str = result
        if last_daily_str:
            last_daily = datetime.fromisoformat(last_daily_str)
            if datetime.now().date() == last_daily.date():
                await interaction.response.send_message("❌ You already claimed your daily today!")
                return
            elif (datetime.now() - last_daily).days == 1:
                streak += 1
            else:
                streak = 0
        else:
            streak = 0
    else:
        c.execute("INSERT INTO economy (user_id, guild_id, balance, bank, daily_streak) VALUES (?, ?, 0, 0, 0)", (user_id, guild_id))
        conn.commit()
        streak = 0
    
    base_reward = 100
    streak_bonus = min(streak * 10, 100)
    reward = base_reward + streak_bonus
    
    update_balance(user_id, guild_id, reward, 'wallet')
    
    c.execute("UPDATE economy SET daily_streak=?, last_daily=? WHERE user_id=? AND guild_id=?", 
              (streak + 1, datetime.now().isoformat(), user_id, guild_id))
    conn.commit()
    
    embed = discord.Embed(title="🎁 Daily Reward", color=discord.Color.green())
    embed.add_field(name="Reward", value=f"${reward:,}", inline=True)
    embed.add_field(name="Streak", value=f"{streak + 1} days", inline=True)
    embed.add_field(name="Streak Bonus", value=f"+${streak_bonus}", inline=True)
    await interaction.response.send_message(embed=embed)

@eco_group.command(name="give", description="Give money to another user")
async def eco_give(interaction: discord.Interaction, member: discord.Member, amount: int):
    if amount <= 0:
        await interaction.response.send_message("❌ Amount must be positive!")
        return
    
    bal = get_balance(interaction.user.id, interaction.guild.id)
    if bal['wallet'] < amount:
        await interaction.response.send_message("❌ You don't have enough money!")
        return
    
    update_balance(interaction.user.id, interaction.guild.id, -amount, 'wallet')
    update_balance(member.id, interaction.guild.id, amount, 'wallet')
    
    add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "ECONOMY_GIVE", f"Gave ${amount} to {member}")
    
    await interaction.response.send_message(f"✅ Gave ${amount:,} to {member.mention}")

@eco_group.command(name="slot", description="Play the slot machine")
async def eco_slot(interaction: discord.Interaction, bet: int):
    if bet <= 0:
        await interaction.response.send_message("❌ Bet must be positive!")
        return
    
    bal = get_balance(interaction.user.id, interaction.guild.id)
    if bal['wallet'] < bet:
        await interaction.response.send_message("❌ You don't have enough money!")
        return
    
    slots = ["🍒", "🍋", "🍊", "🍇", "💎", "7️⃣", "🎰"]
    result = [random.choice(slots) for _ in range(3)]
    
    if result[0] == result[1] == result[2]:
        multiplier = random.choice([5, 10, 20])
        winnings = bet * multiplier
    elif result[0] == result[1] or result[1] == result[2] or result[0] == result[2]:
        multiplier = 2
        winnings = bet * multiplier
    else:
        winnings = -bet
    
    update_balance(interaction.user.id, interaction.guild.id, winnings, 'wallet')
    
    embed = discord.Embed(title="🎰 Slots", color=discord.Color.gold() if winnings > 0 else discord.Color.red())
    embed.add_field(name="Result", value=f"{'  '.join(result)}", inline=False)
    embed.add_field(name="Bet", value=f"${bet:,}", inline=True)
    if winnings > 0:
        embed.add_field(name="Won", value=f"+${winnings:,}", inline=True)
    else:
        embed.add_field(name="Lost", value=f"-${bet:,}", inline=True)
    embed.add_field(name="Balance", value=f"${get_balance(interaction.user.id, interaction.guild.id)['wallet']:,}", inline=True)
    
    await interaction.response.send_message(embed=embed)

# ===== GIVEAWAY COMMANDS =====
giveaway_group = app_commands.Group(name="giveaway", description="Giveaway commands")

@giveaway_group.command(name="start", description="Start a giveaway")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(administrator=True)
async def giveaway_start(interaction: discord.Interaction, prize: str, duration_minutes: int, winners: int = 1, channel: discord.TextChannel = None):
    channel = channel or interaction.channel
    end_time = datetime.now() + timedelta(minutes=duration_minutes)
    
    embed = discord.Embed(title="🎉 Giveaway", description=f"**Prize:** {prize}", color=discord.Color.gold())
    embed.add_field(name="Ends", value=f"<t:{int(end_time.timestamp())}:R>", inline=True)
    embed.add_field(name="Winners", value=winners, inline=True)
    embed.add_field(name="Hosted by", value=interaction.user.mention, inline=True)
    embed.set_footer(text="Click the button to enter!")
    
    msg = await channel.send(embed=embed)
    await msg.add_reaction("🎉")
    
    c.execute("INSERT INTO giveaways (guild_id, channel_id, prize, winner_count, end_time, host_id, message_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (interaction.guild.id, channel.id, prize, winners, end_time.isoformat(), interaction.user.id, msg.id))
    conn.commit()
    
    add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "GIVEAWAY_START", f"Giveaway: {prize} ({winners} winner(s))")
    
    await interaction.response.send_message(f"✅ Giveaway started in {channel.mention}!", ephemeral=True)

@giveaway_group.command(name="reroll", description="Reroll a giveaway winner")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(administrator=True)
async def giveaway_reroll(interaction: discord.Interaction, message_id: str):
    try:
        msg = await interaction.channel.fetch_message(int(message_id))
        if not msg:
            await interaction.response.send_message("❌ Message not found.")
            return
        
        reactions = msg.reactions
        users = []
        for reaction in reactions:
            async for user in reaction.users():
                if user != bot.user:
                    users.append(user)
        
        if users:
            winner = random.choice(users)
            await interaction.response.send_message(f"🎉 New winner: {winner.mention}!")
            add_history(interaction.guild.id, winner.id, str(winner), "GIVEAWAY_WIN", f"Rerolled win")
        else:
            await interaction.response.send_message("❌ No one entered.")
    except Exception as e:
        await interaction.response.send_message("❌ Invalid message ID.")

# ===== OTHER COMMANDS (standalone, not in groups to save slots) =====

@bot.tree.command(name="nick", description="Change nickname")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(manage_nicknames=True)
async def nick(interaction: discord.Interaction, member: discord.Member, nickname: str):
    old = member.display_name
    await member.edit(nick=nickname)
    add_history(interaction.guild.id, member.id, str(member), "NICK_CHANGE", f"{old} -> {nickname} (by {interaction.user})")
    await interaction.response.send_message(f"✏️ Changed {old}'s nickname to {nickname}")

@bot.tree.command(name="giverole", description="Give role to member")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(manage_roles=True)
async def giverole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    await interaction.response.defer()
    await member.add_roles(role)
    add_history(interaction.guild.id, member.id, str(member), "ROLE_ADD", f"Role added: {role.name} (by {interaction.user})")
    await interaction.followup.send(f"✅ Gave {role.name} to {member.mention}")

@bot.tree.command(name="removerole", description="Remove role from member")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(manage_roles=True)
async def removerole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    await interaction.response.defer()
    await member.remove_roles(role)
    add_history(interaction.guild.id, member.id, str(member), "ROLE_REMOVE", f"Role removed: {role.name} (by {interaction.user})")
    await interaction.followup.send(f"❌ Removed {role.name} from {member.mention}")

@bot.tree.command(name="setlevel", description="Set a user's level (Admin)")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(administrator=True)
async def set_level(interaction: discord.Interaction, member: discord.Member, level: int):
    c.execute("UPDATE leveling SET level=? WHERE user_id=? AND guild_id=?", (level, member.id, interaction.guild.id))
    if c.rowcount == 0:
        c.execute("INSERT INTO leveling (user_id, guild_id, xp, level) VALUES (?, ?, 0, ?)", (member.id, interaction.guild.id, level))
    conn.commit()
    add_history(interaction.guild.id, member.id, str(member), "LEVEL_SET", f"Level set to {level} by {interaction.user}")
    await interaction.response.send_message(f"✅ Set {member.mention}'s level to {level}")

@bot.tree.command(name="reactionrole", description="Setup reaction role message")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(administrator=True)
async def reaction_role(interaction: discord.Interaction, channel: discord.TextChannel, message_id: str, emoji: str, role: discord.Role):
    try:
        msg_id = int(message_id)
        c.execute("INSERT INTO reaction_roles (guild_id, channel_id, message_id, emoji, role_id) VALUES (?, ?, ?, ?, ?)",
                  (interaction.guild.id, channel.id, msg_id, emoji, role.id))
        conn.commit()
        add_history(interaction.guild.id, role.id, str(role), "REACTION_ROLE", f"Setup: {emoji} -> @{role.name}")
        await interaction.response.send_message(f"✅ Reaction role set: {emoji} -> {role.name}")
    except ValueError:
        await interaction.response.send_message("❌ Invalid message ID.")

@bot.tree.command(name="setbirthday", description="Set your birthday (MM-DD)")
async def set_birthday(interaction: discord.Interaction, date: str, year: int = None):
    if not re.match(r"^\d{2}-\d{2}$", date):
        await interaction.response.send_message("❌ Invalid date format. Use MM-DD (e.g., 06-15)")
        return
    
    c.execute("INSERT OR REPLACE INTO birthdays (user_id, guild_id, date, year) VALUES (?, ?, ?, ?)",
              (interaction.user.id, interaction.guild.id, date, year or 0))
    conn.commit()
    add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "BIRTHDAY_SET", f"Birthday set to {date}")
    await interaction.response.send_message(f"✅ Birthday set to {date}!")

@bot.tree.command(name="setwelcome", description="Set welcome channel and message")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(administrator=True)
async def set_welcome(interaction: discord.Interaction, channel: discord.TextChannel, message: str = None):
    welcome_configs[interaction.guild.id] = {
        'channel_id': channel.id,
        'message': message or "Welcome {user} to {server}!"
    }
    add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "WELCOME_SETUP", f"Welcome channel set to #{channel.name}")
    await interaction.response.send_message(f"✅ Welcome messages will be sent to {channel.mention}")

@bot.tree.command(name="addcommand", description="Add a custom command")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(administrator=True)
async def add_command(interaction: discord.Interaction, name: str, response: str):
    c.execute("INSERT OR REPLACE INTO custom_commands (guild_id, name, response) VALUES (?, ?, ?)",
              (interaction.guild.id, name.lower(), response))
    conn.commit()
    add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "CUSTOM_CMD_ADD", f"Added command ?{name}")
    await interaction.response.send_message(f"✅ Added command `?{name}`")

@bot.tree.command(name="removecommand", description="Remove a custom command")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(administrator=True)
async def remove_command(interaction: discord.Interaction, name: str):
    c.execute("DELETE FROM custom_commands WHERE guild_id=? AND name=?", (interaction.guild.id, name.lower()))
    conn.commit()
    if c.rowcount:
        add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "CUSTOM_CMD_REMOVE", f"Removed command ?{name}")
        await interaction.response.send_message(f"✅ Removed command `?{name}`")
    else:
        await interaction.response.send_message("❌ Command not found.")

@bot.tree.command(name="commands", description="List all custom commands")
async def list_commands(interaction: discord.Interaction):
    c.execute("SELECT name, response FROM custom_commands WHERE guild_id=?", (interaction.guild.id,))
    cmds = c.fetchall()
    if not cmds:
        await interaction.response.send_message("No custom commands setup. Use `/addcommand` to create one.")
        return
    
    embed = discord.Embed(title=f"📋 Custom Commands for {interaction.guild.name}", color=discord.Color.blue())
    for name, response in cmds:
        embed.add_field(name=f"?{name}", value=response[:100] + ("..." if len(response) > 100 else ""), inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="voicekick", description="Disconnect a user from voice channel")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(move_members=True)
async def voice_kick(interaction: discord.Interaction, member: discord.Member):
    if member.voice and member.voice.channel:
        await member.move_to(None)
        add_history(interaction.guild.id, member.id, str(member), "VOICE_KICK", f"Disconnected from voice by {interaction.user}")
        await interaction.response.send_message(f"🔇 Disconnected {member.mention} from voice")
        await log(interaction.guild, f"VOICEKICK | {member}")
    else:
        await interaction.response.send_message("❌ User is not in a voice channel.")

@bot.tree.command(name="voicemove", description="Move a user to another voice channel")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(move_members=True)
async def voice_move(interaction: discord.Interaction, member: discord.Member, channel: discord.VoiceChannel):
    if member.voice:
        await member.move_to(channel)
        add_history(interaction.guild.id, member.id, str(member), "VOICE_MOVE", f"Moved to {channel.name} by {interaction.user}")
        await interaction.response.send_message(f"🔊 Moved {member.mention} to {channel.name}")
    else:
        await interaction.response.send_message("❌ User is not in a voice channel.")

@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    embed = discord.Embed(title="🏓 Pong!", color=discord.Color.green())
    embed.add_field(name="Latency", value=f"{round(bot.latency * 1000)}ms", inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="uptime", description="Check bot uptime")
async def uptime(interaction: discord.Interaction):
    uptime_delta = datetime.now() - START_TIME
    days = uptime_delta.days
    hours, remainder = divmod(uptime_delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    await interaction.response.send_message(f"⏱️ Uptime: {days}d {hours}h {minutes}m {seconds}s")

@bot.tree.command(name="say", description="Make the bot say something")
@app_commands.check(owner_check)
async def say(interaction: discord.Interaction, message: str, channel: discord.TextChannel = None):
    channel = channel or interaction.channel
    await channel.send(message)
    await interaction.response.send_message("✅ Message sent!", ephemeral=True)

@bot.tree.command(name="embed", description="Send an embed message")
@app_commands.check(owner_check)
async def send_embed(interaction: discord.Interaction, title: str, description: str, color: str = "blue", channel: discord.TextChannel = None):
    colors = {
        "red": discord.Color.red(), "blue": discord.Color.blue(), "green": discord.Color.green(),
        "gold": discord.Color.gold(), "purple": discord.Color.purple(), "orange": discord.Color.orange(),
        "random": discord.Color.random()
    }
    embed = discord.Embed(title=title, description=description, color=colors.get(color.lower(), discord.Color.blue()))
    channel = channel or interaction.channel
    await channel.send(embed=embed)
    await interaction.response.send_message("✅ Embed sent!", ephemeral=True)

# ===== PLAYLIST COMMANDS =====
@bot.tree.command(name="addsong", description="Add a song to the server playlist")
async def add_song(interaction: discord.Interaction, name: str, url: str):
    c.execute("INSERT INTO playlists (guild_id, name, url, added_by) VALUES (?, ?, ?, ?)",
              (interaction.guild.id, name, url, interaction.user.id))
    conn.commit()
    add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "PLAYLIST_ADD", f"Added song: {name}")
    await interaction.response.send_message(f"✅ Added **{name}** to playlist!")

@bot.tree.command(name="playlist", description="Show server playlist")
async def show_playlist(interaction: discord.Interaction):
    c.execute("SELECT name, url, added_by FROM playlists WHERE guild_id=?", (interaction.guild.id,))
    songs = c.fetchall()
    if not songs:
        await interaction.response.send_message("No songs in playlist. Use `/addsong` to add one.")
        return
    
    embed = discord.Embed(title=f"🎵 {interaction.guild.name} Playlist", color=discord.Color.blue())
    for i, (name, url, added_by) in enumerate(songs[:15], 1):
        member = interaction.guild.get_member(added_by)
        embed.add_field(name=f"{i}. {name}", value=f"[Link]({url}) | Added by {member.display_name if member else 'Unknown'}", inline=False)
    await interaction.response.send_message(embed=embed)

# =========================
# AI Chat Command
# =========================
@bot.tree.command(name="ai", description="🤖 Chat with the AI — remembers you across sessions!")
async def ai_chat(interaction: discord.Interaction, message: str):
    await interaction.response.defer()
    
    guild_id = interaction.guild.id
    channel_id = interaction.channel.id
    user_id = interaction.user.id
    
    save_conversation(guild_id, channel_id, user_id, "user", message)
    
    context = await build_ai_context(guild_id, channel_id, user_id, interaction.user.display_name, message)
    
    guild_mems = get_all_guild_memories(guild_id, limit=10)
    guild_memory_text = ""
    if guild_mems:
        lines = []
        for uid, key, val, _ in guild_mems[:5]:
            user_obj = interaction.guild.get_member(uid)
            name = user_obj.display_name if user_obj else f"User({uid})"
            lines.append(f"- {name}'s {key}: {val}")
        if lines:
            guild_memory_text = "What I know about server members:\n" + "\n".join(lines) + "\n"
    
    channel_context = get_recent_conversation(guild_id, channel_id, limit=8)
    channel_text = ""
    if channel_context:
        lines = []
        for role, content, ts, uid in channel_context:
            if role == "user":
                user_obj = interaction.guild.get_member(uid)
                name = user_obj.display_name if user_obj else f"User({uid})"
                lines.append(f"{name}: {content[:200]}")
            else:
                lines.append(f"Bot: {content[:200]}")
        channel_text = "Recent channel discussion:\n" + "\n".join(lines) + "\n"
    
    prompt = f"""You are a friendly AI assistant in a Discord server. You have memory of users.

{context}

{guild_memory_text}
{channel_text}

{interaction.user.display_name}: {message}

Respond naturally, conversationally, and keep it concise (under 300 words). Reference things you remember about the user when relevant."""
    
    reply = await get_ai_response(prompt, temperature=0.8)
    
    save_conversation(guild_id, channel_id, user_id, "assistant", reply)
    
    embed = discord.Embed(
        description=reply,
        color=discord.Color.purple()
    )
    embed.set_footer(text=f"🧠 I remember you, {interaction.user.display_name}!")
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="whatiknow", description="🧠 See what the AI has learned about you")
async def what_i_know(interaction: discord.Interaction):
    memories = get_ai_memories(interaction.guild.id, interaction.user.id)
    traits = get_user_personality(interaction.guild.id, interaction.user.id)
    
    if not memories and not traits:
        await interaction.response.send_message("🤖 I don't know much about you yet! Chat with me using `/ai` and I'll learn about you naturally.")
        return
    
    embed = discord.Embed(
        title=f"🧠 What I Know About {interaction.user.display_name}",
        description="Things I've learned from our conversations",
        color=discord.Color.purple()
    )
    
    if memories:
        memory_text = ""
        for key, value, importance, created, accessed in memories:
            icon = "⭐" if importance >= 4 else "📌" if importance >= 2 else "🔹"
            memory_text += f"{icon} **{key.replace('_', ' ').title()}**: {value}\n"
        embed.add_field(name="📝 Memories", value=memory_text, inline=False)
    
    if traits:
        trait_text = "\n".join([f"• **{t.replace('_', ' ').title()}**: {v}" for t, v in traits])
        embed.add_field(name="🎭 Personality", value=trait_text, inline=False)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="forgetme", description="🧹 Make the AI forget everything about you")
async def forget_me(interaction: discord.Interaction):
    c.execute("DELETE FROM ai_memories WHERE guild_id=? AND user_id=?", (interaction.guild.id, interaction.user.id))
    c.execute("DELETE FROM ai_personality WHERE guild_id=? AND user_id=?", (interaction.guild.id, interaction.user.id))
    c.execute("DELETE FROM ai_conversations WHERE guild_id=? AND user_id=?", (interaction.guild.id, interaction.user.id))
    conn.commit()
    await interaction.response.send_message("🧹 Done! I've forgotten everything about you. Let's start fresh whenever you're ready.")

# =========================
# 🎵 MUSIC SYSTEM - SLASH COMMANDS
# =========================

@bot.tree.command(name="play", description="🎵 Play a song by name or URL (YouTube, Spotify, SoundCloud)")
async def play_music(interaction: discord.Interaction, query: str):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ You must be in a voice channel first!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    player = get_music_player(interaction.guild.id)
    
    connected = await player.connect_voice(interaction.user.voice.channel)
    if not connected:
        await interaction.followup.send("❌ Could not connect to voice channel.", ephemeral=True)
        return
    
    try:
        is_url = bool(URL_REGEX.match(query))
        
        if is_url and ('playlist' in query.lower() or '&list=' in query.lower()):
            playlist_title, tracks = await YTDLSource.from_playlist(query)
            
            if not tracks:
                await interaction.followup.send("❌ Could not find any tracks in that playlist.")
                return
            
            for track in tracks:
                await player.add_to_queue(track)
            
            embed = discord.Embed(
                title="📋 Playlist Added",
                description=f"**{playlist_title}**",
                color=discord.Color.green()
            )
            embed.add_field(name="Tracks", value=f"{len(tracks)} songs added to queue", inline=False)
            
            if player.current:
                embed.add_field(name="Now Playing", value=f"[{player.current.get('title', 'Unknown')}]({player.current.get('url', '')})", inline=False)
            
            await interaction.followup.send(embed=embed)
            add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "MUSIC_PLAYLIST", f"Added playlist: {playlist_title} ({len(tracks)} tracks)")
            return
        
        source = await YTDLSource.from_url(query)
        
        track_data = {
            'title': source.title,
            'url': source.url,
            'duration': source.duration,
            'thumbnail': source.thumbnail,
            'channel': source.channel,
            'channel_url': source.channel_url,
            'uploader': source.uploader,
            'views': source.views,
        }
        
        await player.add_to_queue(track_data)
        
        duration_str = MusicPlayer._format_duration(source.duration)
        
        embed = discord.Embed(
            title="🎵 Added to Queue" if player.is_playing() else "🎵 Now Playing",
            description=f"[{source.title}]({source.url})",
            color=discord.Color.green()
        )
        
        if source.thumbnail:
            embed.set_thumbnail(url=source.thumbnail)
        
        embed.add_field(name="Duration", value=duration_str, inline=True)
        embed.add_field(name="Uploader", value=source.uploader, inline=True)
        
        queue_size = player.queue.qsize()
        if queue_size > 0:
            embed.add_field(name="Position in Queue", value=f"#{queue_size}", inline=True)
        
        await interaction.followup.send(embed=embed)
        add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "MUSIC_PLAY", f"Played: {source.title}")
        
    except ValueError as e:
        await interaction.followup.send(f"❌ {str(e)}")
    except Exception as e:
        await interaction.followup.send(f"❌ Error playing song: {str(e)[:100]}")

@bot.tree.command(name="search", description="🔍 Search YouTube and pick a song to play")
async def search_music(interaction: discord.Interaction, query: str):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ You must be in a voice channel first!", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        results = await YTDLSource.search_results(query, max_results=5)
        
        if not results:
            await interaction.followup.send(f"❌ No results found for '{query}'.")
            return
        
        embed = discord.Embed(
            title="🔍 Search Results",
            description=f"Results for: **{query}**\n*Click a button below to play*",
            color=discord.Color.blue()
        )
        
        for i, result in enumerate(results, 1):
            duration = MusicPlayer._format_duration(result.get('duration', 0))
            embed.add_field(
                name=f"{i}. {result['title'][:80]}",
                value=f"Duration: {duration} | {result.get('uploader', 'Unknown')}",
                inline=False
            )
        
        class SearchSelect(discord.ui.Select):
            def __init__(self):
                options = []
                for i, result in enumerate(results, 1):
                    label = f"{i}. {result['title'][:80]}"
                    options.append(discord.SelectOption(
                        label=label[:100],
                        description=f"{MusicPlayer._format_duration(result.get('duration', 0))}",
                        value=str(i - 1)
                    ))
                super().__init__(placeholder="Choose a song to play...", min_values=1, max_values=1, options=options)
            
            async def callback(self, interaction: discord.Interaction):
                selected_idx = int(self.values[0])
                selected = results[selected_idx]
                
                player = get_music_player(interaction.guild.id)
                connected = await player.connect_voice(interaction.user.voice.channel)
                if not connected:
                    await interaction.response.send_message("❌ Could not connect to voice channel.", ephemeral=True)
                    return
                
                await player.add_to_queue(selected)
                
                duration_str = MusicPlayer._format_duration(selected.get('duration', 0))
                
                embed = discord.Embed(
                    title="🎵 Added to Queue",
                    description=f"[{selected['title']}]({selected['url']})",
                    color=discord.Color.green()
                )
                if selected.get('thumbnail'):
                    embed.set_thumbnail(url=selected['thumbnail'])
                embed.add_field(name="Duration", value=duration_str, inline=True)
                embed.add_field(name="Uploader", value=selected.get('uploader', 'Unknown'), inline=True)
                
                await interaction.response.edit_message(embed=embed, view=None)
                add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "MUSIC_SEARCH", f"Searched and played: {selected['title']}")
        
        view = discord.ui.View(timeout=30)
        view.add_item(SearchSelect())
        
        await interaction.followup.send(embed=embed, view=view)
        
    except Exception as e:
        await interaction.followup.send(f"❌ Search error: {str(e)[:100]}")

@bot.tree.command(name="pause", description="⏸️ Pause the current song")
async def pause_music(interaction: discord.Interaction):
    player = get_music_player(interaction.guild.id)
    
    if not player.voice_client or not player.voice_client.is_connected():
        await interaction.response.send_message("❌ I'm not in a voice channel.")
        return
    
    if not player.is_playing():
        await interaction.response.send_message("❌ Nothing is playing right now.")
        return
    
    player.pause()
    await interaction.response.send_message("⏸️ **Paused** — Use `/resume` to continue.")
    add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "MUSIC_PAUSE", "Paused music")

@bot.tree.command(name="resume", description="▶️ Resume the paused song")
async def resume_music(interaction: discord.Interaction):
    player = get_music_player(interaction.guild.id)
    
    if not player.voice_client or not player.voice_client.is_connected():
        await interaction.response.send_message("❌ I'm not in a voice channel.")
        return
    
    if not player.is_paused:
        await interaction.response.send_message("❌ The music isn't paused.")
        return
    
    player.resume()
    await interaction.response.send_message("▶️ **Resumed** — Enjoy the music!")
    add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "MUSIC_RESUME", "Resumed music")

@bot.tree.command(name="skip", description="⏭️ Skip the current song")
async def skip_music(interaction: discord.Interaction):
    player = get_music_player(interaction.guild.id)
    
    if not player.voice_client or not player.voice_client.is_connected():
        await interaction.response.send_message("❌ I'm not in a voice channel.")
        return
    
    if not player.is_playing() and not player.is_paused:
        await interaction.response.send_message("❌ Nothing is playing right now.")
        return
    
    current_title = player.current.get('title', 'Unknown') if player.current else 'Unknown'
    player.skip()
    
    await interaction.response.send_message(f"⏭️ **Skipped** `{current_title}`")
    add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "MUSIC_SKIP", f"Skipped: {current_title}")

@bot.tree.command(name="previous", description="⏮️ Go back to the previous song")
async def previous_music(interaction: discord.Interaction):
    player = get_music_player(interaction.guild.id)
    
    if not player.voice_client or not player.voice_client.is_connected():
        await interaction.response.send_message("❌ I'm not in a voice channel.")
        return
    
    if player.previous():
        await interaction.response.send_message("⏮️ **Going back to previous song**")
        add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "MUSIC_PREVIOUS", "Went to previous song")
    else:
        await interaction.response.send_message("❌ No previous song in history.")

@bot.tree.command(name="stop", description="⏹️ Stop music and clear the queue")
async def stop_music(interaction: discord.Interaction):
    player = get_music_player(interaction.guild.id)
    
    if not player.voice_client or not player.voice_client.is_connected():
        await interaction.response.send_message("❌ I'm not in a voice channel.")
        return
    
    player.stop()
    await interaction.response.send_message("⏹️ **Stopped** — Music stopped and queue cleared.")
    add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "MUSIC_STOP", "Stopped music and cleared queue")

@bot.tree.command(name="queue", description="📋 Show the current music queue")
async def queue_music(interaction: discord.Interaction, page: int = 1):
    player = get_music_player(interaction.guild.id)
    
    queue_list = player.get_queue_list()
    total_songs = len(queue_list)
    
    if total_songs == 0 and not player.current:
        await interaction.response.send_message("📋 **Queue is empty** — Use `/play` to add songs!")
        return
    
    items_per_page = 10
    total_pages = max(1, (total_songs + items_per_page - 1) // items_per_page)
    page = max(1, min(page, total_pages))
    
    start_idx = (page - 1) * items_per_page
    end_idx = min(start_idx + items_per_page, total_songs)
    
    embed = discord.Embed(
        title="📋 Music Queue",
        color=discord.Color.blue()
    )
    
    if player.current:
        current = player.current
        duration_str = MusicPlayer._format_duration(current.get('duration', 0))
        status = "▶️ Playing" if player.is_playing() else "⏸️ Paused"
        embed.add_field(
            name=f"{status} — Now Playing",
            value=f"[{current.get('title', 'Unknown')}]({current.get('url', '')})\n`{duration_str}` • {current.get('uploader', 'Unknown')}",
            inline=False
        )
    
    if queue_list:
        queue_text = ""
        for i in range(start_idx, end_idx):
            track = queue_list[i]
            duration_str = MusicPlayer._format_duration(track.get('duration', 0))
            queue_text += f"**{i + 1}.** [{track.get('title', 'Unknown')[:50]}]({track.get('url', '')}) `{duration_str}`\n"
        
        embed.add_field(
            name=f"⏭️ Up Next ({start_idx + 1}-{end_idx} of {total_songs})",
            value=queue_text or "No more songs",
            inline=False
        )
    
    loop_icons = {'none': '➡️ No Loop', 'one': '🔂 Loop One', 'all': '🔁 Loop All'}
    embed.set_footer(text=f"Page {page}/{total_pages} | {loop_icons.get(player.loop_mode, '➡️ No Loop')} | Volume: {int(player.volume * 100)}%")
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="nowplaying", description="🎶 Show what's currently playing")
async def now_playing(interaction: discord.Interaction):
    player = get_music_player(interaction.guild.id)
    
    if not player.current:
        await interaction.response.send_message("❌ Nothing is playing right now.")
        return
    
    track = player.current
    duration_str = MusicPlayer._format_duration(track.get('duration', 0))
    status = "▶️ Playing" if player.is_playing() else "⏸️ Paused"
    
    embed = discord.Embed(
        title=f"{status} — Now Playing",
        description=f"[{track.get('title', 'Unknown')}]({track.get('url', '')})",
        color=discord.Color.green()
    )
    
    if track.get('thumbnail'):
        embed.set_thumbnail(url=track['thumbnail'])
    
    embed.add_field(name="Duration", value=duration_str, inline=True)
    embed.add_field(name="Uploader", value=track.get('uploader', 'Unknown'), inline=True)
    embed.add_field(name="Channel", value=track.get('channel', 'Unknown'), inline=True)
    
    queue_size = player.queue.qsize()
    embed.add_field(name="Songs in Queue", value=str(queue_size), inline=True)
    
    loop_icons = {'none': '➡️', 'one': '🔂', 'all': '🔁'}
    embed.set_footer(text=f"Volume: {int(player.volume * 100)}% | Loop: {loop_icons.get(player.loop_mode, '➡️')}")
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="volume", description="🔊 Set the volume (0-100)")
async def volume_music(interaction: discord.Interaction, level: int):
    player = get_music_player(interaction.guild.id)
    
    if not player.voice_client or not player.voice_client.is_connected():
        await interaction.response.send_message("❌ I'm not in a voice channel.")
        return
    
    if level < 0 or level > 100:
        await interaction.response.send_message("❌ Volume must be between 0 and 100.")
        return
    
    player.set_volume(level / 100)
    await interaction.response.send_message(f"🔊 **Volume set to {level}%**")
    add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "MUSIC_VOLUME", f"Volume set to {level}%")

@bot.tree.command(name="loop", description="🔁 Set loop mode: none, one, or all")
async def loop_music(interaction: discord.Interaction, mode: str):
    player = get_music_player(interaction.guild.id)
    
    mode = mode.lower().strip()
    if mode not in ['none', 'one', 'all']:
        await interaction.response.send_message("❌ Mode must be `none`, `one`, or `all`.")
        return
    
    player.loop_mode = mode
    
    loop_names = {'none': '➡️ No Loop', 'one': '🔂 Loop One', 'all': '🔁 Loop All'}
    await interaction.response.send_message(f"**Loop: {loop_names[mode]}**")
    add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "MUSIC_LOOP", f"Loop mode: {mode}")

@bot.tree.command(name="shuffle", description="🔀 Shuffle the queue")
async def shuffle_music(interaction: discord.Interaction):
    player = get_music_player(interaction.guild.id)
    
    if not player.voice_client or not player.voice_client.is_connected():
        await interaction.response.send_message("❌ I'm not in a voice channel.")
        return
    
    queue_list = player.get_queue_list()
    if len(queue_list) < 2:
        await interaction.response.send_message("❌ Not enough songs in queue to shuffle (need at least 2).")
        return
    
    player.shuffle()
    await interaction.response.send_message(f"🔀 **Queue shuffled!** ({len(queue_list)} songs)")
    add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "MUSIC_SHUFFLE", "Shuffled queue")

@bot.tree.command(name="remove", description="❌ Remove a song from the queue by its number")
async def remove_music(interaction: discord.Interaction, position: int):
    player = get_music_player(interaction.guild.id)
    
    removed = player.remove_from_queue(position)
    if removed:
        await interaction.response.send_message(f"❌ Removed **{removed.get('title', 'Unknown')}** from position #{position}")
        add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "MUSIC_REMOVE", f"Removed #{position} from queue")
    else:
        await interaction.response.send_message(f"❌ Invalid position. Use `/queue` to see positions.")

@bot.tree.command(name="clearqueue", description="🧹 Clear the entire queue")
async def clear_queue_music(interaction: discord.Interaction):
    player = get_music_player(interaction.guild.id)
    
    queue_list = player.get_queue_list()
    if not queue_list:
        await interaction.response.send_message("❌ Queue is already empty.")
        return
    
    count = len(queue_list)
    player.clear_queue()
    await interaction.response.send_message(f"🧹 **Cleared {count} songs** from the queue. Current song continues.")
    add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "MUSIC_CLEAR", f"Cleared {count} songs from queue")

@bot.tree.command(name="join", description="🔊 Make the bot join your voice channel")
async def join_voice(interaction: discord.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ You must be in a voice channel first!")
        return
    
    player = get_music_player(interaction.guild.id)
    connected = await player.connect_voice(interaction.user.voice.channel)
    
    if connected:
        await interaction.response.send_message(f"🔊 **Joined** {interaction.user.voice.channel.mention}")
        add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "MUSIC_JOIN", f"Joined {interaction.user.voice.channel.name}")
    else:
        await interaction.response.send_message("❌ Could not connect to that voice channel.")

@bot.tree.command(name="leave", description="👋 Make the bot leave the voice channel")
async def leave_voice(interaction: discord.Interaction):
    player = get_music_player(interaction.guild.id)
    
    if not player.voice_client or not player.voice_client.is_connected():
        await interaction.response.send_message("❌ I'm not in a voice channel.")
        return
    
    channel_name = player.voice_client.channel.name
    await player.disconnect_voice()
    if interaction.guild.id in music_players:
        del music_players[interaction.guild.id]
    
    await interaction.response.send_message(f"👋 **Left** {channel_name} — Queue cleared.")
    add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "MUSIC_LEAVE", f"Left voice channel")

@bot.tree.command(name="lyrics", description="📝 Get lyrics for the current song")
async def lyrics_music(interaction: discord.Interaction, song: str = None):
    await interaction.response.defer()
    
    if not song:
        player = get_music_player(interaction.guild.id)
        if player.current:
            song = player.current.get('title', '')
        else:
            await interaction.followup.send("❌ Provide a song name or play something first.")
            return
    
    try:
        async with aiohttp.ClientSession() as session:
            search_query = urllib.parse.quote(song)
            async with session.get(f"https://api.lyrics.ovh/v1/{search_query}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    lyrics = data.get('lyrics', '')
                    
                    if len(lyrics) > 4000:
                        lyrics = lyrics[:4000] + "\n\n*...truncated*"
                    
                    embed = discord.Embed(
                        title=f"📝 Lyrics: {song}",
                        description=lyrics or "No lyrics found.",
                        color=discord.Color.purple()
                    )
                    await interaction.followup.send(embed=embed)
                else:
                    async with session.get(f"https://api.lyrics.ovh/v1/{song.replace(' - ', '/')}") as resp2:
                        if resp2.status == 200:
                            data = await resp2.json()
                            lyrics = data.get('lyrics', '')
                            if len(lyrics) > 4000:
                                lyrics = lyrics[:4000] + "\n\n*...truncated*"
                            embed = discord.Embed(
                                title=f"📝 Lyrics: {song}",
                                description=lyrics or "No lyrics found.",
                                color=discord.Color.purple()
                            )
                            await interaction.followup.send(embed=embed)
                        else:
                            await interaction.followup.send(f"❌ Could not find lyrics for `{song}`.")
    except Exception as e:
        await interaction.followup.send(f"❌ Lyrics lookup failed: {str(e)[:100]}")

@bot.tree.command(name="favorite", description="⭐ Save the current song to your favorites")
async def favorite_music(interaction: discord.Interaction):
    player = get_music_player(interaction.guild.id)
    
    if not player.current:
        await interaction.response.send_message("❌ Nothing is playing right now.")
        return
    
    track = player.current
    
    c.execute("SELECT id FROM music_favorites WHERE user_id=? AND guild_id=? AND url=?",
              (interaction.user.id, interaction.guild.id, track.get('url', '')))
    existing = c.fetchone()
    
    if existing:
        await interaction.response.send_message("⭐ This song is already in your favorites!")
        return
    
    c.execute("INSERT INTO music_favorites (user_id, guild_id, title, url, added_at) VALUES (?, ?, ?, ?, ?)",
              (interaction.user.id, interaction.guild.id, track.get('title', 'Unknown'), track.get('url', ''), datetime.now().isoformat()))
    conn.commit()
    
    await interaction.response.send_message(f"⭐ **Added to favorites:** {track.get('title', 'Unknown')}")
    add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "MUSIC_FAVORITE", f"Favorited: {track.get('title', 'Unknown')}")

@bot.tree.command(name="favorites", description="⭐ Show your favorite songs")
async def favorites_music(interaction: discord.Interaction):
    c.execute("SELECT title, url, added_at FROM music_favorites WHERE user_id=? AND guild_id=? ORDER BY id DESC LIMIT 25",
              (interaction.user.id, interaction.guild.id))
    favorites = c.fetchall()
    
    if not favorites:
        await interaction.response.send_message("⭐ You don't have any favorites yet. Use `/favorite` to save a song!")
        return
    
    embed = discord.Embed(
        title=f"⭐ {interaction.user.display_name}'s Favorites",
        color=discord.Color.gold()
    )
    
    for i, (title, url, added_at) in enumerate(favorites, 1):
        date_only = added_at[:10] if added_at else 'Unknown'
        embed.add_field(name=f"{i}. {title[:50]}", value=f"[Link]({url}) • Added {date_only}", inline=False)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="playlistcreate", description="📁 Create a new music playlist")
async def playlist_create(interaction: discord.Interaction, name: str):
    c.execute("SELECT id FROM music_playlists WHERE guild_id=? AND name=? AND user_id=?",
              (interaction.guild.id, name, interaction.user.id))
    existing = c.fetchone()
    
    if existing:
        await interaction.response.send_message(f"❌ You already have a playlist named `{name}`.")
        return
    
    c.execute("INSERT INTO music_playlists (guild_id, name, user_id, created_at) VALUES (?, ?, ?, ?)",
              (interaction.guild.id, name, interaction.user.id, datetime.now().isoformat()))
    conn.commit()
    
    await interaction.response.send_message(f"📁 **Playlist created:** `{name}` — Use `/playlistadd` to add songs!")
    add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "MUSIC_PLAYLIST_CREATE", f"Created playlist: {name}")

@bot.tree.command(name="playlistadd", description="➕ Add the current song to a playlist")
async def playlist_add(interaction: discord.Interaction, playlist_name: str):
    player = get_music_player(interaction.guild.id)
    
    if not player.current:
        await interaction.response.send_message("❌ Nothing is playing right now.")
        return
    
    c.execute("SELECT id FROM music_playlists WHERE guild_id=? AND name=? AND user_id=?",
              (interaction.guild.id, playlist_name, interaction.user.id))
    pl = c.fetchone()
    
    if not pl:
        await interaction.response.send_message(f"❌ Playlist `{playlist_name}` not found. Use `/playlistcreate` first.")
        return
    
    playlist_id = pl[0]
    track = player.current
    
    c.execute("SELECT MAX(position) FROM music_playlist_tracks WHERE playlist_id=?", (playlist_id,))
    max_pos = c.fetchone()[0] or 0
    
    c.execute("INSERT INTO music_playlist_tracks (playlist_id, title, url, position, added_at) VALUES (?, ?, ?, ?, ?)",
              (playlist_id, track.get('title', 'Unknown'), track.get('url', ''), max_pos + 1, datetime.now().isoformat()))
    conn.commit()
    
    await interaction.response.send_message(f"➕ **Added to `{playlist_name}`:** {track.get('title', 'Unknown')}")

@bot.tree.command(name="playlists", description="📁 Show your playlists")
async def playlists_show(interaction: discord.Interaction):
    c.execute("SELECT id, name, created_at FROM music_playlists WHERE guild_id=? AND user_id=? ORDER BY id DESC",
              (interaction.guild.id, interaction.user.id))
    playlists = c.fetchall()
    
    if not playlists:
        await interaction.response.send_message("📁 You don't have any playlists. Use `/playlistcreate` to make one!")
        return
    
    embed = discord.Embed(
        title=f"📁 {interaction.user.display_name}'s Playlists",
        color=discord.Color.blue()
    )
    
    for pl_id, name, created_at in playlists:
        c.execute("SELECT COUNT(*) FROM music_playlist_tracks WHERE playlist_id=?", (pl_id,))
        track_count = c.fetchone()[0]
        date_only = created_at[:10] if created_at else 'Unknown'
        embed.add_field(name=f"📁 {name}", value=f"{track_count} tracks • Created {date_only}", inline=False)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="playlistplay", description="▶️ Play all songs from a playlist")
async def playlist_play(interaction: discord.Interaction, name: str):
    if not interaction.user.voice:
        await interaction.response.send_message("❌ You must be in a voice channel first!", ephemeral=True)
        return
    
    c.execute("SELECT id FROM music_playlists WHERE guild_id=? AND name=? AND user_id=?",
              (interaction.guild.id, name, interaction.user.id))
    pl = c.fetchone()
    
    if not pl:
        await interaction.response.send_message(f"❌ Playlist `{name}` not found.")
        return
    
    c.execute("SELECT title, url FROM music_playlist_tracks WHERE playlist_id=? ORDER BY position",
              (pl[0],))
    tracks = c.fetchall()
    
    if not tracks:
        await interaction.response.send_message(f"❌ Playlist `{name}` is empty!")
        return
    
    await interaction.response.defer()
    
    player = get_music_player(interaction.guild.id)
    connected = await player.connect_voice(interaction.user.voice.channel)
    if not connected:
        await interaction.followup.send("❌ Could not connect to voice channel.")
        return
    
    for title, url in tracks:
        track_data = {
            'title': title,
            'url': url,
            'duration': 0,
            'thumbnail': '',
            'channel': '',
            'channel_url': '',
            'uploader': '',
            'views': 0,
        }
        await player.add_to_queue(track_data)
    
    embed = discord.Embed(
        title="📁 Playlist Started",
        description=f"**{name}**",
        color=discord.Color.green()
    )
    embed.add_field(name="Tracks", value=f"{len(tracks)} songs added to queue", inline=False)
    
    await interaction.followup.send(embed=embed)
    add_history(interaction.guild.id, interaction.user.id, str(interaction.user), "MUSIC_PLAYLIST_PLAY", f"Playing playlist: {name} ({len(tracks)} tracks)")

@bot.tree.command(name="playlistdelete", description="🗑️ Delete a playlist")
async def playlist_delete(interaction: discord.Interaction, name: str):
    c.execute("SELECT id FROM music_playlists WHERE guild_id=? AND name=? AND user_id=?",
              (interaction.guild.id, name, interaction.user.id))
    pl = c.fetchone()
    
    if not pl:
        await interaction.response.send_message(f"❌ Playlist `{name}` not found.")
        return
    
    playlist_id = pl[0]
    c.execute("DELETE FROM music_playlist_tracks WHERE playlist_id=?", (playlist_id,))
    c.execute("DELETE FROM music_playlists WHERE id=?", (playlist_id,))
    conn.commit()
    
    await interaction.response.send_message(f"🗑️ **Deleted playlist:** `{name}`")

# =========================
# ADMIN COMMANDS
# =========================
@bot.tree.command(name="servers", description="List all servers the bot is in")
@app_commands.check(owner_check)
async def list_servers(interaction: discord.Interaction):
    embed = discord.Embed(title="📋 Servers", color=discord.Color.blue())
    for guild in bot.guilds:
        embed.add_field(name=guild.name, value=f"ID: {guild.id}\nMembers: {guild.member_count}", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leaveserver", description="Leave a server by ID")
@app_commands.check(owner_check)
async def leave_server(interaction: discord.Interaction, guild_id: str):
    try:
        guild = bot.get_guild(int(guild_id))
        if guild:
            add_history(guild.id, interaction.user.id, str(interaction.user), "BOT_LEAVE", f"Bot left server by admin request")
            await guild.leave()
            await interaction.response.send_message(f"✅ Left {guild.name}")
        else:
            await interaction.response.send_message("❌ Server not found.")
    except ValueError:
        await interaction.response.send_message("❌ Invalid server ID.")

@bot.tree.command(name="reload", description="Reload bot commands")
@app_commands.check(owner_check)
async def reload_commands(interaction: discord.Interaction):
    await bot.tree.sync()
    await interaction.response.send_message("✅ Commands reloaded!")

# =========================
# REGISTER GROUPS
# =========================
bot.tree.add_command(mod_group)
bot.tree.add_command(history_group)
bot.tree.add_command(fun_group)
bot.tree.add_command(ticket_group)
bot.tree.add_command(level_group)
bot.tree.add_command(eco_group)
bot.tree.add_command(giveaway_group)

# =========================
# ERROR HANDLER
# =========================
@bot.tree.error
async def error_handler(interaction: discord.Interaction, error):
    if interaction.response.is_done():
        await interaction.followup.send(
            f"❌ Error: {error}",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"❌ Error: {error}",
            ephemeral=True
        )

# =========================
# RUN
# =========================
bot.run(TOKEN)
