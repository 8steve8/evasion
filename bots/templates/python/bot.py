"""Evasion bot (Python).

Edit ONLY the section marked below. Everything outside it talks to the referee.
Print debug output to stderr, never stdout: stdout is reserved for your moves.
"""
import json
import math
import sys
import time
from types import SimpleNamespace

WIDTH, HEIGHT = 300, 300


# ---------- helpers you may use (a copy of the referee's rules) ----------

def wall_cells(x1, y1, x2, y2):
    """The cells a wall [x1, y1, x2, y2] occupies (horizontal or vertical, one cell thick)."""
    if (x2, y2) < (x1, y1):
        x1, y1, x2, y2 = x2, y2, x1, y1
    if y1 == y2:
        return {(x, y1) for x in range(x1, x2 + 1)}
    return {(x1, y) for y in range(y1, y2 + 1)}


def occupied(walls):
    """Every cell of every wall in the list (walls as [x1, y1, x2, y2])."""
    cells = set()
    for w in walls:
        cells |= wall_cells(*w)
    return cells


def blocked(cells, x, y):
    return not (0 <= x < WIDTH and 0 <= y < HEIGHT) or (x, y) in cells


def bounce(x, y, vx, vy, cells):
    """One step of a player at (x, y) moving (vx, vy): returns (x, y, vx, vy). Same as the referee."""
    if vx == 0 and vy == 0:
        return x, y, vx, vy
    tx, ty = x + vx, y + vy
    if not blocked(cells, tx, ty):
        return tx, ty, vx, vy
    if vx == 0 or vy == 0:
        return x, y, -vx, -vy
    beside, above = blocked(cells, tx, y), blocked(cells, x, ty)
    if beside and above:
        return x, y, -vx, -vy
    if beside:
        return x, ty, -vx, vy
    if above:
        return tx, y, vx, -vy
    bx, by = blocked(cells, tx + vx, ty), blocked(cells, tx, ty + vy)
    if bx and by:
        return x, y, -vx, -vy
    if bx:
        return tx, y, vx, -vy
    if by:
        return x, ty, -vx, vy
    return x, y, -vx, -vy


def can_build(s):
    return s.last_wall is None or s.tick - s.last_wall >= s.N


def longest_wall(s, kind):
    """The longest wall of this kind through the hunter that does not touch another wall
    or the prey: kind is 'h' (horizontal) or 'v' (vertical). Returns [x1, y1, x2, y2]."""
    cells = occupied(s.walls)
    dx, dy = {'h': (1, 0), 'v': (0, 1)}[kind]
    prey = (s.prey.x, s.prey.y)

    def free(x, y):
        return not blocked(cells, x, y) and (x, y) != prey

    x1, y1 = s.hunter.x, s.hunter.y
    while free(x1 - dx, y1 - dy):
        x1, y1 = x1 - dx, y1 - dy
    x2, y2 = s.hunter.x, s.hunter.y
    while free(x2 + dx, y2 + dy):
        x2, y2 = x2 + dx, y2 + dy
    return [x1, y1, x2, y2]


# Do not edit above this line.
# ================== EDIT ONLY THIS SECTION ==================
# Write your strategy in hunter_move and prey_move. You may add imports and helper functions here.
#
# s (the game state, updated every tick):
#   s.role        'hunter' or 'prey'          s.N, s.M, s.tick_cap   the parameters
#   s.tick        time steps so far (the prey is asked on odd ticks: 1, 3, 5, ...)
#   s.hunter      .x .y .vx .vy               s.prey   .x .y .vx .vy
#   s.last_wall   tick the last wall was built, or None
#   s.walls       list of [x1, y1, x2, y2]    s.time_used  seconds you have thought so far
#
# hunter_move returns a dict: {"remove": [walls...], "create": [x1, y1, x2, y2]}; both keys optional,
# {} for nothing. A wall that breaks a rule is not built and you are not penalised.
# prey_move returns (vx, vy) with each in -1, 0, 1, or None to keep going as you are.
# You have 120 seconds of thinking time per game. The helpers above (bounce, longest_wall, ...)
# are copies of the referee's rules; use them to look ahead.

def hunter_move(s):
    # TODO: your hunter strategy here
    return {}


def prey_move(s):
    # TODO: your prey strategy here
    return None

# ============================================================
# Do not edit below this line.


def main():
    s = None
    for line in sys.stdin:
        msg = json.loads(line)
        if msg['type'] == 'start':
            s = SimpleNamespace(role=msg['role'], N=msg['N'], M=msg['M'], tick_cap=msg['tick_cap'],
                                tick=0, hunter=None, prey=None, last_wall=None, walls=[], time_used=0.0)
            print(json.dumps({'ready': True}, separators=(',', ':')), flush=True)
        elif msg['type'] == 'tick':
            s.tick = msg['tick']
            s.hunter = SimpleNamespace(x=msg['hx'], y=msg['hy'], vx=msg['hvx'], vy=msg['hvy'])
            s.prey = SimpleNamespace(x=msg['px'], y=msg['py'], vx=msg['pvx'], vy=msg['pvy'])
            s.last_wall = None if msg['last_wall'] < 0 else msg['last_wall']
            flat = msg['walls']
            s.walls = [flat[i:i + 4] for i in range(0, len(flat), 4)]
            started = time.monotonic()
            if s.role == 'hunter':
                reply = hunter_move(s) or {}
            else:
                v = prey_move(s)
                reply = {} if v is None else {'velocity': [int(v[0]), int(v[1])]}
            s.time_used += time.monotonic() - started
            print(json.dumps(reply, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    main()
