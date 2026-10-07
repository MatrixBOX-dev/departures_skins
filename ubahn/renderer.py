"""U-Bahn Berlin skin for /departures.

Clones the classic BVG amber dot-matrix "Linie / Ziel / Abfahrt" boards:
    U9   Rathaus Steglitz          1'
    U9   Rathaus Steglitz          6'

Unlike dsa/tfl_dlr/vbz, the real hardware this is modeled on is a plain
single-color (amber/yellow) LED matrix - there are no per-line colors or
route-line graphics on the actual boards (those were an earlier, inaccurate
draft of this skin). Line ID, destination and countdown are all drawn in
the same color: the host's existing accent LED tone (palette index 1, the
same "Tone" swatch list/scroll/DSA/TfL DLR/VBZ already use by default), so
changing that setting still re-colors this skin too.

Data source: the host's existing get_departure() pipeline (same one
DSA/VBZ use), so no new API integration is needed. Berlin line IDs are
short ("U1".."U9", "S1".."S9", "S41"/"S42"/etc.), so the line-ID column is
narrower than VBZ's.

The header row ("Linie Ziel Abfahrt") and footer ("Gleis N") visible on
the real boards, plus the white scrolling service-message row, are left
for later - this first pass only covers line/destination/minutes.

The line ID is drawn noticeably bigger than the destination/countdown
text (matching the reference photos), but not a flat 2x: a straight
pixel-double made the badge dominate the whole row and left room for
only a single departure on small (32px-tall) panels. Instead, only the
badge's *height* is stretched, via nearest-neighbor row mapping, which
duplicates only *some* source rows rather than doubling every one of
them - so it grows by a modest ratio (~1.3x by default), and that ratio
is further reduced automatically so at least two rows always still fit
whatever panel height is actually available. Width is always drawn at
the font's native pixel widths - stretching columns too made some glyph
strokes end up a different pixel width than others (e.g. "M"'s two
legs), which looked uneven/wrong.

Settings:
    ubahn_countdown_style - 0: "5'" ticks (default, matches most photos)
                             1: "in 5 min" (older board style)

Contract: on_enter(), render(), animate_tick(), message_active(), refresh_settings()
"""
import time, json
import varinit
from functions import cls, refresh, get_departure

_FONT_PATH = "skins/ubahn/ubahn_font.json"
try:
    with open(_FONT_PATH) as f:
        varinit.fonts.append(json.load(f))
    _UBAHN_FONT_INDEX = len(varinit.fonts) - 1
except Exception as e:
    print("UBAHN: font load failed, falling back to host small font:", e)
    _UBAHN_FONT_INDEX = 1

# --- layout constants -----------------------------------------------------
_LINE_X = 1              # line-ID column start
_LINE_SCALE_TARGET = 1.3 # desired line-ID enlargement ratio (see module docstring)
_LINE_MIN_ROWS = 2       # always keep at least this many departure rows visible
_DEST_GAP = 5            # px between the line-ID column and the destination text
_TAIL_GAP = 2            # px kept clear between destination text and the countdown
_ROW_GAP = 2             # px between one row's text and the next row's
_CENTER_NUDGE = 1        # extra px to shift destination/countdown text down,
                         # beyond plain box-centering against the line-ID badge -
                         # a pure center lands a touch high since the font's
                         # glyphs aren't perfectly symmetric top/bottom; bump
                         # to 2 if it still looks high on the real panel.


def _font():
    return varinit.fonts[_UBAHN_FONT_INDEX]


def _fontheight():
    return _font()["fontheight"]


def _line_scale():
    # Shrinks _LINE_SCALE_TARGET (down to 1.0, i.e. no enlargement at all)
    # as needed so _LINE_MIN_ROWS rows always still fit the active panel
    # height - a flat target ratio alone could still blow up on very short
    # panels the way an unconditional 2x did on a 32px-tall one.
    fontheight = _fontheight()
    max_h = (varinit.if_tall + _ROW_GAP) // _LINE_MIN_ROWS - _ROW_GAP
    max_scale = max(1.0, max_h / fontheight)
    return min(_LINE_SCALE_TARGET, max_scale)


def _badge_height():
    return max(_fontheight(), round(_fontheight() * _line_scale()))


def _row_pitch():
    return _badge_height() + _ROW_GAP


def _line_col_w():
    # worst-case line ID width: Berlin U-Bahn is "U1".."U9" (2 chars), S-Bahn
    # runs up to "S41"/"S85" (3 chars) - size the column for the latter.
    # Width is always drawn at the font's native pixel widths (only height
    # is stretched - see _draw()), so this doesn't depend on badge height.
    return _width("S41") + _DEST_GAP


def _max_rows():
    return max(1, (varinit.if_tall + _ROW_GAP) // _row_pitch())


def _width(text):
    # always the font's native (unscaled) glyph widths - only height is
    # ever stretched (see _draw()), so column count/stroke width never
    # changes and layout math stays simple.
    f = _font()
    total = 0
    for c in str(text):
        if c not in f: c = "_"
        glyph = f[c]
        total += glyph[0] if isinstance(glyph[1], int) else len(glyph[1])
    return total


def _draw(text, x, y, dst_h=None):
    # always the host's accent LED tone (palette index 1) - this skin has no
    # per-line colors, matching the real board's single-color LED matrix.
    # dst_h stretches the glyph vertically to that many pixels tall via
    # nearest-neighbor row mapping (duplicates only some source rows, not
    # every one, so non-integer ratios like 1.25x are possible, not just
    # 2x). Width is always drawn at the font's native pixel widths - also
    # stretching columns made some glyph strokes end up a different pixel
    # width than others (e.g. "M"'s two legs), which looked uneven/wrong.
    font = _font()
    src_h = font["fontheight"]
    dst_h = src_h if dst_h is None else dst_h
    pixwidth = x
    for character in str(text):
        if character not in font: character = "_"
        glyph = font[character]
        src_w = glyph[0]
        for col in range(src_w):
            for row in range(dst_h):
                src_row = min(src_h - 1, (row * src_h) // dst_h)
                bit = 1 if glyph[src_row + 1][col] == "1" else 0
                try:
                    varinit.topbottom[pixwidth + col, y + row] = bit
                except Exception:
                    pass
        pixwidth += src_w
    return pixwidth


def _truncate(text, max_w):
    while text and _width(text) > max_w:
        text = text[:-1]
    return text


# --- station defaulting -----------------------------------------------------
def _ensure_station():
    stn = varinit.settings["stations"]["1"]
    # Berlin: country="de", operator="vbb" (VBB = Berlin-Brandenburg transport
    # association), same pipeline DSA uses for other German operators.
    if stn.get("country") != "de":
        stn["country"] = "de"
        stn["operator"] = "vbb"
        stn["siteid"] = "900000100001"   # Berlin Hbf - sensible default until the user searches their own stop
        stn["mystation"] = "Berlin Hbf"


# --- row drawing ------------------------------------------------------------
def _draw_row(row, y):
    line = str(row[1]).strip().upper()
    dest = str(row[2])
    mins = str(row[3]).strip()
    raw_delay = row[6] if len(row) > 6 else ""

    line_col_w = _line_col_w()
    dest_x = line_col_w
    badge_h = _badge_height()
    line = _truncate(line, line_col_w - _DEST_GAP)

    verbose = int(varinit.settings.get("ubahn_countdown_style", 0))
    is_now = mins.isdigit() and int(mins) <= 0
    if is_now:
        tail = "now"
    elif verbose:
        tail = "in " + mins + " min"
    else:
        tail = mins + "'"
    if raw_delay and raw_delay[0] in ("+", "-") and raw_delay != "+0":
        tail += raw_delay

    tail_w = _width(tail)
    tail_x = max(0, varinit.if_long - tail_w - 1)
    max_dest_w = max(0, tail_x - _TAIL_GAP - dest_x)
    dest = _truncate(dest, max_dest_w)

    # destination/countdown stay at the font's native size, vertically
    # centered against the (modestly) taller line-ID badge on the same row.
    text_y = y + (badge_h - _fontheight()) // 2 + _CENTER_NUDGE

    _draw(line, _LINE_X, y, badge_h)
    _draw(dest, dest_x, text_y)
    _draw(tail, tail_x, text_y)


# --- contract ---------------------------------------------------------------
def render():
    _ensure_station()
    varinit.tg1.y, varinit.tg2.y = varinit.if_tall, varinit.if_tall
    varinit.tg3.x, varinit.tg3.y = 0, 0
    cls(varinit.topbottom)
    rows = get_departure(num="1")
    if rows and rows[0][0] != "1":
        pitch = _row_pitch()
        for i, row in enumerate(rows[:_max_rows()]):
            _draw_row(row, i * pitch)
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
