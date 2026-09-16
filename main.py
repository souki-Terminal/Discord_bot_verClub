import discord
from discord.ext import commands, tasks
import aiosqlite
import os
from dotenv import load_dotenv
from datetime import datetime
from zoneinfo import ZoneInfo

# .envファイルから環境変数を読み込む
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
ADMIN_CHANNEL_ID = int(os.getenv('ADMIN_CHANNEL_ID', 0))
ATTENDANCE_CHANNEL_ID = int(os.getenv('ATTENDANCE_CHANNEL_ID', 0))
ROOM_STATUS_CHANNEL_ID = int(os.getenv('ROOM_STATUS_CHANNEL_ID', 0))
ADMIN_ROLE_ID = int(os.getenv('ADMIN_ROLE_ID', 0)) # 任意: 管理操作を許可する役職ID


DB_FILE = 'bot_database.db'

# データベース初期化関数
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        # イベント(出欠)テーブル
        await db.execute('''
            CREATE TABLE IF NOT EXISTS events (
                message_id INTEGER PRIMARY KEY,
                name TEXT,
                date TEXT,
                event_date TEXT
            )
        ''')
        # 古いDBへの対応として、カラム追加を試みる (既に存在する場合はエラーになるため無視)
        try:
            await db.execute('ALTER TABLE events ADD COLUMN event_date TEXT')
        except Exception:
            pass
        # 出欠状況テーブル
        await db.execute('''
            CREATE TABLE IF NOT EXISTS attendances (
                message_id INTEGER,
                user_id INTEGER,
                user_name TEXT,
                status TEXT,
                PRIMARY KEY (message_id, user_id)
            )
        ''')
        # 部屋テーブル
        await db.execute('''
            CREATE TABLE IF NOT EXISTS rooms (
                name TEXT PRIMARY KEY,
                is_open INTEGER,
                last_updated TEXT
            )
        ''')
        # 部屋パネルメッセージID保存用
        await db.execute('''
            CREATE TABLE IF NOT EXISTS room_panel (
                id INTEGER PRIMARY KEY,
                message_id INTEGER
            )
        ''')
        await db.commit()

# --- UI コンポーネント (Persistent Views & Modals) ---

# 出欠入力ボタンのView
class AttendanceView(discord.ui.View):
    def __init__(self):
        # timeout=None にすることで、Bot再起動後もボタンが機能し続ける (Persistent View)
        super().__init__(timeout=None)

    async def update_attendance(self, interaction: discord.Interaction, status: str):
        # ボタンを押した際の応答を素早く返す (API制限回避のため)
        await interaction.response.defer(ephemeral=True)
        
        today_str = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d")
        
        async with aiosqlite.connect(DB_FILE) as db:
            # イベントの開催日をチェック
            async with db.execute('SELECT event_date FROM events WHERE message_id = ?', (interaction.message.id,)) as cursor:
                row = await cursor.fetchone()
                if row and row[0]:
                    event_date = row[0]
                    # 開催日前なら登録ブロックしてパネルを削除
                    if today_str < event_date:
                        await interaction.message.delete()
                        await interaction.followup.send(f"このイベント（{event_date}）は本日ではないため、古いパネルを削除しました。", ephemeral=True)
                        return

            # データベースの出欠情報を更新 (なければ挿入)
            await db.execute('''
                INSERT INTO attendances (message_id, user_id, user_name, status)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(message_id, user_id) DO UPDATE SET status=excluded.status
            ''', (interaction.message.id, interaction.user.id, interaction.user.display_name, status))
            await db.commit()
            
        # ユーザーには一時的なメッセージで通知
        await interaction.followup.send(f"あなたの出欠を「{status}」で登録しました！\n※一覧への反映には数秒かかる場合があります。", ephemeral=True)

    @discord.ui.button(label="出席", style=discord.ButtonStyle.success, emoji="⭕", custom_id="attend_yes")
    async def btn_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_attendance(interaction, "出席")

    @discord.ui.button(label="欠席", style=discord.ButtonStyle.danger, emoji="❌", custom_id="attend_no")
    async def btn_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_attendance(interaction, "欠席")

    @discord.ui.button(label="遅刻/未定", style=discord.ButtonStyle.secondary, emoji="🔺", custom_id="attend_maybe")
    async def btn_maybe(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.update_attendance(interaction, "遅刻/未定")

# 部屋状態切り替え用セレクトメニューを含むView
# セレクトメニューを使うことで、動的に増減する部屋にも固定のcustom_idで対応可能
class RoomStatusSelect(discord.ui.Select):
    def __init__(self, rooms: list):
        options = []
        if not rooms:
            options.append(discord.SelectOption(label="登録されている部屋がありません", value="none"))
        else:
            for room in rooms:
                name = room[0]
                is_open = room[1]
                status_emoji = "🟢" if is_open else "🔴"
                status_text = "開放中" if is_open else "施錠中"
                options.append(discord.SelectOption(
                    label=f"{name}", 
                    description=f"現在の状態: {status_text}", 
                    emoji=status_emoji,
                    value=name
                ))
        
        super().__init__(
            placeholder="状態を変更する部屋を選択してください...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="room_status_select" # Persistent Viewのための固定ID
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message("部屋が登録されていません。", ephemeral=True)
            return

        room_name = self.values[0]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        async with aiosqlite.connect(DB_FILE) as db:
            # 現在の状態を取得して反転させる
            async with db.execute('SELECT is_open FROM rooms WHERE name = ?', (room_name,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    new_status = 0 if row[0] == 1 else 1
                    await db.execute('UPDATE rooms SET is_open = ?, last_updated = ? WHERE name = ?', 
                                     (new_status, now, room_name))
                    await db.commit()
                    
                    status_text = "開放" if new_status == 1 else "施錠"
                    await interaction.response.send_message(f"「{room_name}」を【{status_text}】に変更しました！\n※一覧への反映には数秒かかる場合があります。", ephemeral=True)
                else:
                    await interaction.response.send_message("部屋が見つかりませんでした。", ephemeral=True)

class RoomStatusView(discord.ui.View):
    def __init__(self, rooms: list):
        super().__init__(timeout=None)
        self.add_item(RoomStatusSelect(rooms))

# イベント選択用セレクトメニューとView
class EventSelect(discord.ui.Select):
    def __init__(self, events):
        options = []
        for event in events:
            date_str = event.start_time.astimezone(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d")
            options.append(discord.SelectOption(
                label=event.name[:100],
                description=f"開催日: {date_str}",
                value=str(event.id)
            ))
        super().__init__(placeholder="パネルを設置するイベントを選択...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        event_id = int(self.values[0])
        # キャッシュからイベントを取得
        guild_event = interaction.guild.get_scheduled_event(event_id)
        if not guild_event:
            await interaction.followup.send("イベントが見つかりませんでした。", ephemeral=True)
            return

        date_str = guild_event.start_time.astimezone(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d")
        
        channel = interaction.client.get_channel(ATTENDANCE_CHANNEL_ID)
        if not channel:
            await interaction.followup.send("出欠チャンネルが見つかりません。設定を確認してください。", ephemeral=True)
            return

        # 日時の表示用フォーマット
        display_time = guild_event.start_time.astimezone(ZoneInfo("Asia/Tokyo")).strftime("%Y年%m月%d日 %H:%M")
        
        embed = discord.Embed(
            title=f"📅 {guild_event.name}",
            description=f"**日時:** {display_time}\n\n下のボタンから出欠を入力してください！\n※開催日({date_str})になるまで登録できません。\n\n**【出席】**\n\n**【欠席】**\n\n**【遅刻/未定】**\n",
            color=discord.Color.blue()
        )
        if guild_event.cover_image:
            embed.set_image(url=guild_event.cover_image.url)
        
        view = AttendanceView()
        msg = await channel.send(embed=embed, view=view)
        
        # DBにイベント情報を保存
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute('INSERT INTO events (message_id, name, date, event_date) VALUES (?, ?, ?, ?)',
                             (msg.id, guild_event.name, display_time, date_str))
            await db.commit()
            
        await interaction.followup.send(f"出欠パネルを作成しました: {msg.jump_url}", ephemeral=True)

class EventSelectView(discord.ui.View):
    def __init__(self, events):
        super().__init__(timeout=60.0) # 一時的なメニューなのでタイムアウトあり
        self.add_item(EventSelect(events))

# 手動出欠イベント作成用のModal
class CreateEventModal(discord.ui.Modal, title='出欠イベントの作成 (手動)'):
    event_name = discord.ui.TextInput(
        label='イベント名',
        placeholder='例: 〇〇大会 ミーティング',
        required=True
    )
    event_date = discord.ui.TextInput(
        label='日付・時間 (表示用)',
        placeholder='例: 10月1日 15:00〜',
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        channel = interaction.client.get_channel(ATTENDANCE_CHANNEL_ID)
        if not channel:
            await interaction.followup.send("出欠チャンネルが見つかりません。設定を確認してください。", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"📅 {self.event_name.value}",
            description=f"**日時:** {self.event_date.value}\n\n下のボタンから出欠を入力してください！\n\n**【出席】**\n\n**【欠席】**\n\n**【遅刻/未定】**\n",
            color=discord.Color.blue()
        )
        
        view = AttendanceView()
        msg = await channel.send(embed=embed, view=view)
        
        # DBにイベント情報を保存 (手動作成の場合は日付チェックを無効にするためNoneを渡す)
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute('INSERT INTO events (message_id, name, date, event_date) VALUES (?, ?, ?, ?)',
                             (msg.id, self.event_name.value, self.event_date.value, None))
            await db.commit()
            
        await interaction.followup.send(f"出欠パネルを設置しました: {msg.jump_url}", ephemeral=True)

# 部屋追加用のModal
class AddRoomModal(discord.ui.Modal, title='部屋の追加'):
    room_name = discord.ui.TextInput(
        label='部屋の名前',
        placeholder='例: 部室A',
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        name = self.room_name.value
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        async with aiosqlite.connect(DB_FILE) as db:
            try:
                await db.execute('INSERT INTO rooms (name, is_open, last_updated) VALUES (?, 0, ?)', (name, now))
                await db.commit()
                await interaction.response.send_message(f"部屋「{name}」を追加しました！", ephemeral=True)
            except aiosqlite.IntegrityError:
                await interaction.response.send_message(f"部屋「{name}」は既に存在します。", ephemeral=True)

# 部屋削除用のModal
class DeleteRoomModal(discord.ui.Modal, title='部屋の削除'):
    room_name = discord.ui.TextInput(
        label='削除する部屋の名前',
        placeholder='正確に入力してください',
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        name = self.room_name.value
        
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute('DELETE FROM rooms WHERE name = ?', (name,))
            if cursor.rowcount > 0:
                await db.commit()
                await interaction.response.send_message(f"部屋「{name}」を削除しました。", ephemeral=True)
            else:
                await interaction.response.send_message(f"部屋「{name}」は見つかりませんでした。", ephemeral=True)

# Bot管理パネルのView
class AdminPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # 権限の二重チェック：管理者、管理権限、または指定役職を持つユーザーのみ操作を許可
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # DMでの操作はブロック
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内でのみ実行可能です。", ephemeral=True)
            return False

        # 1. 管理者権限、サーバー管理権限、チャンネル管理権限のいずれかを持っているか
        perms = interaction.user.guild_permissions
        has_permission = perms.administrator or perms.manage_guild or perms.manage_channels

        # 2. 指定された管理役職（ADMIN_ROLE_ID）を持っているか
        if not has_permission and ADMIN_ROLE_ID > 0:
            has_permission = any(role.id == ADMIN_ROLE_ID for role in interaction.user.roles)

        if has_permission:
            return True

        # 権限がない場合は親切な案内メッセージを返して処理をブロック
        await interaction.response.send_message(
            "⚠️ **操作権限がありません**\n"
            "この操作を実行するには「管理者」または「サーバー/チャンネルの管理」権限（または指定の管理役職）が必要です。\n"
            "※操作が必要な場合は、部長やサーバー管理者にお問い合わせください。",
            ephemeral=True
        )
        return False



    @discord.ui.button(label="本日のイベントから出欠", style=discord.ButtonStyle.primary, emoji="📅", custom_id="admin_create_event_discord")
    async def btn_create_event_discord(self, interaction: discord.Interaction, button: discord.ui.Button):
        today_str = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d")
        # サーバーのスケジュール済みイベント一覧から「本日」のものだけを抽出
        events = []
        for e in interaction.guild.scheduled_events:
            e_date = e.start_time.astimezone(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d")
            if e_date == today_str:
                events.append(e)
                
        events.sort(key=lambda e: e.start_time)
        
        if not events:
            await interaction.response.send_message("本日のDiscordイベントは見つかりませんでした。\n事前にDiscord上部の「イベント」から予定を作成してください。", ephemeral=True)
            return
            
        await interaction.response.send_message("本日のイベントから、出欠パネルを設置するものを選択してください。", view=EventSelectView(events), ephemeral=True)

    @discord.ui.button(label="手動で出欠作成", style=discord.ButtonStyle.primary, emoji="📝", custom_id="admin_create_event_manual")
    async def btn_create_event_manual(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CreateEventModal())

    @discord.ui.button(label="部屋を追加", style=discord.ButtonStyle.success, emoji="🏠", custom_id="admin_add_room")
    async def btn_add_room(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddRoomModal())

    @discord.ui.button(label="部屋を削除", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="admin_delete_room")
    async def btn_delete_room(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DeleteRoomModal())

    @discord.ui.button(label="部屋パネルを再設置", style=discord.ButtonStyle.secondary, emoji="🔄", custom_id="admin_reset_room_panel")
    async def btn_reset_room_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        channel = interaction.client.get_channel(ROOM_STATUS_CHANNEL_ID)
        if not channel:
            await interaction.followup.send("部室状況チャンネルが見つかりません。", ephemeral=True)
            return
            
        # 過去のBotのメッセージ（パネル）を削除して重複を防ぐ
        try:
            await channel.purge(check=lambda m: m.author == interaction.client.user, limit=50)
        except Exception:
            pass
            
        async with aiosqlite.connect(DB_FILE) as db:
            # 登録されている部屋一覧を取得
            async with db.execute('SELECT name, is_open FROM rooms ORDER BY name') as cursor:
                rooms = await cursor.fetchall()
                
        view = RoomStatusView(rooms)
        embed = discord.Embed(title="🏢 部室・施設の利用状況", description="ローディング中...", color=discord.Color.green())
        msg = await channel.send(embed=embed, view=view)
        
        async with aiosqlite.connect(DB_FILE) as db:
            # 古いパネルIDを削除して新しいものを登録
            await db.execute('DELETE FROM room_panel WHERE id = 1')
            await db.execute('INSERT INTO room_panel (id, message_id) VALUES (1, ?)', (msg.id,))
            await db.commit()
            
        await interaction.followup.send("部室状況パネルを再設置しました！", ephemeral=True)

# --- Bot 本体 ---

class CircleManagerBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    # 起動時の初期化処理
    async def setup_hook(self):
        # データベースの初期化
        await init_db()
        
        # 永続Viewの登録 (再起動してもボタンが動くようにする)
        self.add_view(AdminPanelView())
        self.add_view(AttendanceView())
        
        # RoomStatusViewは動的要素（Selectの選択肢）が含まれるため、
        # DBから現在の部屋情報を読み込んで初期化してから登録する
        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute('SELECT name, is_open FROM rooms ORDER BY name') as cursor:
                rooms = await cursor.fetchall()
        self.add_view(RoomStatusView(rooms))
        
        # 定期更新タスクの開始
        self.update_attendance_panels.start()
        self.update_room_panels.start()

    async def on_ready(self):
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print('------')
        
        # パネルの自己修復機能：管理チャンネルにパネルがなければ送信する
        admin_channel = self.get_channel(ADMIN_CHANNEL_ID)
        if admin_channel:
            panel_found = False
            async for message in admin_channel.history(limit=50):
                if message.author == self.user and message.embeds:
                    if message.embeds[0].title == "⚙️ Bot管理パネル":
                        panel_found = True
                        break
            
            if not panel_found:
                embed = discord.Embed(
                    title="⚙️ Bot管理パネル",
                    description="以下のボタンをクリックして操作してください。\n※このメッセージを削除してしまった場合は、Botを再起動すると再設置されます。",
                    color=discord.Color.dark_theme()
                )
                await admin_channel.send(embed=embed, view=AdminPanelView())
                print("管理パネルを自己修復（再送信）しました。")

    # 定期タスク：出欠パネルのバッチ更新 (5秒に1回)
    # リアルタイム更新によるDiscord APIのレートリミット（制限）を回避するための設計
    @tasks.loop(seconds=5.0)
    async def update_attendance_panels(self):
        attendance_channel = self.get_channel(ATTENDANCE_CHANNEL_ID)
        if not attendance_channel:
            return

        today_str = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d")

        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute('SELECT message_id, name, date, event_date FROM events') as event_cursor:
                events = await event_cursor.fetchall()
                
                for event in events:
                    msg_id, name, date, event_date = event
                    
                    # Discordイベント連携で作られたパネル(event_dateが存在する)のうち、今日以外のものは自動削除
                    if event_date is not None and event_date != today_str:
                        try:
                            msg = await attendance_channel.fetch_message(msg_id)
                            await msg.delete()
                        except Exception:
                            pass
                        # DBからも削除
                        await db.execute('DELETE FROM events WHERE message_id = ?', (msg_id,))
                        await db.execute('DELETE FROM attendances WHERE message_id = ?', (msg_id,))
                        await db.commit()
                        continue
                        
                    try:
                        msg = await attendance_channel.fetch_message(msg_id)
                    except discord.NotFound:
                        # メッセージが既に削除されている場合もDBから削除してお掃除
                        await db.execute('DELETE FROM events WHERE message_id = ?', (msg_id,))
                        await db.execute('DELETE FROM attendances WHERE message_id = ?', (msg_id,))
                        await db.commit()
                        continue
                    except discord.HTTPException:
                        continue
                        
                    # そのイベントの出欠状況を取得
                    async with db.execute('SELECT user_name, status FROM attendances WHERE message_id = ?', (msg_id,)) as att_cursor:
                        attendances = await att_cursor.fetchall()
                        
                    att_yes = [a[0] for a in attendances if a[1] == "出席"]
                    att_no = [a[0] for a in attendances if a[1] == "欠席"]
                    att_maybe = [a[0] for a in attendances if a[1] == "遅刻/未定"]
                    
                    if event_date is None:
                        desc = f"**日時:** {date}\n\n下のボタンから出欠を入力してください！\n\n"
                    else:
                        desc = f"**日時:** {date}\n\n下のボタンから出欠を入力してください！\n※開催日({event_date})になるまで登録できません。\n\n"

                    desc += f"**【出席】 ({len(att_yes)}名)**\n" + (", ".join(att_yes) if att_yes else "なし") + "\n\n"
                    desc += f"**【欠席】 ({len(att_no)}名)**\n" + (", ".join(att_no) if att_no else "なし") + "\n\n"
                    desc += f"**【遅刻/未定】 ({len(att_maybe)}名)**\n" + (", ".join(att_maybe) if att_maybe else "なし")
                    
                    # 現在のEmbedの内容と異なる場合のみ編集する (API呼び出しを節約)
                    current_desc = msg.embeds[0].description if msg.embeds else ""
                    if current_desc != desc:
                        embed = discord.Embed(title=f"📅 {name}", description=desc, color=discord.Color.blue())
                        await msg.edit(embed=embed)

    @update_attendance_panels.before_loop
    async def before_update_attendance(self):
        await self.wait_until_ready()

    # 定期タスク：部屋状況パネルのバッチ更新 (5秒に1回)
    @tasks.loop(seconds=5.0)
    async def update_room_panels(self):
        room_channel = self.get_channel(ROOM_STATUS_CHANNEL_ID)
        if not room_channel:
            return

        async with aiosqlite.connect(DB_FILE) as db:
            async with db.execute('SELECT message_id FROM room_panel WHERE id = 1') as cursor:
                row = await cursor.fetchone()
                if not row:
                    return
                msg_id = row[0]
                
            try:
                msg = await room_channel.fetch_message(msg_id)
            except (discord.NotFound, discord.HTTPException):
                return
                
            async with db.execute('SELECT name, is_open, last_updated FROM rooms ORDER BY name') as cursor:
                rooms = await cursor.fetchall()
                
            desc = ""
            if not rooms:
                desc = "登録されている部屋がありません。"
            else:
                for room in rooms:
                    name, is_open, last_updated = room
                    status_emoji = "🟢" if is_open else "🔴"
                    status_text = "開放中" if is_open else "施錠中"
                    desc += f"{status_emoji} **{name}** : {status_text} (更新: {last_updated})\n"
                    
            # 状態が変わっていたらメッセージとView(セレクトメニュー)を更新
            current_desc = msg.embeds[0].description if msg.embeds else ""
            if current_desc != desc:
                embed = discord.Embed(title="🏢 部室・施設の利用状況", description=desc, color=discord.Color.green())
                # SelectMenuの中身も最新の部屋一覧に合わせて再生成する
                view = RoomStatusView(rooms)
                await msg.edit(embed=embed, view=view)

    @update_room_panels.before_loop
    async def before_update_rooms(self):
        await self.wait_until_ready()


# エントリーポイント
if __name__ == "__main__":
    if not TOKEN:
        print("エラー: .env ファイルに DISCORD_TOKEN が設定されていません。")
    else:
        bot = CircleManagerBot()
        bot.run(TOKEN)
