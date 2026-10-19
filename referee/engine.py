"""Evasion rules, independent of networking and display.

The arena is a 300 x 300 grid of integer cells, (0, 0) to (299, 299). Everything a
player can bump into is a set of blocked cells: the four sides of the arena (the
cells just outside it) and every cell a wall occupies. Walls are horizontal,
(x1, y) .. (x2, y), or vertical, (x, y1) .. (x, y2), one cell thick (no diagonal walls
this year). Bouncing follows the pixel algorithm in the rules ("Super-technical: An
algorithm for bouncing"), applied to this set of blocked cells.
"""
import math
import re
from pathlib import Path

WIDTH = 300
HEIGHT = 300
HUNTER_START = (0, 0)
HUNTER_START_VELOCITY = (1, 1)
PREY_START = (230, 200)
CATCH_DISTANCE = 4          # Euclidean; the hunter catches the prey at distance <= 4 with no wall between
DEFAULT_TICK_CAP = 24000    # a prey still free after this many ticks scores this many ticks


def load_config(path):
    """A parameter file: N (ticks between walls), M (most walls at once), then optionally
    the tick cap. Numbers may be on one line or several; lines starting with # are comments."""
    lines = [s.split('#')[0].strip() for s in Path(path).read_text(encoding='utf-8-sig').splitlines()]
    nums = [s for s in re.split(r'[,\s]+', ' '.join(lines)) if s]
    if len(nums) not in (2, 3):
        raise ValueError('Expected N and M (and optionally the tick cap).')
    return make_config(nums[0], nums[1], nums[2] if len(nums) == 3 else DEFAULT_TICK_CAP)


def make_config(n, m, tick_cap=DEFAULT_TICK_CAP):
    """Check N, M and the tick cap (strings or numbers) and return the config dict."""
    try:
        n, m, cap = int(n), int(m), int(tick_cap if tick_cap not in (None, '') else DEFAULT_TICK_CAP)
    except (TypeError, ValueError):
        raise ValueError('N, M and the tick cap must be whole numbers.')
    if n < 1 or m < 0 or cap < 1:
        raise ValueError('N must be at least 1, M at least 0 and the tick cap at least 1.')
    return {'N': n, 'M': m, 'tick_cap': cap}


def in_arena(x, y):
    return 0 <= x < WIDTH and 0 <= y < HEIGHT


class Wall:
    """A wall, identified by its two end points. Equal walls have equal end points."""

    KINDS = {'h': 'horizontal', 'v': 'vertical'}

    def __init__(self, x1, y1, x2, y2):
        pts = (x1, y1, x2, y2)
        if any(type(p) is not int for p in pts):
            raise ValueError('wall coordinates must be whole numbers')
        if (x2, y2) < (x1, y1):                      # normalise: start at the smaller x (then y)
            x1, y1, x2, y2 = x2, y2, x1, y1
        dx, dy = x2 - x1, y2 - y1
        if dy == 0:
            kind = 'h'
        elif dx == 0:
            kind = 'v'
        else:
            raise ValueError('a wall must be horizontal or vertical')
        self.x1, self.y1, self.x2, self.y2, self.kind = x1, y1, x2, y2, kind
        if kind == 'h':
            cells = {(x, y1) for x in range(x1, x2 + 1)}
        else:
            cells = {(x1, y) for y in range(y1, y2 + 1)}
        if not all(in_arena(x, y) for x, y in cells):
            raise ValueError('wall must lie inside the arena (0..299)')
        self.cells = frozenset(cells)

    @property
    def key(self):
        return (self.x1, self.y1, self.x2, self.y2)

    def __eq__(self, other):
        return isinstance(other, Wall) and self.key == other.key

    def __hash__(self):
        return hash(self.key)

    def __repr__(self):
        return f'Wall{self.key}'

    def as_list(self):
        return list(self.key)


def parse_wall(spec):
    """[x1, y1, x2, y2] -> Wall (raises ValueError if it is not a legal shape)."""
    if not isinstance(spec, (list, tuple)) or len(spec) != 4:
        raise ValueError('a wall is [x1, y1, x2, y2]')
    return Wall(*spec)


class Grid:
    """The blocked cells: the arena boundary plus the cells of every wall."""

    def __init__(self):
        self.walls = {}            # key -> Wall
        self.occupied = set()      # every cell of every wall

    def blocked(self, x, y):
        return not in_arena(x, y) or (x, y) in self.occupied

    def add(self, wall):
        self.walls[wall.key] = wall
        self.occupied |= wall.cells

    def remove(self, wall):
        w = self.walls.pop(wall.key, None)
        if w is not None:
            self.occupied -= w.cells
        return w is not None

    def wall_list(self):
        return list(self.walls.values())


def bounce(x, y, vx, vy, blocked):
    """One step of a player at (x, y) moving (vx, vy), given blocked(x, y).

    This is the rules' pixel algorithm. Returns (x, y, vx, vy) after the step.
    """
    if vx == 0 and vy == 0:
        return x, y, vx, vy
    tx, ty = x + vx, y + vy
    if not blocked(tx, ty):
        return tx, ty, vx, vy
    if vx == 0 or vy == 0:                       # straight into something: bounce back in place
        return x, y, -vx, -vy
    # Diagonal mover. First look at the two cells adjacent to both the mover and the hit cell.
    beside = blocked(tx, y)                      # the cell in the mover's row, towards the hit
    above = blocked(x, ty)                       # the cell in the mover's column, towards the hit
    if beside and above:
        return x, y, -vx, -vy                    # wedged in a corner: straight back
    if beside:
        return x, ty, -vx, vy                    # slides along a vertical surface
    if above:
        return tx, y, vx, -vy                    # slides along a horizontal surface
    # Neither: widen the search to the cells beyond the hit cell.
    beyond_x = blocked(tx + vx, ty)              # the hit cell continues horizontally
    beyond_y = blocked(tx, ty + vy)              # the hit cell continues vertically
    if beyond_x and beyond_y:
        return x, y, -vx, -vy
    if beyond_x:
        return tx, y, vx, -vy                    # hit a horizontal wall
    if beyond_y:
        return x, ty, -vx, vy                    # hit a vertical wall
    return x, y, -vx, -vy                        # a lone cell (or the tip of a wall) acts as a corner


def cells_between(x1, y1, x2, y2):
    """The cells strictly between two points on the straight line joining them (Bresenham)."""
    dx, dy = abs(x2 - x1), abs(y2 - y1)
    sx, sy = (1 if x2 > x1 else -1), (1 if y2 > y1 else -1)
    err, x, y, out = dx - dy, x1, y1, []
    while True:
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            x += sx
        if e2 < dx:
            err += dx
            y += sy
        if (x, y) == (x2, y2):
            return out
        out.append((x, y))


class Game:
    """One game. Call step() once per time step with the players' requests."""

    def __init__(self, config):
        self.N = int(config['N'])
        self.M = int(config['M'])
        self.tick_cap = int(config.get('tick_cap', DEFAULT_TICK_CAP))
        self.tick = 0                                     # time steps completed so far
        self.hx, self.hy = HUNTER_START
        self.hvx, self.hvy = HUNTER_START_VELOCITY
        self.px, self.py = PREY_START
        self.pvx, self.pvy = 0, 0
        self.last_wall = None                             # tick at which the last wall was built
        self.grid = Grid()
        self.caught = False
        # the velocities each player was heading with just before its last move (before any bounce)
        self.heading_h = HUNTER_START_VELOCITY
        self.heading_p = (0, 0)
        self.walls_built = 0                              # walls built so far this game

    # ----- what the players and the display see -----

    def prey_moves_now(self):
        """The prey moves on every second step: the ones with an odd tick number (1, 3, 5, ...)."""
        return self.tick % 2 == 1

    def state(self):
        return {'tick': self.tick,
                'hx': self.hx, 'hy': self.hy, 'hvx': self.hvx, 'hvy': self.hvy,
                'last_wall': -1 if self.last_wall is None else self.last_wall,
                'px': self.px, 'py': self.py, 'pvx': self.pvx, 'pvy': self.pvy,
                'walls': [c for w in self.grid.wall_list() for c in w.key]}

    def can_build(self):
        return self.last_wall is None or self.tick - self.last_wall >= self.N

    # ----- the rules -----

    def wall_problem(self, wall):
        """Why this wall may not be built right now, or None if it may."""
        if not self.can_build():
            return f'too soon: the last wall was built at tick {self.last_wall}, next allowed at {self.last_wall + self.N}'
        if len(self.grid.walls) >= self.M:
            return f'already {self.M} walls (M); remove one first'
        if (self.hx, self.hy) not in wall.cells:
            return 'the wall must touch the hunter'
        if (self.px, self.py) in wall.cells:
            return 'the wall would hit the prey'
        if wall.cells & self.grid.occupied:
            return 'the wall goes through another wall'
        # Where would the hunter go with this wall in place? If it would stay put, it is squished.
        self.grid.add(wall)
        nx, ny, _, _ = bounce(self.hx, self.hy, self.hvx, self.hvy, self.grid.blocked)
        self.grid.remove(wall)
        if (nx, ny) in wall.cells:
            return 'the wall would squish the hunter'
        return None

    def step(self, remove=(), create=None, prey_velocity=None):
        """Advance one time step.

        remove: walls (Wall objects) the hunter wants gone; create: a Wall or None;
        prey_velocity: (vx, vy) or None (ignored on the hunter-only steps).
        Returns a dict describing what happened, for the log and the replay.
        """
        ev = {'tick': self.tick}
        notes = []
        removed = []
        for w in remove:
            if self.grid.remove(w):
                removed.append(w.as_list())
            else:
                notes.append(f'no such wall to remove: {w.key}')
        if removed:
            ev['removed'] = removed
        if create is not None:
            problem = self.wall_problem(create)
            if problem is None:
                self.grid.add(create)
                self.last_wall = self.tick
                self.walls_built += 1
                ev['created'] = create.as_list()
            else:
                notes.append(f'wall {create.key} not built: {problem}')
        blocked = self.grid.blocked
        # Both move at the same time, each unaware of the other (they never collide).
        self.heading_h = (self.hvx, self.hvy)
        self.hx, self.hy, self.hvx, self.hvy = bounce(self.hx, self.hy, self.hvx, self.hvy, blocked)
        if self.prey_moves_now():
            if prey_velocity is not None:
                vx, vy = prey_velocity
                self.pvx, self.pvy = vx, vy
            self.heading_p = (self.pvx, self.pvy)          # what the prey asked for (or kept)
            self.px, self.py, self.pvx, self.pvy = bounce(self.px, self.py, self.pvx, self.pvy, blocked)
        else:
            self.heading_p = (self.pvx, self.pvy)
        self.tick += 1
        if notes:
            ev['notes'] = notes
        if self.is_caught():
            self.caught = True
            ev['caught'] = True
        return ev

    def is_caught(self):
        dx, dy = self.hx - self.px, self.hy - self.py
        if dx * dx + dy * dy > CATCH_DISTANCE * CATCH_DISTANCE:
            return False
        return not any(self.grid.blocked(x, y) for x, y in cells_between(self.hx, self.hy, self.px, self.py))

    def distance(self):
        return math.hypot(self.hx - self.px, self.hy - self.py)


def parse_prey_velocity(v):
    """(vx, vy) from a bot's reply, or raise ValueError."""
    if not isinstance(v, (list, tuple)) or len(v) != 2 or any(type(c) is not int or c not in (-1, 0, 1) for c in v):
        raise ValueError('velocity must be [vx, vy] with each of vx, vy in -1, 0, 1')
    return int(v[0]), int(v[1])
