# uom_telegram.py - Telegram notification path for UOM-1 (CR-2, CR-4)
#
# Sends alarms to registered Telegram chats by role, and answers commands:
#   /rate N   urine output over the last N hours (1 to 12)
#   /status   current alarm, rates and scale state
#   /hourly   the last hourly totals
#   /ack      acknowledge the active alarms
#   /stop     stop sending me alerts      /resume   send them again
#   /help     list the commands
#
# Plain Python: `http` is anything with post(url, json=..., timeout=...) returning a
# response with .json() and .close() - adafruit_requests on the Pico, requests on a laptop.
#
# Rules: the ring and speaker never wait for Telegram; each call to step() makes at most
# one network request; messages carry the patient code only (no names); every message
# sent, failed or received is written to the alarm and event record.

from uom import _down, NURSE_RATE, CRIT_RATE

API = "https://api.telegram.org/bot"
RETRIES = 3
RETRY_MS = 30000
POLL_MS = 10000
POLL_FAIL_MS = 30000
MAX_HOURS = 12

HELP = ("UOM-1 commands:\n"
        "/rate N - urine output over the last N hours (1 to 12), e.g. /rate 4\n"
        "/status - current alarm, rates and scale\n"
        "/hourly - the last hourly totals\n"
        "/ack - acknowledge the active alarms\n"
        "/stop - stop sending me alerts\n"
        "/resume - send me alerts again")


def _clock(epoch_s, tz_min):
    t = epoch_s - tz_min * 60
    return "{:02d}:{:02d}".format(t // 3600 % 24, t // 60 % 60)


class TelegramBot:
    def __init__(self, http, token, recipients, mon, wall_fn, ms_fn, poll_ms=POLL_MS):
        self.http = http
        self.base = API + token + "/"
        self.rec = {}
        for r in recipients:
            r = dict(r)
            r["active"] = True
            self.rec[int(r["chat_id"])] = r
        self.mon = mon
        self.wall = wall_fn
        self.ms = ms_fn
        self.poll_ms = poll_ms
        self.queue = []
        self.offset = None
        self.next_poll = 0
        self.seen = mon.log_count
        self.consent_logged = False

    # ------------------------------------------------------------ network
    def _call(self, method, payload):
        r = self.http.post(self.base + method, json=payload, timeout=6)
        try:
            data = r.json()
        finally:
            r.close()
        if not data.get("ok"):
            raise RuntimeError(data.get("description", "Telegram error"))
        return data["result"]

    def step(self):
        """Do at most one network request. Returns True if one was made."""
        self._collect()
        t = self.ms()
        if self.offset is None:                     # first call: skip old messages
            self.next_poll = t + self.poll_ms
            try:
                res = self._call("getUpdates", {"offset": -1, "timeout": 0})
                self.offset = res[-1]["update_id"] + 1 if res else 0
                print("Telegram: connected")
            except Exception as e:
                self.offset = None
                self.next_poll = t + POLL_FAIL_MS
                print("Telegram: cannot connect:", e)
            return True
        for q in self.queue:
            if q["next"] <= t:
                self._send(q, t)
                return True
        if t >= self.next_poll:
            self._poll(t)
            return True
        return False

    def _send(self, q, t):
        try:
            self._call("sendMessage", {"chat_id": q["chat"], "text": q["text"]})
            self.queue.remove(q)
            if q["label"]:
                self.mon.note(self.wall(), "msg", "Telegram: {} sent to {}".format(
                    q["label"], q["name"]))
        except Exception as e:
            q["tries"] += 1
            if q["tries"] >= RETRIES:
                self.queue.remove(q)
                self.mon.note(self.wall(), "msg", "Telegram: {} to {} FAILED after {} tries ({})"
                              .format(q["label"] or "reply", q["name"], RETRIES, e))
            else:
                q["next"] = t + RETRY_MS * q["tries"]
            print("Telegram send error:", e)

    def _poll(self, t):
        try:
            res = self._call("getUpdates", {"offset": self.offset, "timeout": 0, "limit": 5})
            self.next_poll = t + self.poll_ms
        except Exception as e:
            self.next_poll = t + POLL_FAIL_MS
            print("Telegram poll error:", e)
            return
        for u in res:
            self.offset = u["update_id"] + 1
            m = u.get("message")
            if m and "chat" in m:
                self._handle(m)

    def _queue(self, chat, name, text, label=None, front=False):
        q = {"chat": chat, "name": name, "text": text, "label": label,
             "tries": 0, "next": 0}
        if front:
            self.queue.insert(0, q)
        else:
            self.queue.append(q)

    # ------------------------------------------------------------ alerts
    def _head(self, wall):
        s = self.mon.session
        if s is None:
            return "UOM-1"
        return "UOM-1 · {} · {}".format(s["code"], _clock(wall, s["tz"]))

    def _to_roles(self, roles, text, label):
        for chat, r in self.rec.items():
            if r["active"] and any(x in r["roles"] for x in roles):
                self._queue(chat, r["name"], text, label)

    def _collect(self):
        """Turn new alarm-record entries into messages (no network)."""
        mon = self.mon
        if mon.session is not None and not self.consent_logged:
            self.consent_logged = True
            for r in self.rec.values():
                mon.note(self.wall(), "msg", "Telegram recipient {} ({}), opted in {}".format(
                    r["name"], ", ".join(r["roles"]), r.get("optin", "not recorded")))
        if mon.session is None:
            self.consent_logged = False
        n = mon.log_count - self.seen
        self.seen = mon.log_count
        if n <= 0:
            return
        for wall, kind, text in mon.log[-n:]:
            head = self._head(wall)
            if kind == "alarm":
                body = text.split("): ", 1)[-1]
                if text.startswith("Critical"):
                    title = "PHYSICIAN ALERT" if "again" not in text else "PHYSICIAN ALERT, STILL UNRESOLVED"
                    self._to_roles(("physician",), "{}\n{}\n{}\nReply /ack to acknowledge, /rate 6 for the "
                                   "last 6 h.".format(head, title, body), "physician alert")
                else:
                    title = "NURSE ALERT" if "again" not in text else "NURSE ALERT, STILL UNRESOLVED"
                    self._to_roles(("nurse",), "{}\n{}\n{}\nReply /ack to acknowledge, /rate 3 for the "
                                   "last 3 h.".format(head, title, body), "nurse alert")
            elif kind == "tech":
                self._to_roles(("technical",), "{}\nTECHNICAL ALERT\n{}".format(
                    head, text.replace("Technical alarm: ", "")), "technical alert")
            elif kind == "ack":
                self._to_roles(("nurse", "physician"), "{}\n{}".format(head, text), "acknowledgement")
            elif kind == "clear":
                roles = ("technical",) if text.startswith("Technical") else ("nurse", "physician")
                self._to_roles(roles, "{}\n{}".format(head, text), "cleared notice")

    # ------------------------------------------------------------ commands
    def _handle(self, m):
        chat = m["chat"]["id"]
        text = (m.get("text") or "").strip()
        wall = self.wall()
        r = self.rec.get(chat)
        if r is None:
            print("Telegram: message from unregistered chat", chat,
                  "- add this chat_id to RECIPIENTS in uom_config.py")
            self.mon.note(wall, "msg", "Telegram: ignored a message from an unregistered chat")
            self._queue(chat, "unregistered chat",
                        "This chat is not registered with UOM-1. Your chat ID is {}. Ask the team "
                        "to add it.".format(chat), front=True)
            return
        parts = text.split()
        cmd = parts[0].lower().split("@")[0] if parts else ""
        self.mon.note(wall, "msg", "Telegram: {} sent {}".format(r["name"], text[:24]))
        if cmd == "/rate":
            reply = self._rate(parts[1] if len(parts) > 1 else "1", wall)
        elif cmd == "/status":
            reply = self._status(wall)
        elif cmd == "/hourly":
            reply = self._hourly(wall)
        elif cmd == "/ack":
            n = self.mon.ack("Telegram", r["name"], wall)
            self.mon.tick(wall)
            reply = "Acknowledged {} alarm{}.".format(n, "" if n == 1 else "s") if n else \
                "There is no unacknowledged alarm."
        elif cmd == "/stop":
            r["active"] = False
            self.mon.note(wall, "msg", "Telegram: {} opted out of alerts".format(r["name"]))
            reply = "You will no longer receive UOM-1 alerts. Send /resume to receive them again."
        elif cmd == "/resume":
            r["active"] = True
            self.mon.note(wall, "msg", "Telegram: {} opted in again".format(r["name"]))
            reply = "You will receive UOM-1 alerts again."
        else:
            reply = HELP
        self._queue(chat, r["name"], reply, front=True)

    def _rate(self, arg, wall):
        mon = self.mon
        s = mon.session
        if s is None:
            return "No monitoring session is running."
        try:
            hours = float(arg)
        except ValueError:
            return "Usage: /rate N, where N is 1 to 12 hours. Example: /rate 4"
        if not 0 < hours <= MAX_HOURS:
            return "The monitor keeps the last {} hours. Ask for 1 to {}, e.g. /rate 4".format(
                MAX_HOURS, MAX_HOURS)
        want = int(hours * 60 + 0.5)
        el = (wall - s["start"]) // 60
        use = min(want, el)
        if use < 1:
            return "Monitoring started less than a minute ago."
        w = s["weight"]
        vol = mon.volume(wall, use)
        rate = vol / (w * use / 60)
        start = wall - use * 60
        arts = sum(1 for e in mon.log if e[1] == "artifact" and start <= e[0] <= wall)
        if rate < CRIT_RATE:
            cmp = "below the physician limit ({})".format(CRIT_RATE)
        elif rate < NURSE_RATE:
            cmp = "below the nurse limit ({})".format(NURSE_RATE)
        else:
            cmp = "at or above the nurse limit ({})".format(NURSE_RATE)
        lines = [self._head(wall)]
        if use < want:
            lines.append("Only {} h {:02d} min monitored so far, so this covers that time.".format(
                use // 60, use % 60))
        lines.append("Last {} ({}–{}): {:.0f} mL = {:.2f} mL/kg/h ({} kg).".format(
            "{} h".format(_fmt_h(use)), _clock(start, s["tz"]), _clock(wall, s["tz"]),
            vol, _down(rate), w))
        lines.append("That is {}. Alarms use 3 h and 6 h averages.".format(cmp))
        lines.append("{} artifact{} rejected in this period.".format(arts, "" if arts == 1 else "s"))
        return "\n".join(lines)

    def _status(self, wall):
        mon = self.mon
        st = mon.state(wall)
        if mon.session is None:
            return "No monitoring session is running. Scale: {}.".format(st["scale"]["status"])
        L = st["live"]
        lines = [self._head(wall), "State: " + st["level"].upper()]
        for a in st["alarms"]:
            lines.append("- {}: {}{}".format(a["level"], a["text"],
                                             " (acknowledged)" if a["acked"] else ""))
        el = L["el_min"]
        lines.append("Monitoring for {} h {:02d} min. Total {:.0f} mL.".format(el // 60, el % 60,
                                                                           L["total_ml"]))
        for label, key, need in (("Last hour", "r1", 60), ("3 h average", "r3", 180),
                                 ("6 h average", "r6", 360)):
            lines.append("{}: {}".format(label, "{:.2f} mL/kg/h".format(_down(L[key]))
                                         if el >= need else "available after {} h".format(need // 60)))
        lines.append("Scale: {}. Bag {:.0f} of {} mL.".format(st["scale"]["status"], L["bag_ml"],
                                                            L["bag_limit"]))
        return "\n".join(lines)

    def _hourly(self, wall):
        s = self.mon.session
        if s is None:
            return "No monitoring session is running."
        rows = self.mon.history(wall)["hours"][-6:]
        lines = [self._head(wall), "Hour | mL | mL/kg/h"]
        for r in rows:
            lines.append("{}–{} | {:.0f} | {:.2f}{}".format(
                _clock(r["h"], s["tz"]), _clock(r["h"] + 3600, s["tz"]), r["ml"], r["rate"],
                " (part)" if r["partial"] else ""))
        return "\n".join(lines)


def _fmt_h(minutes):
    if minutes % 60 == 0:
        return str(minutes // 60)
    return "{:.1f}".format(minutes / 60)
