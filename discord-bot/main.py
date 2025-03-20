import os
import hikari
import lightbulb
import aiohttp
import asyncio
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# Load environment variables or replace with your own values
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
TWITCH_CLIENT_ID = os.environ.get("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET")
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
TWITCH_CHANNEL = os.environ.get("TWITCH_CHANNEL")
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "60"))  # Seconds between checks

# Initialize the bot
bot = lightbulb.BotApp(
    token=DISCORD_TOKEN,
    # intents=hikari.Intents.ALL
)

# Store stream states
stream_status = {}
twitch_access_token = None
token_expiry = datetime.utcnow()
# Initialize cooldown_check to a time in the past so the webhook can trigger immediately
cooldown_check = datetime.utcnow() - timedelta(hours=3)

JSON_FILE = "stream_status.json"

def load_stream_status():
    """Load the stream status from a JSON file."""
    global stream_status
    if os.path.exists(JSON_FILE):
        try:
            with open(JSON_FILE, "r") as f:
                stream_status = json.load(f)
            print("Loaded stream status from JSON file.")
        except Exception as e:
            print(f"Error loading stream status from {JSON_FILE}: {e}")
            stream_status = {}
    else:
        stream_status = {}

def save_stream_status():
    """Save the current stream status to a JSON file."""
    try:
        with open(JSON_FILE, "w") as f:
            json.dump(stream_status, f, indent=4)
        print("Saved stream status to JSON file.")
    except Exception as e:
        print(f"Error saving stream status to {JSON_FILE}: {e}")


async def get_twitch_access_token():
    """Get a Twitch access token for API calls."""
    global twitch_access_token, token_expiry
    
    # If token is still valid, return it
    if twitch_access_token and datetime.utcnow() < token_expiry:
        return twitch_access_token
    
    url = "https://id.twitch.tv/oauth2/token"
    params = {
        "client_id": TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
        "grant_type": "client_credentials"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, params=params) as response:
            if response.status == 200:
                data = await response.json()
                twitch_access_token = data["access_token"]
                # Set expiry time (usually 60 days, but we'll be conservative)
                token_expiry = datetime.utcnow() + timedelta(hours=24)
                return twitch_access_token
            else:
                print(f"Failed to get Twitch token: {response.status}")
                return None

async def check_stream_status():
    """Check if the Twitch channel is live and update status accordingly."""
    global stream_status, cooldown_check

    if not TWITCH_CHANNEL:
        return

    token = await get_twitch_access_token()
    if not token:
        print("Failed to get Twitch access token")
        return

    headers = {
        "Client-ID": TWITCH_CLIENT_ID,
        "Authorization": f"Bearer {token}"
    }
    query_params = f"user_login={TWITCH_CHANNEL}"
    url = f"https://api.twitch.tv/helix/streams?{query_params}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                # Create a dict mapping live stream usernames to their info.
                live_streams = {stream["user_login"]: stream for stream in data["data"]}

                # If the channel just went live...
                now = datetime.utcnow()
                if TWITCH_CHANNEL in live_streams and TWITCH_CHANNEL not in stream_status:
                    stream_info = live_streams[TWITCH_CHANNEL]
                    stream_status[TWITCH_CHANNEL] = stream_info
                    save_stream_status()
                    # Check the cooldown before sending a webhook.
                    if now >= cooldown_check:
                        await send_webhook_notification(stream_info)
                        cooldown_check = now + timedelta(hours=3)
                    else:
                        print("Webhook cooldown in effect; not sending notification.")
                # If the channel went offline...
                elif TWITCH_CHANNEL not in live_streams and TWITCH_CHANNEL in stream_status:
                    del stream_status[TWITCH_CHANNEL]
                    save_stream_status()
            else:
                print(f"Error checking Twitch streams: {response.status}")


async def send_webhook_notification(stream_info):
    """Send a notification to Discord webhook when a stream goes live."""
    
    streamer_name = stream_info["user_name"]
    stream_title = stream_info["title"]
    game_name = stream_info["game_name"]
    viewer_count = stream_info["viewer_count"]
    thumbnail_url = stream_info["thumbnail_url"].replace("{width}", "1280").replace("{height}", "720")
    stream_url = f"https://twitch.tv/{stream_info['user_login']}"
    
    # Create webhook embed
    embed = {
        "title": stream_title,
        "description": f"{streamer_name} is now streaming {game_name}!\nCurrent viewers: {viewer_count}",
        "url": stream_url,
        "color": 0x6441A4,  # Twitch purple
        "timestamp": datetime.utcnow().isoformat(),
        "image": {"url": thumbnail_url},
        "author": {
            "name": f"{streamer_name} is LIVE!",
            "url": stream_url,
            "icon_url": "https://static.twitchcdn.net/assets/favicon-32-d6025c14e900565d6177.png"
        },
        "footer": {
            "text": "Twitch Stream Notification",
            "icon_url": "https://static.twitchcdn.net/assets/favicon-32-d6025c14e900565d6177.png"
        }
    }
    
    # Send webhook
    webhook_data = {
        "content": f"🔴 **{streamer_name}** is now live on Twitch! @everyone",
        "embeds": [embed]
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            WEBHOOK_URL,
            json=webhook_data
        ) as response:
            if response.status >= 400:
                error_text = await response.text()
                print(f"Error sending webhook: {response.status} - {error_text}")

@bot.listen(hikari.StartedEvent)
async def on_started(_event):
    print("Bot started!")
    # Start checking for streams
    bot.create_task(stream_check_loop())

async def stream_check_loop():
    """Loop to check stream status at regular intervals."""
    while True:
        print(f'staus check - {datetime.utcnow()}')
        await check_stream_status()
        await asyncio.sleep(CHECK_INTERVAL)

# # Add a command to add a new channel to monitor
# @bot.command()
# @lightbulb.command("addchannel", "Add a Twitch channel to monitor")
# @lightbulb.implements(lightbulb.SlashCommand)
# async def add_channel(ctx: lightbulb.Context) -> None:
#     await ctx.respond("Feature not implemented yet. Please add channels through environment variables.")

@bot.command()
@lightbulb.command("status", "Check if pattybuilds is streaming or not")
@lightbulb.implements(lightbulb.SlashCommand)
async def status(ctx: lightbulb.Context) -> None:
    channel = TWITCH_CHANNEL
    if channel in stream_status:
        # Retrieve stream info from the global dict.
        stream_info = stream_status[channel]
        title_text = stream_info.get("title", "No Title Provided")
        viewer_count = stream_info.get("viewer_count", 0)
        game_name = stream_info.get("game_name", "Unknown Game")
        # Replace the thumbnail template dimensions with desired values.
        thumbnail_template = stream_info.get("thumbnail_url", "")
        thumbnail_url = thumbnail_template.replace("{width}", "1280").replace("{height}", "720")

        embed = hikari.Embed(
            title=f"🔴 {channel} is Live!",
            description=title_text,
            url=f"https://twitch.tv/{stream_info['user_login']}",
            color=0x9146FF,  # Twitch-like purple.
            timestamp=datetime.now()
        )
        embed.add_field(name="Viewer Count", value=str(viewer_count), inline=True)
        embed.add_field(name="Game", value=game_name, inline=True)
        embed.set_thumbnail(thumbnail_url)
    else:
        # Offline status embed.
        embed = hikari.Embed(
            title=f"{channel} is Offline",
            description="The stream is currently offline.",
            color=0xCCCCCC,  # A neutral color for offline.
            timestamp=datetime.now()
        )

    await ctx.respond(embed=embed)

# # Add a command to force a check
# @bot.command()
# @lightbulb.command("checkstreams", "Force check of stream status")
# @lightbulb.implements(lightbulb.SlashCommand)
# async def force_check(ctx: lightbulb.Context) -> None:
#     await ctx.respond("Checking stream status...")
#     await check_stream_status()
#     await ctx.respond("Check complete!")

# Run the bot
def main():
    # Load any persisted stream status from JSON on startup.
    load_stream_status()

    if not all([DISCORD_TOKEN, TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, WEBHOOK_URL]):
        print("Missing required environment variables!")
        print("Required: DISCORD_TOKEN, TWITCH_CHANNEL, TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, DISCORD_WEBHOOK_URL")
        print("Optional: CHECK_INTERVAL (seconds)")
        return
    
    bot.run()

if __name__ == "__main__":
    main()