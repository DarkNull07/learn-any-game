"""Detailed live console output, the same lines in logs\\run_<time>.log, one row per level in logs\\report.csv,
and failure pictures in logs\\unknown\\ (only when something went wrong)."""
import csv
import datetime as dt
import os
import sys

import cv2

REPORT_FIELDS = ["time", "run", "level", "result", "seconds", "moves", "mistakes", "ads", "ad_seconds", "restarts",
                 "note"]


class Logger:
    def __init__(self, game_folder):
        self.folder = os.path.join(game_folder, "logs")
        os.makedirs(os.path.join(self.folder, "unknown"), exist_ok=True)
        self.stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = os.path.join(self.folder, f"run_{self.stamp}.log")
        self.f = open(self.path, "a", encoding="utf-8")
        self.recent = []   # last actions, saved with failure pictures

    def __call__(self, msg, indent=0):
        line = f"{dt.datetime.now():%H:%M:%S} {'  ' * indent}{msg}"
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode(sys.stdout.encoding or "ascii", "replace").decode(), flush=True)
        self.f.write(line + "\n")
        self.f.flush()
        self.recent = (self.recent + [line])[-20:]

    def report(self, **row):
        p = os.path.join(self.folder, "report.csv")
        new = not os.path.exists(p)
        row.setdefault("time", dt.datetime.now().isoformat(timespec="seconds"))
        row.setdefault("run", self.stamp)
        with open(p, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=REPORT_FIELDS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow(row)

    def picture(self, img, tag):
        """Only when config save_screens = "all": a picture of a normal moment (logs\\screens\\)."""
        folder = os.path.join(self.folder, "screens")
        os.makedirs(folder, exist_ok=True)
        p = os.path.join(folder, f"{dt.datetime.now():%Y%m%d_%H%M%S}_{tag}.png")
        if img is not None:
            cv2.imwrite(p, img)
        return p

    def failure(self, img, tag):
        """Saves the screen plus the last 20 log lines, for the next fix (no need to ask the user what happened)."""
        name = f"{dt.datetime.now():%Y%m%d_%H%M%S}_{tag}"
        p = os.path.join(self.folder, "unknown", name + ".png")
        if img is not None:
            cv2.imwrite(p, img)
        with open(os.path.join(self.folder, "unknown", name + ".txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(self.recent))
        return p
