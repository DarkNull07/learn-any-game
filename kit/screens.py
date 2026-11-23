"""Recognises a game's screens from model\\screens.json + model\\templates\\*.png (PNG crops from live screenshots,
never from video), and guards every tap against the game's danger list (money offers, reward-ad buttons).

screens.json:
{
  "playing": "level",                      // the screen where solver.play() runs
  "screens": [
    {"name": "level", "template": "level_header.png", "region": [0, 0, 1080, 400], "min_score": 0.85,
     "action": "solve"},
    {"name": "win", "template": "win_title.png", "action": {"tap_template": "next_button.png"}},
    {"name": "offer", "template": "offer_title.png", "action": {"tap": [930, 420]}, "note": "red X only"},
    {"name": "no_lives", "template": "no_lives.png", "action": "stop"}
  ],
  "never_tap": [
    {"template": "price_button.png", "pad": 25, "note": "real money"},
    {"template": "watch_ad_button.png", "pad": 25, "note": "reward ad"}
  ]
}
Actions: "solve", "wait", "stop", {"tap": [x, y]}, {"tap_template": "file.png"}, {"swipe": [x1, y1, x2, y2, ms]}.
Coordinates are phone pixels."""
import json
import os

import cv2
import numpy as np


class Screens:
    def __init__(self, game_folder, log=print):
        self.folder = os.path.join(game_folder, "model")
        self.tdir = os.path.join(self.folder, "templates")
        self.log = log
        self.reload()

    def reload(self):
        p = os.path.join(self.folder, "screens.json")
        self.spec = {"playing": None, "screens": [], "never_tap": []}
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                self.spec.update(json.load(f))
        self._t = {}
        self._half = {}
        self._danger_cache = (None, [])
        self.missing = []
        for item in self.spec["screens"] + self.spec["never_tap"]:
            for key in ("template",):
                self._tpl(item.get(key))
            act = item.get("action")
            if isinstance(act, dict) and act.get("tap_template"):
                self._tpl(act["tap_template"])

    def _tpl(self, name):
        if not name or name in self._t:
            return self._t.get(name)
        img = cv2.imread(os.path.join(self.tdir, name), cv2.IMREAD_GRAYSCALE)
        if img is None:
            self.missing.append(name)
            self.log(f"  WARNING: template {name} is missing in {self.tdir}")
        self._t[name] = img
        return img

    def problems(self):
        """Reasons the bot must not run: a danger-list template that can't be loaded means the guard can't work."""
        danger = {d.get("template") for d in self.spec["never_tap"]}
        return [f"never_tap template missing: {n}" for n in self.missing if n in danger]

    def find(self, img, name, region=None, gray=None):
        """(x, y, score, (x1, y1, x2, y2)) of template `name` in img, or None."""
        t = self._tpl(name)
        if t is None:
            return None
        g = gray if gray is not None else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        x0, y0 = 0, 0
        if region:
            x0, y0, x1, y1 = region
            g = g[y0:y1, x0:x1]
        if g.shape[0] < t.shape[0] or g.shape[1] < t.shape[1]:
            return None
        r = cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED)
        _, v, _, (x, y) = cv2.minMaxLoc(r)
        h, w = t.shape
        return (x0 + x + w // 2, y0 + y + h // 2, float(v), (x0 + x, y0 + y, x0 + x + w, y0 + y + h))

    def classify(self, img):
        """(screen dict, score) of the best matching screen, or (None, best score)."""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        best, best_v = None, 0.0
        for s in self.spec["screens"]:
            m = self.find(img, s.get("template"), s.get("region"), gray)
            if m and m[2] >= s.get("min_score", 0.85) and m[2] > best_v:
                best, best_v = s, m[2]
        return best, best_v

    def name(self, img):
        s, _ = self.classify(img)
        return s["name"] if s else "unknown"

    def danger_boxes(self, img):
        """Visible danger-list buttons as ((x1, y1, x2, y2), note), padded. Matched at half size (fast, ~4x cheaper),
        with a lower score bar than normal so a doubtful match counts as danger. Cached per picture."""
        if self._danger_cache[0] is img:
            return self._danger_cache[1]
        small = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        out = []
        for d in self.spec["never_tap"]:
            name = d.get("template")
            t = self._tpl(name)
            if t is None:
                continue
            if name not in self._half:
                self._half[name] = cv2.resize(t, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
            th = self._half[name]
            g, x0, y0 = small, 0, 0
            if d.get("region"):
                rx1, ry1, rx2, ry2 = [v // 2 for v in d["region"]]
                g, x0, y0 = small[ry1:ry2, rx1:rx2], rx1, ry1
            if g.shape[0] < th.shape[0] or g.shape[1] < th.shape[1]:
                continue
            _, v, _, (x, y) = cv2.minMaxLoc(cv2.matchTemplate(g, th, cv2.TM_CCOEFF_NORMED))
            if v >= d.get("min_score", 0.85) - 0.05:
                pad = d.get("pad", 20)
                x1, y1 = (x0 + x) * 2, (y0 + y) * 2
                out.append(((x1 - pad, y1 - pad, x1 + t.shape[1] + pad, y1 + t.shape[0] + pad), d.get("note", name)))
        self._danger_cache = (img, out)
        return out

    def guard_for(self, dev):
        """A guard for Device: refuses input inside any visible danger box, and refuses everything when the danger
        list itself is broken (missing template) or there is no picture to check."""
        def guard(x, y, img=None):
            bad = self.problems()
            if bad:
                return "; ".join(bad)
            img = img if img is not None else dev.last_img
            if img is None:
                return "no picture of the screen to check against the danger list"
            for (x1, y1, x2, y2), note in self.danger_boxes(img):
                if x1 <= x <= x2 and y1 <= y <= y2:
                    return f"on the danger list ({note})"
            return None
        return guard


def crop(img_path, box, out_path):
    img = cv2.imread(img_path)
    x1, y1, x2, y2 = box
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cv2.imwrite(out_path, img[y1:y2, x1:x2])
    return out_path


def diff_score(a, b):
    return float(cv2.absdiff(cv2.resize(a, (135, 300)), cv2.resize(b, (135, 300))).mean()) if a is not None and b is not None else 0.0


__all__ = ["Screens", "crop", "diff_score", "np"]
