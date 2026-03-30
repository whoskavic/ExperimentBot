import json
import pytz

with open("appsettings.json", "r") as f:
    _config = json.load(f)

with open("user.json", "r") as f:
    USER_MAP = json.load(f)

TOKEN                = _config["bot_token"]
CHANNEL_IDS          = _config["channel_ids"]
THREAD_IDS           = _config["thread_ids"]
DESTINATION_CHANNEL  = _config["destination_channel"]
RECAP_HOUR           = _config.get("recap_hour", 21)
RECAP_MINUTE         = _config.get("recap_minute", 0)

TZ = pytz.timezone("Asia/Jakarta")