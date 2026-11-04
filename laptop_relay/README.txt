UOM-1 Telegram relay (runs on the laptop)

1. Join the laptop to the same Wi-Fi/hotspot as the Pico.
2. Terminal:  cd into this folder, then
       pip3 install requests
       python3 telegram_relay.py
3. Edit uom_config.py here: PICO_URL (the address Thonny prints) and RECIPIENTS.
4. On the Pico, set TELEGRAM_TOKEN = "" in CIRCUITPY/uom_config.py so only the laptop sends.
Keep this folder on the laptop - not on CIRCUITPY. Never upload uom_config.py to GitHub.
