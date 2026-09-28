import os
import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
import random
import time

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ==================== 1. ระบบฐานข้อมูล SQLite ====================
db = sqlite3.connect("bot_database.db")
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    total_time INTEGER DEFAULT 0
)
""")

# ตารางข้อมูลตู้กาชา (เพิ่ม channel_id และ message_id เพื่อใช้อัปเดต Embed)
cursor.execute("""
CREATE TABLE IF NOT EXISTS gacha_boxes_info (
    box_name TEXT PRIMARY KEY,
    cost_minutes INTEGER DEFAULT 60,
    image_url TEXT,
    channel_id INTEGER,
    message_id INTEGER
)
""")

# ตารางเก็บของรางวัลในตู้ (ไม่จำกัดจำนวนยศต่อตู้)
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
db.commit()

voice_sessions = {}

def format_time(seconds):
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

@bot.event
async def on_ready():
    print(f"บอทออนไลน์แล้ว: {bot.user.name}")
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

    # ดึงรายการของรางวัลทั้งหมดในตู้
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

# ==================== 3. ระบบ UI ปุ่มกดสุ่มกาชา ====================
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

        # อัปเดตหน้าตู้แบบ Real-time ทันทีที่มีคนสุ่มไป (สต๊อกลด)
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

# ==================== 4. คำสั่งแอดมิน ====================
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
    
    # ส่งข้อความตู้กาชาลงในห้อง
    message = await interaction.channel.send(embed=embed, view=view)

    # บันทึกข้อมูลลง Database พร้อมเก็บ Message ID เพื่อใช้อัปเดต Real-time
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
        await interaction.response.send_message(f"❌ ไม่พบตู้กาชาชื่อ `{ชื่อตู้}` (กรุณาสร้างตู้ด้วยคำสั่ง /สร้างตู้กาชา ก่อน)", ephemeral=True)
        return

    cursor.execute("INSERT OR REPLACE INTO gacha_prizes (box_name, role_id, role_name, rate, stock) VALUES (?, ?, ?, ?, ?)",
                   (ชื่อตู้, ยศรางวัล.id, ยศรางวัล.name, เรทเปอร์เซ็นต์, จำนวนสต๊อก))
    db.commit()

    # สั่งอัปเดตหน้าตู้กาชาแบบ Real-time ทันที
    await update_gacha_embed(interaction.guild, ชื่อตู้)

    await interaction.response.send_message(f"✅ เพิ่มยศ `{ยศรางวัล.name}` (เรท: `{เรทเปอร์เซ็นต์}%`, สต๊อก: `{จำนวนสต๊อก}` ชิ้น) ลงในตู้ `{ชื่อตู้}` และอัปเดตหน้าตู้เรียบร้อยแล้ว!", ephemeral=True)

@bot.tree.command(name="เติมสต๊อก", description="[แอดมิน] เติมสต๊อกยศในตู้กาชา (อัปเดตหน้าตู้ Real-time)")
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

    # สั่งอัปเดตหน้าตู้กาชาแบบ Real-time ทันที
    await update_gacha_embed(interaction.guild, ชื่อตู้)

    await interaction.response.send_message(f"✅ เติมสต๊อกยศ `{ยศรางวัล.name}` สำเร็จ! ตอนนี้เหลือ `{new_stock}` ชิ้น (หน้าตู้ถูกอัปเดตแล้ว)", ephemeral=True)

# ==================== 5. ระบบคำสั่งลบของรางวัลในตู้ (Real-time) ====================
@bot.tree.command(name="ลบของรางวัลในตู้", description="[แอดมิน] ลบยศหรือของรางวัลออกจากตู้กาชา และอัปเดตหน้าตู้แบบเรียลไทม์")
@app_commands.describe(ชื่อตู้="ชื่อตู้กาชาที่ต้องการลบของ", ยศรางวัล="เลือกยศที่ต้องการลบออกจากตู้")
async def delete_gacha_prize(interaction: discord.Interaction, ชื่อตู้: str, ยศรางวัล: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)
        return

    # เช็คว่ามีของรางวัลนี้อยู่ในตู้นั้นจริงๆ หรือไม่
    cursor.execute("SELECT * FROM gacha_prizes WHERE box_name = ? AND role_id = ?", (ชื่อตู้, ยศรางวัล.id))
    prize = cursor.fetchone()
    if not prize:
        await interaction.response.send_message(f"❌ ไม่พบยศ `{ยศรางวัล.name}` ในตู้กาชา `{ชื่อตู้}`", ephemeral=True)
        return

    # ลบข้อมูลออกจากฐานข้อมูล
    cursor.execute("DELETE FROM gacha_prizes WHERE box_name = ? AND role_id = ?", (ชื่อตู้, ยศรางวัล.id))
    db.commit()

    # สั่งอัปเดตหน้าตู้กาชาแบบ Real-time ทันที
    await update_gacha_embed(interaction.guild, ชื่อตู้)

    await interaction.response.send_message(f"✅ ลบยศ `{ยศรางวัล.name}`ออกจากตู้ `{ชื่อตู้}` สำเร็จ! (หน้าตู้ถูกอัปเดตเรียบร้อยแล้ว)", ephemeral=True)

@bot.tree.command(name="เพิ่มเวลาออน", description="[สำหรับแอดมิน] เพิ่มเวลาออนให้สมาชิก")
@app_commands.describe(สมาชิก="เลือกผู้ใช้งาน", จำนวนนาที="จำนวนนาทีที่ต้องการเพิ่ม")
async def add_total_time(interaction: discord.Interaction,สมาชิก: discord.Member, จำนวนนาที: int):
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

    await interaction.response.send_message(f"✅ เพิ่มเวลาออนให้ {สมาชิก.mention} จำนวน `{จำนวนนาที}` นาทีเรียบร้อยแล้ว!", ephemeral=True)

bot.run(os.getenv("DISCORD_TOKEN"))
