"""DSA (Westfrankenbahn) skin for /departures.

Downloaded on demand and imported by /departures' skin loader. Talks
directly to Bahn.de / VVO Dresden (not the host's data.t-skylt.se
pipeline), using its own custom dot-matrix font for line numbers.

Settings this skin reads (none are registered in the host's settingstxt
yet, so they all fall back to sane defaults until a web-UI row is added):
    dsa_api_provider  - 1: Bahn.de ICE/IC/Regional/SBahn (default)
                         2: Bahn.de, all services
                         3: VVO Dresden (EFA)
    dsa_layout        - 1: line badge + large destination (default)
                         2: destination in small font next to the time
    dsa_utc_offset    - manual UTC offset in hours (default 1)
    dsa_summer_time   - add 1 more hour for DST (default 0)
Station id: stations[num]["siteid"] must hold an EFA/VBB-style stop id
(e.g. "de:14522:70048"), not the usual data.t-skylt.se siteid - the
host's station search doesn't produce these, so it needs to be set
manually (e.g. via /cmd) until a dedicated text field exists.

Contract expected by /departures' skin loader:
    on_enter()        - called once when this skin becomes active
    render()          - periodic full redraw; returns a monotonic timestamp
    animate_tick()     - called every main-loop iteration; returns True if it
                         drew something (host should skip the normal redraw)
    message_active()   - True while a secondary animation is mid-flight
    refresh_settings() - called after a relevant web-settings change

Not yet ported: the original app's scrolling cancellation/disruption
ticker. Cancelled departures are shown inline (time text gets a "^"
suffix) rather than as a scrolling banner - add the ticker later using
the same varinit.tg2-hijack technique the tfl_dlr skin uses for its
message scroll, once this base version is confirmed working on hardware.
"""
import time, json
import varinit, dicts
from __main__ import requests
from functions import cls, refresh, renderstring
from varinit import topbottom, fonts

_FONT_PATH = "skins/dsa/custom_font.json"
try:
    with open(_FONT_PATH) as f:
        _dsa_font = json.load(f)
    fonts.append(_dsa_font)
    _DSA_FONT_INDEX = len(fonts) - 1
except Exception as e:
    print("DSA: custom font load failed, falling back to small font:", e)
    _DSA_FONT_INDEX = 1


def _width(text, font_index):
    f = fonts[font_index]
    total = 0
    for c in str(text):
        if c not in f: c = "_"
        total += f[c][0] if isinstance(f[c][1], int) else len(f[c][1])
    return total


def _draw(text, x, y, font_index=1):
    before = varinit.currentfont
    varinit.currentfont = font_index
    w = renderstring(str(text), target_bmp=topbottom, target_offs=y, start_x=x)
    varinit.currentfont = before
    return w


def _draw_clock(y):
    # no font currently loaded has a degree-symbol glyph (not even custom_font.json -
    # it's digits/A-Z/space/"|" only), so the clock is shown plain, without one.
    _draw(str(getattr(varinit, "_currenttime", "")), max(0, varinit.if_long - 35), y, 1)


def _convert_date(dt):
    try: varinit.today = dt.split(",")[0].replace(" ", "")
    except: pass
    try:
        parts = dt.replace(",", "").split()
        _hour, _minute, _second = parts[4].split(":")
    except Exception as e:
        print("DSA: date parse error:", e, dt)
        return
    if not varinit.first_start:
        utc_offset = int(varinit.settings.get("dsa_utc_offset", 1))
        summer_time = int(varinit.settings.get("dsa_summer_time", 0))
        local_hour = (int(_hour) + utc_offset + summer_time) % 24
        varinit._currenttime = "{:02d}:{}".format(local_hour, _minute)


def _time_to_minutes(t):
    t = str(t)
    if "T" in t: t = t.split("T")[1]
    return int(t[0:2]) * 60 + int(t[3:5])


def _get_api_request(station_id, api_provider):
    if api_provider == 1:
        return ("www.bahn.de", "/web/api/reiseloesung/abfahrten?ortExtId=" + station_id
                + "&verkehrsmittel[]=ICE&verkehrsmittel[]=EC_IC&verkehrsmittel[]=IR"
                + "&verkehrsmittel[]=REGIONAL&verkehrsmittel[]=SBAHN&mitVias=true&maxVias=2")
    if api_provider == 2:
        return ("www.bahn.de", "/web/api/reiseloesung/abfahrten?ortExtId=" + station_id + "&mitVias=true&maxVias=2")
    if api_provider == 3:
        return ("efa.vvo-online.de", "/VMSSL3/XSLT_DM_REQUEST?language=de&stateless=1&type_dm=stop&name_dm="
                + station_id + "&mode=direct&useRealtime=1&limit=10&outputFormat=json")
    return ("", "")


def _fetch_departures(num="1"):
    stn = varinit.settings["stations"][num]
    station_id = stn.get("siteid") or "de:14522:70048"
    api_provider = int(varinit.settings.get("dsa_api_provider", 1))
    host, args = _get_api_request(station_id, api_provider)
    print("DSA: provider", api_provider, "station", station_id, "host", host)
    if not host:
        return []
    try:
        resp = requests.get("https://" + host + args, timeout=10)
        print("DSA: HTTP", resp.status_code)
        if resp.status_code != 200:
            print("DSA: non-200 body:", resp.text[:200])
            resp.close()
            return [["0", "", "HTTP " + str(resp.status_code), "--:--", "", ""]]
        try: _convert_date(resp.headers["date"])
        except Exception: pass
        raw = resp.text
        print("DSA: response length", len(raw), "start:", raw[:150])
        data = json.loads(raw)
        resp.close()
    except Exception as e:
        print("DSA: fetch error:", repr(e))
        return [["0", "", "Verbindungsfehler", "--:--", "", ""]]

    if api_provider == 3:
        dep_list = data.get("departureList", {})
        if isinstance(dep_list, dict):
            deps = dep_list.get("departure", [])
            deps = [deps] if isinstance(deps, dict) else (deps if isinstance(deps, list) else [])
        elif isinstance(dep_list, list): deps = dep_list
        else: deps = []
    else:
        deps = data if isinstance(data, list) else data.get("entries", [])
    print("DSA: deps found:", len(deps) if hasattr(deps, "__len__") else "?")

    rows = []
    for dep in deps[:10]:
        if not isinstance(dep, dict): continue
        hour = minute = delay = None
        cancelled = False
        platform_text = ""

        if api_provider == 3:
            serving = dep.get("servingLine", {}) or {}
            line = str(serving.get("symbol", ""))
            dest = str(serving.get("direction", ""))
            dt_ = dep.get("dateTime", {}) or {}
            hour, minute = dt_.get("hour"), dt_.get("minute")
            try:
                delay = int(serving.get("delay"))
                if delay == -9999: cancelled, delay = True, None
            except Exception: delay = None
            platform_text = dep.get("platformName") or dep.get("platform") or ""
            if platform_text and any(a.get("name") == "platformChange" and a.get("value") == "changed"
                                      for a in dep.get("attrs", [])):
                platform_text = "~" + platform_text
        else:
            serving = dep.get("verkehrmittel", {}) or {}
            line = str(serving.get("kurzText", ""))
            dest = str(dep.get("terminus") or "").strip()
            if not dest:
                ueber = dep.get("ueber")
                if isinstance(ueber, dict): dest = str(ueber.get("1") or "").strip()
                elif isinstance(ueber, list) and len(ueber) > 1: dest = str(ueber[1] or "").strip()
            for key in ("zeit", "ezZeit", "time", "plannedTime", "abfahrtsZeitpunkt", "departureTime"):
                t = str(dep.get(key, ""))
                if "T" in t and ":" in t:
                    hour, minute = t.split("T")[1][0:2], t.split("T")[1][3:5]; break
                if len(t) >= 5 and ":" in t:
                    hour, minute = t[0:2], t[3:5]; break
            for msg in dep.get("meldungen", []):
                if str(msg.get("type", "")) in ("DEPARTURE_CANCELLED", "TRIP_CANCELLED", "HALT_AUSFALL"):
                    cancelled = True; break
            planzeit, echtzeit = dep.get("zeit", ""), dep.get("ezZeit", "")
            if planzeit and echtzeit:
                try:
                    delay = _time_to_minutes(echtzeit) - _time_to_minutes(planzeit)
                    if delay < -720: delay += 1440
                    elif delay > 720: delay -= 1440
                except Exception: delay = None
            platform_text = ("~" + dep["ezGleis"]) if dep.get("ezGleis") else dep.get("gleis", "")

        if hour is None or minute is None: continue
        hour, minute = str(int(hour)), str(minute)
        if len(minute) == 1: minute = "0" + minute
        time_text = hour + ":" + minute + ("^" if cancelled else "")
        delay_text = "" if delay is None else ("+" + str(delay) if delay >= 0 else str(delay))
        rows.append(["0", line, dest, time_text, delay_text, platform_text])
    print("DSA: rows parsed:", len(rows))
    return rows


def _draw_row(row, top_row):
    layout = int(varinit.settings.get("dsa_layout", 1))
    line_text = str(row[1]).upper()[:varinit.settings["line_length"] or 8]
    dest_text, time_text, delay_text = row[2], row[3], row[4]
    platform_text = row[5] if len(row) > 5 else ""
    if not int(varinit.settings["clocktime"]) and not time_text.endswith(varinit.settings["mins"]):
        time_text += varinit.settings["mins"]
    y = 0 if top_row else 12

    line_width = _width(line_text, _DSA_FONT_INDEX)
    x_dest = 48 + line_width + (2 if line_width else 0)
    platform_width = _width(platform_text, 0)
    x_platform = max(0, varinit.if_long - platform_width)

    if layout == 1:
        _draw(line_text, 50, y + 3, _DSA_FONT_INDEX)
        _draw(delay_text, 30, y + 2, 1)
    _draw(time_text, -1, y, 0)
    if layout == 1:
        _draw(dest_text, x_dest + 1, y, 0)
    else:
        _draw(dest_text, _width(time_text, 0) + 2, y + 2, 1)
    _draw(platform_text, x_platform, y, 0)
    if not top_row:
        _draw_clock(24)


def render():
    # tg1/tg2 are parked off-screen and tg3 (topbottom) brought fully on-screen -
    # otherwise whatever TileGrid position list/scroll mode last left behind
    # stays in effect and this skin's drawing stays invisible.
    varinit.tg1.y, varinit.tg2.y = varinit.if_tall, varinit.if_tall
    varinit.tg3.x, varinit.tg3.y = 0, 0
    cls(topbottom)
    rows = _fetch_departures("1")
    if not rows:
        _draw_clock(12)
        refresh()
        return time.monotonic()
    _draw_row(rows[0], top_row=True)
    if len(rows) > 1:
        _draw_row(rows[1], top_row=False)
    else:
        _draw_clock(24)
    refresh()
    return time.monotonic()


def animate_tick():
    return False  # no secondary animation yet - see module docstring


def message_active():
    return False


def refresh_settings():
    render()


def on_enter():
    return render()
