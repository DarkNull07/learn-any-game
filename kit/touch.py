"""Turns `getevent -lt` lines into gestures (tap, long-press, swipe, multi-touch) in screen pixels.

The recorder writes each line as "<pc_time>\t<getevent line>". Kernel time -> PC time uses the smallest
(pc_time - kernel_time) seen, i.e. the line that arrived with the least delay."""
import re

LINE = re.compile(r"\[\s*([\d.]+)\]\s+(?:/dev/input/event\d+:\s+)?(\w+)\s+(\w+)\s+(\w+)")
TAP_MOVE = 30        # px: moved less than this = tap / long-press
LONG_PRESS = 0.35    # s


def parse_lines(lines):
    """-> (events, offset) where events are (kernel_t, type, code, value) and offset maps kernel time to PC time."""
    events, offset = [], None
    for raw in lines:
        pc = None
        if "\t" in raw:
            a, raw = raw.split("\t", 1)
            try:
                pc = float(a)
            except ValueError:
                pc = None
        m = LINE.search(raw)
        if not m:
            continue
        t = float(m.group(1))
        events.append((t, m.group(2), m.group(3), m.group(4)))
        if pc is not None:
            d = pc - t
            offset = d if offset is None or d < offset else offset
    return events, offset or 0.0


def _val(v):
    if v in ("DOWN", "UP"):
        return v
    try:
        return int(v, 16)
    except ValueError:
        return v


def contacts(events, raw_max, screen, rotation=0):
    """Fingers as lists of (t, x, y). raw_max=(max_x, max_y) of the touch device, screen=(w, h) in the NATURAL
    (portrait) orientation; rotation 0-3 maps to what the user saw."""
    W, H = screen
    sx, sy = W / (raw_max[0] + 1), H / (raw_max[1] + 1)

    def to_screen(x, y):
        nx, ny = x * sx, y * sy
        if rotation == 1:
            return ny, W - nx
        if rotation == 2:
            return W - nx, H - ny
        if rotation == 3:
            return H - ny, nx
        return nx, ny

    slot, active, done = 0, {}, []
    pos = {}         # slot -> [x, y]
    dirty = set()
    for t, typ, code, value in events:
        v = _val(value)
        if typ == "EV_ABS":
            if code == "ABS_MT_SLOT":
                slot = v
            elif code == "ABS_MT_TRACKING_ID":
                if v == 0xFFFFFFFF or v == -1:
                    c = active.pop(slot, None)
                    if c is not None:
                        c["up"] = t
                        done.append(c)
                else:
                    active[slot] = {"id": v, "down": t, "pts": [], "up": None}
                    pos.setdefault(slot, [None, None])
            elif code in ("ABS_MT_POSITION_X", "ABS_X"):
                pos.setdefault(slot, [None, None])[0] = v
                dirty.add(slot)
            elif code in ("ABS_MT_POSITION_Y", "ABS_Y"):
                pos.setdefault(slot, [None, None])[1] = v
                dirty.add(slot)
        elif typ == "EV_KEY" and code == "BTN_TOUCH" and v == "DOWN" and not active:
            active[slot] = {"id": -2, "down": t, "pts": [], "up": None}   # single-touch screens without tracking ids
        elif typ == "EV_KEY" and code == "BTN_TOUCH" and v == "UP":
            for s, c in list(active.items()):
                c["up"] = t
                done.append(c)
            active.clear()
        elif typ == "EV_SYN" and code == "SYN_REPORT":
            for s in list(dirty | set(active)):
                c = active.get(s)
                p = pos.get(s)
                if c is None or p is None or p[0] is None or p[1] is None:
                    continue
                x, y = to_screen(*p)
                if not c["pts"] or (s in dirty):
                    c["pts"].append((t, round(x), round(y)))
            dirty.clear()
    for c in active.values():   # still down when recording stopped
        c["up"] = c["pts"][-1][0] if c["pts"] else c["down"]
        done.append(c)
    return sorted([c for c in done if c["pts"]], key=lambda c: c["down"])


def gestures(cs, offset=0.0):
    """Groups fingers that overlap in time into one gesture. Times are converted to PC time."""
    out, cur = [], None
    for c in cs:
        if cur and c["down"] <= cur["up"]:
            cur["fingers"].append(c)
            cur["up"] = max(cur["up"], c["up"])
        else:
            cur = {"down": c["down"], "up": c["up"], "fingers": [c]}
            out.append(cur)
    res = []
    for g in out:
        f0 = g["fingers"][0]
        (t0, x0, y0), (t1, x1, y1) = f0["pts"][0], f0["pts"][-1]
        path = sum(((b[1] - a[1]) ** 2 + (b[2] - a[2]) ** 2) ** 0.5 for a, b in zip(f0["pts"], f0["pts"][1:]))
        span = max(max(abs(p[1] - x0), abs(p[2] - y0)) for p in f0["pts"])
        dur = g["up"] - g["down"]
        if len(g["fingers"]) > 1:
            kind = "multi"
        elif span < TAP_MOVE:
            kind = "long-press" if dur >= LONG_PRESS else "tap"
        else:
            kind = "swipe"
        res.append({
            "t_down": g["down"] + offset, "t_up": g["up"] + offset, "dur": round(dur, 3), "type": kind,
            "start": (x0, y0), "end": (x1, y1), "path_px": round(path), "fingers": len(g["fingers"]),
            "points": [[(round(t + offset, 3), x, y) for t, x, y in f["pts"]] for f in g["fingers"]],
        })
    return res


def load(path, raw_max, screen, rotation=0):
    with open(path, encoding="utf-8", errors="ignore") as f:
        events, offset = parse_lines(f.read().splitlines())
    return gestures(contacts(events, raw_max, screen, rotation), offset)
