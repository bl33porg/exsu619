# uom_web.py - the web interface's API, shared by the Pico and the laptop test
#
#   GET  /                  the page (index.html)
#   GET  /api/state         live values and alarms (polled every 2 s)
#   GET  /api/history       hourly table, trend and event log (every 15 s)
#   POST /api/start         {code, weight, gender, now_ms, tz}
#   POST /api/weight        {weight}
#   POST /api/ack           {who}
#   POST /api/stop
#   POST /api/demo          {action} or {flow}   (demo mode only)
#   GET  /api/export.csv    hourly table and log as a CSV file
#
# Routes for the UroMonitor dashboard (dashboard.html, the group's interface):
#   POST /api/session                       {weight_kg, now_ms, tz}
#   GET  /api/status                        live values, alarm, hourly records, events
#   POST /api/alarms/<alarm_id>/acknowledge
#
# Routes for the laptop Telegram relay (tools/telegram_relay.py):
#   GET  /api/log?since=N       new alarm-record entries, the session and the device clock
#   GET  /api/volume?minutes=N  accepted urine (mL) over the last N minutes
#   POST /api/note              {text}: add a message record to the alarm log
#   POST /api/ack               {who, source}

from adafruit_httpserver import (Server, Request, Response, JSONResponse, FileResponse,
                                 GET, POST, OPTIONS, BAD_REQUEST_400)

GENDERS = ("Female", "Male", "Other")
RECIPIENT = {"critical": "Physician", "advisory": "Nurse", "technical": "Technician"}
SCALE_WORDS = {"still": "Scale OK", "moving": "Scale moving", "settling": "Scale settling",
               "no_data": "Scale not answering", "saturated": "Scale saturated"}


def _clock(epoch_s, tz_min):
    """HH:MM local time from UTC epoch seconds and a browser time-zone offset."""
    t = epoch_s - tz_min * 60
    return "{:02d}:{:02d}".format(t // 3600 % 24, t // 60 % 60)


def dashboard_status(mon, wall):
    """The /api/status reply in the shape dashboard.html reads."""
    st = mon.state(wall)
    s = mon.session
    out = {"timestamp": wall * 1000, "rate_ml_kg_h": None, "hourly_ml": None,
           "total_ml": None, "artifact_count": None, "alarm_state": "normal",
           "alarm_id": None, "acknowledged": False, "alarm_message": "",
           "alarm_recipient": None, "events": [], "hourly_records": []}
    scale = SCALE_WORDS.get(st["scale"]["status"], st["scale"]["status"])
    if st["scale"]["grams"] is not None:
        scale += " ({:.1f} g)".format(st["scale"]["grams"])
    if s is None:
        out["device_status"] = scale + " \u00b7 enter a weight (40 to 250 kg) and press Connect"
        out["alarm_message"] = "Monitoring has not started: no patient weight set."
        return out
    L = st["live"]
    tz = s["tz"]
    out["weight_kg"] = s["weight"]
    if L["hold"]:
        scale += " \u00b7 checking a disturbance"
    out["device_status"] = scale
    if L["el_min"] >= 60:
        out["rate_ml_kg_h"] = L["r1"]
        out["hourly_ml"] = round(L["r1"] * s["weight"], 1)
    else:
        out["rate_ml_kg_h"] = L["hour_rate"]
        out["hourly_ml"] = L["hour_ml"]
    out["total_ml"] = L["total_ml"]
    out["artifact_count"] = sum(st["artifacts"].values())
    out["alarm_state"] = st["level"]
    alarms = st["alarms"]
    if alarms:
        top = alarms[0]
        out["alarm_id"] = "{}-{}".format(top["level"], top["since"])
        out["acknowledged"] = all(a["acked"] for a in alarms)
        out["alarm_recipient"] = RECIPIENT[top["level"]]
        out["alarm_message"] = " | ".join(a["text"] for a in alarms)
    elif not L["evaluable"]:
        out["alarm_message"] = "Urine output cannot be measured right now."
    else:
        out["alarm_message"] = "No active alarm. Monitoring for {} h {:02d} min.".format(
            L["el_min"] // 60, L["el_min"] % 60)
    h = mon.history(wall, trend=False, log_n=40)
    rows = []
    for r in h["hours"]:
        a = r["art"]
        n = sum(a.values())
        if r["partial"]:
            q = "Hour in progress" if r["h"] + 3600 > wall else "Partial hour"
        else:
            q = "Complete"
        if n:
            q += ", {} artifact{} rejected".format(n, "" if n == 1 else "s")
        rows.append({"time": _clock(r["h"], tz) + "\u2013" + _clock(r["h"] + 3600, tz),
                     "volume_ml": r["ml"], "rate_ml_kg_h": r["rate"], "quality": q})
    out["hourly_records"] = rows[-24:]
    names = {"alarm": "ALARM", "ack": "ACK", "clear": "CLEARED", "tech": "TECHNICAL",
             "artifact": "REJECTED", "session": "SESSION", "weight": "WEIGHT",
             "msg": "MESSAGE"}
    ev = sorted(h["log"], key=lambda e: e[0], reverse=True)[:40]
    out["events"] = [{"timestamp": _clock(e[0], tz), "kind": names.get(e[1], e[1]),
                      "detail": e[2]} for e in ev]
    return out


def _bad(request, msg):
    return JSONResponse(request, {"error": msg}, status=BAD_REQUEST_400)


def _weight(value):
    try:
        w = round(float(value), 1)
    except (TypeError, ValueError):
        return None
    if w < 40.0 or w > 250.0:
        return None
    return w


def _code(value):
    code = str(value or "").strip().upper()
    if not 2 <= len(code) <= 16:
        return None
    for ch in code:
        if not (ch.isdigit() or "A" <= ch <= "Z" or ch in "-_"):
            return None
    return code


def make_server(pool, app, root):
    """app needs: mon, sim (or None), speed, wall(), set_clock(epoch_s, tz)."""
    server = Server(pool, root, debug=False)
    # let the dashboard work even when it is opened from another address
    server.headers = {"Access-Control-Allow-Origin": "*",
                      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                      "Access-Control-Allow-Headers": "Content-Type"}

    def body(request):
        try:
            return request.json() or {}
        except (ValueError, TypeError):
            return {}

    @server.route("/", GET)
    def page(request: Request):
        return FileResponse(request, "dashboard.html", root)

    @server.route("/classic", GET)
    def classic(request: Request):
        return FileResponse(request, "index.html", root)

    @server.route("/chart.umd.js", GET)
    def chartjs(request: Request):
        return FileResponse(request, "chart.umd.js", root, content_type="text/javascript",
                            headers={"Cache-Control": "max-age=86400"})

    @server.route("/api/<anything>", OPTIONS)
    def preflight(request: Request, anything):
        return Response(request, "")

    @server.route("/api/alarms/<alarm_id>/acknowledge", OPTIONS)
    def preflight_ack(request: Request, alarm_id):
        return Response(request, "")

    # ---------------- UroMonitor dashboard routes ----------------
    @server.route("/api/status", GET)
    def status(request: Request):
        return JSONResponse(request, dashboard_status(app.mon, app.wall()))

    @server.route("/api/session", POST)
    def session(request: Request):
        d = body(request)
        w = _weight(d.get("weight_kg"))
        if w is None:
            return _bad(request, "Weight must be between 40.0 and 250.0 kg.")
        if app.mon.session is not None:
            if w != app.mon.session["weight"]:
                app.mon.set_weight(w, app.wall())
            return JSONResponse(request, {"ok": True, "started": False})
        if app.mon.grams is None:
            return _bad(request, "The scale is not answering. Check the load cell first.")
        tz = 0
        try:
            tz = int(d.get("tz", 0) or 0)
            app.set_clock(int(d.get("now_ms")) // 1000, tz)
        except (TypeError, ValueError):
            pass
        code = _code(d.get("code")) or "BENCH"
        app.mon.start(code, w, "Not recorded", app.wall(), tz)
        return JSONResponse(request, {"ok": True, "started": True})

    @server.route("/api/session/end", POST)
    def session_end(request: Request):
        app.mon.stop(app.wall())
        return JSONResponse(request, {"ok": True})

    @server.route("/api/alarms/<alarm_id>/acknowledge", POST)
    def ack_alarm(request: Request, alarm_id):
        n = app.mon.ack("dashboard", "", app.wall())
        return JSONResponse(request, {"ok": True, "acknowledged": n})

    @server.route("/api/state", GET)
    def state(request: Request):
        out = app.mon.state(app.wall())
        out["demo"] = app.sim is not None
        out["speed"] = app.speed
        if app.sim is not None:
            out["flow"] = app.sim.flow
        return JSONResponse(request, out)

    @server.route("/api/history", GET)
    def history(request: Request):
        return JSONResponse(request, app.mon.history(app.wall()))

    @server.route("/api/start", POST)
    def start(request: Request):
        d = body(request)
        code = _code(d.get("code"))
        if code is None:
            return _bad(request, "Patient code: 2 to 16 letters, numbers, - or _. No names.")
        w = _weight(d.get("weight"))
        if w is None:
            return _bad(request, "Weight must be between 40.0 and 250.0 kg.")
        gender = d.get("gender")
        if gender not in GENDERS:
            return _bad(request, "Choose a gender.")
        if app.mon.grams is None:
            return _bad(request, "The scale is not answering. Check the load cell first.")
        try:
            app.set_clock(int(d.get("now_ms")) // 1000, int(d.get("tz", 0)))
        except (TypeError, ValueError):
            pass
        app.mon.start(code, w, gender, app.wall(), int(d.get("tz", 0) or 0))
        return JSONResponse(request, {"ok": True})

    @server.route("/api/weight", POST)
    def weight(request: Request):
        if app.mon.session is None:
            return _bad(request, "No patient.")
        w = _weight(body(request).get("weight"))
        if w is None:
            return _bad(request, "Weight must be between 40.0 and 250.0 kg.")
        app.mon.set_weight(w, app.wall())
        return JSONResponse(request, {"ok": True})

    @server.route("/api/ack", POST)
    def ack(request: Request):
        d = body(request)
        who = str(d.get("who") or "").strip()[:24]
        source = str(d.get("source") or "web page").strip()[:16]
        n = app.mon.ack(source, who, app.wall())
        app.mon.tick(app.wall())
        return JSONResponse(request, {"ok": True, "acknowledged": n})

    # ---------------- laptop Telegram relay routes ----------------
    @server.route("/api/log", GET)
    def log_since(request: Request):
        mon = app.mon
        try:
            since = int(request.query_params.get("since") or 0)
        except ValueError:
            since = 0
        n = mon.log_count - since
        entries = [] if n <= 0 else mon.log[-n:]
        return JSONResponse(request, {"now": app.wall(), "speed": app.speed,
                                      "log_count": mon.log_count, "entries": entries,
                                      "session": mon.session})

    @server.route("/api/volume", GET)
    def volume(request: Request):
        try:
            n = int(request.query_params.get("minutes") or 60)
        except ValueError:
            return _bad(request, "minutes must be a number")
        n = max(1, min(720, n))
        return JSONResponse(request, {"ml": round(app.mon.volume(app.wall(), n), 2), "minutes": n})

    @server.route("/api/note", POST)
    def note(request: Request):
        text = str(body(request).get("text") or "").strip()[:160]
        if text:
            app.mon.note(app.wall(), "msg", text)
        return JSONResponse(request, {"ok": True})

    @server.route("/api/stop", POST)
    def stop(request: Request):
        app.mon.stop(app.wall())
        return JSONResponse(request, {"ok": True})

    @server.route("/api/demo", POST)
    def demo(request: Request):
        if app.sim is None:
            return _bad(request, "Demo mode is off.")
        d = body(request)
        if "flow" in d:
            try:
                app.sim.flow = max(0.0, min(300.0, float(d["flow"])))
            except (TypeError, ValueError):
                return _bad(request, "Bad flow.")
        if d.get("action"):
            app.sim.action(d["action"], app.ms())
        return JSONResponse(request, {"ok": True})

    @server.route("/api/export.csv", GET)
    def export(request: Request):
        s = app.mon.session
        name = "uom1_{}.csv".format(s["code"] if s else "none")
        return Response(request, app.mon.csv(app.wall()), content_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="{}"'.format(name)})

    return server
