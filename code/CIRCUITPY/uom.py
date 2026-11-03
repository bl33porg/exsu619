# uom.py - UOM-1 measurement, artifact rejection and alarm rules
#
# Plain Python with no hardware: it runs on the Pico (CircuitPython) and on a
# laptop, so the rules can be tested without the rig.
#
# Times are integers (CircuitPython floats cannot hold epoch seconds):
#   t     = real milliseconds from the Pico's clock (for artifact timers)
#   wall  = clock seconds (UTC epoch) for booking urine and alarm windows
#           In demo mode the wall clock runs faster than real time.

import random

# ---- clinical rules (CR-2) ----
NURSE_RATE = 0.5        # mL/kg/h averaged over 3 h  -> advisory (nurse)
CRIT_RATE = 0.3         # mL/kg/h averaged over 6 h  -> critical (physician)
NONE_ML = 2.0           # less than this in 2 h       -> critical ("stopped")
REALERT_S = 3600        # an acknowledged alarm sounds again after 1 h

# ---- measurement and artifact rules (CR-1, CR-3) ----
DENSITY = 1.015         # grams per mL of urine
MOVE_G = 1.0            # wobble of the 1 s averages above this = bag moving
ACCEPT_G = 1.0          # smallest settled rise booked as urine
STEP_G = 5.0            # bigger rises are held until confirmed
EMPTY_G = 20.0          # bigger falls can be an emptying
RISE_CONFIRM_MS = 180000    # a step up must stay 3 min to count as urine
EMPTY_CONFIRM_MS = 60000    # a step down must stay 1 min to count as emptying
FALL_LIMIT_MS = 600000      # a small drop lasting 10 min = unexplained step
UNSTABLE_LIMIT_MS = 600000  # moving for 10 min = technical alarm
NODATA_MS = 10000           # no usable sample for 10 s = technical alarm
UNDO_EMPTY_MS = 600000      # bag back at old level within 10 min = it was lifted
BAG_LIMIT_ML = 550          # 1 kg load cell: empty the bag before this

LOG_MAX = 300

TECH_TEXT = {
    "no_data": "Scale not answering. Check the HX711 wires.",
    "saturated": "Scale reading saturated. Check the load cell wires and mount.",
    "unstable": "Scale has been moving for 10 min. Urine cannot be measured.",
    "negative_step": "Unexplained drop in weight. Check the bag and tubing.",
    "bag_full": "Bag nearly full. Empty the urimeter.",
}
DATA_FAULTS = ("no_data", "saturated", "unstable")


def _down(x):
    """Round down to 2 decimals, so 0.498 shows as 0.49 (below 0.5), not 0.50."""
    return int(x * 100) / 100


class Monitor:
    def __init__(self):
        self.session = None
        self.log = []
        self.log_count = 0          # total entries ever logged (the log itself keeps 300)
        self._reset_measure()
        self.blocks = []
        self.blk_sum = 0.0
        self.blk_n = 0
        self.blk_t = None
        self.grams = None
        self.moving = False
        self.disturbed = False
        self.dist_t = 0
        self.dist_wall = 0
        self.dist_moved = False
        self.fault = None
        self.fault_since = None

    def _reset_measure(self):
        self.ref = None
        self.pending = None
        self.pend_t = 0
        self.pend_wall = 0
        self.pend_level = 0.0
        self.pend_moved = False
        self.nested_up = False
        self.last_still = None
        self.hold = False
        self.last_empty = None
        self.bag_ml = 0.0
        self.total_ml = 0.0
        self.mins = {}
        self.hours = {}
        self.art_hours = {}
        self.counts = {"emptying": 0, "swing": 0, "tubing": 0, "negative": 0}
        self.alarms = {
            "advisory": {"active": False, "armed": True},
            "critical": {"active": False, "armed": True},
        }
        self.tech = {}
        self.last_min = None
        self.evaluable = True

    # ------------------------------------------------------------ session
    def start(self, code, weight, gender, wall, tz):
        self._reset_measure()
        self.log = []
        self.session = {"code": code, "weight": weight, "gender": gender,
                        "start": wall, "tz": tz}
        if self.grams is not None and not self.moving and not self.disturbed:
            self.ref = self.grams
        self._log(wall, "session", "Monitoring started: {}, {} kg, {}".format(
            code, weight, gender))

    def stop(self, wall):
        if self.session:
            self._log(wall, "session", "Monitoring ended")
        self.session = None

    def set_weight(self, weight, wall):
        old = self.session["weight"]
        self.session["weight"] = weight
        self._log(wall, "weight", "Weight changed from {} to {} kg".format(old, weight))

    # ------------------------------------------------------------ samples
    def feed(self, g, t, wall, fault=None):
        """One sample in grams, or None with fault 'no_data' / 'saturated'."""
        if g is None:
            if self.fault_since is None:
                self.fault_since = t
            self.fault = fault
            self.grams = None
            self._disturb(t, wall)
            self.blocks = []
            self.blk_n = 0
            self.blk_sum = 0.0
            self.blk_t = None
            if t - self.fault_since >= NODATA_MS:
                self._tech_on(fault, wall)
            return
        if self.fault_since is not None:
            self.fault_since = None
            self.fault = None
            self._tech_off("no_data", wall)
            self._tech_off("saturated", wall)
        if self.blk_t is None:
            self.blk_t = t
        self.blk_sum += g
        self.blk_n += 1
        if t - self.blk_t >= 1000:
            m = self.blk_sum / self.blk_n
            self.blk_sum = 0.0
            self.blk_n = 0
            self.blk_t = t
            self._block(m, t, wall)

    def _disturb(self, t, wall):
        if not self.disturbed:
            self.disturbed = True
            self.dist_t = t
            self.dist_wall = wall
            self.dist_moved = False
        elif t - self.dist_t >= UNSTABLE_LIMIT_MS and self.fault is None:
            self._tech_on("unstable", wall)

    def _block(self, m, t, wall):
        self.grams = m
        b = self.blocks
        b.append(m)
        if len(b) > 5:
            b.pop(0)
        if len(b) < 5:
            self._disturb(t, wall)
            return
        wobble = max(abs(b[i + 1] - 2 * b[i] + b[i - 1]) for i in (1, 2, 3))
        if wobble > MOVE_G:
            self.moving = True
            self._disturb(t, wall)
            self.dist_moved = True
            return
        self.moving = False
        was = self.disturbed and self.dist_moved
        t0wall = self.dist_wall if self.disturbed else wall
        if self.disturbed:
            self.disturbed = False
            self._tech_off("unstable", wall)
        if self.session is None:
            return
        if self.ref is None:
            self.ref = m
            return
        self._still(m, t, wall, was, t0wall)

    def _pend(self, kind, t, wall, level, moved):
        self.pending = kind
        self.pend_t = t
        self.pend_wall = wall
        self.pend_level = level
        self.pend_moved = moved
        self.nested_up = False

    def _still(self, s, t, wall, was, t0wall):
        jump = 0.0 if self.last_still is None else s - self.last_still
        self._still_core(s, t, wall, was, t0wall, jump)
        self.last_still = s

    def _still_core(self, s, t, wall, was, t0wall, jump):
        for _ in range(3):
            d = s - self.ref
            p = self.pending
            if p is None:
                if d >= STEP_G:
                    if not self._undo_empty(s, t, wall):
                        self._pend("rise", t, t0wall, s, was)
                    return
                if d <= -EMPTY_G:
                    self._pend("empty", t, t0wall, s, was)
                    return
                if d <= -3.0:
                    self._pend("fall", t, t0wall, s, was)
                    return
                if was:
                    self._artifact("swing", t0wall, "Bag moved or swung, level unchanged")
                if d >= ACCEPT_G:
                    self._accept(d, wall)
                return
            el = t - self.pend_t
            if p == "rise":
                # a second artifact during the check (e.g. a lean): note it, count it on release
                if was and jump >= STEP_G:
                    self.nested_up = True
                elif self.nested_up and jump <= -STEP_G:
                    self.nested_up = False
                    self._artifact("tubing", wall, "Weight on the tubing or bag, then released")
                if d >= STEP_G - 2:
                    if self._undo_empty(s, t, wall):
                        self.pending = None
                    elif el >= RISE_CONFIRM_MS and not self.nested_up:
                        self.pending = None
                        if self.pend_moved:
                            self._artifact("swing", self.pend_wall,
                                           "Bag moved; urine that arrived meanwhile kept")
                        self._accept(d, wall, self.pend_wall)
                    return
                if abs(d) < 3:
                    self.pending = None
                    self._artifact("tubing", self.pend_wall,
                                   "Weight on the tubing or bag, then released")
                    return
                self.pending = None
                continue
            if p == "empty":
                # a sudden return to the old level = the bag was only lifted;
                # a slow climb is urine refilling the emptied meter
                if was and jump >= STEP_G and abs(d) < 5:
                    self.pending = None
                    self._artifact("tubing", self.pend_wall, "Bag lifted and put back")
                    return
                if d > 5 and jump >= STEP_G:
                    self.pending = None
                    continue
                if el >= EMPTY_CONFIRM_MS:
                    self.pending = None
                    self._emptying(s, t, self.pend_wall, wall)
                return
            if p == "fall":
                if abs(d) < 3:
                    self.pending = None
                    self._artifact("tubing", self.pend_wall,
                                   "Bag or tubing pushed up, then released")
                    return
                if d <= -EMPTY_G:
                    self._pend("empty", t, self.pend_wall, s, self.pend_moved)
                    return
                if d >= 3:
                    self.pending = None
                    continue
                if el >= FALL_LIMIT_MS:
                    self.pending = None
                    self._artifact("negative", self.pend_wall,
                                   "Unexplained drop of {:.0f} g, level reset".format(-d))
                    self.ref = s
                    self._tech_on("negative_step", wall)
                return
        return

    def _accept(self, d, wall, since=None):
        """Book d grams as urine. With `since`, spread it over the minutes it arrived."""
        ml = d / DENSITY
        self.ref += d
        self.bag_ml += ml
        self.total_ml += ml
        start = self.session["start"]
        b = max(wall, start)
        a = b if since is None else min(max(since, start), b)
        m0 = a // 60
        m1 = b // 60
        per = ml / (m1 - m0 + 1)
        for m in range(m0, m1 + 1):
            self.mins[m] = self.mins.get(m, 0.0) + per
            h = m // 60
            self.hours[h] = self.hours.get(h, 0.0) + per
        if self.bag_ml >= BAG_LIMIT_ML:
            self._tech_on("bag_full", wall)

    def _emptying(self, s, t, at, wall):
        level = self.pend_level              # first settled level after the drain
        drained = (self.ref - level) / DENSITY
        self.last_empty = {"t": t, "ref": self.ref, "bag": self.bag_ml}
        self.ref = level
        self.bag_ml = 0.0
        self._artifact("emptying", at, "Urimeter emptied: {:.0f} mL drained".format(drained))
        self._tech_off("bag_full", wall)
        if s - self.ref >= ACCEPT_G:         # urine that arrived during the check
            self._accept(s - self.ref, wall, at)

    def _undo_empty(self, s, t, wall):
        le = self.last_empty
        if le is None or t - le["t"] > UNDO_EMPTY_MS or abs(s - le["ref"]) >= 5:
            return False
        self.last_empty = None
        self.ref = le["ref"]
        self.bag_ml = le["bag"]
        self.counts["emptying"] -= 1
        h = wall // 3600
        if h in self.art_hours:
            self.art_hours[h]["emptying"] = self.art_hours[h].get("emptying", 1) - 1
        self._artifact("tubing", wall, "Not an emptying: bag was lifted and put back")
        if s - self.ref >= ACCEPT_G:
            self._accept(s - self.ref, wall)
        return True

    def _artifact(self, kind, wall, text):
        self.counts[kind] += 1
        h = wall // 3600
        a = self.art_hours.setdefault(h, {})
        a[kind] = a.get(kind, 0) + 1
        self._log(wall, "artifact", text)

    # ------------------------------------------------------------ alarms
    def _tech_on(self, name, wall):
        if self.session is None or name in self.tech:
            return
        self.tech[name] = {"since": wall, "acked": False, "ack_wall": 0}
        self._log(wall, "tech", "Technical alarm: " + TECH_TEXT[name])

    def _tech_off(self, name, wall):
        if name in self.tech:
            del self.tech[name]
            self._log(wall, "clear", "Technical alarm cleared: " + TECH_TEXT[name])

    def volume(self, wall, n):
        m = wall // 60
        mins = self.mins
        return sum(mins.get(k, 0.0) for k in range(m - n + 1, m + 1))

    def rate(self, wall, n):
        return self.volume(wall, n) / (self.session["weight"] * n / 60)

    def tick(self, wall):
        """Re-check the alarm rules once per clock minute."""
        if self.session is None:
            return
        m = wall // 60
        if m == self.last_min:
            return
        self.last_min = m
        self._evaluate(wall)
        for k in [k for k in self.mins if k < m - 720]:
            del self.mins[k]
        h = wall // 3600
        for k in [k for k in self.hours if k < h - 48]:
            del self.hours[k]
        for k in [k for k in self.art_hours if k < h - 48]:
            del self.art_hours[k]

    def _evaluate(self, wall):
        s = self.session
        el = (wall - s["start"]) // 60
        r1 = self.rate(wall, 60)
        r3 = self.rate(wall, 180)
        r6 = self.rate(wall, 360)
        v2 = self.volume(wall, 120)
        self.evaluable = not any(k in self.tech for k in DATA_FAULTS)
        self.hold = self.pending is not None or self.disturbed
        can_raise = not self.hold
        adv = el >= 180 and r3 < NURSE_RATE
        low6 = el >= 360 and r6 < CRIT_RATE
        none2 = el >= 120 and v2 < NONE_ML
        A = self.alarms["advisory"]
        C = self.alarms["critical"]
        if self.evaluable:
            ccond = low6 or none2
            none_txt = ("No urine for 2 h ({:.0f} mL). Check the catheter for kinks or a "
                        "blockage: this device cannot tell a blocked catheter from kidneys "
                        "that have stopped.".format(v2))
            if not C["active"]:
                if not ccond:
                    C["armed"] = True
                elif C["armed"] and can_raise:
                    if none2:
                        txt = none_txt
                    else:
                        txt = "Output {:.2f} mL/kg/h over 6 h, below {}.".format(_down(r6), CRIT_RATE)
                    self._raise(C, "critical", txt, wall)
            elif none2 and can_raise and not C["text"].startswith("No urine"):
                C["text"] = none_txt
                C["acked"] = False
                self._log(wall, "alarm", "Critical alarm (Physician): " + none_txt)
            elif (C["acked"] and not ccond and r1 >= NURSE_RATE
                  and wall - C["since"] >= 3600):
                self._clear(C, "critical", wall)
            if not A["active"]:
                if not adv:
                    A["armed"] = True
                elif A["armed"] and can_raise:
                    self._raise(A, "advisory",
                                "Output {:.2f} mL/kg/h over 3 h, below {}.".format(
                                    _down(r3), NURSE_RATE), wall)
            elif r1 >= NURSE_RATE and wall - A["since"] >= 3600:
                self._clear(A, "advisory", wall)
                A["armed"] = not adv
        for name, a in self.alarms.items():
            if a["active"] and a["acked"] and wall - a["ack_wall"] >= REALERT_S:
                a["acked"] = False
                self._log(wall, "alarm", "{} alarm sounding again: still unresolved 1 h "
                          "after acknowledgement".format(name.capitalize()))
        for name, a in self.tech.items():
            if a["acked"] and wall - a["ack_wall"] >= REALERT_S:
                a["acked"] = False

    def _raise(self, a, name, text, wall):
        a.update({"active": True, "armed": False, "acked": False,
                  "since": wall, "ack_wall": 0, "text": text})
        who = "Nurse" if name == "advisory" else "Physician"
        self._log(wall, "alarm", "{} alarm ({}): {}".format(name.capitalize(), who, text))

    def _clear(self, a, name, wall):
        a["active"] = False
        self._log(wall, "clear", "{} alarm cleared: output back above {} mL/kg/h for 1 h".format(
            name.capitalize(), NURSE_RATE))

    def ack(self, source, who, wall):
        names = []
        for name, a in self.alarms.items():
            if a["active"] and not a["acked"]:
                a["acked"] = True
                a["ack_wall"] = wall
                names.append(name)
        for name, a in self.tech.items():
            if not a["acked"]:
                a["acked"] = True
                a["ack_wall"] = wall
                names.append("technical")
        if "negative_step" in self.tech:
            del self.tech["negative_step"]
        if names:
            by = " by " + who if who else ""
            self._log(wall, "ack", "Acknowledged{} ({}): {}".format(
                by, source, ", ".join(names)))
        return len(names)

    def signal(self):
        """(level, sound) for the ring, speaker and web page."""
        if self.session is None:
            return "standby", False
        C = self.alarms["critical"]
        A = self.alarms["advisory"]
        if C["active"]:
            level = "critical"
        elif A["active"]:
            level = "advisory"
        elif self.tech:
            level = "technical"
        else:
            level = "normal"
        if C["active"] and not C["acked"]:
            sound = "critical"
        elif A["active"] and not A["acked"]:
            sound = "advisory"
        elif any(not a["acked"] for a in self.tech.values()):
            sound = "technical"
        else:
            sound = None
        return level, sound

    # ------------------------------------------------------------ reports
    def note(self, wall, kind, text):
        """Add an entry to the alarm and event record (used by the Telegram bot)."""
        self._log(wall, kind, text)

    def _log(self, wall, kind, text):
        self.log_count += 1
        self.log.append([wall, kind, text])
        if len(self.log) > LOG_MAX:
            self.log.pop(0)

    def state(self, wall):
        level, sound = self.signal()
        out = {"now": wall, "session": self.session, "level": level, "sound": sound,
               "scale": {"grams": None if self.grams is None else round(self.grams, 1),
                         "status": self._scale_status(), "pending": self.pending}}
        s = self.session
        if s is None:
            return out
        w = s["weight"]
        el = (wall - s["start"]) // 60
        hour_start = max(s["start"], wall - wall % 3600)
        hour_ml = self.hours.get(wall // 3600, 0.0)
        hour_h = max(wall - hour_start, 60) / 3600
        out["live"] = {
            "el_min": el, "total_ml": round(self.total_ml, 1),
            "bag_ml": round(self.bag_ml, 1), "bag_limit": BAG_LIMIT_ML,
            "hour_ml": round(hour_ml, 1), "hour_rate": round(hour_ml / (w * hour_h), 2),
            "r1": round(self.rate(wall, 60), 2), "r3": round(self.rate(wall, 180), 2),
            "r6": round(self.rate(wall, 360), 2), "v2": round(self.volume(wall, 120), 1),
            "evaluable": self.evaluable, "hold": self.hold,
        }
        out["rules"] = {"nurse": NURSE_RATE, "crit": CRIT_RATE, "none_ml": NONE_ML}
        alarms = []
        for name in ("critical", "advisory"):
            a = self.alarms[name]
            if a["active"]:
                alarms.append({"level": name, "text": a["text"], "since": a["since"],
                               "acked": a["acked"], "ack_wall": a["ack_wall"]})
        for name, a in self.tech.items():
            alarms.append({"level": "technical", "text": TECH_TEXT[name], "since": a["since"],
                           "acked": a["acked"], "ack_wall": a["ack_wall"]})
        out["alarms"] = alarms
        out["artifacts"] = self.counts
        return out

    def _scale_status(self):
        if self.fault:
            return self.fault
        if self.grams is None or len(self.blocks) < 5:
            return "settling"
        return "moving" if self.moving else "still"

    def history(self, wall):
        s = self.session
        if s is None:
            return {"hours": [], "trend": [], "log": []}
        w = s["weight"]
        start = s["start"]
        tz = s["tz"]
        # hourly rows (ward-round chart)
        rows = []
        h0 = max(start // 3600, wall // 3600 - 47)
        cum = 0.0
        day = None
        for h in range(start // 3600, wall // 3600 + 1):
            hs = h * 3600
            ml = self.hours.get(h, 0.0)
            d = (hs - tz * 60 - 7 * 3600) // 86400   # fluid day starts 07:00 local
            if d != day:
                day = d
                cum = 0.0
            cum += ml
            if h < h0:
                continue
            covered = min(hs + 3600, wall) - max(hs, start)
            rate = ml / (w * max(covered, 60) / 3600)
            alarm_txt = []
            for e in self.log:
                if hs <= e[0] < hs + 3600:
                    if e[1] == "alarm":
                        alarm_txt.append(["alarm", e[0], e[2].split(" (")[0].split(":")[0]])
                    elif e[1] == "ack":
                        alarm_txt.append(["ack", e[0], "Acknowledged"])
            rows.append({"h": hs, "ml": round(ml, 1), "rate": round(rate, 2),
                         "cum": round(cum, 1), "partial": covered < 3600,
                         "art": self.art_hours.get(h, {}), "events": alarm_txt})
        # trend: 1 h and 3 h rates every 10 min, last 12 h
        m_now = wall // 60
        m_first = max(start // 60, m_now - 720)
        lo = m_first - 180
        pre = [0.0]
        for k in range(lo, m_now + 1):
            pre.append(pre[-1] + self.mins.get(k, 0.0))
        trend = []
        p = m_first - m_first % 10 + 10
        pts = list(range(p, m_now + 1, 10))
        if not pts or pts[-1] != m_now:
            pts.append(m_now)
        for pm in pts:
            i = pm - lo + 1
            elm = pm - start // 60
            r1 = (pre[i] - pre[i - 60]) / w if elm >= 60 else None
            r3 = (pre[i] - pre[i - 180]) / (w * 3) if elm >= 180 else None
            trend.append([pm * 60, None if r1 is None else round(r1, 3),
                          None if r3 is None else round(r3, 3)])
        return {"hours": rows, "trend": trend, "log": self.log[-120:]}

    def csv(self, wall):
        s = self.session
        if s is None:
            return "No session\n"
        h = self.history(wall)
        lines = ["UOM-1 export,patient code,{},weight kg,{},gender,{}".format(
            s["code"], s["weight"], s["gender"]),
            "",
            "hour start (UTC epoch),urine mL,mL/kg/h,running total since 07:00 mL,"
            "emptyings,swings,tubing/bag,unexplained,partial hour"]
        for r in h["hours"]:
            a = r["art"]
            lines.append("{},{},{},{},{},{},{},{},{}".format(
                r["h"], r["ml"], r["rate"], r["cum"], a.get("emptying", 0),
                a.get("swing", 0), a.get("tubing", 0), a.get("negative", 0),
                "yes" if r["partial"] else "no"))
        lines += ["", "time (UTC epoch),type,event"]
        for e in self.log:
            lines.append('{},{},"{}"'.format(e[0], e[1], e[2].replace('"', "'")))
        return "\n".join(lines) + "\n"


class SimScale:
    """Fake bag for demos and tests: urine flows in, artifacts on request."""

    def __init__(self, flow=70.0, empty_g=180.0):
        self.flow = flow
        self.empty_g = empty_g
        self.content = 0.0
        self.last_wall = None
        self.events = []

    def action(self, name, t):
        if name == "empty":
            self.events.append(["swing", t, t + 15000])
            self.events.append(["drain", t + 15000, t + 15000])
        elif name == "swing":
            self.events.append(["swing", t, t + 30000])
        elif name == "lean":
            self.events.append(["lean", t, t + 60000])
        elif name == "lift":
            self.events.append(["lift", t, t + 40000])
        elif name == "disconnect":
            self.events.append(["disconnect", t, t + 20000])

    def read(self, t, wall):
        if self.last_wall is not None and wall > self.last_wall:
            self.content += self.flow * DENSITY * (wall - self.last_wall) / 3600
        self.last_wall = wall
        g = self.empty_g + self.content
        keep = []
        for e in self.events:
            kind, t0, t1 = e
            if kind == "drain":
                if t >= t0:
                    self.content = 0.0
                    g = self.empty_g
                    continue
            elif t0 <= t <= t1:
                if kind == "swing":
                    phase = ((t - t0) % 1500) / 1500
                    g += 25 * (1 - 4 * abs(phase - 0.5))
                elif kind == "lean":
                    g += 150
                elif kind == "lift":
                    g = 3.0
                elif kind == "disconnect":
                    return None
            if t <= t1 or kind == "drain":
                keep.append(e)
        self.events = keep
        return g + random.uniform(-0.03, 0.03)
