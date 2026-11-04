# code.py - UOM-1 urine output monitor with a Wi-Fi web interface
#
# Files on CIRCUITPY:  code.py, uom.py, uom_web.py, uom_telegram.py, uom_config.py,
#                      dashboard.html, chart.umd.js, index.html (older page, at /classic),
#                      lib/: adafruit_httpserver, adafruit_requests, adafruit_connection_manager
#                      (circup install adafruit_httpserver adafruit_requests)
#
# Wi-Fi and Telegram settings live in uom_config.py (copy uom_config.example.py).
#  - "station": the Pico joins a phone hotspot, sends Telegram alerts, and serves the
#    dashboard at the address printed in the Serial Monitor.
#  - "ap": the Pico makes its own network (no internet, no Telegram); open http://192.168.4.1
# The dashboard opens at that address: enter the weight and press "Connect to service".

import time
import os
import gc
import traceback
import supervisor
import board
import digitalio
import pwmio
import neopixel_write
import wifi
import socketpool

from uom import Monitor, SimScale
from uom_web import make_server

# Wi-Fi and Telegram settings (kept out of GitHub in uom_config.py)
try:
    import uom_config as cfg
except ImportError:
    cfg = None
    print("No uom_config.py: using the Pico's own network, Telegram off")
WIFI_MODE = getattr(cfg, "WIFI_MODE", "ap")
AP_NAME = getattr(cfg, "AP_NAME", "UOM-1")
AP_PASSWORD = getattr(cfg, "AP_PASSWORD", "uom1bench")
STATION_SSID = getattr(cfg, "WIFI_SSID", "")
STATION_PASSWORD = getattr(cfg, "WIFI_PASSWORD", "")
TELEGRAM_TOKEN = getattr(cfg, "TELEGRAM_TOKEN", "")
RECIPIENTS = getattr(cfg, "RECIPIENTS", [])

# ===================== settings =====================

FACTOR = 2220.7               # counts per gram, from your calibration
SIMULATE = False              # True = fake bag, no load cell needed (for demos)
SPEED = 60                    # demo: up to 60 (one minute per second). Never above 60.

DT_PIN, SCK_PIN = board.GP5, board.GP6       # HX711
RING_PIN = board.GP18                        # NeoPixel ring Data Input
SPEAKER_PIN = board.GP20                     # PAM8302 A+
BUTTON_PIN = board.GP17                      # acknowledge button
BRIGHT = 40                                  # ring brightness, 0 to 255
# ====================================================


def ms():
    return time.monotonic_ns() // 1000000


class App:
    def __init__(self):
        self.mon = Monitor()
        self.sim = SimScale() if SIMULATE else None
        self.speed = SPEED
        self.mono0 = ms()
        self.wall0 = 1700000000     # replaced by the browser's clock at start
        self.ms = ms

    def wall(self):
        return self.wall0 + (ms() - self.mono0) * self.speed // 1000

    def set_clock(self, epoch_s, tz):
        self.mono0 = ms()
        self.wall0 = epoch_s


app = App()
assert 1 <= SPEED <= 60, "SPEED must be 1 to 60"

# ---------------- HX711 ----------------
dt = digitalio.DigitalInOut(DT_PIN)
dt.direction = digitalio.Direction.INPUT
sck = digitalio.DigitalInOut(SCK_PIN)
sck.direction = digitalio.Direction.OUTPUT
sck.value = False


def read_hx711():
    """Raw count, or None if no new sample is ready yet."""
    if dt.value:
        return None
    v = 0
    for _ in range(24):
        sck.value = True
        v = (v << 1) | dt.value
        sck.value = False
    sck.value = True
    sck.value = False
    return v - 0x1000000 if v & 0x800000 else v


# ---------------- ring, speaker, button ----------------
ring = digitalio.DigitalInOut(RING_PIN)
ring.direction = digitalio.Direction.OUTPUT
speaker = pwmio.PWMOut(SPEAKER_PIN, frequency=880, duty_cycle=0, variable_frequency=True)
button = digitalio.DigitalInOut(BUTTON_PIN)
button.direction = digitalio.Direction.INPUT
button.pull = digitalio.Pull.UP

B = BRIGHT
COLOURS = {"standby": (0, 0, B // 3), "normal": (0, B, 0), "advisory": (B, B * 45 // 100, 0),
           "critical": (B, 0, 0), "technical": (0, B * 8 // 10, B), "off": (0, 0, 0)}
FLASH = {"advisory": 1000, "critical": 250}          # half-period in ms (0.5 Hz, 2 Hz)
# (pitch Hz, [(on ms, off ms), ...], repeat every ms)
SOUNDS = {"advisory": (660, [(150, 100)] * 3, 20000),
          "critical": (880, [(120, 80)] * 4 + [(120, 400)] + [(120, 80)] * 5, 10000),
          "technical": (1200, [(80, 0)], 30000)}


class Signals:
    def __init__(self):
        self.shown = None
        self.sound = None
        self.next_burst = 0
        self.plan = []

    def update(self, t, level, sound):
        colour = level
        if level in FLASH and (t // FLASH[level]) % 2:
            colour = "off"
        if colour != self.shown:
            r, g, b = COLOURS[colour]
            neopixel_write.neopixel_write(ring, bytearray([g, r, b] * 16))
            self.shown = colour
        if sound != self.sound:
            self.sound = sound
            self.next_burst = t
            self.plan = []
        if not sound:                 # None or False: silent
            speaker.duty_cycle = 0
            return
        pitch, pulses, period = SOUNDS[sound]
        if t >= self.next_burst:
            self.next_burst = t + period
            speaker.frequency = pitch
            self.plan = []
            at = t
            for on, off in pulses:
                self.plan.append((at, at + on))
                at += on + off
        on = False
        for a, b in self.plan:
            if a <= t < b:
                on = True
        speaker.duty_cycle = 32768 if on else 0


signals = Signals()

# ---------------- Wi-Fi and web server ----------------
host = None
if WIFI_MODE == "station":
    for attempt in range(1, 4):
        try:
            print("Joining Wi-Fi", STATION_SSID, "(try {} of 3)".format(attempt))
            wifi.radio.connect(STATION_SSID, STATION_PASSWORD)
            host = str(wifi.radio.ipv4_address)
            break
        except Exception as e:
            print("  could not join:", e)
            time.sleep(2)
    if host is None:
        print("Hotspot not found: falling back to the Pico's own network (no Telegram)")
if host is None:
    wifi.radio.start_ap(AP_NAME, AP_PASSWORD)
    host = str(wifi.radio.ipv4_address_ap)
    print("Wi-Fi network:", AP_NAME, " password:", AP_PASSWORD)
port = 80
if os.getenv("CIRCUITPY_WIFI_SSID"):
    port = 8080    # CircuitPython's own web editor already uses port 80
pool = socketpool.SocketPool(wifi.radio)
server = make_server(pool, app, "/")
server.start(host, port)
print("Open http://{}{}".format(host, "" if port == 80 else ":" + str(port)))
print("Demo mode" if SIMULATE else "Load cell mode", "| speed x", SPEED)

# ---------------- Telegram ----------------
bot = None
if TELEGRAM_TOKEN and wifi.radio.ipv4_address is not None:
    try:
        import ssl
        import adafruit_requests
        from uom_telegram import TelegramBot
        http = adafruit_requests.Session(pool, ssl.create_default_context())
        bot = TelegramBot(http, TELEGRAM_TOKEN, RECIPIENTS, app.mon, app.wall, ms)
        print("Telegram on:", len(RECIPIENTS), "recipient(s)")
    except Exception as e:
        print("Telegram off:", e)
elif TELEGRAM_TOKEN:
    print("Telegram off: needs WIFI_MODE = \"station\" and a hotspot with internet")

# ---------------- main loop ----------------
last_sample = ms()
last_none = 0
last_gc = ms()
pressed = False
errors = []                    # times of recent unexpected errors


def survive(where, e):
    """Print the error, free memory and keep running; restart only if errors pile up."""
    print("!! error in", where, "-", repr(e), "| free memory before clean-up:", gc.mem_free())
    traceback.print_exception(e)
    gc.collect()
    now = ms()
    errors.append(now)
    while errors and now - errors[0] > 60000:
        errors.pop(0)
    if len(errors) >= 20:
        print("!! 20 errors in 1 minute: restarting code.py")
        speaker.duty_cycle = 0
        supervisor.reload()


while True:
    t = ms()
    wall = app.wall()

    try:
        if app.sim is not None:
            if t - last_sample >= 100:
                last_sample = t
                g = app.sim.read(t, wall)
                app.mon.feed(g, t, wall, None if g is not None else "no_data")
        else:
            raw = read_hx711()
            if raw is not None:
                last_sample = t
                if raw in (0, -1):
                    app.mon.feed(None, t, wall, "no_data")          # DT line stuck low
                elif raw >= 8388000 or raw <= -8388000:
                    app.mon.feed(None, t, wall, "saturated")
                else:
                    app.mon.feed(raw / FACTOR, t, wall)
            elif t - last_sample > 1000 and t - last_none >= 200:
                last_none = t
                app.mon.feed(None, t, wall, "no_data")              # no samples at all

        app.mon.tick(wall)

        down = not button.value
        if down and not pressed:
            app.mon.ack("button", "", wall)
            app.mon.tick(wall)
        pressed = down

        level, sound = app.mon.signal()
        signals.update(t, level, sound)
    except Exception as e:                  # never let one bad sample stop the alarms
        survive("measurement", e)

    try:
        server.poll()
    except Exception as e:                  # a failed web request must not stop the monitor
        survive("web request", e)

    # Telegram: at most one request per loop, never while a beep is playing
    if bot is not None and speaker.duty_cycle == 0:
        try:
            if bot.step():
                last_sample = ms()     # the pause was ours, not a scale fault
        except Exception as e:
            print("Telegram error:", e)

    if t - last_gc >= 5000:                 # tidy memory every 5 s
        last_gc = t
        gc.collect()
