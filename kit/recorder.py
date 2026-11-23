"""Records one play session: the screen (video + exact frame times), every touch (getevent), which app/activity is in
front (ads show up here), and Android's view of the screen (uiautomator) whenever that changes.

Output: <game folder>\\sessions\\<time>\\  meta.json, touches.log, frames.csv, video_000.mp4..., activity.log, ui\\*.xml
Stop with Enter (or Ctrl+C)."""
import datetime as dt
import json
import os
import subprocess
import sys
import threading
import time

import cv2

from . import __version__, config
from .device import Device

STREAM_LATENCY = 0.05   # s: a frame reaches the PC about this long after it was on the phone screen
FPS = 30


class Recorder:
    def __init__(self, game_folder, minutes=None, log=print):
        self.cfg = config.load()
        self.game_folder = game_folder
        self.minutes = minutes
        self.log = log
        self.stop = threading.Event()
        self.n_frames = self.n_touch = 0
        self.fg = (None, None)

    def run(self):
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.dir = os.path.join(self.game_folder, "sessions", stamp)
        os.makedirs(os.path.join(self.dir, "ui"), exist_ok=True)
        self.log(f"Recording session {stamp}")
        self.log(f"  Folder: {self.dir}")
        dev = self.dev = Device(self.cfg, log=self.log, use_stream=True)
        path, mx, my = dev.touch_device()
        rot = dev.rotation()
        nat = (dev.w, dev.h) if rot in (0, 2) else (dev.h, dev.w)
        pkg, act = dev.foreground()
        meta = {"kit": __version__, "started": time.time(), "stamp": stamp, "screen_natural": nat, "rotation": rot,
                "touch_device": path, "touch_max": [mx, my], "phone": dev.props(), "first_app": [pkg, act],
                "video": "stream" if dev.live() else "screenrecord", "stream_latency": STREAM_LATENCY}
        self._save_meta(meta)
        self.log(f"  Phone {meta['phone'].get('model')} {dev.w}x{dev.h}, touch screen {path} (raw max {mx}x{my}).")
        self.log(f"  App in front: {pkg}")
        old_touches = dev.adb_once("shell", "settings get system show_touches").strip()
        if old_touches not in ("0", "1"):      # "null" (never set) or no answer: Android's default is off
            old_touches = "0"
        self._getevent = None
        threads = [threading.Thread(target=self._touches, args=(path,), daemon=True),
                   threading.Thread(target=self._activity, daemon=True)]
        threads.append(threading.Thread(target=self._video_stream if dev.live() else self._video_screenrecord,
                                        daemon=True))
        try:
            dev.adb_once("shell", "settings put system show_touches 1")
            for t in threads:
                t.start()
            threading.Thread(target=self._wait_enter, daemon=True).start()
            self.log("  Play the game now. Press Enter here when you are done.")
            t0 = time.time()
            while not self.stop.is_set():
                self.stop.wait(10)
                el = time.time() - t0
                self.log(f"  {el / 60:4.1f} min | frames {self.n_frames} | touches {self.n_touch} | "
                         f"in front: {self.fg[1] or self.fg[0]}")
                if self.minutes and el > self.minutes * 60:
                    self.stop.set()
        except KeyboardInterrupt:
            pass
        finally:   # whatever happens: stop everything and put show_touches back
            self.stop.set()
            self.log("  Stopping...")
            if self._getevent and self._getevent.poll() is None:
                self._getevent.kill()          # unblocks the touch reader at once
            for t in threads:
                if t.is_alive():
                    t.join(timeout=30 if t is threads[0] else 200)
            dev.adb_once("shell", f"settings put system show_touches {old_touches}")
            meta["ended"] = time.time()
            meta["frames"] = self.n_frames
            meta["touch_downs"] = self.n_touch
            self._save_meta(meta)
            dev.close()
        self.log(f"Saved {self.n_frames} frames and {self.n_touch} touches in {self.dir}")
        self.log(f'Next: gamekit distill "{os.path.basename(self.game_folder)}"')
        return self.dir

    def _save_meta(self, meta):
        with open(os.path.join(self.dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

    def _wait_enter(self):
        try:
            sys.stdin.readline()
        except Exception:  # noqa: BLE001
            return
        self.stop.set()

    # ------------------------------------------------------------ parts
    def _touches(self, path):
        p = self._getevent = subprocess.Popen(self.dev._cmd("shell", f"getevent -lt {path}"), stdout=subprocess.PIPE,
                                              stderr=subprocess.DEVNULL, bufsize=0)
        with open(os.path.join(self.dir, "touches.log"), "w", encoding="utf-8") as f:
            buf = b""
            while not self.stop.is_set():
                chunk = p.stdout.read(4096)   # unbuffered pipe: returns as soon as any bytes arrive
                if not chunk:
                    break
                now = time.time()
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for ln in lines:
                    s = ln.decode(errors="ignore").rstrip("\r")
                    if "ABS_MT_TRACKING_ID" in s and not s.rstrip().endswith("ffffffff"):
                        self.n_touch += 1
                    f.write(f"{now:.4f}\t{s}\n")
                f.flush()
        p.kill()

    def _activity(self):
        last_ui = 0.0
        with open(os.path.join(self.dir, "activity.log"), "w", encoding="utf-8") as f:
            f.write("pc_time\tpackage\tactivity\trotation\n")
            last, rot, t_rot = None, None, 0.0
            while not self.stop.is_set():
                fg = self.dev.foreground()
                if time.time() - t_rot > 2:
                    rot, t_rot = self.dev.rotation(), time.time()
                if fg[0] is None:          # Android didn't answer this time: not a change
                    self.stop.wait(1.0)
                    continue
                if (fg, rot) != last:
                    now = time.time()
                    f.write(f"{now:.3f}\t{fg[0]}\t{fg[1]}\t{rot}\n")
                    f.flush()
                    self.fg = fg
                    if last is not None and fg != last[0] and now - last_ui > 2:
                        self.log(f"  Screen changed: {fg[1] or fg[0]}")
                        xml = self.dev.ui_dump()
                        if xml:
                            with open(os.path.join(self.dir, "ui", f"{now:.1f}.xml"), "w", encoding="utf-8") as u:
                                u.write(xml)
                        last_ui = now
                    last = (fg, rot)
                self.stop.wait(1.0)

    def _video_stream(self):
        s = self.dev.stream
        seg, writer, size, idx = -1, None, None, 0
        last_t = 0.0
        with open(os.path.join(self.dir, "frames.csv"), "w", encoding="utf-8") as f:
            f.write("segment,index,pc_time\n")
            while not self.stop.is_set() and s.alive:
                img = s.next_frame(timeout=0.2)
                t = s.frame_time - STREAM_LATENCY
                if img is None or t - last_t < 1.0 / FPS - 0.002:
                    continue
                last_t = t
                h, w = img.shape[:2]
                small = cv2.resize(img, (w // 2 // 2 * 2, h // 2 // 2 * 2), interpolation=cv2.INTER_AREA)
                if small.shape[:2] != size:   # first frame, or the phone rotated: new video file
                    if writer:
                        writer.release()
                    seg, idx, size = seg + 1, 0, small.shape[:2]
                    writer = cv2.VideoWriter(os.path.join(self.dir, f"video_{seg:03d}.mp4"),
                                             cv2.VideoWriter_fourcc(*"mp4v"), FPS, (size[1], size[0]))
                writer.write(small)
                f.write(f"{seg},{idx},{t:.4f}\n")
                idx += 1
                self.n_frames += 1
        if writer:
            writer.release()
        if not self.stop.is_set():
            self.log("  The live stream stopped; switching to screenrecord.")
            self._video_screenrecord()

    def _video_screenrecord(self):
        """Fallback: Android's own recorder in 170 s parts (about 0.5 s gaps between parts, timing less exact)."""
        w, h = self.dev.w // 2 // 2 * 2, self.dev.h // 2 // 2 * 2
        part = 0
        while not self.stop.is_set():
            remote = f"/sdcard/gk_rec_{part:03d}.mp4"
            t_start = time.time()
            p = subprocess.Popen(self.dev._cmd("shell", f"screenrecord --size {w}x{h} --bit-rate 4000000 "
                                                        f"--time-limit 170 {remote}"),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            while p.poll() is None and not self.stop.is_set():
                time.sleep(0.2)
            if p.poll() is None:
                self.dev.adb_once("shell", "pkill -INT screenrecord")
                p.wait(timeout=10)
            time.sleep(0.5)
            local = os.path.join(self.dir, f"screenrecord_{part:03d}.mp4")
            self.dev.adb_once("pull", remote, local, timeout=120)
            self.dev.adb_once("shell", f"rm {remote}")
            with open(os.path.join(self.dir, "screenrecord_parts.csv"), "a", encoding="utf-8") as f:
                f.write(f"{part},{t_start + 0.4:.3f}\n")   # recording starts ~0.4 s after the command
            cap = cv2.VideoCapture(local)
            self.n_frames += int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            cap.release()
            part += 1
