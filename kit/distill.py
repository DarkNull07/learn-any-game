"""Cuts a recorded session into what an agent needs to read, with zero tokens spent:

  out\\timeline.txt   one line per touch, app/ad change and scene change (read this first)
  out\\triplets\\     per touch: before (touch drawn on it) | finger lifted | settled after
  out\\scenes\\       screens that changed with no touch (auto ads, popups, level transitions, animations)
  out\\sheets\\       contact sheets of all of the above, for skimming
  out\\keyframes\\    every kept frame at half resolution
  out\\dropped.txt    what was left out and why, so nothing is silently lost

Nothing important is dropped: every touch, every app/activity change and every settled screen that differs from the
last kept one is kept. "Differs" is judged per small cell (16x16 px of the half-size video), so a small new X, a
counter tick or one moved tile counts, even when the picture as a whole looks the same.
Any other moment can be pulled later with:  gamekit frame "<Game>" <seconds>"""
import csv
import glob
import json
import os
import re

import cv2
import numpy as np

from . import config, touch

BLOCK = 4            # px of the half-size video averaged into one change block (small enough for one digit)
COARSE = 4           # blocks per side of a coarse cell (for the per-frame motion map kept in memory)
MOTION = 10          # block change (0-255) that counts as "the screen is moving"
SCENE = 16           # block change vs the last kept frame that counts as "a new screen"
ALWAYS_MOVING = 0.25 # coarse cells moving in more than this share of frames are background animation: ignored
SETTLE = 0.3         # s without motion = settled
BEFORE = 0.15        # s before touch-down for the "before" frame (allows for timing error)
AFTER_MAX = 4.0      # s to wait for the screen to settle after a touch
ANIM_EVERY = 1.0     # s: during long motion keep one frame per second
ANIM_AFTER = 2.0     # s of continuous motion before animation frames are kept
PANEL = 360          # px width of each triplet panel
SHEET_PANEL = 240    # px width of each panel in contact sheets


def _fine(img):
    """Grey picture averaged in BLOCK x BLOCK blocks (averaging keeps video compression noise below MOTION)."""
    h, w = img.shape[:2]
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    f = cv2.resize(g, (max(COARSE, w // BLOCK // COARSE * COARSE), max(COARSE, h // BLOCK // COARSE * COARSE)),
                   interpolation=cv2.INTER_AREA)
    return f


def _coarse_max(d):
    h, w = d.shape
    return d.reshape(h // COARSE, COARSE, w // COARSE, COARSE).max(axis=(1, 3))


def _fmt(t):
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m):02d}:{s:05.2f}"


class Session:
    def __init__(self, folder):
        self.dir = folder
        self.out = os.path.join(folder, "out")
        with open(os.path.join(folder, "meta.json"), encoding="utf-8") as f:
            self.meta = json.load(f)
        self.t0 = self.meta["started"]
        self.videos = []        # (path, [frame times]) in order

    # ------------------------------------------------------------ inputs
    def frame_list(self):
        fc = os.path.join(self.dir, "frames.csv")
        if os.path.exists(fc):
            per = {}
            with open(fc, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    per.setdefault(int(r["segment"]), []).append(float(r["pc_time"]))
            for seg in sorted(per):
                self.videos.append((os.path.join(self.dir, f"video_{seg:03d}.mp4"), per[seg]))
        pc = os.path.join(self.dir, "screenrecord_parts.csv")
        if os.path.exists(pc):
            with open(pc, encoding="utf-8") as f:
                for line in f:
                    part, start = line.strip().split(",")
                    self.videos.append((os.path.join(self.dir, f"screenrecord_{int(part):03d}.mp4"), float(start)))

    def gestures(self):
        nat = self.meta["screen_natural"]
        return touch.load(os.path.join(self.dir, "touches.log"), self.meta["touch_max"], nat, self.meta["rotation"])

    def activities(self):
        rows = []
        p = os.path.join(self.dir, "activity.log")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                next(f, None)
                for line in f:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) >= 3:
                        rows.append((float(parts[0]), parts[1], parts[2]))
        return rows

    # ------------------------------------------------------------ frames
    def frames(self):
        """Yields (global index, time, video index, index in video, picture) for every frame."""
        gi = 0
        for vi, (path, tinfo) in enumerate(self.videos):
            cap = cv2.VideoCapture(path)
            i = 0
            while True:
                ok, img = cap.read()
                if not ok:
                    break
                if isinstance(tinfo, list):
                    if i >= len(tinfo):
                        break
                    t = tinfo[i]
                else:   # screenrecord: the file's own frame times
                    t = tinfo + cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                yield gi, t, vi, i, img
                gi += 1
                i += 1
            cap.release()

    # ------------------------------------------------------------ pass 1: times + a motion map for every frame
    def scan(self, log):
        self.frame_list()
        times, maps, prev, self.scale = [], [], None, None
        for gi, t, vi, i, img in self.frames():
            if self.scale is None:
                nat = self.meta["screen_natural"]
                self.scale = img.shape[1] / (nat[0] if self.meta["rotation"] in (0, 2) else nat[1])
            f = _fine(img)
            times.append(t)
            maps.append(_coarse_max(cv2.absdiff(f, prev)) if prev is not None and prev.shape == f.shape
                        else np.full((f.shape[0] // COARSE, f.shape[1] // COARSE), 255, np.uint8))
            prev = f
        log(f"  read {len(times)} frames from {len(self.videos)} video file(s)")
        self.times = np.array(times)
        if not maps:
            self.motion, self.mask = np.array([]), None
            return
        shape = maps[0].shape
        same = [m for m in maps if m.shape == shape]
        moving_share = (np.stack(same) >= MOTION).mean(axis=0)
        self.mask = moving_share > ALWAYS_MOVING          # background animation: ignored everywhere
        self.mask_share = float(self.mask.mean())
        keep_cells = ~self.mask
        self.motion = np.array([float(m[keep_cells].max()) if m.shape == shape and keep_cells.any() else float(m.max())
                                for m in maps])
        self.motion[0] = 0.0

    def idx_at(self, t):
        i = int(np.searchsorted(self.times, t, side="right")) - 1
        return min(max(i, 0), len(self.times) - 1)

    def settled_after(self, i, limit_t):
        """First frame from i on after which the screen stays still for SETTLE seconds (else the frame at limit_t)."""
        n, j = len(self.times), i
        while j < n and self.times[j] <= limit_t:
            k, ok = j + 1, True
            while k < n and self.times[k] - self.times[j] <= SETTLE:
                if self.motion[k] >= MOTION:
                    ok = False
                    break
                k += 1
            if ok:
                return j, True
            j = k
        return self.idx_at(limit_t), False


def run(session_dir, log=print):
    s = Session(session_dir)
    log(f"Distilling {session_dir}")
    for sub in ("triplets", "scenes", "sheets", "keyframes"):
        os.makedirs(os.path.join(s.out, sub), exist_ok=True)
        for old in glob.glob(os.path.join(s.out, sub, "*.jpg")):
            os.remove(old)
    s.scan(log)
    n = len(s.times)
    s.mask_share = getattr(s, "mask_share", 0.0)
    if not n:
        log("  No video frames found. Was the recording stopped too early?")
        return None
    gests = s.gestures()
    acts = s.activities()
    ad_words = _ad_patterns()
    log(f"  {n} frames, {len(gests)} touches, {len(acts)} app/activity changes")

    keep = {}     # frame index -> label
    items = []    # timeline entries: (time, kind, data)
    busy = []     # (i0, i1) frame ranges covered by touch triplets
    for gi, g in enumerate(gests, 1):
        nxt = gests[gi]["t_down"] if gi < len(gests) else s.times[-1]
        b = s.idx_at(g["t_down"] - BEFORE)
        u = s.idx_at(g["t_up"])
        a, settled = s.settled_after(u, min(g["t_up"] + AFTER_MAX, max(nxt - 0.05, g["t_up"])))
        g.update(idx=(b, u, a), settled=settled, n=gi)
        for i, lab in ((b, "before"), (u, "up"), (a, "after")):
            keep.setdefault(i, f"touch{gi:03d}_{lab}")
        busy.append((b, a))
        items.append((g["t_down"], "touch", g))
    for t, pkg, act in acts:
        i = s.idx_at(t + 0.7)
        keep.setdefault(i, "app_change")
        items.append((t, "app", (pkg, act, any(p.search(f"{pkg} {act}") for p in ad_words), i)))

    # pass 2: scenes with no touch (settled screens that differ from the last kept frame, long animations),
    # and saving every kept frame
    busy_mask = np.zeros(n, bool)
    for b0, b1 in busy:
        busy_mask[b0:b1 + 1] = True
    keep.setdefault(0, "start")
    ref, last_kept_t, moving_since = None, s.times[0], None
    dropped, gap_start, gap_max = [], None, 0.0
    fine_mask = None
    imgs = {}

    def save(gi, img):
        p = os.path.join(s.out, "keyframes", f"{gi:06d}_{_fmt(s.times[gi] - s.t0).replace(':', 'm')}.jpg")
        cv2.imwrite(p, img, [cv2.IMWRITE_JPEG_QUALITY, 88])
        imgs[gi] = p

    for i, t, vi, j, img in s.frames():
        if i >= n:
            break
        f = _fine(img)
        if fine_mask is None or fine_mask.shape != f.shape:
            fine_mask = np.ones(f.shape, bool)
            if s.mask is not None and s.mask.shape == (f.shape[0] // COARSE, f.shape[1] // COARSE):
                fine_mask = ~np.kron(s.mask, np.ones((COARSE, COARSE), bool))
        if i in keep:
            save(i, img)
            ref, last_kept_t = f, t
            if gap_start is not None:
                dropped.append((gap_start, s.times[i - 1], gap_max))
                gap_start, gap_max = None, 0.0
            continue
        moving_since = (moving_since or t) if s.motion[i] >= MOTION else None
        diff = float(cv2.absdiff(ref, f)[fine_mask].max()) if ref is not None and ref.shape == f.shape and fine_mask.any() else 255.0
        still = all(s.motion[k] < MOTION for k in range(max(1, i - 3), i + 1))
        label = None
        if not busy_mask[i]:
            if still and diff >= SCENE:
                label = "scene"
            elif moving_since and t - moving_since >= ANIM_AFTER and t - last_kept_t >= ANIM_EVERY:
                label = "animation"
        if label:
            keep[i] = label
            save(i, img)
            ref, last_kept_t = f, t
            items.append((t, label, i))
            if gap_start is not None:
                dropped.append((gap_start, s.times[i - 1], gap_max))
                gap_start, gap_max = None, 0.0
        else:
            if gap_start is None:
                gap_start = t
            if still:
                gap_max = max(gap_max, diff)
    if gap_start is not None:
        dropped.append((gap_start, s.times[-1], gap_max))
    log(f"  kept {len(keep)} of {n} frames"
        + (f" ({s.mask_share:.0%} of the screen ignored as constant background animation)" if s.mask_share else ""))

    sheet_items = []
    for t, kind, data in sorted(items, key=lambda x: x[0]):
        if kind == "touch":
            g = data
            b, u, a = g["idx"]
            panels = []
            for i, title in ((b, "before"), (u, "finger up"), (a, "after" if g["settled"] else "after (still moving)")):
                im = cv2.imread(imgs[i])
                if i == b:
                    _draw_gesture(im, g, s.scale or 0.5)
                panels.append(_label(_fit(im, PANEL), f"{title} {_fmt(s.times[i] - s.t0)}"))
            trip = np.hstack(panels)
            head = f"#{g['n']:03d} {g['type']} {g['start']}->{g['end']} {g['dur']}s"
            trip = _label(trip, head, top=True)
            g["file"] = f"triplets/{g['n']:03d}.jpg"
            cv2.imwrite(os.path.join(s.out, g["file"]), trip, [cv2.IMWRITE_JPEG_QUALITY, 85])
            before = cv2.imread(imgs[b])
            _draw_gesture(before, g, s.scale or 0.5)
            sheet_items.append((f"#{g['n']:03d} {g['type']}", [before, cv2.imread(imgs[a])]))
        elif kind in ("scene", "animation"):
            i = data
            name = f"scenes/{i:06d}_{kind}.jpg"
            cv2.imwrite(os.path.join(s.out, name), cv2.imread(imgs[i]), [cv2.IMWRITE_JPEG_QUALITY, 85])
            sheet_items.append((f"{kind} {_fmt(t - s.t0)}", [cv2.imread(imgs[i])]))
    _sheets(sheet_items, os.path.join(s.out, "sheets"))

    with open(os.path.join(s.out, "timeline.txt"), "w", encoding="utf-8") as f:
        f.write(f"# Session {s.meta.get('stamp')} | phone {s.meta.get('phone', {}).get('model')} | "
                f"screen {s.meta['screen_natural']} rot {s.meta['rotation']} | coords are phone pixels\n")
        f.write(f"# {len(gests)} touches, {len(keep)} kept frames of {n}. Pictures: triplets/NNN.jpg, scenes/, sheets/\n")
        if s.mask_share:
            f.write(f"# {s.mask_share:.0%} of the screen kept moving all session (background animation) and was ignored "
                    f"for change detection\n")
        front = None
        for t, kind, data in sorted(items, key=lambda x: x[0]):
            ts = _fmt(t - s.t0)
            if kind == "touch":
                g = data
                extra = "" if g["settled"] else " (screen still moving after)"
                pts = ""
                if g["type"] in ("swipe", "multi"):
                    for fi, p in enumerate(g["points"], 1):
                        step = max(1, len(p) // 6)
                        pts += f" | finger {fi} path " + " ".join(f"({x},{y})" for _, x, y in p[::step] + [p[-1]])
                f.write(f"{ts} #{g['n']:03d} {g['type']:<10} {g['start']}->{g['end']} {g['dur']}s "
                        f"fingers {g['fingers']}{pts}{extra} | {g['file']}\n")
            elif kind == "app":
                pkg, act, is_ad, _ = data
                front = act or pkg
                game_pkg = (s.meta.get("first_app") or [None])[0]
                tag = "   [AD]" if is_ad else ("   [OTHER APP: left the game]" if game_pkg and pkg != game_pkg else "")
                f.write(f"{ts} APP IN FRONT: {front}{tag}\n")
            else:
                f.write(f"{ts}      {kind}: screen changed with no touch | scenes/{data:06d}_{kind}.jpg\n")
    with open(os.path.join(s.out, "dropped.txt"), "w", encoding="utf-8") as f:
        f.write(f"# Frames left out. A frame is left out only if no touch/app change was near it and every cell differs\n"
                f"# from the last kept frame by less than {SCENE} (or the screen was still moving). Largest cell change in\n"
                f"# each gap is shown; pull any moment with: gamekit frame \"<Game>\" <seconds>\n")
        for a, b, mx in dropped:
            if b - a >= 0.5:
                f.write(f"{_fmt(a - s.t0)} - {_fmt(b - s.t0)}  ({b - a:.1f}s)  largest change {mx:.0f}\n")
    log(f"  Done: {len(gests)} triplets, {sum(1 for x in items if x[1] in ('scene', 'animation'))} scenes. "
        f"Read {os.path.join(s.out, 'timeline.txt')}")
    return s.out


def frame_at(session_dir, seconds, full=True):
    """Saves the frame closest to <seconds> since the recording started into out\\frames\\."""
    s = Session(session_dir)
    s.frame_list()
    t_want = s.t0 + seconds
    best = None
    for vi, (path, tinfo) in enumerate(s.videos):
        cap = cv2.VideoCapture(path)
        i = 0
        while True:
            ok, img = cap.read()
            if not ok:
                break
            t = tinfo[i] if isinstance(tinfo, list) and i < len(tinfo) else (
                tinfo + cap.get(cv2.CAP_PROP_POS_MSEC) / 1000 if not isinstance(tinfo, list) else None)
            if t is not None and (best is None or abs(t - t_want) < best[0]):
                best = (abs(t - t_want), img, t)
            i += 1
        cap.release()
    if not best:
        return None
    os.makedirs(os.path.join(s.out, "frames"), exist_ok=True)
    p = os.path.join(s.out, "frames", f"at_{seconds:.2f}s.png")
    cv2.imwrite(p, best[1])
    return p


# ------------------------------------------------------------ drawing helpers
def _ad_patterns():
    p = os.path.join(config.LIBRARY, "ads", "sdk_activities.txt")
    pats = []
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if line:
                    pats.append(re.compile(re.escape(line), re.I))
    return pats


def _fit(img, width):
    h, w = img.shape[:2]
    return cv2.resize(img, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)


def _label(img, text, top=False):
    bar = np.full((26, img.shape[1], 3), 30, np.uint8)
    cv2.putText(bar, text[:90], (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return np.vstack([bar, img])


def _draw_gesture(img, g, scale):
    colors = [(0, 0, 255), (255, 0, 255), (0, 200, 255)]
    for fi, pts in enumerate(g["points"]):
        c = colors[fi % len(colors)]
        p = [(int(x * scale), int(y * scale)) for _, x, y in pts]
        if g["type"] in ("tap", "long-press"):
            cv2.circle(img, p[0], 16, c, 3)
            cv2.circle(img, p[0], 3, c, -1)
        else:
            cv2.polylines(img, [np.array(p, np.int32)], False, c, 3)
            if len(p) > 1:
                cv2.arrowedLine(img, p[-2] if len(p) > 2 else p[0], p[-1], c, 3, tipLength=0.5)
            cv2.circle(img, p[0], 8, c, -1)


def _sheets(items, folder, cols=3, rows=3):
    if not items:
        return
    per = cols * rows
    for si in range(0, len(items), per):
        cells = []
        for title, ims in items[si:si + per]:
            pan = np.hstack([_fit(im, SHEET_PANEL) for im in ims])
            if pan.shape[1] < 2 * SHEET_PANEL:
                pan = np.hstack([pan, np.zeros((pan.shape[0], 2 * SHEET_PANEL - pan.shape[1], 3), np.uint8)])
            cells.append(_label(pan, title))
        h = max(c.shape[0] for c in cells)
        cells = [np.vstack([c, np.zeros((h - c.shape[0], c.shape[1], 3), np.uint8)]) for c in cells]
        while len(cells) % cols:
            cells.append(np.zeros_like(cells[0]))
        grid = np.vstack([np.hstack(cells[r:r + cols]) for r in range(0, len(cells), cols)])
        cv2.imwrite(os.path.join(folder, f"sheet_{si // per + 1:02d}.jpg"), grid, [cv2.IMWRITE_JPEG_QUALITY, 82])
