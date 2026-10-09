# Bootstraps departuresplus from https://github.com/jnbp/matrixbox-departures-plus/tree/main/departuresplus on first run
from __main__ import *

_RAW    = "https://raw.githubusercontent.com/jnbp/matrixbox-departures-plus/refs/heads/main/departuresplus/"
_API    = "https://api.github.com/repos/jnbp/matrixbox-departures-plus/git/trees/main"
_DIR    = "/departuresplus"
_MARKER = _DIR + "/.installed"
_SUBDIR = "departuresplus"

def _progress(current, total, name):
    from load_screen import window, pset, font_mini
    w = display.width
    h = display.height
    window.fill(0)
    pprint(name.split("/")[-1], 1, _clearscreen=False, color="yellow")
    bar_h = 4
    bar_y = h - bar_h - 9
    bar_x = 1
    bar_w = w - 2
    for px in range(bar_x, bar_x + bar_w):
        pset(px, bar_y, 5)
        pset(px, bar_y + bar_h, 5)
    for py in range(bar_y, bar_y + bar_h + 1):
        pset(bar_x, py, 5)
        pset(bar_x + bar_w - 1, py, 5)
    fill_w = int((bar_w - 2) * current / max(total, 1))
    for px in range(bar_x + 1, bar_x + 1 + fill_w):
        for py in range(bar_y + 1, bar_y + bar_h):
            pset(px, py, 7)
    pprint("installing " + str(current) + "/" + str(total), 0, _clearscreen=False)

try:
    open(_MARKER).close()
except:
    try: microcontroller.cpu.frequency = 240000000
    except: pass
    clearscreen(False)

    # 1) Get the root tree, find the departuresplus/ subtree
    r = requests.get(_API, headers={"User-Agent": "MatrixBOX"}, timeout=10)
    root = json.loads(r.text)["tree"]
    r.close()

    sub = None
    for i in root:
        if i["path"] == _SUBDIR and i["type"] == "tree":
            sub = i
            break
    if sub is None:
        pprint("subdir not found", 0, _clearscreen=True, color="red")
        try: microcontroller.cpu.frequency = 180000000
        except: pass
        raise Exception("missing subdir: " + _SUBDIR)

    # 2) Recursively list the departuresplus/ subtree
    r = requests.get(sub["url"] + "?recursive=1",
                     headers={"User-Agent": "MatrixBOX"}, timeout=10)
    tree = json.loads(r.text)["tree"]
    r.close()

    blobs = [i for i in tree if i["type"] == "blob"]
    total = len(blobs)
    downloads = []

    # 3) Download everything (paths are relative to departuresplus/)
    for x, item in enumerate(blobs):
        path = item["path"]
        _progress(x, total, path)
        r = requests.get(_RAW + path, timeout=10)
        if r.status_code != 200:
            code = r.status_code
            r.close()
            pprint("http " + str(code), 0, _clearscreen=True, color="red")
            try: microcontroller.cpu.frequency = 180000000
            except: pass
            raise Exception("download failed: " + path)
        ext = path.rsplit(".", 1)[-1] if "." in path else ""
        mode = "wb" if ext in ("mpy", "bin", "bmp", "png", "gif", "jpg") else "w"
        downloads.append((path, r.content if mode == "wb" else r.text, mode))
        r.close()
        _progress(x + 1, total, path)

    # 4) Clear the progress screen, then write everything to disk
    from load_screen import window
    window.fill(0)
    display.refresh()

    for path, data, mode in downloads:
        parts = path.split("/")
        if len(parts) > 1:
            d = _DIR
            for p in parts[:-1]:
                d += "/" + p
                try: os.mkdir(d)
                except: pass
        with open(_DIR + "/" + path, mode) as f:
            f.write(data)

    with open(_MARKER, "w") as f:
        f.write("")

    try: microcontroller.cpu.frequency = 180000000
    except: pass
    pprint("Done!", 0, _clearscreen=True)
    microcontroller.reset()

os.rename("/renderer.py","/__init__,py")
exec(open(_DIR + "/__init__.py").read())
#exec(open(_DIR + "/renderer.py").read())
