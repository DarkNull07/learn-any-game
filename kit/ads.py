"""The ad close ladder. Ads put their X/Skip anywhere (top-left, top-right, a skip arrow, "skip to Google Play" that
opens the store), so nothing here relies on fixed coordinates. Instead every step is checked:

 1. Is it an ad? (ad SDK activity in front, from library\\ads\\sdk_activities.txt) Game popups are NOT handled here:
    the game's own screens.json decides those, so an X inside a paid offer is never tapped by this generic code.
 2. Another app in front (Play Store, browser)? Back, then a warm resume of the game (never a restart for this).
 3. Known close buttons from library\\ads\\close_templates (shared by all games), match >= 0.88.
 4. Close/Skip controls that Android names (uiautomator), never ones with install/get/play/reward/price words.
 5. X glyphs in the top corners; inside the game's own activity they must sit in a small clickable ad control.
 6. Nothing yet: wait (countdowns), re-checking every frame.
 7. Back key, only while an ad SDK activity is in front.
 8. After each tap: closed -> learn the button (saved as a new template for every game); store opened -> blacklist it.
 9. Restart the game only when stuck longer than max(20 s, the game's cold start time), or 3 store jumps.
Every ad is logged to library\\ads\\ads_log.csv (game, ad screen, method, seconds, result)."""
import csv
import datetime as dt
import glob
import os
import threading
import time

import cv2
import numpy as np

from . import adclose, config

MIN_SCORE = 0.88
LEARNED_MIN_SCORE = 0.92   # buttons the kit learned by itself must match more closely
INNER_R = 18           # templates are 37x37 at 1080 px screen width; only the inner X is compared
REF_W = 1080


def _inner_mask(size, r):
    yy, xx = np.mgrid[:size, :size] - (size - 1) / 2
    return (np.hypot(xx, yy) <= r).astype(np.uint8) * 255


class AdCloser:
    def __init__(self, dev, game, log=print):
        self.dev, self.game, self.log = dev, game, log
        self.pkg = game.get("package")
        self.lib = os.path.join(config.LIBRARY, "ads")
        os.makedirs(os.path.join(self.lib, "close_templates"), exist_ok=True)
        os.makedirs(os.path.join(self.lib, "blacklist"), exist_ok=True)
        self.sdk = self._patterns()
        self._load_templates()
        if not self.sdk or not self.templates:
            self.log(f"  WARNING: the shared ad library looks empty ({self.lib}): ad screens can't be recognised. "
                     f"Run  gamekit sync \"{game.get('name')}\"  from the kit folder to fix the link.")

    # ------------------------------------------------------------ library
    def _patterns(self):
        p = os.path.join(self.lib, "sdk_activities.txt")
        out = []
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.split("#", 1)[0].strip()
                    if line:
                        out.append(line.lower())
        return out

    def _load_templates(self):
        self.templates = []
        for f in sorted(glob.glob(os.path.join(self.lib, "close_templates", "*.png"))):
            t = cv2.imread(f, cv2.IMREAD_GRAYSCALE)
            if t is not None and t.std() >= 1:
                self.templates.append((os.path.basename(f), t))
        self.blacklist = [cv2.imread(f, cv2.IMREAD_GRAYSCALE)
                          for f in glob.glob(os.path.join(self.lib, "blacklist", "*.png"))]

    def is_ad_activity(self, activity):
        a = (activity or "").lower()
        return any(p in a for p in self.sdk)

    def _crop(self, img, x, y):
        s = max(1.0, img.shape[1] / REF_W)
        r = int(18 * s)
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        c = g[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1]
        if c.shape[0] != 2 * r + 1 or c.shape[1] != 2 * r + 1:
            return None
        return cv2.resize(c, (37, 37), interpolation=cv2.INTER_AREA) if s != 1.0 else c

    def _blacklisted(self, img, x, y):
        c = self._crop(img, x, y)
        if c is None:
            return False
        for b in self.blacklist:
            if b is not None and b.shape == c.shape:
                v = cv2.matchTemplate(c, b, cv2.TM_CCOEFF_NORMED)[0][0]
                if v > 0.9:
                    return True
        return False

    def _learn(self, img, x, y, folder, name):
        c = self._crop(img, x, y)
        if c is not None and c.std() > 8:
            p = os.path.join(self.lib, folder, f"{name}_{dt.datetime.now():%Y%m%d_%H%M%S}.png")
            cv2.imwrite(p, c)
            self._load_templates()
            return p
        return None

    def _record(self, row):
        p = os.path.join(self.lib, "ads_log.csv")
        new = not os.path.exists(p)
        with open(p, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["time", "game", "ad_screen", "method", "seconds", "result", "taps"])
            if new:
                w.writeheader()
            w.writerow(row)

    # ------------------------------------------------------------ finders
    def known(self, img):
        """(x, y, score, name) of the best known close/skip button in the corners, or None."""
        H, W = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        s0 = W / REF_W
        zones = [(0, int(H * .30), 0, int(W * .40), False), (0, int(H * .30), int(W * .60), W, False),
                 (int(H * .82), H, int(W * .55), W, True)]       # bottom right: skip buttons only
        best = None
        for y0, y1, x0, x1, skip_only in zones:
            part = gray[y0:y1, x0:x1]
            for name, t in self.templates:
                if skip_only and not name.lower().startswith("skip"):
                    continue
                need = LEARNED_MIN_SCORE if "learned" in name else MIN_SCORE
                for s in (0.8, 1.0, 1.25):
                    k = s * s0
                    tt = cv2.resize(t, None, fx=k, fy=k, interpolation=cv2.INTER_AREA) if k != 1 else t
                    if tt.shape[0] >= part.shape[0] or tt.shape[1] >= part.shape[1]:
                        continue
                    m = _inner_mask(tt.shape[0], INNER_R * k)
                    r = np.nan_to_num(cv2.matchTemplate(part, tt, cv2.TM_CCOEFF_NORMED, mask=m), nan=0, posinf=0, neginf=0)
                    _, v, _, (x, y) = cv2.minMaxLoc(r)
                    if v >= need and (best is None or v > best[2]):
                        best = (x0 + x + tt.shape[1] // 2, y0 + y + tt.shape[0] // 2, float(v), name)
        return best

    # ------------------------------------------------------------ the ladder
    def handle(self, is_game_screen, why="unknown screen"):
        """Runs until the game is back. is_game_screen(img) -> True when a known game screen shows.
        Returns 'closed', 'restarted' or 'failed'."""
        t0 = t_progress = time.time()
        cold = float(self.game.get("cold_start_s") or 20)
        limit = max(20.0, cold)
        taps, store_jumps, backs = [], 0, 0
        spot = {}
        last_cand, t_cand = None, 0.0
        ui = {"xml": "", "t": 0.0, "busy": False}
        fg_pkg, fg_act, t_fg = self.pkg, None, 0.0
        first_ad_act = None
        self.log(f"  Ad ladder started ({why}). Restart only after {limit:.0f}s stuck.")

        def ui_worker():
            ui["busy"] = True
            ui["xml"], ui["t"] = self.dev.ui_dump(), time.time()
            ui["busy"] = False

        def finish(result, method):
            secs = time.time() - t0
            self._record({"time": dt.datetime.now().isoformat(timespec="seconds"), "game": self.game.get("name"),
                          "ad_screen": first_ad_act or fg_act, "method": method, "seconds": round(secs, 1),
                          "result": result, "taps": len(taps)})
            self.log(f"  Ad {result} by {method} after {secs:.1f}s.")
            return result

        def look():
            """Foreground app; when Android doesn't answer, the last known answer stays (unknown is not 'left')."""
            nonlocal fg_pkg, fg_act, t_fg
            p, a = self.dev.foreground()
            t_fg = time.time()
            if p:
                fg_pkg, fg_act = p, a
            return fg_pkg, fg_act

        while True:
            img = self.dev.screenshot()
            if time.time() - t_fg > 1.0:
                look()
                if self.is_ad_activity(fg_act) and not first_ad_act:
                    first_ad_act = fg_act
                    self.log(f"  Ad screen: {fg_act}")
            if fg_pkg == self.pkg and not self.is_ad_activity(fg_act) and is_game_screen(img):
                return finish("closed", taps[-1][2] if taps else "waiting")
            ad_front = self.is_ad_activity(fg_act)

            # another app in front: an ad opened the store or a browser
            if fg_pkg and self.pkg and fg_pkg != self.pkg:
                store_jumps += 1
                self.log(f"  Another app opened ({fg_pkg}). Going back to the game (no restart).")
                if taps:
                    bx, by, _, bimg = taps[-1]
                    p = self._learn(bimg, bx, by, "blacklist", "opens_store")
                    if p:
                        self.log(f"  That button opens the store: blacklisted ({os.path.basename(p)}).")
                self.dev.back()
                time.sleep(0.8)
                if look()[0] != self.pkg:
                    self.dev.resume(self.pkg)
                    time.sleep(float(self.game.get("warm_resume_s") or 1.5))
                t_fg = 0.0
                t_progress = time.time()
                if store_jumps >= 3:
                    return self._restart(finish, "the ad kept opening other apps")
                continue

            # uiautomator in the background (it takes 1-3 s)
            if not ui["busy"] and time.time() - ui["t"] > 3.0 and time.time() - t0 > 1.0:
                threading.Thread(target=ui_worker, daemon=True).start()

            cand = None
            k = self.known(img)
            if k and not self._blacklisted(img, k[0], k[1]):
                cand = (k[0], k[1], f"known button {k[3]} ({k[2]:.2f})")
            if not cand and ui["xml"] and time.time() - ui["t"] < 5:
                surface, named, anon = adclose.ui_candidates(ui["xml"], width=img.shape[1], height=img.shape[0])
                for x, y in named:
                    if not self._blacklisted(img, x, y):
                        cand = (x, y, "Close/Skip control named by Android")
                        break
                if not cand:
                    vis = adclose.visual_candidates(img)
                    hits = adclose.corroborated(vis, anon) if not ad_front else vis
                    for x, y, sc in hits:
                        if not self._blacklisted(img, x, y):
                            cand = (x, y, f"X shape in a corner ({sc:.2f})")
                            break
            elif not cand and ad_front and time.time() - t0 > 2.0:
                for x, y, sc in adclose.visual_candidates(img):
                    if not self._blacklisted(img, x, y):
                        cand = (x, y, f"X shape in a corner ({sc:.2f})")
                        break

            # the candidate must stay put for a moment (buttons fade in; a moving one is part of the ad)
            if cand:
                same = last_cand and abs(cand[0] - last_cand[0]) < 12 and abs(cand[1] - last_cand[1]) < 12
                if not same:
                    last_cand, t_cand = cand, time.time()
                key = (round(cand[0] / 30), round(cand[1] / 30))
                n, t_last = spot.get(key, (0, 0.0))
                if same and time.time() - t_cand >= 0.12 and n < 2 and time.time() - t_last >= 4.0:
                    self.log(f"  Tapping {cand[2]} at ({cand[0]},{cand[1]}) after {time.time() - t0:.1f}s.")
                    was_ad_screen = ad_front
                    if self.dev.tap(cand[0], cand[1]):
                        spot[key] = (n + 1, time.time())
                        taps.append((cand[0], cand[1], cand[2].split(" (")[0], img))
                        t_progress = time.time()
                        time.sleep(0.8)
                        after = self.dev.screenshot()
                        pk, act = look()
                        back_in_game = pk == self.pkg and is_game_screen(after)
                        ad_screen_gone = was_ad_screen and pk == self.pkg and not self.is_ad_activity(act)
                        # learn only a button whose tap really ended the ad, never one that merely got tapped
                        if (back_in_game or ad_screen_gone) and not cand[2].startswith("known"):
                            zone = "skip_learned" if cand[1] > img.shape[0] * 0.82 else "learned"
                            p = self._learn(img, cand[0], cand[1], "close_templates", zone)
                            if p:
                                self.log(f"  New close button learned for every game: {os.path.basename(p)}")
                        if back_in_game:
                            return finish("closed", taps[-1][2])
                    last_cand = None
                    continue
            elif ad_front and backs < 2 and time.time() - t0 > 8.0 and time.time() - t_progress > 4.0:
                backs += 1
                self.log("  No close button yet: pressing Back once (ad screen in front).")
                self.dev.back()
                taps.append((0, 0, "back key", img))
                t_progress = time.time()
                time.sleep(0.8)
                continue

            if time.time() - t_progress > limit:
                return self._restart(finish, f"{time.time() - t_progress:.0f}s with no way to close it")

    def _restart(self, finish, why):
        self.log(f"  Restarting the game: {why}.")
        self.dev.restart(self.pkg)
        time.sleep(float(self.game.get("cold_start_s") or 15))
        return finish("restarted", why)
