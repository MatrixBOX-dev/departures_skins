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

API-blocked/connection-error notices scroll across row 2 (with the clock
still visible on row 3) via a small state machine in animate_tick(), drawn
directly onto the topbottom canvas - not a TileGrid-hijack like tfl_dlr's,
since this skin only ever uses the single topbottom canvas to begin with.
"""
import time, json
import displayio
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

# DSA's own large/small fonts (ported from font_large.py/font_small.py in the
# original app) - these carry German umlauts, en-dash, and (font_small only)
# the degree glyph the host's shared fonts don't have.
try:
    with open("skins/dsa/font_large.json") as f:
        fonts.append(json.load(f))
    _DSA_LARGE_INDEX = len(fonts) - 1
except Exception as e:
    print("DSA: font_large load failed, falling back to host large font:", e)
    _DSA_LARGE_INDEX = 0
try:
    with open("skins/dsa/font_small.json") as f:
        fonts.append(json.load(f))
    _DSA_SMALL_INDEX = len(fonts) - 1
except Exception as e:
    print("DSA: font_small load failed, falling back to host small font:", e)
    _DSA_SMALL_INDEX = 1


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


_layers = {"ticker_tg": None, "mask_tg": None, "clock_tg": None, "clock_last": None}


def _ensure_layers():
    # Three separate TileGrids, appended in this exact order so z-order comes
    # out right: scrolling ticker text (bottom), an opaque mask the same size
    # as the clock (middle - blocks the ticker's own transparent gaps from
    # showing the layer below it), then the clock itself (top, always clean).
    # Mirrors the original DSA fork's cancel_scroll_tg/cancel_mask_tg/
    # cancel_clock_tg trio, at the same y=24 row.
    if _layers["ticker_tg"] is not None:
        return
    height = fonts[_DSA_SMALL_INDEX]["fontheight"]

    ticker_palette = displayio.Palette(2)
    ticker_palette[0] = 0x000000
    ticker_palette[1] = varinit.palette[1]
    ticker_palette.make_transparent(0)
    ticker_bmp = displayio.Bitmap(max(500, varinit.if_long * 6), height, 2)
    ticker_tg = displayio.TileGrid(ticker_bmp, pixel_shader=ticker_palette, x=varinit.if_long, y=24)
    ticker_tg.hidden = True
    varinit.group.append(ticker_tg)

    mask_w = 35
    mask_palette = displayio.Palette(2)
    mask_palette[0] = 0x000000
    mask_palette[1] = 0x000000
    mask_bmp = displayio.Bitmap(mask_w, height, 2)
    mask_tg = displayio.TileGrid(mask_bmp, pixel_shader=mask_palette, x=max(0, varinit.if_long - mask_w), y=24)
    varinit.group.append(mask_tg)

    clock_palette = displayio.Palette(2)
    clock_palette[0] = 0x000000
    clock_palette[1] = varinit.palette[1]
    clock_palette.make_transparent(0)
    clock_bmp = displayio.Bitmap(mask_w, height, 2)
    clock_tg = displayio.TileGrid(clock_bmp, pixel_shader=clock_palette, x=max(0, varinit.if_long - mask_w), y=24)
    varinit.group.append(clock_tg)

    _layers["ticker_tg"] = ticker_tg
    _layers["mask_tg"] = mask_tg
    _layers["clock_tg"] = clock_tg


def _draw_clock(y):
    _ensure_layers()
    tg, mask = _layers["clock_tg"], _layers["mask_tg"]
    tg.y = mask.y = y
    # _convert_date() (which sets _currenttime) only ever runs on a successful
    # fetch - while the API is blocked/erroring there may be no time yet, so
    # fall back to a placeholder rather than drawing (and caching) an empty
    # string, which would make the clock silently vanish.
    text = "\u00b0" + (str(getattr(varinit, "_currenttime", "")) or "--:--")
    if text == _layers["clock_last"]:
        return
    _layers["clock_last"] = text
    tg.bitmap.fill(0)
    before = varinit.currentfont
    varinit.currentfont = _DSA_SMALL_INDEX
    renderstring(text, target_bmp=tg.bitmap, target_offs=0, start_x=0)
    varinit.currentfont = before


_ticker = {"message": "", "phase": "idle", "width": 0, "last": 0.0}


def _queue_ticker(msg):
    if _ticker["phase"] != "idle" and _ticker["message"] == msg:
        return  # already showing/about to show this exact notice
    _ticker["message"] = msg
    _ticker["phase"] = "pending"


def message_active():
    return _ticker["phase"] != "idle"


def _ticker_tick():
    _ensure_layers()
    tg = _layers["ticker_tg"]
    now = time.monotonic()
    if _ticker["phase"] == "pending":
        tg.bitmap.fill(0)
        before = varinit.currentfont
        varinit.currentfont = _DSA_SMALL_INDEX
        _ticker["width"] = renderstring(_ticker["message"], target_bmp=tg.bitmap, target_offs=0, start_x=0)
        varinit.currentfont = before
        tg.x = varinit.if_long
        tg.hidden = False
        _ticker["phase"] = "scroll"
        _ticker["last"] = now
        return True
    if now - _ticker["last"] < 0.03:
        return True
    _ticker["last"] = now
    tg.x -= 1
    refresh(1)
    if tg.x < -_ticker["width"]:
        tg.hidden = True
        _ticker["phase"] = "idle"
    return True


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


def search_station(query):
    """Search German stops via the host's own data.t-skylt.se /search_stop proxy
    (country=de, operator=vvo or db_trains depending on the active API provider)
    rather than hitting bahn.de/efa.vvo-online.de directly - "db" looked plausible
    but actually returns an unscoped nationwide GTFS substring match; db_trains is
    the real Bahn.de operator code (confirmed against both the live endpoint and
    the host's own country_and_operators registry in web.py).
    Returns the same <option> HTML shape host's own /search route builds, and
    populates varinit.datadict[id]=name the same way, so selecting a result
    reuses the host's existing newstation handler as-is."""
    api_provider = int(varinit.settings.get("dsa_api_provider", 1))
    operator = "vvo" if api_provider == 3 else "db_trains"
    datastr = '<option value="0">Search...</option>'
    try:
        from functions import fetch_data
        raw = fetch_data("data.t-skylt.se", 90, "/search_stop?country=de&operator=" + operator + "&station=" + query)
        data = json.loads(raw)
        for name, sid in sorted(((n, data[n]) for n in data), key=lambda p: p[0]):
            name, sid = str(name), str(sid)
            varinit.datadict[sid] = name
            datastr += '<option value="' + sid + '">' + name + '</option>'
    except Exception as e:
        print("DSA: search error:", repr(e))
    return datastr



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
            _queue_ticker("DSA: API blocked or unavailable (HTTP " + str(resp.status_code)
                          + ", provider " + str(api_provider) + ") - try another provider.")
            return []
        try: _convert_date(resp.headers["date"])
        except Exception: pass
        raw = resp.text
        print("DSA: response length", len(raw), "start:", raw[:150])
        data = json.loads(raw)
        resp.close()
    except Exception as e:
        print("DSA: fetch error:", repr(e))
        _queue_ticker("DSA: connection error (" + repr(e) + ")")
        return []

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
    platform_width = _width(platform_text, _DSA_LARGE_INDEX)
    x_platform = max(0, varinit.if_long - platform_width)

    if layout == 1:
        _draw(line_text, 50, y + 3, _DSA_FONT_INDEX)
        _draw(delay_text, 30, y + 2, _DSA_SMALL_INDEX)
    _draw(time_text, -1, y, _DSA_LARGE_INDEX)
    if layout == 1:
        _draw(dest_text, x_dest + 1, y, _DSA_LARGE_INDEX)
    else:
        _draw(dest_text, _width(time_text, _DSA_LARGE_INDEX) + 2, y + 2, _DSA_SMALL_INDEX)
    _draw(platform_text, x_platform, y, _DSA_LARGE_INDEX)
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
        if _ticker["phase"] == "idle":
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
    if _ticker["phase"] == "idle":
        return False
    return _ticker_tick()


def refresh_settings():
    render()


def on_enter():
    return render()
