import discord
from discord import app_commands
from discord.ext import tasks
import requests
import json
import os
from aiohttp import web

async def health_check(request):
    return web.Response(text="PeteBot is running!")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    print(f"Web server running on port {port}")


TOKEN = os.getenv("TOKEN")


GUILD_ID = 1140792771624583292

INVASION_CHANNEL_ID = 1499102867355734056
FO_CHANNEL_ID = 1499103735207563458
PROOFS_CHANNEL_ID = 1499104914146594869
RANK_CHANNEL_ID = 1499104025575035081
PROMOTION_CHANNEL_ID = 1499105649135714334

MOD_ROLE_ID = 1140847777824387092

invasion_message = None
fo_message = None
last_invasion_data = None
last_fo_data = None
processing_messages = set()
last_promotions = {}

POINT_EMOJIS = {
    "Pete_1_Point": 1,
    "Pete_2_Points": 2,
    "Pete_3_Points": 3,
    "Pete_4_Points": 4,
    "Pete_5_Points": 5,
    "Pete_10Points": 10,
    "Pete_50Points": 50
}

FO_STREETS = {
    "3100": "Walrus Way",
    "3200": "Sleet Street",
    "3300": "Polar Place",
    "4100": "Alto Avenue",
    "4200": "Baritone Boulevard",
    "4300": "Tenor Terrace",
    "5100": "Elm Street",
    "5200": "Maple Street",
    "5300": "Oak Street",
    "9100": "Lullaby Lane",
    "9200": "Pajama Place"
}

RANK_ROLES = [
    (0, 1140845481161916456),
    (10, 1499114528045269032),
    (25, 1499115325961273414),
    (50, 1499115426569912400),
    (80, 1499115515929428031),
    (120, 1499115553162399996),
    (170, 1499115616383275202),
    (230, 1499115782473384102),
    (300, 1499115807953653947),
    (400, 1499115885409865728)
]


def load_data():
    if not os.path.exists("ranking.json"):
        return {"users": {}, "validated_messages": {}, "last_promotions": {}}

    with open("ranking.json", "r") as f:
        data = json.load(f)

    data.setdefault("users", {})
    data.setdefault("validated_messages", {})
    data.setdefault("last_promotions", {})

    if isinstance(data["validated_messages"], list):
        data["validated_messages"] = {}

    return data


def save_data(data):
    with open("ranking.json", "w") as f:
        json.dump(data, f, indent=4)


def load_panel_data():
    if not os.path.exists("panels.json"):
        return {}

    with open("panels.json", "r") as f:
        return json.load(f)


def save_panel_data(data):
    with open("panels.json", "w") as f:
        json.dump(data, f, indent=4)


class MeuBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.loop.create_task(start_web_server())

        guild = discord.Object(id=GUILD_ID)
        synced = await self.tree.sync()
        print(f"Synced {len(synced)} command(s).")


bot = MeuBot()
guild = discord.Object(id=GUILD_ID)


async def get_channel_safe(channel_id):
    channel = bot.get_channel(channel_id)

    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception as e:
            print(f"Channel error {channel_id}:", e)
            return None

    return channel


async def get_panel_message(channel, panel_key, title):
    panel_data = load_panel_data()
    message_id = panel_data.get(panel_key)

    if message_id:
        try:
            return await channel.fetch_message(int(message_id))
        except Exception:
            panel_data.pop(panel_key, None)
            save_panel_data(panel_data)

    panels = []

    async for msg in channel.history(limit=50):
        if msg.author == bot.user and msg.embeds:
            if msg.embeds[0].title == title:
                panels.append(msg)

    if panels:
        main_panel = panels[0]

        for duplicate in panels[1:]:
            try:
                await duplicate.delete()
            except Exception:
                pass

        panel_data[panel_key] = main_panel.id
        save_panel_data(panel_data)
        return main_panel

    return None


async def save_panel_message(panel_key, message):
    panel_data = load_panel_data()
    panel_data[panel_key] = message.id
    save_panel_data(panel_data)


def find_rank_role_id_by_points(points):
    role_id = RANK_ROLES[0][1]

    for required_points, rank_role_id in RANK_ROLES:
        if points >= required_points:
            role_id = rank_role_id

    return role_id


async def cleanup_promotion_messages(member, role_id, keep_one=True):
    promotion_channel = bot.get_channel(PROMOTION_CHANNEL_ID)

    if promotion_channel is None:
        promotion_channel = await bot.fetch_channel(PROMOTION_CHANNEL_ID)

    role_mention = f"<@&{role_id}>"
    found = []

    async for msg in promotion_channel.history(limit=100):
        if (
            msg.author == bot.user
            and member.mention in msg.content
            and role_mention in msg.content
            and "has been promoted to" in msg.content
        ):
            found.append(msg)

    if keep_one:
        for msg in found[1:]:
            try:
                await msg.delete()
            except Exception:
                pass
    else:
        for msg in found:
            try:
                await msg.delete()
            except Exception:
                pass

    return len(found)


async def update_rank_role(guild_obj, member, points):
    points = int(points)

    data = load_data()
    data.setdefault("last_promotions", {})
    promotions = data["last_promotions"]

    rank_role_ids = [role_id for _, role_id in RANK_ROLES]

    old_role_id = None
    old_threshold = None

    for role in member.roles:
        if role.id in rank_role_ids:
            old_role_id = role.id
            break

    if old_role_id is not None:
        for required_points, role_id in RANK_ROLES:
            if role_id == old_role_id:
                old_threshold = required_points
                break

    new_role_id = find_rank_role_id_by_points(points)

    new_threshold = 0
    for required_points, role_id in RANK_ROLES:
        if role_id == new_role_id:
            new_threshold = required_points
            break

    if old_role_id == new_role_id:
        return

    new_role = guild_obj.get_role(new_role_id)

    if new_role is None:
        print("Rank role not found.")
        return

    roles_to_remove = [
        role for role in member.roles
        if role.id in rank_role_ids and role.id != new_role_id
    ]

    try:
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="Rank role update")

        if new_role not in member.roles:
            await member.add_roles(new_role, reason="Rank role update")

        user_id = str(member.id)

        if old_threshold is not None and new_threshold > old_threshold:
            promotion_channel = bot.get_channel(PROMOTION_CHANNEL_ID)

            if promotion_channel is None:
                promotion_channel = await bot.fetch_channel(PROMOTION_CHANNEL_ID)

            existing = await cleanup_promotion_messages(
                member,
                new_role_id,
                keep_one=True
            )

            if existing == 0:
                await promotion_channel.send(
                    f"{member.mention} has been promoted to {new_role.mention}! <:Pete_imonfire:1497814466195099708>"
                )

            promotions[user_id] = str(new_role_id)
            save_data(data)

            await cleanup_promotion_messages(
                member,
                new_role_id,
                keep_one=True
            )

    except discord.Forbidden:
        print("Bot does not have permission to manage roles.")

    except discord.HTTPException as e:
        print("Error updating role:", e)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    if not update_invasions_channel.is_running():
        update_invasions_channel.start()

    if not update_fo_panel.is_running():
        update_fo_panel.start()


@bot.tree.command(name="test", description="Test command", guild=guild)
async def test(interaction: discord.Interaction):
    await interaction.response.send_message("Bot is working", ephemeral=True)


@tasks.loop(minutes=1)
async def update_invasions_channel():
    global invasion_message, last_invasion_data

    channel = await get_channel_safe(INVASION_CHANNEL_ID)
    if channel is None:
        return

    try:
        invasions_data = requests.get(
            "https://www.toontownrewritten.com/api/invasions",
            timeout=10
        ).json()

        population_data = requests.get(
            "https://www.toontownrewritten.com/api/population",
            timeout=10
        ).json()

    except Exception as e:
        print("Invasion API error:", e)
        return

    invasions = invasions_data.get("invasions", {})
    districts = population_data.get("populationByDistrict", {})

    embed = discord.Embed(
        title="🚨 Toontown Rewritten District Status",
        color=discord.Color.red()
    )

    active_text = ""
    available_text = ""

    for district, population in districts.items():
        if district in invasions and isinstance(invasions[district], dict):
            cog = invasions[district].get("type", "Unknown Cog")
            progress = invasions[district].get("progress", "0/1")

            try:
                current, total = map(int, progress.split("/"))
                remaining = total - current
                minutes_left = max(1, remaining // 100)
                time_text = f"{minutes_left} min left"
            except Exception:
                time_text = "Unknown time"

            active_text += f"🔴 **{district}** — {cog} ({progress}) ⏳ {time_text}\n"
        else:
            available_text += f"🟢 **{district}** — {population} toons\n"

    embed.add_field(
        name="Active Invasions",
        value=active_text or "No active invasions.",
        inline=False
    )

    embed.add_field(
        name="Available Districts",
        value=available_text or "No available districts.",
        inline=False
    )

    new_data = {
        "invasions": invasions,
        "districts": districts
    }

    try:
        invasion_message = await get_panel_message(
            channel,
            "invasion_message_id",
            "🚨 Toontown Rewritten District Status"
        )

        if invasion_message is None:
            invasion_message = await channel.send(embed=embed)
            await save_panel_message("invasion_message_id", invasion_message)
        else:
            await invasion_message.edit(embed=embed)
            await save_panel_message("invasion_message_id", invasion_message)

        last_invasion_data = new_data

    except discord.NotFound:
        invasion_message = None

    except discord.HTTPException as e:
        print("Invasion panel error:", e)


@tasks.loop(minutes=2)
async def update_fo_panel():
    global fo_message, last_fo_data

    channel = await get_channel_safe(FO_CHANNEL_ID)
    if channel is None:
        return

    try:
        data = requests.get(
            "https://www.toontownrewritten.com/api/fieldoffices",
            timeout=10
        ).json()

    except Exception as e:
        print("Field Office API error:", e)
        return

    field_offices = data.get("fieldOffices", {})

    embed = discord.Embed(
        title="<:Pete_Boiler:1498028470884892814> Field Offices",
        color=discord.Color.orange()
    )

    if not field_offices:
        embed.description = "No active Field Offices."
    else:
        text = ""

        sorted_fos = sorted(
            field_offices.items(),
            key=lambda x: x[1].get("difficulty", 0)
        )

        for zone_id, info in sorted_fos:
            street = FO_STREETS.get(str(zone_id), f"Zone {zone_id}")
            difficulty = info.get("difficulty", 0) + 1
            stars = "<:Pete_Star:1499084954410291381>" * difficulty
            annexes = info.get("annexes", "?")

            if info.get("open"):
                status = "<:Pete_verified_by_lil_oldman:1499092210811932834> Open"
            else:
                status = "🔴 Closed"

            text += (
                f"**{street}**\n"
                f"{stars}\n"
                f"Annexes: **{annexes}**\n"
                f"Status: {status}\n\n"
            )

        embed.add_field(
            name="Active",
            value=text,
            inline=False
        )

    try:
        if fo_message is None:
            fo_message = await get_panel_message(
                channel,
                "fo_message_id",
                "<:Pete_Boiler:1498028470884892814> Field Offices"
            )

        if fo_message is None:
            fo_message = await channel.send(embed=embed)
            await save_panel_message("fo_message_id", fo_message)

        elif data != last_fo_data:
            await fo_message.edit(embed=embed)

        last_fo_data = data

    except discord.NotFound:
        fo_message = await channel.send(embed=embed)
        await save_panel_message("fo_message_id", fo_message)

    except discord.HTTPException as e:
        print("FO panel error:", e)


@bot.event
async def on_raw_reaction_add(payload):
    print("REACTION DETECTED")
    print("Canal:", payload.channel_id)
    print("Emoji:", payload.emoji.name, payload.emoji.id)
    print("User ID:", payload.user_id)

    if payload.channel_id != PROOFS_CHANNEL_ID:
        print("Canal errado")
        return

    emoji_name = payload.emoji.name
    emoji_id = str(payload.emoji.id) if payload.emoji.id else None

    if emoji_name not in POINT_EMOJIS:
        print("Emoji não está no POINT_EMOJIS:", emoji_name)
        return

    data = load_data()
    data.setdefault("users", {})
    data.setdefault("validated_messages", {})

    message_id = str(payload.message_id)

    if message_id in data["validated_messages"]:
        print("Mensagem já validada")
        return

    if message_id in processing_messages:
        print("Mensagem já está sendo processada")
        return

    processing_messages.add(message_id)

    try:
        guild_obj = bot.get_guild(payload.guild_id)
        if guild_obj is None:
            print("Servidor não encontrado")
            return

        member = payload.member
        if member is None:
            member = await guild_obj.fetch_member(payload.user_id)

        if member.bot:
            print("Quem reagiu é bot")
            return

        if not any(role.id == MOD_ROLE_ID for role in member.roles):
            print("Usuário que reagiu não é mod")
            return

        channel = bot.get_channel(payload.channel_id)
        if channel is None:
            channel = await bot.fetch_channel(payload.channel_id)

        message = await channel.fetch_message(payload.message_id)
        author = message.author

        if author.bot:
            print("Autor do print é bot")
            return

        if author.id == member.id:
            print("Moderador tentou validar o próprio print")
            return

        author_id = str(author.id)
        moderator_id = str(member.id)
        points = POINT_EMOJIS[emoji_name]

        data["users"].setdefault(author_id, {"points": 0, "tasks": 0})

        data["users"][author_id]["points"] += points
        data["users"][author_id]["tasks"] += 1

        data["validated_messages"][message_id] = {
            "author_id": author_id,
            "moderator_id": moderator_id,
            "emoji": emoji_name,
            "emoji_id": emoji_id,
            "points": points
        }

        save_data(data)

        target_member = await guild_obj.fetch_member(int(author_id))
        await update_rank_role(
            guild_obj,
            target_member,
            data["users"][author_id]["points"]
        )

        print(f"{author} recebeu {points} ponto(s).")

    except Exception as e:
        print("Reaction add error:", e)

    finally:
        processing_messages.discard(message_id)


@bot.event
async def on_raw_reaction_remove(payload):
    if payload.channel_id != PROOFS_CHANNEL_ID:
        return

    data = load_data()
    data.setdefault("users", {})
    data.setdefault("validated_messages", {})
    data.setdefault("last_promotions", {})

    message_id = str(payload.message_id)

    if message_id not in data.get("validated_messages", {}):
        return

    validation = data["validated_messages"][message_id]

    emoji_name = payload.emoji.name
    emoji_id = str(payload.emoji.id) if payload.emoji.id else None

    saved_emoji = validation.get("emoji")
    saved_emoji_id = validation.get("emoji_id")

    if saved_emoji_id:
        if emoji_id != saved_emoji_id:
            return
    else:
        if emoji_name != saved_emoji:
            return

    guild_obj = bot.get_guild(payload.guild_id)
    if guild_obj is None:
        return

    try:
        member = await guild_obj.fetch_member(payload.user_id)
    except Exception:
        return

    if member.bot:
        return

    if not any(role.id == MOD_ROLE_ID for role in member.roles):
        return

    if str(member.id) != str(validation.get("moderator_id")):
        return

    author_id = str(validation.get("author_id"))
    points = int(validation.get("points", 0))

    if author_id not in data.get("users", {}):
        return

    old_points = data["users"][author_id].get("points", 0)
    new_points = max(0, old_points - points)

    old_rank_role_id = find_rank_role_id_by_points(old_points)
    new_rank_role_id = find_rank_role_id_by_points(new_points)

    data["users"][author_id]["points"] = new_points
    data["users"][author_id]["tasks"] = max(
        0,
        data["users"][author_id].get("tasks", 0) - 1
    )

    del data["validated_messages"][message_id]

    if old_rank_role_id != new_rank_role_id:
        data["last_promotions"][author_id] = str(new_rank_role_id)

    save_data(data)

    target_member = await guild_obj.fetch_member(int(author_id))

    if old_rank_role_id != new_rank_role_id:
        await cleanup_promotion_messages(
            target_member,
            old_rank_role_id,
            keep_one=False
        )

    await update_rank_role(
        guild_obj,
        target_member,
        new_points
    )


@bot.event
async def on_raw_message_delete(payload):
    if payload.channel_id != PROOFS_CHANNEL_ID:
        return

    data = load_data()
    data.setdefault("users", {})
    data.setdefault("validated_messages", {})
    data.setdefault("last_promotions", {})

    message_id = str(payload.message_id)

    if message_id not in data["validated_messages"]:
        return

    validation = data["validated_messages"][message_id]

    author_id = str(validation.get("author_id"))
    points = int(validation.get("points", 0))

    if author_id not in data["users"]:
        del data["validated_messages"][message_id]
        save_data(data)
        return

    old_points = data["users"][author_id].get("points", 0)
    new_points = max(0, old_points - points)

    old_rank_role_id = find_rank_role_id_by_points(old_points)
    new_rank_role_id = find_rank_role_id_by_points(new_points)

    data["users"][author_id]["points"] = new_points
    data["users"][author_id]["tasks"] = max(
        0,
        data["users"][author_id].get("tasks", 0) - 1
    )

    del data["validated_messages"][message_id]

    if old_rank_role_id != new_rank_role_id:
        data["last_promotions"][author_id] = str(new_rank_role_id)

    save_data(data)

    guild_obj = bot.get_guild(payload.guild_id)
    if guild_obj is not None:
        target_member = await guild_obj.fetch_member(int(author_id))

        if old_rank_role_id != new_rank_role_id:
            await cleanup_promotion_messages(
                target_member,
                old_rank_role_id,
                keep_one=False
            )

        await update_rank_role(
            guild_obj,
            target_member,
            new_points
        )

    print(f"Deleted print removed {points} point(s) from user {author_id}.")


@bot.tree.command(name="rank", description="View your points", guild=guild)
async def rank(interaction: discord.Interaction):
    if interaction.channel_id != RANK_CHANNEL_ID:
        await interaction.response.send_message(
            "📍 Use this command in the ranking channel.",
            ephemeral=True
        )
        return

    data = load_data()
    users = data.get("users", {})
    user_id = str(interaction.user.id)

    if user_id not in users:
        await interaction.response.send_message(
            "You don't have any points yet.",
            ephemeral=True
        )
        return

    points = users[user_id].get("points", 0)
    tasks = users[user_id].get("tasks", 0)

    await interaction.response.send_message(
        f"📊 **Your Stats**\n\n"
        f"<:Pete_Toon_Trophy:1499092782529380535> Points: **{points}**\n"
        f"<:Pete_verified_by_lil_oldman:1499092210811932834> Completed Tasks: **{tasks}",
        ephemeral=True
    )


@bot.tree.command(name="top", description="View the server ranking", guild=guild)
@app_commands.describe(page="Page number from 1 to 10")
async def top(interaction: discord.Interaction, page: int = 1):
    if interaction.channel_id != RANK_CHANNEL_ID:
        await interaction.response.send_message(
            "📍 Use this command in the ranking channel.",
            ephemeral=True
        )
        return

    page = max(1, min(page, 10))

    data = load_data()
    users = data.get("users", {})

    if not users:
        await interaction.response.send_message("No ranking data yet.")
        return

    ranking = sorted(
        users.items(),
        key=lambda x: x[1].get("points", 0),
        reverse=True
    )[:100]

    total_pages = max(1, (len(ranking) + 9) // 10)

    if page > total_pages:
        await interaction.response.send_message(
            f"That page doesn't exist yet. Current max page: {total_pages}."
        )
        return

    start = (page - 1) * 10
    page_ranking = ranking[start:start + 10]

    medals = ["🥇", "🥈", "🥉"]
    text = ""

    for i, (user_id, info) in enumerate(page_ranking, start=start + 1):
        medal = medals[i - 1] if i <= 3 else f"{i}."
        points = info.get("points", 0)
        tasks = info.get("tasks", 0)

        text += (
            f"{medal} <@{user_id}> — "
            f"<:Pete_Toon_Trophy:1499092782529380535> {points} pts | "
            f"<:Pete_verified_by_lil_oldman:1499092210811932834> {tasks} tasks\n"
        )

    await interaction.response.send_message(
        f"🏆 **Ranking — Page {page}/{total_pages}**\n\n{text}"
    )



if __name__ == "__main__":
    print("INICIANDO BOT...")
    bot.run(TOKEN)