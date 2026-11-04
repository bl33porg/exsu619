# uom_config.py - UOM-1 settings. Contains the bot token: never put this file on GitHub.

# Phone hotspot with internet (2.4 GHz; on iPhone turn on "Maximize Compatibility")
WIFI_MODE = "station"
WIFI_SSID = "Allexie (6)"
WIFI_PASSWORD = "allexiecla"

AP_NAME = "UOM-1"                 # used if the hotspot is not found
AP_PASSWORD = "uom1bench"

# @YourineBot
TELEGRAM_TOKEN = ""

# Add people after they send /start to @YourineBot (see the steps for their chat ID).
# roles: "nurse", "physician", "technical". optin: when they agreed to receive messages.
RECIPIENTS = [
    # {"name": "Nurse (Ana)", "chat_id": 123456789, "roles": ["nurse", "technical"],
    #  "optin": "2026-11-03 17:15"},
    # {"name": "Physician (Ben)", "chat_id": 987654321, "roles": ["physician"],
    #  "optin": "2026-11-03 17:15"},
]
