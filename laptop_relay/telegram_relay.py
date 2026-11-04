# telegram_relay.py - sends UOM-1 Telegram alerts from a laptop
#
# Use this when the Pico cannot reach the internet itself (some phone hotspots
# block it). The Pico keeps measuring and sounding its own alarms; this program,
# on a laptop joined to the same Wi-Fi, reads the Pico every 2 s and handles
# Telegram: alerts by role, /rate N, /status, /hourly, /ack, /stop, /resume.
# Everything is still recorded in the Pico's alarm and event log.
#
# Needs:  pip3 install requests
# Config: uom_config.py next to this file, with TELEGRAM_TOKEN, RECIPIENTS and
#         PICO_URL (the address the Pico prints, e.g. "http://172.20.10.3").
# Run:    python3 telegram_relay.py
#
# On the Pico, set TELEGRAM_TOKEN = "" in its uom_config.py so only one of the two
# sends messages.

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "firmware"))

import requests                                   # noqa: E402
from uom_telegram import TelegramBot              # noqa: E402

REFRESH_MS = 2000
UNREACHABLE_MS = 60000


def ms():
    return time.monotonic_ns() // 1_000_000


class RemoteMonitor:
    """Looks like uom.Monitor to the Telegram bot, but asks the Pico over Wi-Fi."""

    def __init__(self, base, http):
        self.base = base.rstrip("/")
        self.http = http
        self.session = None
        self.log = []
        self.log_count = 0
        self.now = 0
        self.speed = 1
        self.t_ref = ms()
        self.first = True

    def _get(self, path):
        r = self.http.get(self.base + path, timeout=5)
        r.raise_for_status()
        return r.json()

    def _post(self, path, data):
        r = self.http.post(self.base + path, json=data, timeout=5)
        r.raise_for_status()
        return r.json()

    def refresh(self):
        d = self._get("/api/log?since={}".format(self.log_count))
        if self.first:
            self.first = False          # do not resend alarms from before the relay started
        else:
            self.log.extend(d["entries"])
            self.log = self.log[-300:]
        self.log_count = d["log_count"]
        self.session = d["session"]
        self.now = d["now"]
        self.speed = d.get("speed", 1)
        self.t_ref = ms()

    def wall(self):
        """The Pico's clock (seconds), estimated between refreshes."""
        return self.now + (ms() - self.t_ref) * self.speed // 1000

    # --- what the bot calls ---
    def note(self, wall, kind, text):
        try:
            self._post("/api/note", {"text": text})
        except Exception as e:
            print("could not record on the Pico:", text, "-", e)

    def volume(self, wall, n):
        return self._get("/api/volume?minutes={}".format(int(n)))["ml"]

    def state(self, wall):
        return self._get("/api/state")

    def history(self, wall):
        return self._get("/api/history")

    def ack(self, source, who, wall):
        return self._post("/api/ack", {"who": who, "source": source})["acknowledged"]

    def tick(self, wall):
        pass


def main():
    try:
        import uom_config as cfg
    except ImportError:
        sys.exit("Put uom_config.py (with TELEGRAM_TOKEN, RECIPIENTS, PICO_URL) next to this file.")
    token = getattr(cfg, "TELEGRAM_TOKEN", "")
    url = getattr(cfg, "PICO_URL", "")
    if not token or not url:
        sys.exit("uom_config.py needs TELEGRAM_TOKEN and PICO_URL")

    http = requests.Session()
    mon = RemoteMonitor(url, http)
    print("Connecting to the Pico at", url, "...")
    while True:
        try:
            mon.refresh()
            break
        except Exception as e:
            print("  Pico not reachable yet ({}). Same Wi-Fi? Address right? Retrying...".format(e))
            time.sleep(3)
    print("Pico found. Session:", mon.session["code"] if mon.session else "none yet")

    bot = TelegramBot(http, token, cfg.RECIPIENTS, mon, mon.wall, ms)
    print("Telegram relay running for", len(cfg.RECIPIENTS), "recipient(s). Ctrl+C to stop.")
    relay = Relay(mon, bot)
    while True:
        relay.step()
        time.sleep(0.05)


class Relay:
    """One pass of the relay loop: refresh from the Pico, warn if it is lost, one Telegram call."""

    def __init__(self, mon, bot, clock=ms, refresh_ms=REFRESH_MS, unreachable_ms=UNREACHABLE_MS):
        self.mon, self.bot, self.clock = mon, bot, clock
        self.refresh_ms, self.unreachable_ms = refresh_ms, unreachable_ms
        self.last_refresh = clock()
        self.lost_since = None
        self.warned = False

    def step(self):
        t = self.clock()
        if t - self.last_refresh >= self.refresh_ms:
            self.last_refresh = t
            try:
                self.mon.refresh()
                if self.warned:
                    self.bot._to_roles(("technical",), "UOM-1 relay\nThe monitor is reachable again.",
                                       "relay notice")
                    self.mon.note(self.mon.wall(), "msg", "Telegram relay: monitor reachable again")
                self.lost_since = None
                self.warned = False
            except Exception as e:
                if self.lost_since is None:
                    self.lost_since = t
                    print("Pico not reachable:", e)
                if not self.warned and t - self.lost_since >= self.unreachable_ms:
                    self.warned = True
                    self.bot._to_roles(("technical",), "UOM-1 relay\nTECHNICAL ALERT\nThe monitor "
                                       "has not answered for 1 min. Its own ring and speaker still "
                                       "work; check its power and Wi-Fi.", "relay alert")
        try:
            self.bot.step()
        except Exception as e:
            print("Telegram error:", e)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nRelay stopped.")
