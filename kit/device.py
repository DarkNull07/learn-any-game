"""Everything that talks to the phone: adb with retries, one open adb shell for fast taps, screenshots (live scrcpy
stream when possible, else screencap), taps/swipes/pinch, Back, which app/activity is in front, uiautomator dumps,
warm resume and cold restart of a game, touch-screen info for the recorder.

Every tap goes through `guard` (set by the bot): it can refuse a tap, e.g. on a money button. Coordinates are always
real phone pixels in the current orientation."""
import re
import subprocess
import time

import cv2
import numpy as np

from . import config


class PhoneError(RuntimeError):
    pass


class Device:
    def __init__(self, cfg=None, serial=None, log=print, use_stream=True):
        self.cfg = cfg or config.load()
        self.adb_path = config.find_adb(self.cfg)
        if not self.adb_path:
            raise PhoneError("adb was not found. Install Android platform-tools, or run: gamekit setup")
        self.serial = serial
        self.log = log
        self._shell = None
        self.stream = None
        self.guard = None          # function(x, y) -> reason string to refuse a tap, or None
        self.last_img = None
        self.w, self.h = self.size()
        if use_stream:
            self.start_stream()

    # ------------------------------------------------------------ adb basics
    def _cmd(self, *args):
        return [self.adb_path] + (["-s", self.serial] if self.serial else []) + list(args)

    def adb(self, *args, binary=False, timeout=15, retries=4):
        err = ""
        for attempt in range(retries):
            try:
                r = subprocess.run(self._cmd(*args), capture_output=True, timeout=timeout)
                if r.returncode == 0:
                    return r.stdout if binary else r.stdout.decode(errors="ignore")
                err = r.stderr.decode(errors="ignore").strip()
            except subprocess.TimeoutExpired:
                err = "timeout"
            if attempt + 1 < retries:
                self.log(f"  adb {' '.join(args[:2])} failed ({err}), retrying ({attempt + 1}/{retries - 1})")
                time.sleep(min(2 ** attempt, 4))
        raise PhoneError(f"phone not answering over adb ({err}). Check the cable, then run: adb devices")

    def adb_once(self, *args, timeout=6):
        """One try, '' on failure (for optional things like uiautomator)."""
        try:
            r = subprocess.run(self._cmd(*args), capture_output=True, timeout=timeout)
            return r.stdout.decode(errors="ignore") if r.returncode == 0 else ""
        except (subprocess.TimeoutExpired, OSError):
            return ""

    def shell(self, cmd, timeout=15):
        return self.adb("shell", cmd, timeout=timeout)

    def _fast(self, line):
        """Runs a short command in one adb shell that stays open (a tap costs ~5 ms instead of ~150 ms)."""
        try:
            if self._shell is None or self._shell.poll() is not None:
                self._shell = subprocess.Popen(self._cmd("shell"), stdin=subprocess.PIPE,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._shell.stdin.write((line + " &\n").encode())
            self._shell.stdin.flush()
        except OSError:
            self._shell = None
            self.shell(line)

    # ------------------------------------------------------------ phone facts
    def size(self):
        out = self.shell("wm size")
        m = re.search(r"Override size:\s*(\d+)x(\d+)", out) or re.search(r"Physical size:\s*(\d+)x(\d+)", out)
        if not m:
            raise PhoneError("could not read the screen size (wm size)")
        w, h = int(m.group(1)), int(m.group(2))
        if self.rotation() in (1, 3):
            w, h = h, w
        return w, h

    def rotation(self):
        out = self.adb_once("shell", "dumpsys input")
        m = re.search(r"SurfaceOrientation:\s*(\d)", out) or re.search(r"orientation=(\d)", out)
        return int(m.group(1)) if m else 0

    def props(self):
        g = lambda k: self.adb_once("shell", f"getprop {k}").strip()
        return {"model": g("ro.product.model"), "brand": g("ro.product.brand"), "android": g("ro.build.version.release"),
                "sdk": g("ro.build.version.sdk")}

    def density(self):
        m = re.search(r"(\d+)", self.adb_once("shell", "wm density"))
        return int(m.group(1)) if m else None

    def touch_device(self):
        """(device path, max x, max y) of the touch screen, from getevent -pl."""
        out = self.shell("getevent -pl", timeout=20)
        best = None
        for block in re.split(r"\nadd device \d+: ", "\n" + out):
            lines = block.strip().splitlines()
            if not lines:
                continue
            path = lines[0].strip()
            mx = re.search(r"ABS_MT_POSITION_X\s*:.*?max (\d+)", block)
            my = re.search(r"ABS_MT_POSITION_Y\s*:.*?max (\d+)", block)
            if mx and my and path.startswith("/dev/input"):
                cand = (path, int(mx.group(1)), int(my.group(1)))
                if best is None or "touch" in block.lower():
                    best = cand
        if not best:
            raise PhoneError("no touch screen found in getevent -pl")
        return best

    def uptime(self):
        return float(self.shell("cat /proc/uptime").split()[0])

    # ------------------------------------------------------------ apps
    def foreground(self):
        """(package, activity) in front, e.g. ('com.foo', 'com.google.android.gms.ads.AdActivity'). (None, None) if unknown."""
        out = self.adb_once("shell", "dumpsys activity activities")
        for key in ("topResumedActivity", "mResumedActivity", "ResumedActivity"):
            m = re.search(key + r"[=:].*?\s([\w.]+)/([\w.$]+)", out)
            if m:
                pkg, act = m.group(1), m.group(2)
                return pkg, (pkg + act if act.startswith(".") else act)
        out = self.adb_once("shell", "dumpsys window")
        m = re.search(r"mCurrentFocus=.*?\s([\w.]+)/([\w.$]+)", out)
        if m:
            pkg, act = m.group(1), m.group(2)
            return pkg, (pkg + act if act.startswith(".") else act)
        return None, None

    def resume(self, pkg):
        """Brings the game back to the front without restarting it (seconds, keeps the level)."""
        self.shell(f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")

    def restart(self, pkg):
        """Cold restart: slow for some games, so callers use it only when nothing else worked."""
        self.shell(f"am force-stop {pkg}")
        time.sleep(0.5)
        self.resume(pkg)

    def ui_dump(self):
        """uiautomator XML of what Android sees (native buttons, ad views). '' if it fails. Takes 1-3 s."""
        out = self.adb_once("exec-out", "uiautomator dump /dev/tty", timeout=10)
        if "<hierarchy" not in out:
            out = self.adb_once("shell", "uiautomator dump /sdcard/gk_ui.xml >/dev/null && cat /sdcard/gk_ui.xml",
                                timeout=12)
        return out if "<hierarchy" in out else ""

    # ------------------------------------------------------------ screen
    def start_stream(self):
        try:
            from .stream import Phone
            self.stream = Phone(adb_path=self.adb_path).start()
            self.log("  Live screen stream on (scrcpy server, nothing installed on the phone).")
        except Exception as e:  # noqa: BLE001
            self.stream = None
            self.log(f"  Live stream not available ({e}); using adb screenshots.")

    def live(self):
        return self.stream is not None and self.stream.alive

    def screenshot(self):
        if self.live():
            img = self.stream.next_frame()
            if img is not None:
                if img.shape[1] != self.w or img.shape[0] != self.h:
                    img = cv2.resize(img, (self.w, self.h))
                self.last_img = img
                return img
        png = self.adb("exec-out", "screencap", "-p", binary=True)
        img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise PhoneError("could not decode a screenshot")
        self.last_img = img
        return img

    def wait_still(self, max_wait=3.0, still_for=0.3, thresh=0.6):
        """Screenshots until the screen stops changing for still_for seconds (animations done)."""
        prev = self.screenshot()
        t0 = t_still = time.time()
        while time.time() - t0 < max_wait:
            cur = self.screenshot()
            if cv2.absdiff(cv2.resize(prev, (135, 300)), cv2.resize(cur, (135, 300))).mean() > thresh:
                t_still = time.time()
            elif time.time() - t_still >= still_for:
                return cur
            prev = cur
        return prev

    # ------------------------------------------------------------ input
    def _check(self, x, y):
        if self.guard:
            why = self.guard(x, y)
            if why:
                self.log(f"  REFUSED tap at ({x},{y}): {why}")
                return False
        return True

    def tap(self, x, y):
        x, y = int(x), int(y)
        if not self._check(x, y):
            return False
        if self.live():
            try:
                self.stream.tap(x, y)
                return True
            except OSError:
                self.stream.alive = False
        self._fast(f"input tap {x} {y}")
        return True

    def swipe(self, x1, y1, x2, y2, ms=250):
        if not self._check(x1, y1):
            return False
        if self.live():
            try:
                steps = max(4, int(ms / 16))
                self.stream.drag(x1, y1, x2, y2, steps=steps, step_time=ms / 1000 / steps)
                return True
            except OSError:
                self.stream.alive = False
        self._fast(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(ms)}")
        return True

    def path(self, points, ms_per_step=16):
        """One finger through several points (word games, drawing lines). Needs the live stream."""
        if not points or not self._check(*points[0]):
            return False
        if not self.live():
            for a, b in zip(points, points[1:]):
                self.shell(f"input swipe {a[0]} {a[1]} {b[0]} {b[1]} 120")
            return True
        from .stream import DOWN, MOVE, UP
        s = self.stream
        s._touch(DOWN, 0x71, *points[0])
        for a, b in zip(points, points[1:]):
            for i in range(1, 5):
                t = i / 4
                s._touch(MOVE, 0x71, a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                time.sleep(ms_per_step / 1000)
        s._touch(UP, 0x71, *points[-1])
        return True

    def pinch(self, a0, b0, a1, b1):
        if self.live():
            self.stream.pinch(a0, b0, a1, b1)
            return True
        self.log("  Pinch needs the live stream; skipped.")
        return False

    def back(self):
        if self.live():
            try:
                self.stream.back()
                return
            except OSError:
                self.stream.alive = False
        self.shell("input keyevent 4")

    def close(self):
        if self.stream:
            self.stream.stop()
        if self._shell and self._shell.poll() is None:
            self._shell.kill()
