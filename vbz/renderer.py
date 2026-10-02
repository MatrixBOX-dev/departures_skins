"""VBZ (Zuerich tram/bus) skin for /departures.

Unlike dsa/tfl_dlr, this skin needs almost no custom data-fetch logic: the
host's existing data.t-skylt.se pipeline already serves real VBZ Zuerich
data via country="ch", operator="ch" - confirmed live by searching "Bellevue"
and getting back siteid 8576193, the exact example station id the official
sschueller/vbz-fahrgastinformation reference project hardcodes, and by
fetching real departures for it (genuine VBZ tram lines 2/4/9/11/15...).
So this skin just calls the host's own functions.get_departure() directly -
no API key, no direct calls to opentransportdata.swiss.

The server now also returns a real per-line color (traffic_parser() in
functions.py passes it through as row[5]), in search.ch's own
"bgHex~fgHex~flag" format, e.g. "00892f~fff~". That's genuine official-ish
line coloring (not the earlier coarse 5-name-color guess this file used to
have), so line-number badges are drawn as small dedicated TileGrids with
their own 2-color palette set from that hex pair - the shared display
palette is a small fixed set of named colors, not full RGB, so true per-line
colors need their own palette the way DLR/DSA's own tickers do.

Only the rendering is otherwise custom: a compact font ported from the
reference project's vbzfont.h (Adafruit GFXfont format - proportional,
per-glyph advance/offset - converted to this app's flat width+row-bitmask
format), and a Zuerich-style line-badge + destination + countdown layout.

Every departure from this data source is treated as live (no scheduled-vs-
live distinction is available here), so the trailing "'" tick is always
shown. search.ch's stationboard API now also returns a real-time delay
(row[6], "+N"/"-N" minutes, or "+0"/null/"X" when on-time or unavailable),
requested server-side via show_delays=1 - shown appended right after the
countdown (e.g. "5'+2"), using a synthesized "+" glyph (vbz_font.json's
source had no plus-sign character). The original project's accessibility
icon is intentionally not implemented - search.ch's API (checked against
its own docs) doesn't expose that field at all.

Contract: on_enter(), render(), animate_tick(), message_active(), refresh_settings()
"""
import time, json
import displayio
import varinit
from functions import cls, refresh, renderstring, get_departure

_FONT_PATH = "skins/vbz/vbz_font.json"
try:
    with open(_FONT_PATH) as f:
        varinit.fonts.append(json.load(f))
    _VBZ_FONT_INDEX = len(varinit.fonts) - 1
except Exception as e:
    print("VBZ: font load failed, falling back to host small font:", e)
    _VBZ_FONT_INDEX = 1

# vbz_font's own canvas height (12px) plus a 2px gap between rows.
_ROW_PITCH = 14
_BADGE_X = 1   # badge TileGrid's own x offset
_BADGE_W = 22  # badge pixel width: worst-case 3-digit line ID (e.g. "999") measures 21px
               # with this font (all digits except "1" are 7px wide) - 20px left zero/negative
               # room for the intended 1px right margin, making wide IDs look flush/overflowing
               # while short ones (e.g. "S2") had visibly more space; 22px fits the real worst case
_BADGE_H = 11  # line IDs are digits/letters, whose glyphs all end at row 9 (yOffset=1,
               # height=9 in the source font); the reference VBZ display's own box extends
               # 1px past that floor (row 10), so this matches rather than cropping flush to it
_DEST_GAP = 4  # px between the badge's right edge and the destination text

_badges = []  # one dedicated TileGrid per row slot, created lazily (needs if_tall)


def _max_rows():
    return max(1, varinit.if_tall // _ROW_PITCH)


def _ensure_badges():
    if _badges:
        return
    for i in range(_max_rows()):
        palette = displayio.Palette(2)
        palette[0] = (40, 40, 40)
        palette[1] = (255, 255, 255)
        bmp = displayio.Bitmap(_BADGE_W, _BADGE_H, 2)
        tg = displayio.TileGrid(bmp, pixel_shader=palette, x=_BADGE_X, y=i * _ROW_PITCH)
        tg.hidden = True
        varinit.group.append(tg)
        _badges.append(tg)


def _hex_to_rgb(h):
    h = str(h).strip().lstrip("#")
    if len(h) == 3: h = "".join(c * 2 for c in h)
    if len(h) != 6: return None
    try: return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except Exception: return None


def _luminance(rgb):
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _host_white():
    # match the dimmed "white" used by the departures/minutes font (varinit.palette[2]),
    # not a maxed-out (255,255,255), so badge text brightness matches the rest of the display
    try:
        v = varinit.palette[2]
        return ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
    except Exception:
        return (50, 50, 50)


def _finish_color(bg, fg):
    bg = bg or (40, 40, 40)
    r, g, b = bg
    is_red = r > 140 and g < 100 and b < 100  # vivid red boxes read better with black text too
    if _luminance(bg) > 170 or is_red:
        fg = (0, 0, 0)  # bright or red box background -> force black line ID text
    else:
        fg = fg or (255, 255, 255)
        if fg == (255, 255, 255): fg = _host_white()
    return bg, fg


def _parse_color(raw):
    # search.ch format: "bgHex~fgHex~flag" (flag seen but unused here)
    parts = str(raw).split("~")
    bg = _hex_to_rgb(parts[0]) if len(parts) > 0 else None
    fg = _hex_to_rgb(parts[1]) if len(parts) > 1 else None
    return _finish_color(bg, fg)


def _width(text, font_index):
    f = varinit.fonts[font_index]
    total = 0
    for c in str(text):
        if c not in f: c = "_"
        total += f[c][0] if isinstance(f[c][1], int) else len(f[c][1])
    return total


def _draw(text, x, y):
    # renderstring()'s sys_msg color override only applies to the host's own
    # int-row-bitmask fonts, not string-row fonts like this one, so sys_msg="white"
    # would silently be ignored here and fall through to the shared topbottom
    # palette's index 1 (the user's global accent color theme, default amber/
    # yellow) instead. Draw directly, writing "on" pixels to palette index 2
    # (the app's fixed dimmed white, never touched by that theme) so VBZ text
    # is always white regardless of the user's chosen accent color.
    font = varinit.fonts[_VBZ_FONT_INDEX]
    fontheight = font["fontheight"]
    pixwidth = x
    for character in str(text):
        if character not in font: character = "_"
        glyph = font[character]
        for col in range(glyph[0]):
            for row in range(fontheight):
                bit = glyph[row + 1][col]
                try: varinit.topbottom[pixwidth + col, row + y] = 2 if bit == "1" else 0
                except Exception: pass
        pixwidth += glyph[0]
    return pixwidth


def _ensure_station():
    stn = varinit.settings["stations"]["1"]
    if stn.get("country") != "ch" or stn.get("operator") != "ch":
        stn["country"] = "ch"
        stn["operator"] = "ch"
        stn["siteid"] = "8503000"  # Zuerich HB - sensible default until the user searches their own stop
        stn["mystation"] = "Zuerich HB"


def _draw_row(i, row, y):
    line = str(row[1])[:3]
    dest = str(row[2])
    mins = str(row[3])
    raw_color = row[5] if len(row) > 5 else ""
    raw_delay = row[6] if len(row) > 6 else ""

    tg = _badges[i]
    bg, fg = _parse_color(raw_color) if int(varinit.settings.get("vbz_color", 1)) else _finish_color((40, 40, 40), None)
    tg.pixel_shader[0] = bg
    tg.pixel_shader[1] = fg
    tg.bitmap.fill(0)
    before = varinit.currentfont
    varinit.currentfont = _VBZ_FONT_INDEX
    line_w = _width(line, _VBZ_FONT_INDEX)
    renderstring(line, target_bmp=tg.bitmap, target_offs=0, start_x=max(1, _BADGE_W - line_w - 1))
    varinit.currentfont = before
    tg.hidden = False
    # the badge bitmap is filled edge-to-edge with the background color
    # regardless of how wide the line number text actually is, so destination
    # text must start past the badge's real right edge (its own x offset + width), not just past the text.
    dest_x = _BADGE_X + _BADGE_W + _DEST_GAP

    is_now = mins.strip().isdigit() and int(mins.strip()) <= 0
    tail = "now" if is_now else (mins + "'")  # "'" = live tick - every departure here is live
    # only append genuine realtime delays ("+2", "-1"); ignore "+0"/null/"X" (no data)
    if raw_delay and raw_delay[0] in ("+", "-") and raw_delay != "+0":
        tail += raw_delay
    tail_w = _width(tail, _VBZ_FONT_INDEX)
    tail_x = max(0, varinit.if_long - tail_w - 1)

    max_dest_w = max(0, tail_x - dest_x)
    while dest and _width(dest, _VBZ_FONT_INDEX) > max_dest_w:
        dest = dest[:-1]

    _draw(dest, dest_x, y)
    _draw(tail, tail_x, y)


def render():
    _ensure_station()
    _ensure_badges()
    varinit.tg1.y, varinit.tg2.y = varinit.if_tall, varinit.if_tall
    varinit.tg3.x, varinit.tg3.y = 0, 0
    cls(varinit.topbottom)
    rows = get_departure(num="1")
    if not rows or rows[0][0] == "1":
        for tg in _badges: tg.hidden = True
        refresh()
        return time.monotonic()
    shown = rows[:_max_rows()]
    for i, row in enumerate(shown):
        _draw_row(i, row, i * _ROW_PITCH)
    for i in range(len(shown), len(_badges)):
        _badges[i].hidden = True
    refresh()
    return time.monotonic()


def animate_tick():
    return False


def message_active():
    return False


def refresh_settings():
    render()


def on_enter():
    _ensure_station()
    return render()


def on_exit():
    for tg in _badges:
        tg.hidden = True

