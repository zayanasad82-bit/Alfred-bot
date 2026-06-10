import discord
from discord import app_commands
from discord.ext import commands
from datetime import timedelta
import sqlite3
import re
import random
import os
from google import genai

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
# OWNER ROLE CHECK
# =========================
async def owner_check(interaction: discord.Interaction):
    return any(role.name == "Owner" for role in interaction.user.roles)

# =========================
# DATABASE (WARN SYSTEM)
# =========================
conn = sqlite3.connect("moderation.db")
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS warnings (
    user_id INTEGER,
    guild_id INTEGER,
    reason TEXT,
    timestamp TEXT
)
""")
conn.commit()

# =========================
# LOG SYSTEM
# =========================
async def log(guild, text):
    channel = discord.utils.get(guild.text_channels, name="mod-logs")
    if channel:
        await channel.send(f"📜 {text}")

dm_memory = {}

# =========================
# AUTO MODERATION + DM HANDLING
# =========================
BAD_WORDS = ["badword1", "badword2"]
INVITE_REGEX = r"(discord\.gg/|discordapp\.com/invite/)"

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # =========================
    # 🟢 DM HANDLING (AI)
    # =========================
    if isinstance(message.channel, discord.DMChannel):

        print("OWNER_ID:", OWNER_ID)
        print("SENDER:", message.author.id)

        # only owner can use AI
        if message.author.id != OWNER_ID:
            await message.channel.send("👋 Only my owner can use AI chat. DM @_spidey_gg for any issue")
            return

        user_id = str(message.author.id)

        # init memory
        if user_id not in dm_memory:
            dm_memory[user_id] = []

        # store user message
        dm_memory[user_id].append({
            "role": "user",
            "parts": [message.content]
        })

        # keep last 10 messages
        dm_memory[user_id] = dm_memory[user_id][-10:]

        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=dm_memory[user_id]
            )

            reply = response.text

        except Exception as e:
            reply = f"⚠️ AI error: {e}"

        # store bot reply
        dm_memory[user_id].append({
            "role": "model",
            "parts": [reply]
        })

        await message.channel.send(reply)
        return

    # =========================
    # 🌐 SERVER MESSAGE HANDLING
    # =========================
    content = message.content.lower()

    if any(word in content for word in BAD_WORDS):
        await message.delete()
        await message.channel.send(f"⚠️ {message.author.mention} no bad words!")

        try:
            await message.author.timeout(
                discord.utils.utcnow() + timedelta(minutes=5),
                reason="Bad language"
            )
        except:
            pass

    if re.search(INVITE_REGEX, content):
        await message.delete()
        await message.channel.send(f"🚫 {message.author.mention} no invites!")

    await bot.process_commands(message)
    
# =========================
# READY EVENT
# =========================
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"PRO BOT ONLINE: {bot.user}")

# =========================
# 🎮 GUESS GAME
# =========================
@bot.tree.command(name="guess", description="Guess a number between 1 and 10")
async def guess(interaction: discord.Interaction, number: int):

    if number < 1 or number > 10:
        await interaction.response.send_message("❌ Pick a number between 1 and 10")
        return

    secret = random.randint(1, 10)

    if number == secret:
        await interaction.response.send_message(f"🎉 You won! I picked {secret}")
    else:
        await interaction.response.send_message(f"❌ Wrong! I picked {secret}")

# =========================
# 🧹 CLEAR
# =========================
@bot.tree.command(name="clear", description="Delete a specific number of messages")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: int):

    deleted = await interaction.channel.purge(limit=amount)

    await interaction.response.send_message(
        f"🧹 Deleted {len(deleted)} messages",
        ephemeral=True
    )

    await log(interaction.guild, f"CLEAR | {len(deleted)} msgs | {interaction.user}")

# =========================
# 🚀 CLEAR ALL
# =========================
@bot.tree.command(name="clearall", description="Wipe all messages in a channel")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(manage_messages=True)
async def clearall(interaction: discord.Interaction):

    await interaction.response.send_message("⚠️ Clearing messages...", ephemeral=True)

    total = 0

    while True:
        deleted = await interaction.channel.purge(limit=100)
        total += len(deleted)

        if len(deleted) == 0:
            break

    await interaction.followup.send(f"🧹 Cleared ALL messages ({total})")

    await log(interaction.guild, f"CLEARALL | {total} msgs | {interaction.user}")

# =========================
# 👢 KICK
# =========================
@bot.tree.command(name="kick", description="Kick a member from the server")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await member.kick(reason=reason)
    await interaction.response.send_message(f"👢 Kicked {member}")
    await log(interaction.guild, f"KICK | {member} | {reason} | {interaction.user}")

# =========================
# 🔨 BAN
# =========================
@bot.tree.command(name="ban", description="Ban a member permanently")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await member.ban(reason=reason)
    await interaction.response.send_message(f"🔨 Banned {member}")
    await log(interaction.guild, f"BAN | {member} | {reason} | {interaction.user}")

# =========================
# 🔇 MUTE
# =========================
@bot.tree.command(name="mute", description="Timeout a member")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(moderate_members=True)
async def mute(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str):
    await member.timeout(
        discord.utils.utcnow() + timedelta(minutes=minutes),
        reason=reason
    )

    await interaction.response.send_message(f"🔇 Muted {member}")
    await log(interaction.guild, f"MUTE | {member} | {minutes}m | {reason}")

# =========================
# 🔊 UNMUTE
# =========================
@bot.tree.command(name="unmute", description="Remove timeout from a member")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(moderate_members=True)
async def unmute(interaction: discord.Interaction, member: discord.Member):
    await member.timeout(None)

    await interaction.response.send_message(f"🔊 Unmuted {member}")
    await log(interaction.guild, f"UNMUTE | {member}")

# =========================
# 🏷️ NICK
# =========================
@bot.tree.command(name="nick", description="Change a member's nickname")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(manage_nicknames=True)
async def nick(interaction: discord.Interaction, member: discord.Member, nickname: str):

    try:
        await member.edit(nick=nickname)

        await interaction.response.send_message(
            f"🏷️ Changed nickname of {member.mention} → **{nickname}**"
        )

        await log(interaction.guild, f"NICK | {member} → {nickname} | {interaction.user}")

    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I don't have permission to change this user's nickname.",
            ephemeral=True
        )

# =========================
# 🏷️ GIVE ROLE
# =========================
@bot.tree.command(name="giverole", description="Give a role to a member")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(manage_roles=True)
async def giverole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):

    try:
        await member.add_roles(role)

        await interaction.response.send_message(
            f"✅ Gave **{role.name}** to {member.mention}"
        )

        await log(interaction.guild, f"GIVEROLE | {member} → {role.name} | {interaction.user}")

    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I don't have permission to give this role.",
            ephemeral=True
        )

# =========================
# 🗑️ REMOVE ROLE
# =========================
@bot.tree.command(name="removerole", description="Remove a role from a member")
@app_commands.check(owner_check)
@app_commands.checks.has_permissions(manage_roles=True)
async def removerole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):

    try:
        await member.remove_roles(role)

        await interaction.response.send_message(
            f"🗑️ Removed **{role.name}** from {member.mention}"
        )

        await log(interaction.guild, f"REMOVEROLE | {member} - {role.name} | {interaction.user}")

    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I don't have permission to remove this role.",
            ephemeral=True
        )

# =========================
# ERROR HANDLER
# =========================
@bot.tree.error
async def error_handler(interaction: discord.Interaction, error):

    if isinstance(error, app_commands.errors.CheckFailure):
        await interaction.response.send_message(
            "❌ Only users with the **Owner** role can use this command.",
            ephemeral=True
        )

    elif isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message(
            "❌ You don't have permission.",
            ephemeral=True
        )

# =========================
# RUN BOT
# =========================
bot.run(TOKEN)
