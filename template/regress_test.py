"""Offline checks every new version must pass before a phone run, so proven behaviour is never lost.

regress\\NAME__EXPECTED.png  -> the screen classifier must say EXPECTED (e.g. win__win.png, ad1__unknown.png)
regress\\expected.json       -> optional {"board_12.png": 34} checked against solver.regress(img)
Add a frame for every problem that was fixed:  probe.py regress NAME EXPECTED  (or copy from logs\\unknown\\)."""
import glob
import json
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from kit.screens import Screens  # noqa: E402
import solver  # noqa: E402


def main():
    scr = Screens(HERE)
    bad = total = 0
    for p in sorted(glob.glob(os.path.join(HERE, "regress", "*__*.png"))):
        exp = os.path.basename(p).rsplit("__", 1)[1][:-4]
        got = scr.name(cv2.imread(p))
        total += 1
        ok = got == exp
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {os.path.basename(p)}: {got}")
    ej = os.path.join(HERE, "regress", "expected.json")
    if os.path.exists(ej):
        with open(ej, encoding="utf-8") as f:
            for name, exp in json.load(f).items():
                got = solver.regress(cv2.imread(os.path.join(HERE, "regress", name)))
                total += 1
                bad += got != exp
                print(f"{'ok  ' if got == exp else 'FAIL'} {name}: solver read {got}, expected {exp}")
    print(f"{total - bad}/{total} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
