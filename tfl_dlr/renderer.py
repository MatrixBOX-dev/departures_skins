"""TfL DLR skin for /departures.

Downloaded on demand and imported by /departures' skin loader. Draws one large
departure on top and two compact departures below, using the host app's shared
data pipeline (get_departure/reformat_data) and render primitives (renderstring/
cls/refresh). Only logic exclusive to this layout lives here; anything shared
with other skins (clock_string, apply_clock_row, station name abbreviation
lists, the data-fetch pipeline itself) stays in the host app's functions.py.

Contract expected by /departures' skin loader:
    on_enter()        - called once when this skin becomes active
    render()          - periodic full redraw; returns a monotonic timestamp
    animate_tick()     - called every main-loop iteration; returns True if it
                         drew something (host should skip the normal redraw)
    message_active()   - True while the lower-half message animation is mid-flight
    refresh_settings() - called after a relevant web-settings change
"""
import time
import varinit
import dicts
from varinit import fonts, top, bottom, topbottom, station_names_dict
from functions import cls, renderstring, refresh, nightcheck, reformat_data, get_departure, get_deviations, reset


def _font_width(text, font_index):
    f = fonts[font_index]
    total = 0
    for c in str(text):
        if c not in f: c = "_"
        total += f[c][0] if isinstance(f[c][1], int) else len(f[c][1])
    return total


def _dlr_upper(text):
    """CircuitPython-safe uppercase for Swedish DLR text (å/ä/ö survive .upper())."""
    text = str(text).upper()
    return text.replace("å", "Å").replace("ä", "Ä").replace("ö", "Ö")


def _dlr_abbreviate_dest(text, max_px, font_index, uppercase=False):
    """Apply DLR destination abbreviations using the width actually rendered."""
    raw_text = str(text)
    text = raw_text

    def _fits(candidate):
        rendered = _dlr_upper(candidate) if uppercase else str(candidate)
        return _font_width(rendered, font_index) <= max_px

    if _fits(text):
        return text

    for pair in varinit.settings.get("dest_abbrev", []):
        if not isinstance(pair, list) or len(pair) != 2:
            continue
        long, short = pair
        if long and long in text:
            text = text.replace(long, short)
            if _fits(text):
                return text

    for pair in dicts.replace_list_destinations:
        try:
            long, short = pair
        except:
            continue
        if long and long in text:
            text = text.replace(long, short)
            if _fits(text):
                return text

    try:
        mapped = station_names_dict.get(raw_text)
    except:
        mapped = None
    if mapped and _fits(mapped):
        return mapped
    if mapped:
        return mapped

    return text


def _dlr_clock_string():
    """Fixed DLR overlay clock: always HH:MM, never date/alignment decoration."""
    t = time.localtime(varinit.currenttime)
    def _z(n):
        s = str(n)
        return "0" + s if len(s) == 1 else s
    return _z(t[3]) + ":" + _z(t[4])


def _dlr_scroll_delay_seconds():
    """Return the configured DLR lower-half dwell time in seconds."""
    try:
        delay = int(varinit.settings.get("dlr_scroll_delay", 15))
    except:
        delay = 15
    return max(1, min(300, delay))


def reset_dlr_message_cycle():
    """Reset the DLR lower-half message cycle to its configured dwell."""
    varinit.dlr_message_state = {
        "phase": "normal",
        "normal_since": time.monotonic(),
        "last_step": 0,
        "message": "",
        "message_width": 0,
    }
    try:
        varinit.tg2.x = 0
        varinit.tg2.y = 16
    except:
        pass


def message_active():
    """True while the DLR lower half is sliding away or scrolling a message."""
    try:
        return varinit.dlr_message_state.get("phase", "normal") != "normal"
    except:
        return False


def _dlr_custom_message():
    if not int(varinit.settings.get("custom_scroll_show", 0)):
        return ""
    return str(varinit.settings.get("custom_scroll_text", "")).strip()


def _dlr_scroll_content_mode():
    """Return DLR's exclusive message source using the existing settings."""
    if int(varinit.settings.get("custom_scroll_show", 0)):
        return "custom"
    if int(varinit.settings.get("show_msgs", 0)):
        return "disruptions"
    return "none"


def _dlr_next_message():
    """Choose the next DLR lower-half message from the selected source."""
    _content_mode = _dlr_scroll_content_mode()
    if _content_mode == "none":
        return ""
    if _content_mode == "custom":
        return _dlr_custom_message()

    now = time.monotonic()
    try:
        _operator = varinit.settings["stations"]["1"]["operator"]
    except:
        _operator = ""

    if (varinit.shared.get("nightcount", 0) < 2
            and _operator in ("sl", "vt")
            and now > varinit.deviations_timer + (varinit.deviations_delay * 60)):
        try:
            msg = str(get_deviations()).strip()
            varinit.deviations_timer = now
            if msg:
                return msg
        except Exception as e:
            print("DLR disruption message error:", repr(e))
            varinit.deviations_timer = now

    return ""


def refresh_settings():
    """Rebuild DLR and restart the configured message dwell after web changes."""
    reset_dlr_message_cycle()
    render()
    varinit.shared["scroll_timer"] = time.monotonic()


def animate_tick():
    """Animate the DLR lower-half message cycle one small step.

    Normal rows 2+3 (and the optional clock) remain for the configured delay.
    If a custom or native disruption message is available, the lower TileGrid
    slides downward, the message scrolls horizontally, and the normal DLR rows
    are rebuilt once it has completely passed. Returns True if it drew anything.
    """
    try:
        state = varinit.dlr_message_state
        if not isinstance(state, dict):
            raise TypeError
    except:
        reset_dlr_message_cycle()
        state = varinit.dlr_message_state

    now = time.monotonic()
    phase = state.get("phase", "normal")

    if phase == "normal":
        if now < float(state.get("normal_since", now)) + _dlr_scroll_delay_seconds():
            return False
        msg = _dlr_next_message()
        state["normal_since"] = now
        if not msg:
            return False
        state["phase"] = "slide_down"
        state["message"] = msg
        state["last_step"] = 0
        return True

    if now < float(state.get("last_step", 0)) + 0.03:
        return False
    state["last_step"] = now

    if phase == "slide_down":
        try:
            varinit.tg2.y += 1
            refresh(1)
        except:
            pass
        if varinit.tg2.y >= 32:
            varinit.tg2.y = 16
            varinit.tg2.x = varinit.if_long
            cls(bottom)
            state["message_width"] = renderstring(state.get("message", ""), large=True, _cls=bottom)
            state["phase"] = "scroll_message"
            refresh(1)
        return True

    if phase == "scroll_message":
        try:
            varinit.tg2.x -= 1
            refresh(1)
        except:
            pass
        if varinit.tg2.x < -int(state.get("message_width", 0)):
            varinit.tg2.x = 0
            varinit.tg2.y = 16
            state["phase"] = "normal"
            state["normal_since"] = now
            state["message"] = ""
            state["message_width"] = 0
            render()
            varinit.shared["scroll_timer"] = time.monotonic()
        return True

    reset_dlr_message_cycle()
    return False


def render():
    """TfL DLR layout: one large departure on top, two compact departures below."""
    try:
        varinit.dlr_message_state.get("phase", "normal")
    except:
        reset_dlr_message_cycle()

    # Keep the original three-row layout on 32px displays; use the lower
    # 32px panel for four additional compact rows on 64px displays.
    _tall_layout = varinit.if_tall >= 64
    varinit.settings["maxdest"] = 7 if _tall_layout else 3
    nightcheck()
    varinit.currentfont = 0

    varinit.tg1.y, varinit.tg2.y, varinit.tg3.y = 0, 16, 32
    varinit.tg1.x, varinit.tg2.x, varinit.tg3.x = 0, 0, 0

    if varinit.shared["loop_counter"] == -7:
        reset()

    trainlist = reformat_data(get_departure())
    cls(topbottom)
    if not isinstance(trainlist, list) or not trainlist:
        cls(top)
        cls(bottom, _refresh=True)
        return time.monotonic()

    rows = [row[:] for row in trainlist if isinstance(row, list) and len(row) >= 4]
    cls(top)
    cls(bottom)

    _show_dlr_clock = int(varinit.settings.get("show_clock_row", 0))
    _dlr_clock = _dlr_clock_string() if _show_dlr_clock else ""
    _dlr_clock_x = max(0, varinit.if_long - _font_width(_dlr_clock, 0)) if _show_dlr_clock else varinit.if_long
    _lower_value_right = max(0, _dlr_clock_x - 2) if _show_dlr_clock else varinit.if_long

    def _draw_row(row, number, bmp, y, font_index):
        prefix = str(number) + " "
        raw_dest = str(row[2]).split('(')[0].split(" via")[0].strip()
        dest = raw_dest
        value = str(row[3])

        if int(varinit.settings.get("clocktime", 0)) != 1 and value.strip().isdigit():
            value += varinit.settings["mins"]

        if number != 1:
            value = _dlr_upper(value)

        value_right = varinit.if_long if number == 1 else _lower_value_right
        if number != 1 and not _show_dlr_clock:
            value_right = max(0, value_right - 1)

        actual_value_width = _font_width(value, font_index)
        is_now = value.strip().lower() == "nu"

        if number != 1:
            try:
                _slots = varinit.dlr_lower_value_slots
            except:
                _slots = {}
                varinit.dlr_lower_value_slots = _slots
            _slot_key = str(number)
            _slot = _slots.get(_slot_key, {})
            _clock_key = 1 if _show_dlr_clock else 0
            if _slot.get("raw") != raw_dest or _slot.get("clock") != _clock_key:
                _slot = {"raw": raw_dest, "clock": _clock_key, "width": actual_value_width}
            elif not is_now:
                _slot["width"] = max(int(_slot.get("width", 0)), actual_value_width)
            reserved_value_width = max(actual_value_width, int(_slot.get("width", actual_value_width)))
            _slot["width"] = reserved_value_width
            _slots[_slot_key] = _slot
            slot_x = max(0, value_right - reserved_value_width)
            value_x = max(0, value_right - actual_value_width)
        else:
            reserved_value_width = actual_value_width
            slot_x = max(0, value_right - actual_value_width)
            value_x = slot_x

        if is_now and number == 1:
            value_x = max(0, value_x - 1)

        left_gap = 3 if (number == 1 and is_now) else 1
        max_left = max(0, slot_x - left_gap)
        max_dest = max(0, max_left - _font_width(prefix, font_index))

        if number == 1:
            try:
                _cache = varinit.dlr_top_abbrev_cache
            except:
                _cache = {}
                varinit.dlr_top_abbrev_cache = _cache
            if _cache.get("raw") == raw_dest and _cache.get("abbr"):
                dest = _cache["abbr"]
            else:
                dest = _dlr_abbreviate_dest(raw_dest, max_dest, font_index)
                if dest != raw_dest:
                    varinit.dlr_top_abbrev_cache = {"raw": raw_dest, "abbr": dest}
                else:
                    varinit.dlr_top_abbrev_cache = {"raw": raw_dest, "abbr": ""}
        else:
            try:
                _abbrs = varinit.dlr_lower_abbrev_cache
            except:
                _abbrs = {}
                varinit.dlr_lower_abbrev_cache = _abbrs
            _abbr_key = str(number)
            _cached = _abbrs.get(_abbr_key, {})
            _clock_key = 1 if _show_dlr_clock else 0
            if (_cached.get("raw") == raw_dest and
                    _cached.get("clock") == _clock_key and _cached.get("abbr")):
                dest = _cached["abbr"]
            else:
                dest = _dlr_abbreviate_dest(raw_dest, max_dest, font_index, uppercase=True)
                _abbrs[_abbr_key] = {
                    "raw": raw_dest,
                    "clock": _clock_key,
                    "abbr": dest if dest != raw_dest else "",
                }
            dest = _dlr_upper(dest)

        left = prefix + dest
        while dest and _font_width(left, font_index) > max_left:
            dest = dest[:-1]
            left = prefix + dest

        line_colour = "white" if int(varinit.settings.get("listcolor", 0)) else "yellow"
        time_colour = "white" if int(varinit.settings.get("listcolor_time", 0)) else "yellow"
        prefix_width = _font_width(prefix, font_index)
        renderstring(prefix, 0, large=(font_index == 0), smallfont=(font_index != 0),
                     target_bmp=bmp, target_offs=y, start_x=0, sys_msg=line_colour)
        renderstring(dest, 0, large=(font_index == 0), smallfont=(font_index != 0),
                     target_bmp=bmp, target_offs=y, start_x=prefix_width)
        renderstring(value, 0, large=(font_index == 0), smallfont=(font_index != 0),
                     target_bmp=bmp, target_offs=y, start_x=value_x, sys_msg=time_colour)

    if len(rows) > 0:
        _draw_row(rows[0], 1, top, 2, 0)
    if len(rows) > 1:
        _draw_row(rows[1], 2, bottom, 0, 1)
    if len(rows) > 2:
        _draw_row(rows[2], 3, bottom, 8, 1)
    if _tall_layout:
        for _i, _row in enumerate(rows[3:7]):
            _draw_row(_row, _i + 4, topbottom, _i * 8, 1)

    if _show_dlr_clock:
        renderstring(_dlr_clock, 0, large=True, target_bmp=bottom, target_offs=3,
                     start_x=_dlr_clock_x, sys_msg=varinit.settings.get("clock_row_color", "white"))

    refresh()
    return time.monotonic()


def on_enter():
    """Called once when this skin becomes the active view."""
    reset_dlr_message_cycle()
    return render()
