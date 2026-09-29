import os
import time
import random
from threading import Thread
from flask import Flask
import discord
from discord import app_commands
from discord.ext import commands
import firebase_admin
from firebase_admin import credentials, firestore

# กำหนดเส้นทางอ่านไฟล์จาก Secret Files ของ Render โดยตรง
secret_file_path = "/etc/secrets/serviceAccountKey.json"

if not firebase_admin._apps:
    if os.path.exists(secret_file_path):
        cred = credentials.Certificate(secret_file_path)
        firebase_admin.initialize_app(cred)
    elif os.path.exists("serviceAccountKey.json"):
        cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(cred)
    else:
        firebase_admin.initialize_app()

db = firestore.client()

# ==================== ระบบเว็บเซิร์ฟเวอร์จำลอง (สำหรับรันบน Render 24 ชม.) ====================
app = Flask('')

@app.route('/')
def home():
    return "Vegas Bot is Online with Firebase!"

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

voice_sessions = {}

def format_time(seconds):
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

@bot.event
async def on_ready():
    print(f" Vegas BOT ออนไลน์แล้ว (เชื่อมต่อ Firebase เรียบร้อย): {bot.user.name}")
    try:
        synced = await bot.tree.sync()
        print(f"ซิงค์ Slash Commands ทั้งหมด {len(synced)} คำสั่งเรียบร้อยแล้ว")
    except Exception as e:
        print(e)

# ==================== คำสั่งพิเศษสำหรับซิงค์คำสั่งด่วน ====================
@bot.tree.command(name="sync", description="[แอดมิน] ซิงค์คำสั่งทั้งหมดของบอท")
async def sync_commands(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)
        return
     
    synced = await bot.tree.sync()
    await interaction.response.send_message(f"✅ ซิงค์คำสั่ง Slash Commands สำเร็จทั้งหมด {len(synced)} คำสั่ง!", ephemeral=True)

# ==================== ฟังก์ชันช่วยอัปเดตหน้าตู้กาชาแบบ Real-time ====================
async def update_gacha_embed(guild: discord.Guild, box_name: str):
    box_ref = db.collection("gacha_boxes_info").document(box_name)
    box_doc = box_ref.get()
    if not box_doc.exists:
        return

    box_info = box_doc.to_dict()
    cost_minutes = box_info.get("cost_minutes", 60)
    image_url = box_info.get("image_url")
    channel_id = box_info.get("channel_id")
    message_id = box_info.get("message_id")

    if not channel_id or not message_id:
        return

    channel = guild.get_channel(channel_id)
    if not channel:
        return

    try:
        message = await channel.fetch_message(message_id)
    except Exception:
        return

    prizes_ref = box_ref.collection("prizes").stream()
    prizes = []
    for p in prizes_ref:
        p_data = p.to_dict()
        prizes.append((p_data.get("role_name"), p_data.get("rate"), p_data.get("stock")))

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
    dash_ref = db.collection("clan_dashboard").document(str(guild.id))
    dash_doc = dash_ref.get()
    if not dash_doc.exists:
        return

    dash_info = dash_doc.to_dict()
    channel_id = dash_info.get("channel_id")
    message_id = dash_info.get("message_id")

    channel = guild.get_channel(channel_id)
    if not channel:
        return

    try:
        message = await channel.fetch_message(message_id)
    except Exception:
        return

    clans_ref = db.collection("clans").stream()
    clans = [c.id for c in clans_ref]

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
        for c_name in clans:
            scores_ref = db.collection("clans").document(c_name).collection("scores").stream()
            count = sum(1 for _ in scores_ref)
            desc += f"🛡 **{c_name}**: มีรูปภาพสะสม `{count}` รูป\n"
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

            user_ref = db.collection("users").document(str(member.id))
            user_doc = user_ref.get()
            
            if user_doc.exists:
                new_total = user_doc.to_dict().get("total_time", 0) + duration
            else:
                new_total = duration

            user_ref.set({"total_time": new_total}, merge=True)

@bot.tree.command(name="เช็คเวลาออน", description="ตรวจสอบชั่วโมงเวลาออนไลน์ของคุณแบบเรียลไทม์")
async def check_time(interaction: discord.Interaction):
    user_id_str = str(interaction.user.id)
    user_ref = db.collection("users").document(user_id_str)
    user_doc = user_ref.get()
    
    total_sec = user_doc.to_dict().get("total_time", 0) if user_doc.exists else 0
    if interaction.user.id in voice_sessions:
        total_sec += int(time.time()) - voice_sessions[interaction.user.id]

    embed = discord.Embed(title="📊 ข้อมูลเวลาออนไลน์ของคุณ", color=discord.Color.blue())
    embed.add_field(name="⏱️️ เวลาออนทั้งหมด (ใช้เป็นแต้มสุ่มกาชา)", value=f"`{format_time(total_sec)}`", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ==================== 3. ระบบ UI กาชา ====================
class GachaView(discord.ui.View):
    def __init__(self, box_name):
        super().__init__(timeout=None)
        self.box_name = box_name

    @discord.ui.button(label="🎰 กดสุ่มกาชา", style=discord.ButtonStyle.green, custom_id="spin_gacha_btn")
    async def spin_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        user_id_str = str(user_id)
        
        box_ref = db.collection("gacha_boxes_info").document(self.box_name)
        box_doc = box_ref.get()
        if not box_doc.exists:
            await interaction.response.send_message("❌ ไม่พบข้อมูลตู้กาชานี้ในระบบ!", ephemeral=True)
            return

        box_info = box_doc.to_dict()
        cost_seconds = box_info.get("cost_minutes", 60) * 60

        prizes_ref = list(box_ref.collection("prizes").stream())
        if not prizes_ref:
            await interaction.response.send_message("❌ ตู้กาชานี้ยังไม่มีของรางวัลในระบบ!", ephemeral=True)
            return

        user_ref = db.collection("users").document(user_id_str)
        user_doc = user_ref.get()
        user_total_sec = user_doc.to_dict().get("total_time", 0) if user_doc.exists else 0

        if user_id in voice_sessions:
            user_total_sec += int(time.time()) - voice_sessions[user_id]

        if user_total_sec < cost_seconds:
            await interaction.response.send_message(f"❌ เวลาออนของคุณไม่เพียงพอ! (ต้องใช้ {box_info.get('cost_minutes', 60)} นาที)", ephemeral=True)
            return

        prizes = [p.to_dict() for p in prizes_ref]
        available_prizes = [p for p in prizes if p.get("stock", 0) > 0]
        if not available_prizes:
            await interaction.response.send_message("❌ เสียใจด้วย! ของรางวัลในตู้หมดเกลี้ยงทุกชิ้นแล้ว รอแอดมินมาเติมสต๊อกก่อนนะ", ephemeral=True)
            return

        total_rate = sum(p.get("rate", 0) for p in available_prizes)
        roll = random.uniform(0, total_rate)
        
        current = 0
        selected = None
        for p in available_prizes:
            current += p.get("rate", 0)
            if roll <= current:
                selected = p
                break

        if not selected:
            selected = available_prizes[0]

        role_id, role_name = selected.get("role_id"), selected.get("role_name")

        new_total_sec = user_total_sec - cost_seconds
        if user_id in voice_sessions:
            voice_sessions[user_id] = int(time.time())
        user_ref.set({"total_time": new_total_sec}, merge=True)

        prize_doc_ref = box_ref.collection("prizes").document(str(role_id))
        current_stock = selected.get("stock", 1)
        prize_doc_ref.update({"stock": current_stock - 1})

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


# ==================== 4. ระบบ UI สกอร์แคลน ====================
class ClanSelectDropdown(discord.ui.Select):
    def __init__(self, clans):
        options = [discord.SelectOption(label=c, value=c, description=f"ดูรูปภาพสกอร์ของแคลน {c}") for c in clans]
        super().__init__(placeholder="📂 เลือกดูรูปภาพสกอร์แคลน...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_clan = self.values[0]
        scores_ref = db.collection("clans").document(selected_clan).collection("scores").order_by("timestamp").stream()
        scores = [(s.id, s.to_dict()) for s in scores_ref]

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
        score_doc_id, score_data = self.scores[self.index]
        image_url = score_data.get("image_url")
        user_id = score_data.get("user_id")
        
        embed = discord.Embed(
            title=f"🛡️ สกอร์แคลน: {self.clan_name}",
            description=f"📌 **ID รูปภาพ:** `{score_doc_id}` (ใช้อ้างอิงตอนลบรูป)\n👤 **ผู้อัปโหลด:** <@{user_id}>",
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

    db.collection("gacha_boxes_info").document(ชื่อตู้).set({
        "cost_minutes": ใช้เวลาเล่นนาที,
        "image_url": ลิงก์รูปภาพหรือgif,
        "channel_id": interaction.channel.id,
        "message_id": message.id
    })

@bot.tree.command(name="เพิ่มของรางวัลในตู้", description="[แอดมิน] เพิ่มยศ เรทเปอร์เซ็นต์ และสต๊อก")
@app_commands.describe(ชื่อตู้="ชื่อตู้กาชาที่ต้องการใส่ของ", ยศรางวัล="เลือกยศที่ต้องการเพิ่ม", เรทเปอร์เซ็นต์="โอกาสออก เช่น 5.5 หรือ 50", จำนวนสต๊อก="จำนวนชิ้นที่มีในตู้")
async def add_gacha_prize(interaction: discord.Interaction, ชื่อตู้: str, ยศรางวัล: discord.Role, เรทเปอร์เซ็นต์: float, จำนวนสต๊อก: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)
        return

    box_ref = db.collection("gacha_boxes_info").document(ชื่อตู้)
    if not box_ref.get().exists:
        await interaction.response.send_message(f"❌ ไม่พบตู้กาชาชื่อ `{ชื่อตู้}`", ephemeral=True)
        return

    box_ref.collection("prizes").document(str(ยศรางวัล.id)).set({
        "role_id": ยศรางวัล.id,
        "role_name": ยศรางวัล.name,
        "rate": เรทเปอร์เซ็นต์,
        "stock": จำนวนสต๊อก
    })

    await update_gacha_embed(interaction.guild, ชื่อตู้)
    await interaction.response.send_message(f"✅ เพิ่มยศ `{ยศรางวัล.name}` ลงในตู้ `{ชื่อตู้}` เรียบร้อยแล้ว!", ephemeral=True)

@bot.tree.command(name="เติมสต๊อก", description="[แอดมิน] เติมสต๊อกยศในตู้กาชา")
@app_commands.describe(ชื่อตู้="ชื่อตู้กาชา", ยศรางวัล="เลือกยศที่ต้องการเติม", จำนวนที่ต้องการเติม="จำนวนชิ้นที่ต้องการเพิ่ม")
async def add_stock(interaction: discord.Interaction, ชื่อตู้: str, ยศรางวัล: discord.Role, จำนวนที่ต้องการเติม: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)
        return

    prize_ref = db.collection("gacha_boxes_info").document(ชื่อตู้).collection("prizes").document(str(ยศรางวัล.id))
    prize_doc = prize_ref.get()
    if not prize_doc.exists:
        await interaction.response.send_message(f"❌ ไม่พบยศนี้ในตู้กาชา `{ชื่อตู้}`", ephemeral=True)
        return

    new_stock = prize_doc.to_dict().get("stock", 0) + จำนวนที่ต้องการเติม
    prize_ref.update({"stock": new_stock})

    await update_gacha_embed(interaction.guild, ชื่อตู้)
    await interaction.response.send_message(f"✅ เติมสต๊อกยศ `{ยศรางวัล.name}` เป็น `{new_stock}` ชิ้นแล้ว!", ephemeral=True)

@bot.tree.command(name="ลบของรางวัลในตู้", description="[แอดมิน] ลบยศออกจากตู้กาชา")
@app_commands.describe(ชื่อตู้="ชื่อตู้กาชา", ยศรางวัล="เลือกยศที่ต้องการลบ")
async def delete_gacha_prize(interaction: discord.Interaction, ชื่อตู้: str, ยศรางวัล: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)
        return

    db.collection("gacha_boxes_info").document(ชื่อตู้).collection("prizes").document(str(ยศรางวัล.id)).delete()
    await update_gacha_embed(interaction.guild, ชื่อตู้)
    await interaction.response.send_message(f"✅ ลบยศ `{ยศรางวัล.name}` ออกจากตู้เรียบร้อยแล้ว", ephemeral=True)

@bot.tree.command(name="แก้ไขเวลากาชา", description="[แอดมิน] เปลี่ยนแปลงเวลาที่ใช้ในการสุ่มของตู้กาชา")
@app_commands.describe(ชื่อตู้="ชื่อตู้กาชา", นาทีใหม่="จำนวนนาทีใหม่ต่อการสุ่ม")
async def edit_gacha_time(interaction: discord.Interaction, ชื่อตู้: str, นาทีใหม่: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)
        return

    db.collection("gacha_boxes_info").document(ชื่อตู้).update({"cost_minutes": นาทีใหม่})
    await update_gacha_embed(interaction.guild, ชื่อตู้)
    await interaction.response.send_message(f"✅ แก้ไขราคาตู้ `{ชื่อตู้}` เป็น `{นาทีใหม่}` นาทีเรียบร้อย", ephemeral=True)

@bot.tree.command(name="เพิ่มเวลาออน", description="[แอดมิน] เพิ่มเวลาออนให้สมาชิก")
@app_commands.describe(สมาชิก="เลือกผู้ใช้งาน", จำนวนนาที="จำนวนนาทีที่ต้องการเพิ่ม")
async def add_total_time(interaction: discord.Interaction, สมาชิก: discord.Member, จำนวนนาที: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)
        return

    add_seconds = จำนวนนาที * 60
    user_ref = db.collection("users").document(str(สมาชิก.id))
    user_doc = user_ref.get()

    if user_doc.exists:
        new_time = user_doc.to_dict().get("total_time", 0) + add_seconds
    else:
        new_time = add_seconds

    user_ref.set({"total_time": new_time}, merge=True)
    await interaction.response.send_message(f"✅ เพิ่มเวลาออนให้ {สมาชิก.mention} จำนวน `{จำนวนนาที}` นาทีแล้ว", ephemeral=True)

@bot.tree.command(name="ลบเวลาผู้คน", description="[แอดมิน] หักเวลาออนของสมาชิก")
@app_commands.describe(สมาชิก="เลือกผู้ใช้งาน", จำนวนนาทีที่ต้องการลบ="จำนวนนาทีที่ต้องการหัก")
async def remove_total_time(interaction: discord.Interaction, สมาชิก: discord.Member, จำนวนนาทีที่ต้องการลบ: int):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)
        return

    remove_seconds = จำนวนนาทีที่ต้องการลบ * 60
    user_ref = db.collection("users").document(str(สมาชิก.id))
    user_doc = user_ref.get()

    if not user_doc.exists:
        await interaction.response.send_message(f"❌ สมาชิกคนนี้ไม่มีข้อมูลเวลาออน", ephemeral=True)
        return

    new_time = max(0, user_doc.to_dict().get("total_time", 0) - remove_seconds)
    user_ref.set({"total_time": new_time}, merge=True)
    await interaction.response.send_message(f"✅ หักเวลาออนของ {สมาชิก.mention} ออก `{จำนวนนาทีที่ต้องการลบ}` นาทีเรียบร้อย", ephemeral=True)

# ==================== รันระบบทั้งหมด ====================
if __name__ == "__main__":
    keep_alive()
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ ไม่พบ Environment Variable 'DISCORD_BOT_TOKEN' กรุณาตั้งค่า Token ของบอทใน Render ก่อนเริ่มใช้งาน")
