"""How to play one level of this game. The agent writes this from model\\GAME_MODEL.md.

play(dev, img, ctx) is called on the level screen. It must:
  - wait for the level to settle before the first move (never act on intro animations)
  - read the board from the screenshot, check what it read makes sense (counts, sizes) before moving
  - move at the user's recorded speed, re-reading the screen whenever a move's result is uncertain
  - return {"result": "done" | "stuck", "moves": n, "mistakes": m}
Useful: dev.screenshot(), dev.wait_still(), dev.tap(x, y), dev.swipe(x1, y1, x2, y2, ms), dev.path(points),
dev.pinch(a0, b0, a1, b1), ctx["log"](text), ctx["screens"].find(img, "template.png")."""


def play(dev, img, ctx):
    ctx["log"]("  solver.py is still the empty template: nothing to do yet.")
    return {"result": "stuck", "moves": 0, "mistakes": 0}


def regress(img):
    """Optional: offline checks for regress_test.py, e.g. return the number of pieces read from a saved board."""
    return None
