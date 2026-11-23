# GAME MODEL: <Game Name>

Single source of truth about this game. Written from the recorded session, then corrected by probes and runs.
Later sessions read this instead of asking the user again.

## 1. Screens
| Screen | How it is recognised (template, region) | What the bot does |
|---|---|---|

## 2. Flow
home -> level -> win / lose -> popups -> next level (ads: where they appear, which ad screens, how they closed)

## 3. Actions
Gesture types, coordinates, speeds and durations the user used (the user's speed is the target).

## 4. Rules, win and lose
Rules, win/lose conditions, tricks seen in the user's move order.

## 5. Reading the board and solving
What to detect (colours, shapes, grid, text) and the solver idea.

## 6. Danger list (never tap)
Money buttons, reward-ad buttons, anything else. Each one also goes into screens.json "never_tap".

## 7. Timings (from probe.py times / watch)
cold start: ?  warm resume: ?  level intro: ?  win animation: ?  transition: ?

## 8. Unknowns
1.
