# Kit changelog

Every change to kit\ or template\ gets a line here and a version bump in kit\__init__.py, so `gamekit sync`
knows a game folder's copy is out of date.

## 1.0.0 (2026-11-23)
- First version: setup (detect phone/adb/Python, ask the rest), new, record (scrcpy stream + getevent +
  activity/uiautomator), distill (triplets, scenes, sheets, timeline, dropped list), frame, sync, pack, list, doctor.
- Ad close ladder (kit\ads.py) with shared, self-growing close-button library and store-jump blacklist.
- Bot skeleton: data-driven screens.json, never_tap guard on every tap, watchdogs, logs + report.csv, regress tests.
- Reused from the Arrow Sort Master bot: scrcpy stream (kit\stream.py), uiautomator/X-glyph rules (kit\adclose.py),
  5 proven ad close templates.
