import discord
from discord import app_commands
from discord.ext import commands
from datetime import timedelta, datetime, timezone
import sqlite3
import re
import random
import os
import io

from google import genai
from pypdf import PdfReader
from docx import Document
from discord.utils import utcnow

TOKEN = os.getenv("TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OWNER_ID = int(os.getenv("OWNER_ID"))

client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_NAME = "gemini-3.5-flash"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

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
    timestamp TEXT
)
""")

# =========================
# WARNING DB FUNCTION
# =========================
def remove_warning(warning_id):

    c.execute("""
    DELETE FROM warnings
    WHERE id=?
    """, (warning_id,))

    conn.commit()

# 🧠 MEMORY TABLE (NEW)
c.execute("""
CREATE TABLE IF NOT EXISTS memory (
    user_id INTEGER PRIMARY KEY,
    user_name TEXT,
    bot_name TEXT
)
""")

conn.commit()

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

        c.execute("""
        UPDATE memory
        SET user_name=?, bot_name=?
        WHERE user_id=?
        """, (user_name, bot_name, user_id))
    else:
        c.execute("""
        INSERT INTO memory (user_id, user_name, bot_name)
        VALUES (?, ?, ?)
        """, (user_id, user_name, bot_name))

    conn.commit()

# =========================
# WARNING FUNCTIONS
# =========================
def add_warning(user_id, guild_id, reason, moderator):

    c.execute("""
    INSERT INTO warnings (user_id, guild_id, reason, timestamp)
    VALUES (?, ?, ?, ?)
    """, (
        user_id,
        guild_id,
        reason,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    conn.commit()


def get_warnings(user_id, guild_id):

    c.execute("""
    SELECT id, reason, timestamp
    FROM warnings
    WHERE user_id=? AND guild_id=?
    """, (user_id, guild_id))

    return c.fetchall()


def remove_warning(warning_id):
    c.execute("""
    DELETE FROM warnings
    WHERE id=?
    """, (warning_id,))
    conn.commit()

# =========================
# LOG SYSTEM
# =========================
async def log(guild, text):
    channel = discord.utils.get(guild.text_channels, name="mod-logs")
    if channel:
        await channel.send(f"📜 {text}")

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
    return any(role.name == "Owner" for role in interaction.user.roles)

# =========================
# MESSAGE EVENT (AI + MEMORY)
# =========================
@bot.event
async def on_message(message):
    if message.author.bot:
        return

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

        # auto save basic memory if missing
        mem = get_memory(user_id)
        if not mem:
            save_memory(user_id, user_name=message.author.name, bot_name="AI Bot")

        dm_memory[key] += f"User: {message.content}\n"

        # =========================
        # NAME DETECTION
        # =========================
        msg = message.content.lower()

        m1 = re.search(r"my name is (.+)", msg)
        if m1:
            save_memory(user_id, user_name=m1.group(1).strip().title())

        m2 = re.search(r"your name is (.+)", msg)
        if m2:
            save_memory(user_id, bot_name=m2.group(1).strip().title())

        mem = get_memory(user_id)
        user_name, bot_name = mem if mem else (None, None)

        prompt = f"""
You are a Discord AI bot.

User name: {user_name or "unknown"}
Bot name: {bot_name or "AI Bot"}

Conversation:
{dm_memory[key]}
"""

        try:

            if message.attachments:

                attachment = message.attachments[0]

                # IMAGE
                if attachment.content_type and attachment.content_type.startswith("image/"):
                    img = await attachment.read()

                    uploaded = client.files.upload(
                        file=img,
                        config={"mime_type": attachment.content_type}
                    )

                    response = client.models.generate_content(
                        model=MODEL_NAME,
                        contents=[prompt, uploaded]
                    )

                # PDF
                elif attachment.filename.endswith(".pdf"):
                    pdf_data = await attachment.read()
                    pdf = PdfReader(io.BytesIO(pdf_data))

                    text = ""
                    for page in pdf.pages:
                        t = page.extract_text()
                        if t:
                            text += t + "\n"

                    response = client.models.generate_content(
                        model=MODEL_NAME,
                        contents=f"{prompt}\nPDF:\n{text}"
                    )

                # DOCX
                elif attachment.filename.endswith(".docx"):
                    doc_data = await attachment.read()
                    doc = Document(io.BytesIO(doc_data))

                    text = "\n".join(p.text for p in doc.paragraphs)

                    response = client.models.generate_content(
                        model=MODEL_NAME,
                        contents=f"{prompt}\nDOCX:\n{text}"
                    )

                # TXT
                elif attachment.filename.endswith(".txt"):
                    txt = await attachment.read()
                    text = txt.decode("utf-8", errors="ignore")

                    response = client.models.generate_content(
                        model=MODEL_NAME,
                        contents=f"{prompt}\nTXT:\n{text}"
                    )

                else:
                    response = client.models.generate_content(
                        model=MODEL_NAME,
                        contents=prompt
                    )

            else:
                response = client.models.generate_content(
                    model=MODEL_NAME,
                    contents=prompt
                )

            reply = response.text

        except Exception as e:
            reply = f"⚠️ AI error: {e}"

        dm_memory[key] += f"Bot: {reply}\n"
        dm_memory[key] = dm_memory[key][-4000:]

        while len(reply) > 1900:
            await message.channel.send(reply[:1900])
            reply = reply[1900:]

        await message.channel.send(reply)
        return

    await bot.process_commands(message)

# =========================
# READY
# =========================
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"BOT ONLINE: {bot.user}")

# =========================================================
# 🎮 ALL YOUR ORIGINAL COMMANDS (UNCHANGED + RESTORED)
# =========================================================

@bot.tree.command(name="guess", description="Guess a number between 1 and 10")
async def guess(interaction: discord.Interaction, number: int):
    secret = random.randint(1, 10)

    if number == secret:
        await interaction.response.send_message(f"🎉 You won! I picked {secret}")
    else:
        await interaction.response.send_message(f"❌ Wrong! I picked {secret}")

# -------------------------
@bot.tree.command(name="clear", description="Delete messages")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: int):

    deleted = await interaction.channel.purge(limit=amount)

    await interaction.response.send_message(
        f"🧹 Deleted {len(deleted)} messages",
        ephemeral=True
    )

    await log(interaction.guild, f"CLEAR | {len(deleted)}")

# -------------------------
@bot.tree.command(name="clearall", description="Wipe channel")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(manage_messages=True)
async def clearall(interaction: discord.Interaction):

    await interaction.response.send_message("Clearing...", ephemeral=True)

    total = 0
    while True:
        deleted = await interaction.channel.purge(limit=100)
        total += len(deleted)
        if not deleted:
            break

    await interaction.followup.send(f"Cleared {total}")
    await log(interaction.guild, f"CLEARALL | {total}")

# -------------------------
@bot.tree.command(name="kick", description="Kick member")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await member.kick(reason=reason)
    await interaction.response.send_message("Kicked")
    await log(interaction.guild, f"KICK | {member}")

# -------------------------
@bot.tree.command(name="ban", description="Ban member")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await member.ban(reason=reason)
    await interaction.response.send_message("Banned")
    await log(interaction.guild, f"BAN | {member}")

# -------------------------
@bot.tree.command(name="mute", description="Timeout member")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(moderate_members=True)
async def mute(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str):
    await member.timeout(utcnow() + timedelta(minutes=minutes), reason=reason)
    await interaction.response.send_message("Muted")

# -------------------------
@bot.tree.command(name="unmute", description="Unmute member")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(moderate_members=True)
async def unmute(interaction: discord.Interaction, member: discord.Member):
    await member.timeout(None)
    await interaction.response.send_message("Unmuted")

# -------------------------
@bot.tree.command(name="nick", description="Change nickname")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(manage_nicknames=True)
async def nick(interaction: discord.Interaction, member: discord.Member, nickname: str):
    await member.edit(nick=nickname)
    await interaction.response.send_message("Nickname changed")

# -------------------------
@bot.tree.command(name="giverole", description="Give role")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(manage_roles=True)
async def giverole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    await member.add_roles(role)
    await interaction.response.send_message("Role given")

# -------------------------
@bot.tree.command(name="removerole", description="Remove role")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(manage_roles=True)
async def removerole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    await member.remove_roles(role)
    await interaction.response.send_message("Role removed")

# -------------------------
@bot.tree.command(name="warn", description="Warn member")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str
):

    # save warning ONCE
    add_warning(
        member.id,
        interaction.guild.id,
        reason,
        str(interaction.user)
    )

    # DM embed
    try:
        embed = discord.Embed(
            title="⚠️ You have been warned",
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Server", value=interaction.guild.name, inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text=f"Moderator: {interaction.user}")

        await member.send(embed=embed)

    except discord.Forbidden:
        pass

    # public response (embed = cleaner UI)
    embed = discord.Embed(
        title="⚠️ Warning Issued",
        color=discord.Color.red()
    )
    embed.add_field(name="User", value=member.mention, inline=True)
    embed.add_field(name="Reason", value=reason, inline=True)
    embed.set_footer(text=f"By {interaction.user}")

    await interaction.response.send_message(embed=embed)

    # log
    await log(
        interaction.guild,
        f"WARN | {member} | {reason}"
    )

# -------------------------
@bot.tree.command(name="warnings", description="Show warnings")
async def warnings(
    interaction: discord.Interaction,
    member: discord.Member
):

    warns = get_warnings(
        member.id,
        interaction.guild.id
    )

    if not warns:
        await interaction.response.send_message(
            "✅ No warnings."
        )
        return

    text = ""

    for warn_id, reason, timestamp in warns:

        text += (
            f"ID: {warn_id}\n"
            f"Reason: {reason}\n"
            f"Time: {timestamp}\n\n"
        )

    await interaction.response.send_message(
        f"⚠️ Warnings for {member}:\n\n{text}"
    )


# -------------------------
@bot.tree.command(name="unwarn", description="Remove warning")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(moderate_members=True)
async def unwarn(
    interaction: discord.Interaction,
    warning_id: int
):

    remove_warning(warning_id)

    await interaction.response.send_message(
        f"✅ Removed warning #{warning_id}"
    )

# -------------------------
@bot.tree.command(name="lock", description="Lock channel")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(manage_channels=True)
async def lock(interaction: discord.Interaction):

    overwrite = interaction.channel.overwrites_for(
        interaction.guild.default_role
    )

    overwrite.send_messages = False

    await interaction.channel.set_permissions(
        interaction.guild.default_role,
        overwrite=overwrite
    )

    await interaction.response.send_message(
        "🔒 Channel locked."
    )


# -------------------------
@bot.tree.command(name="unlock", description="Unlock channel")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(manage_channels=True)
async def unlock(interaction: discord.Interaction):

    overwrite = interaction.channel.overwrites_for(
        interaction.guild.default_role
    )

    overwrite.send_messages = None

    await interaction.channel.set_permissions(
        interaction.guild.default_role,
        overwrite=overwrite
    )

    await interaction.response.send_message(
        "🔓 Channel unlocked."
    )


# -------------------------
@bot.tree.command(name="slowmode", description="Set slowmode")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(manage_channels=True)
async def slowmode(
    interaction: discord.Interaction,
    seconds: int
):

    await interaction.channel.edit(
        slowmode_delay=seconds
    )

    await interaction.response.send_message(
        f"🐌 Slowmode set to {seconds} seconds."
    )

# 🧹 Remove ALL warnings from a specific user in this server
@bot.tree.command(name="clearwarns", description="Remove all warnings from a user")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(moderate_members=True)
async def clearwarns(
    interaction: discord.Interaction,
    member: discord.Member
):

    # Delete all warnings for that user in this guild
    c.execute("""
    DELETE FROM warnings
    WHERE user_id=? AND guild_id=?
    """, (member.id, interaction.guild.id))

    conn.commit()

    await interaction.response.send_message(
        f"🧹 Removed all warnings for {member.mention}"
    )

    # Log action in mod-log channel (if exists)
    await log(interaction.guild, f"CLEARWARNS | {member}")

# 🖼️ Show a user's Discord profile picture (avatar)
@bot.tree.command(name="avatar", description="Show user avatar")
async def avatar(
    interaction: discord.Interaction,
    member: discord.Member = None
):

    # If no member is mentioned, show command user avatar
    member = member or interaction.user

    # Create embed for better display
    embed = discord.Embed(
        title=f"{member.name}'s Avatar",
        color=discord.Color.blue()
    )

    # Set image to user's avatar
    embed.set_image(url=member.display_avatar.url)

    await interaction.response.send_message(embed=embed)

# 🌄 Show a user's Discord profile banner (if available)
@bot.tree.command(name="banner", description="Show user banner")
async def banner(
    interaction: discord.Interaction,
    member: discord.Member = None
):

    # Default to command user if no member is given
    member = member or interaction.user

    # Fetch full user data (required for banner)
    user = await bot.fetch_user(member.id)

    # Check if user has a banner set
    if not user.banner:
        await interaction.response.send_message("❌ No banner found.")
        return

    # Create embed for banner display
    embed = discord.Embed(
        title=f"{member.name}'s Banner",
        color=discord.Color.purple()
    )

    # Set banner image
    embed.set_image(url=user.banner.url)

    await interaction.response.send_message(embed=embed)

# =========================
# ERROR HANDLER
# =========================
@bot.tree.error
async def error_handler(interaction: discord.Interaction, error):
    await interaction.response.send_message(f"❌ Error: {error}", ephemeral=True)

# =========================
# RUN
# =========================
bot.run(TOKEN)
