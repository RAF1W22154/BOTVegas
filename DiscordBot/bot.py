import os
import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
import random
import time
from flask import Flask
from threading import Thread

# ==================== ระบบเว็บเซิร์ฟเวอร์จำลอง (สำหรับรันบน Render 24 ชม.) ====================
app = Flask('')

@app.route('/')
def home():
    return "Vegas Bot is Online!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# ==================== ตั้งค่าบอท Discord ====================
intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ==================== 1. ระบบฐานข้อมูล SQLite (รวมตู้กาชาและสกอร์แคลน) ====================
db = sqlite3.connect("bot_database.db")
cursor = db.cursor()

# ตารางเวลาออนของผู้ใช้
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    total_time INTEGER DEFAULT 0
)
""")

# ตารางข้อมูลตู้กาชา
cursor.execute("""
CREATE TABLE IF NOT EXISTS gacha_boxes_info (
    box_name TEXT PRIMARY KEY,
    cost_minutes INTEGER DEFAULT 60,
    image_url TEXT,
    channel_id INTEGER,
    message_id INTEGER
)
""")

# ตารางเก็บของรางวัลในตู้กาชา
cursor.execute("""
CREATE TABLE IF NOT EXISTS gacha_prizes (
    box_name TEXT,
    role_id INTEGER,
    role_name TEXT,
    rate REAL,
    stock INTEGER DEFAULT 0,
    PRIMARY KEY (box_name, role_id)
)
""")

# ตารางแคลนสำหรับระบบสกอร์
cursor.execute("""
CREATE TABLE IF NOT EXISTS clans (
    clan_name TEXT PRIMARY KEY
)
""")

# ตารางเก็บรูปภาพสกอร์แคลน
cursor.execute("""
CREATE TABLE IF NOT EXISTS clan_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clan_name TEXT,
    user_id INTEGER,
    image_url TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")

# ตารางเก็บสถานะข้อความสรุปสกอร์แคลน (สำหรับระบบ Real-time)
cursor.execute("""
CREATE TABLE IF NOT EXISTS clan_dashboard (
    guild_id INTEGER PRIMARY KEY,
    channel_id INTEGER,
    message_id INTEGER
)
""")

db.commit()

voice_sessions = {}

def format_time(seconds):
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

@bot.event
async def on_ready():
    print(f" Vegas BOT ออนไลน์แล้ว: {bot.user.name}")
    try:
        synced = await bot.tree.sync()
        print(f"ซิงค์ Slash Commands ทั้งหมด {len(synced)} คำสั่งเรียบร้อยแล้ว")
    except Exception as e:
        print(e)

# ==================== ฟังก์ชันช่วยอัปเดตหน้าตู้กาชาแบบ Real-time ====================
async def update_gacha_embed(guild: discord.Guild, box_name: str):
    cursor.execute("SELECT cost_minutes, image_url, channel_id, message_id FROM gacha_boxes_info WHERE box_name = ?", (box_name,))
    box_info = cursor.fetchone()
    if not box_info:
        return

    cost_minutes, image_url, channel_id, message_id = box_info
    if not channel_id or not message_id:
        return

    channel = guild.get_channel(channel_id)
    if not channel:
        return

    try:
        message = await channel.fetch_message(message_id)
    except Exception:
        return

    cursor.execute("SELECT role_name, rate, stock FROM gacha_prizes WHERE box_name = ?", (box_name,))
    prizes = cursor.fetchall()

    embed = discord.Embed(
        title=f"🎰 ตู้กาชา: {box_name}", 
        description=f"⏱️ **ค่าใช้จ่าย:** ใช้เวลาออน `{cost_minutes}` นาทีต่อการสุ่ม\n\n📦 **รายการของรางวัลในตู้:**", 
        color=discord.Color.gold()
    )

    if not prizes:
        embed.add_field(name="สถานะ", value="❌ ยังไม่มีของรางวัลในตู้ (รอแอดมินเติม)", inline=False)
    else:
        desc = ""
        for idx, (r_name, rate, stock) in enumerate(prizes, 1):
            desc += f"**{idx}. ยศ:** `{r_name}` | **เรท:** `{rate}%` | **สต๊อก:** `{stock}` ชิ้น\n"
        embed.add_field(name="🎁 รายละเอียดไอเทม", value=desc, inline=False)

    if image_url:
        embed.set_image(url=image_url)

    view = GachaView(box_name)
    await message.edit(embed=embed, view=view)


# ==================== ฟังก์ชันอัปเดตหน้าสรุปผลรวมสกอร์แคลนแบบ Real-time ====================
async def update_clan_dashboard(guild: discord.Guild):
    cursor.execute("SELECT channel_id, message_id FROM clan_dashboard")
    dash = cursor.fetchone()
    if not dash:
        return

    channel_id, message_id = dash
    channel = guild.get_channel(channel_id)
    if not channel:
        return

    try:
        message = await channel.fetch_message(message_id)
    except Exception:
        return

    cursor.execute("SELECT clan_name FROM clans")
    clans = cursor.fetchall()

    embed = discord.Embed(
        title="📊 ระบบสรุปผลและรูปภาพสกอร์แคลน (Vegas Clan Score)",
        description="เลือกชื่อแคลนจากเมนูดรอปดาวน์ด้านล่างเพื่อดูรูปภาพสกอร์การแข่งทั้งหมด",
        color=discord.Color.blue()
    )

    if not clans:
        embed.add_field(name="สถานะ", value="❌ ยังไม่มีแคลนในระบบ", inline=False)
        view = None
    else:
        desc = ""
        for clan in clans:
            c_name = clan[0]
            cursor.execute("SELECT COUNT(*) FROM clan_scores WHERE clan_name = ?", (c_name,))
            count = cursor.fetchone()[0]
            desc += f"🛡️ **{c_name}**: มีรูปภาพสะสม `{count}` รูป\n"
        embed.add_field(name="📋 รายชื่อแคลนทั้งหมด", value=desc, inline=False)
        view = ClanSelectView(clans)

    await message.edit(embed=embed, view=view)


# ==================== 2. ระบบนับเวลาออน (Real-time) ====================
@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return
    current_time = int(time.time())

    if before.channel is None and after.channel is not None:
        voice_sessions[member.id] = current_time
    elif before.channel is not None and after.channel is None:
        if member.id in voice_sessions:
            start_time = voice_sessions.pop(member.id)
            duration = current_time - start_time

            cursor.execute("SELECT total_time FROM users WHERE user_id = ?", (member.id,))
            result = cursor.fetchone()
            if result:
                new_total = result[0] + duration
                cursor.execute("UPDATE users SET total_time = ? WHERE user_id = ?", (new_total, member.id))
            else:
                cursor.execute("INSERT INTO users (user_id, total_time) VALUES (?, ?)", (member.id, duration))
            db.commit()

@bot.tree.command(name="เช็คเวลาออน", description="ตรวจสอบชั่วโมงเวลาออนไลน์ของคุณแบบเรียลไทม์")
async def check_time(interaction: discord.Interaction):
    user_id = interaction.user.id
    cursor.execute("SELECT total_time FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    
    total_sec = result[0] if result else 0
    if user_id in voice_sessions:
        total_sec += int(time.time()) - voice_sessions[user_id]

    embed = discord.Embed(title="📊 ข้อมูลเวลาออนไลน์ของคุณ", color=discord.Color.blue())
    embed.add_field(name="⏱️ เวลาออนทั้งหมด (ใช้เป็นแต้มสุ่มกาชา)", value=f"`{format_time(total_sec)}`", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ==================== 3. ระบบ UI กาชา ====================
class GachaView(discord.ui.View):
    def __init__(self, box_name):
        super().__init__(timeout=None)
        self.box_name = box_name

    @discord.ui.button(label="🎰 กดสุ่มกาชา", style=discord.ButtonStyle.green, custom_id="spin_gacha_btn")
    async def spin_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        
        cursor.execute("SELECT cost_minutes FROM gacha_boxes_info WHERE box_name = ?", (self.box_name,))
        box_info = cursor.fetchone()
        if not box_info:
            await interaction.response.send_message("❌ ไม่พบข้อมูลตู้กาชานี้ในระบบ!", ephemeral=True)
            return

        cost_seconds = box_info[0] * 60

        cursor.execute("SELECT role_id, role_name, rate, stock FROM gacha_prizes WHERE box_name = ?", (self.box_name,))
        prizes = cursor.fetchall()
        if not prizes:
            await interaction.response.send_message("❌ ตู้กาชานี้ยังไม่มีของรางวัลในระบบ!", ephemeral=True)
            return

        cursor.execute("SELECT total_time FROM users WHERE user_id = ?", (user_id,))
        user_data = cursor.fetchone()
        user_total_sec = user_data[0] if user_data else 0

        if user_id in voice_sessions:
            user_total_sec += int(time.time()) - voice_sessions[user_id]

        if user_total_sec < cost_seconds:
            await interaction.response.send_message(f"❌ เวลาออนของคุณไม่เพียงพอ! (ต้องใช้ {box_info[0]} นาที)", ephemeral=True)
            return

        available_prizes = [p for p in prizes if p[3] > 0]
        if not available_prizes:
            await interaction.response.send_message("❌ เสียใจด้วย! ของรางวัลในตู้หมดเกลี้ยงทุกชิ้นแล้ว รอแอดมินมาเติมสต๊อกก่อนนะ", ephemeral=True)
            return

        total_rate = sum(p[2] for p in available_prizes)
        roll = random.uniform(0, total_rate)
        
        current = 0
        selected = None
        for p in available_prizes:
            current += p[2]
            if roll <= current:
                selected = p
                break

        if not selected:
            selected = available_prizes[0]

        role_id, role_name = selected[0], selected[1]

        if user_id in voice_sessions:
            voice_sessions[user_id] = int(time.time())
            new_total_sec = user_total_sec - cost_seconds
            cursor.execute("INSERT OR REPLACE INTO users (user_id, total_time) VALUES (?, ?)", (user_id, new_total_sec))
        else:
            new_total_sec = user_total_sec - cost_seconds
            cursor.execute("UPDATE users SET total_time = ? WHERE user_id = ?", (new_total_sec, user_id))

        cursor.execute("UPDATE gacha_prizes SET stock = stock - 1 WHERE box_name = ? AND role_id = ?", (self.box_name, role_id))
        db.commit()

        await update_gacha_embed(interaction.guild, self.box_name)

        role = interaction.guild.get_role(role_id)
        if role:
            try:
                await interaction.user.add_roles(role)
                await interaction.response.send_message(f"🎉 ยินดีด้วย! คุณสุ่มได้ยศ **{role.name}** และระบบได้ติดยศให้คุณเรียบร้อยแล้ว!", ephemeral=True)
            except Exception:
                await interaction.response.send_message(f"🎉 สุ่มได้ยศ **{role.name}** สำเร็จ แต่บอทไม่มีสิทธิ์แจกยศ (กรุณาตรวจสอบลำดับยศของบอท)", ephemeral=True)
        else:
            await interaction.response.send_message(f"🎁 สุ่มได้ยศ **{role_name}** สำเร็จ!", ephemeral=True)


# ==================== 4. ระบบ UI สกอร์แคลน (ดรอปดาวน์เลือกแคลน & ปุ่มเลื่อนภาพ) ====================
class ClanSelectDropdown(discord.ui.Select):
    def __init__(self, clans):
        options = [discord.SelectOption(label=clan[0], value=clan[0], description=f"ดูรูปภาพสกอร์ของแคลน {clan[0]}") for clan in clans]
        super().__init__(placeholder="📂 เลือกดูรูปภาพสกอร์แคลน...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_clan = self.values[0]
        cursor.execute("SELECT id, image_url, user_id FROM clan_scores WHERE clan_name = ? ORDER BY id ASC", (selected_clan,))
        scores = cursor.fetchall()

        if not scores:
            await interaction.response.send_message(f"❌ แคลน `{selected_clan}` ยังไม่มีรูปภาพสกอร์ในระบบ", ephemeral=True)
            return

        view = ClanGalleryView(scores, selected_clan, index=0)
        embed = view.get_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class ClanSelectView(discord.ui.View):
    def __init__(self, clans):
        super().__init__(timeout=None)
        self.add_item(ClanSelectDropdown(clans))

class ClanGalleryView(discord.ui.View):
    def __init__(self, scores, clan_name, index=0):
        super().__init__(timeout=180)
        self.scores = scores
        self.clan_name = clan_name
        self.index = index
        self.update_buttons()

    def update_buttons(self):
        self.prev_button.disabled = self.index <= 0
        self.next_button.disabled = self.index >= len(self.scores) - 1

    def get_embed(self):
        score_id, image_url, user_id = self.scores[self.index]
        embed = discord.Embed(
            title=f"🛡️ สกอร์แคลน: {self.clan_name}",
            description=f"📌 **ID รูปภาพ:** `{score_id}` (ใช้อ้างอิงตอนลบรูป)\n👤 **ผู้อัปโหลด:** <@{user_id}>",
            color=discord.Color.green()
        )
        embed.set_image(url=image_url)
        embed.set_footer(text=f"รูปที่ {self.index + 1} จาก {len(self.scores)}")
        return embed

    @discord.ui.button(label="◀️ ก่อนหน้า", style=discord.ButtonStyle.blurple, custom_id="prev_score_img")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.index > 0:
            self.index -= 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="ถัดไป ▶️", style=discord.ButtonStyle.blurple, custom_id="next_score_img")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.index < len(self.scores) - 1:
            self.index += 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_embed(), view=self)


# ==================== 5. คำสั่งแอดมิน & สมาชิกทั้งหมด ====================

# --- คำสั่งกาชา (แอดมิน) ---
@bot.tree.command(name="สร้างตู้กาชา", description="[แอดมิน] สร้างห้องตู้กาชาใหม่ พร้อมกำหนดราคาและรูปภาพ GIF")
@app_commands.describe(ชื่อตู้="ชื่อระบุตู้กาชา", ใช้เวลาเล่นนาที="ใช้เวลาออนกี่นาทีต่อการสุ่ม", ลิงก์รูปภาพหรือgif="ลิงก์ GIF หรือลิงก์ตรงรูปภาพหน้าตู้")
async def create_gacha_box(interaction: discord.Interaction, ชื่อตู้: str, ใช้เวลาเล่นนาที: int, ลิงก์รูปภาพหรือgif: str = None):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"🎰 ตู้กาชา: {ชื่อตู้}", 
        description=f"⏱️ **ค่าใช้จ่าย:** ใช้เวลาออน `{ใช้เวลาเล่นนาที}` นาทีต่อการสุ่ม\n\n📦 **รายการของรางวัลในตู้:**\n❌ ยังไม่มีของรางวัลในตู้ (รอแอดมินเติม)", 
        color=discord.Color.gold()
    )
    if ลิงก์รูปภาพหรือgif:
        embed.set_image(url=ลิงก์รูปภาพหรือgif)

    view = GachaView(ชื่อตู้)
    await interaction.response.send_message(f"✅ สร้างตู้กาชา `{ชื่อตู้}` สำเร็จ!", ephemeral=True)
    message = await interaction.channel.send(embed=embed, view=view)

    cursor.execute("""
    INSERT OR REPLACE INTO gacha_boxes_info (box_name, cost_minutes, image_url, channel_id, message_id) 
    VALUES (?, ?, ?, ?, ?)
    """, (ชื่อตู้, ใช้เวลาเล่นนาที, ลิงก์รูปภาพหรือgif, interaction.channel.id, message.id))
    db.commit()

@bot.tree.command(name="เพิ่มของรางวัลในตู้", description="[แอดมิน] เพิ่มยศ เรทเปอร์เซ็นต์ และสต๊อก (อัปเดตหน้าตู้ Real-time)")
@app_commands.describe(ชื่อตู้="ชื่อตู้กาชาที่ต้องการใส่ของ", ยศรางวัล="เลือกยศที่ต้องการเพิ่ม", เรทเปอร์เซ็นต์="โอกาสออก เช่น 5.5 หรือ 50", จำนวนสต๊อก="จำนวนชิ้นที่มีในตู้")
async def add_gacha_prize(interaction: discord.Interaction, ชื่อตู้: str, ยศรางวัล: discord.Role, เรทเปอร์เซ็นต์: float, จำนวนสต๊อก: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)
        return

    cursor.execute("SELECT box_name FROM gacha_boxes_info WHERE box_name = ?", (ชื่อตู้,))
    if not cursor.fetchone():
        await interaction.response.send_message(f"❌ ไม่พบตู้กาชาชื่อ `{ชื่อตู้}`", ephemeral=True)
        return

    cursor.execute("INSERT OR REPLACE INTO gacha_prizes (box_name, role_id, role_name, rate, stock) VALUES (?, ?, ?, ?, ?)",
                   (ชื่อตู้, ยศรางวัล.id, ยศรางวัล.name, เรทเปอร์เซ็นต์, จำนวนสต๊อก))
    db.commit()

    await update_gacha_embed(interaction.guild, ชื่อตู้)
    await interaction.response.send_message(f"✅ เพิ่มยศ `{ยศรางวัล.name}` ลงในตู้ `{ชื่อตู้}` เรียบร้อยแล้ว!", ephemeral=True)

@bot.tree.command(name="เติมสต๊อก", description="[แอดมิน] เติมสต๊อกยศในตู้กาชา")
@app_commands.describe(ชื่อตู้="ชื่อตู้กาชา", ยศรางวัล="เลือกยศที่ต้องการเติม", จำนวนที่ต้องการเติม="จำนวนชิ้นที่ต้องการเพิ่ม")
async def add_stock(interaction: discord.Interaction, ชื่อตู้: str, ยศรางวัล: discord.Role, จำนวนที่ต้องการเติม: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)
        return

    cursor.execute("SELECT stock FROM gacha_prizes WHERE box_name = ? AND role_id = ?", (ชื่อตู้, ยศรางวัล.id))
    result = cursor.fetchone()
    if not result:
        await interaction.response.send_message(f"❌ ไม่พบยศนี้ในตู้กาชา `{ชื่อตู้}`", ephemeral=True)
        return

    new_stock = result[0] + จำนวนที่ต้องการเติม
    cursor.execute("UPDATE gacha_prizes SET stock = ? WHERE box_name = ? AND role_id = ?", (new_stock, ชื่อตู้, ยศรางวัล.id))
    db.commit()

    await update_gacha_embed(interaction.guild, ชื่อตู้)
    await interaction.response.send_message(f"✅ เติมสต๊อกยศ `{ยศรางวัล.name}` เป็น `{new_stock}` ชิ้นแล้ว!", ephemeral=True)

@bot.tree.command(name="ลบของรางวัลในตู้", description="[แอดมิน] ลบยศออกจากตู้กาชา")
@app_commands.describe(ชื่อตู้="ชื่อตู้กาชา", ยศรางวัล="เลือกยศที่ต้องการลบ")
async def delete_gacha_prize(interaction: discord.Interaction, ชื่อตู้: str, ยศรางวัล: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)
        return

    cursor.execute("DELETE FROM gacha_prizes WHERE box_name = ? AND role_id = ?", (ชื่อตู้, ยศรางวัล.id))
    db.commit()

    await update_gacha_embed(interaction.guild, ชื่อตู้)
    await interaction.response.send_message(f"✅ ลบยศ `{ยศรางวัล.name}` ออกจากตู้เรียบร้อยแล้ว", ephemeral=True)

@bot.tree.command(name="แก้ไขเวลากาชา", description="[แอดมิน] เปลี่ยนแปลงเวลาที่ใช้ในการสุ่มของตู้กาชา")
@app_commands.describe(ชื่อตู้="ชื่อตู้กาชา", นาทีใหม่="จำนวนนาทีใหม่ต่อการสุ่ม")
async def edit_gacha_time(interaction: discord.Interaction, ชื่อตู้: str, นาทีใหม่: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)
        return

    cursor.execute("UPDATE gacha_boxes_info SET cost_minutes = ? WHERE box_name = ?", (นาทีใหม่, ชื่อตู้))
    db.commit()

    await update_gacha_embed(interaction.guild, ชื่อตู้)
    await interaction.response.send_message(f"✅ แก้ไขราคาตู้ `{ชื่อตู้}` เป็น `{นาทีใหม่}` นาทีเรียบร้อย", ephemeral=True)

@bot.tree.command(name="เพิ่มเวลาออน", description="[แอดมิน] เพิ่มเวลาออนให้สมาชิก")
@app_commands.describe(สมาชิก="เลือกผู้ใช้งาน", จำนวนนาที="จำนวนนาทีที่ต้องการเพิ่ม")
async def add_total_time(interaction: discord.Interaction, สมาชิก: discord.Member, จำนวนนาที: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)
        return

    add_seconds = จำนวนนาที * 60
    cursor.execute("SELECT total_time FROM users WHERE user_id = ?", (สมาชิก.id,))
    result = cursor.fetchone()

    if result:
        new_time = result[0] + add_seconds
        cursor.execute("UPDATE users SET total_time = ? WHERE user_id = ?", (new_time, สมาชิก.id))
    else:
        cursor.execute("INSERT INTO users (user_id, total_time) VALUES (?, ?)", (สมาชิก.id, add_seconds))
    db.commit()

    await interaction.response.send_message(f"✅ เพิ่มเวลาออนให้ {สมาชิก.mention} จำนวน `{จำนวนนาที}` นาทีแล้ว", ephemeral=True)

@bot.tree.command(name="ลบเวลาผู้คน", description="[แอดมิน] หักเวลาออนของสมาชิก")
@app_commands.describe(สมาชิก="เลือกผู้ใช้งาน", จำนวนนาทีที่ต้องการลบ="จำนวนนาทีที่ต้องการหัก")
async def remove_total_time(interaction: discord.Interaction, สมาชิก: discord.Member, จำนวนนาทีที่ต้องการลบ: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)
        return

    remove_seconds = จำนวนนาทีที่ต้องการลบ * 60
    cursor.execute("SELECT total_time FROM users WHERE user_id = ?", (สมาชิก.id,))
    result = cursor.fetchone()

    if not result:
        await interaction.response.send_message("❌ สมาชิกคนนี้ไม่มีข้อมูลเวลาออน", ephemeral=True)
        return

    new_time = max(0, result[0] - remove_seconds)
    cursor.execute("UPDATE users SET total_time = ? WHERE user_id = ?", (new_time, สมาชิก.id))
    db.commit()

    await interaction.response.send_message(f"✅ หักเวลา {สมาชิก.mention} ออก `{จำนวนนาทีที่ต้องการลบ}` นาทีแล้ว", ephemeral=True)


# --- คำสั่งระบบสกอร์แคลน (แอดมิน & สมาชิก) ---
@bot.tree.command(name="สร้างสกอแคลน", description="[แอดมิน] สร้างหัวข้อแคลนใหม่ในระบบสกอร์")
@app_commands.describe(ชื่อแคลน="ชื่อแคลนที่ต้องการสร้าง")
async def create_clan(interaction: discord.Interaction, ชื่อแคลน: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)
        return

    try:
        cursor.execute("INSERT INTO clans (clan_name) VALUES (?)", (ชื่อแคลน,))
        db.commit()
        await interaction.response.send_message(f"✅ สร้างแคลน `{ชื่อแคลน}` ในระบบสกอร์เรียบร้อยแล้ว!", ephemeral=True)
        await update_clan_dashboard(interaction.guild)
    except sqlite3.IntegrityError:
        await interaction.response.send_message(f"❌ มีแคลน `{ชื่อแคลน}` อยู่ในระบบแล้ว", ephemeral=True)

@bot.tree.command(name="ลบแคลน", description="[แอดมิน] ลบชื่อแคลนและรูปภาพทั้งหมดในแคลนนั้นทิ้ง")
@app_commands.describe(ชื่อแคลน="ชื่อแคลนที่ต้องการลบ")
async def delete_clan(interaction: discord.Interaction, ชื่อแคลน: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)
        return

    cursor.execute("DELETE FROM clans WHERE clan_name = ?", (ชื่อแคลน,))
    cursor.execute("DELETE FROM clan_scores WHERE clan_name = ?", (ชื่อแคลน,))
    db.commit()

    await interaction.response.send_message(f"⚠️ ลบแคลน `{ชื่อแคลน}` และข้อมูลรูปภาพทั้งหมดเรียบร้อยแล้ว", ephemeral=True)
    await update_clan_dashboard(interaction.guild)

@bot.tree.command(name="แสดงผลรวม", description="[แอดมิน] สร้างหน้าต่างรายงานสกอร์แคลนแบบ Real-time พร้อมดรอปดาวน์")
async def show_clan_dashboard(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)
        return

    embed = discord.Embed(
        title="📊 ระบบสรุปผลและรูปภาพสกอร์แคลน (Vegas Clan Score)",
        description="กำลังโหลดข้อมูล...",
        color=discord.Color.blue()
    )

    await interaction.response.send_message("✅ สร้างหน้าต่างรายงานผลรวมเรียบร้อยแล้ว!", ephemeral=True)
    message = await interaction.channel.send(embed=embed)

    cursor.execute("INSERT OR REPLACE INTO clan_dashboard (guild_id, channel_id, message_id) VALUES (?, ?, ?)",
                   (interaction.guild.id, interaction.channel.id, message.id))
    db.commit()
    await update_clan_dashboard(interaction.guild)

@bot.tree.command(name="เพิ่มรูป", description="อัปโหลดรูปภาพสกอร์แคลนเข้าสู่ระบบ")
@app_commands.describe(ชื่อแคลน="เลือกชื่อแคลน", รูปภาพสกอร์="แนบไฟล์รูปภาพสกอร์การแข่ง")
async def add_clan_score(interaction: discord.Interaction, ชื่อแคลน: str, รูปภาพสกอร์: discord.Attachment):
    cursor.execute("SELECT clan_name FROM clans WHERE clan_name = ?", (ชื่อแคลน,))
    if not cursor.fetchone():
        await interaction.response.send_message(f"❌ ไม่พบแคลน `{ชื่อแคลน}` ในระบบ (กรุณาให้แอดมินสร้างแคลนก่อน)", ephemeral=True)
        return

    cursor.execute("INSERT INTO clan_scores (clan_name, user_id, image_url) VALUES (?, ?, ?)",
                   (ชื่อแคลน, interaction.user.id, รูปภาพสกอร์.url))
    db.commit()

    await interaction.response.send_message(f"✅ บันทึกรูปภาพสกอร์ของแคลน `{ชื่อแคลน}` สำเร็จเรียบร้อยแล้ว!", ephemeral=True)
    await update_clan_dashboard(interaction.guild)

@bot.tree.command(name="ลบรูปภาพ", description="ลบรูปภาพสกอร์แคลนของคุณด้วยรหัสรูป (ID)")
@app_commands.describe(รหัสรูปภาพ_id="รหัส ID ของรูปภาพที่ต้องการลบ")
async def delete_clan_score(interaction: discord.Interaction, รหัสรูปภาพ_id: int):
    cursor.execute("SELECT user_id, clan_name FROM clan_scores WHERE id = ?", (รหัสรูปภาพ_id,))
    score = cursor.fetchone()

    if not score:
        await interaction.response.send_message(f"❌ ไม่พบรูปภาพที่มีรหัส ID `{รหัสรูปภาพ_id}` นี้ในระบบ", ephemeral=True)
        return

    owner_id, clan_name = score

    if interaction.user.id != owner_id and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ลบรูปภาพนี้ (ลบได้เฉพาะรูปที่คุณอัปโหลดเท่านั้น)", ephemeral=True)
        return

    cursor.execute("DELETE FROM clan_scores WHERE id = ?", (รหัสรูปภาพ_id,))
    db.commit()

    await interaction.response.send_message(f"🗑️ ลบรูปภาพ ID `{รหัสรูปภาพ_id}` ออกจากระบบเรียบร้อยแล้ว", ephemeral=True)
    await update_clan_dashboard(interaction.guild)


# ==================== บรรทัดรันระบบหลัก ====================
if __name__ == "__main__":
    keep_alive()
    bot.run(os.getenv("DISCORD_TOKEN"))
