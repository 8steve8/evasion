"""Runs Evasion matches between bots. The referee starts each bot itself.

How a bot is run (same idea as the Kelly and No Tipping architectures):
  * Every game starts a fresh process for each bot, from its folder in bots.json.
  * Startup is free: the bot gets a "start" message and replies {"ready": true}
    before any clock runs (up to STARTUP_LIMIT seconds).
  * Every time step the hunter gets a "tick" message and replies with its wall
    requests; on every second step the prey then gets the same message (the same
    state, not the hunter's answer) and replies with its velocity. The bots think
    one at a time, so neither slows the other down; each is timed from its own
    message to its own answer. 120 s per bot per game.
  * A hunter that runs out of time, crashes or breaks the protocol ends the game
    with its score set to the tick cap, exactly as if the prey had never been caught.
    A prey that does so is caught at that tick: the game ends there and that is the
    hunter's score. If both fail on the same tick the prey's failure is the one that
    counts.
  * A wall request that breaks a rule is simply not built (the reason is logged),
    exactly as the rules say. A badly formed velocity is ignored.

Messages are one JSON object per line. See README "The messages between the referee and a bot".
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .engine import Game, load_config, make_config, parse_prey_velocity, parse_wall

ROOT = Path(__file__).resolve().parent.parent      # the repository folder
PROTOCOL_VERSION = 1
STARTUP_LIMIT = 30.0        # seconds a bot may take to start (not on its clock)
OUTPUT_LIMIT = 65536        # bytes a bot may print per reply (stdout or stderr)
NOTE_LIMIT = 200            # referee notes kept per game (rejected walls and the like)


class BotError(Exception):
    """A bot broke the protocol: crash, bad JSON, too much output, or timeout."""


def _resolve(arg, cwd):
    """{python} -> this Python; ./prog -> full path to prog in the bot folder (prog.exe on Windows)."""
    if arg == '{python}':
        return sys.executable
    if arg.startswith(('./', '.\\')):
        p = Path(cwd) / arg[2:]
        if os.name == 'nt' and not p.suffix:
            p = p.with_suffix('.exe')
        return str(p)
    return arg


def load_bots(path='bots.json'):
    """Read the roster. Each entry: name, cwd, command, optional build.

    bots.local.json next to it, if present, is read too and its entries added: it is
    ignored by git, so the players' bots can be listed there without ever being pushed.
    """
    path = (ROOT / path).resolve()
    entries = json.loads(path.read_text(encoding='utf-8'))
    local = path.with_name(path.stem + '.local.json')
    if local.exists():
        extra = json.loads(local.read_text(encoding='utf-8'))
        if not isinstance(extra, list):
            raise ValueError(f'{local.name} must be a list of bots')
        entries = list(entries) + extra
    if not isinstance(entries, list) or not entries:
        raise ValueError('bots.json must be a list of bots')
    entries = list(entries) + discover_bots(path.parent, entries)
    names = set()
    for bot in entries:
        name = bot.get('name')
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError('Every bot needs a unique, non-empty "name"')
        names.add(name)
        cwd = (path.parent / bot.get('cwd', '.')).resolve()
        if not cwd.is_dir():
            raise ValueError(f'{name}: folder not found: {cwd}')
        for key in ('command', 'build'):
            cmd = bot.get(key)
            if key == 'build' and cmd is None:
                continue
            if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) for x in cmd):
                raise ValueError(f'{name}: "{key}" must be a non-empty list of strings')
            bot[key] = [_resolve(x, cwd) for x in cmd]
        bot['cwd'] = str(cwd)
        samples = (path.parent / 'bots' / 'samples').resolve()
        bot['sample'] = samples == cwd or samples in cwd.parents   # ships with the architecture, not a player's bot
    return entries


def discover_bots(root, listed):
    """Bots dropped into bots/ without a roster entry: any folder (other than templates/ and
    samples/) that holds a bot.py, bot.jl or bot.cpp is registered under the folder's name.

    A folder already named in bots.json / bots.local.json is left alone, so an entry there
    can override the guessed command. Folders under bots/ are ignored by git, so this is
    how the players' bots get in without ever touching a tracked file.
    """
    root = Path(root)
    bots_dir = root / 'bots'
    if not bots_dir.is_dir():
        return []
    taken_dirs = {str((root / b.get('cwd', '.')).resolve()) for b in listed if isinstance(b, dict)}
    taken_names = {b.get('name') for b in listed if isinstance(b, dict)}
    found = []
    for folder in sorted(p for p in bots_dir.iterdir() if p.is_dir()):
        if folder.name in ('templates', 'samples') or folder.name.startswith(('.', '_')):
            continue
        if str(folder.resolve()) in taken_dirs:
            continue
        rel = folder.relative_to(root).as_posix()
        if (folder / 'bot.py').is_file():
            entry = {'name': folder.name, 'cwd': rel, 'command': ['{python}', 'bot.py']}
        elif (folder / 'bot.jl').is_file():
            entry = {'name': folder.name, 'cwd': rel, 'command': ['julia', '-t', 'auto', '--startup-file=no', 'bot.jl']}
        elif (folder / 'bot.cpp').is_file():
            entry = {'name': folder.name, 'cwd': rel, 'command': ['./bot'],
                     'build': ['{python}', 'build.py'] if (folder / 'build.py').is_file()
                     else ['c++', '-std=c++17', '-O2', 'bot.cpp', '-o', 'bot']}
        else:
            continue                                   # nothing recognisable: list it in bots.local.json
        if entry['name'] in taken_names:
            entry['name'] = f"{entry['name']} ({rel})"
        found.append(entry)
        taken_names.add(entry['name'])
    return found


_built = {}   # bot folder -> fingerprint of its files right after the last successful build


def _fingerprint(folder):
    out = []
    for f in sorted(Path(folder).rglob('*')):
        if f.is_file() and '__pycache__' not in f.parts:
            st = f.stat()
            out.append((str(f), st.st_mtime_ns, st.st_size))
    return hash(tuple(out))


def build(bot):
    """Run a bot's build step (e.g. compiling C++) outside any clock.

    Skipped when nothing in the bot's folder changed since its last build.
    """
    if not bot.get('build'):
        return
    key = str(Path(bot['cwd']).resolve())
    if _built.get(key) == _fingerprint(key):
        return
    r = subprocess.run(bot['build'], cwd=bot['cwd'], capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"build failed: {(r.stderr or r.stdout).strip()[-300:]}")
    _built[key] = _fingerprint(key)



# ---------------------------------------------------------------- memory use of a bot process
def rss_bytes(pid):
    """Resident memory of a process in bytes (0 if it cannot be read). Linux: /proc; macOS: ps;
    Windows: psapi via ctypes. Threads are inside the process, so a multi-threaded bot is covered;
    children a bot spawns are not."""
    if pid is None:
        return 0
    try:
        if sys.platform.startswith('linux'):
            with open(f'/proc/{pid}/statm') as f:
                return int(f.read().split()[1]) * os.sysconf('SC_PAGE_SIZE')
        if sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes
            class PMC(ctypes.Structure):
                _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                            ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                            ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                            ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                            ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            k32 = ctypes.WinDLL('kernel32', use_last_error=True)
            psapi = ctypes.WinDLL('psapi', use_last_error=True)
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not h:
                return 0
            try:
                pmc = PMC(); pmc.cb = ctypes.sizeof(PMC)
                if psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                    return int(pmc.WorkingSetSize)
                return 0
            finally:
                k32.CloseHandle(h)
        out = subprocess.run(['ps', '-o', 'rss=', '-p', str(pid)], capture_output=True, text=True, timeout=2).stdout.strip()
        return int(out.split()[0]) * 1024 if out else 0
    except Exception:
        return 0


def thread_count(pid):
    """Number of threads in a process (0 if it cannot be read)."""
    if pid is None:
        return 0
    try:
        if sys.platform.startswith('linux'):
            with open(f'/proc/{pid}/status') as f:
                for line in f:
                    if line.startswith('Threads:'):
                        return int(line.split()[1])
            return 0
        if sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes
            TH32CS_SNAPTHREAD = 0x4
            class THREADENTRY32(ctypes.Structure):
                _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ThreadID', wintypes.DWORD),
                            ('th32OwnerProcessID', wintypes.DWORD), ('tpBasePri', wintypes.LONG),
                            ('tpDeltaPri', wintypes.LONG), ('dwFlags', wintypes.DWORD)]
            k32 = ctypes.windll.kernel32
            snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
            if snap == ctypes.c_void_p(-1).value:
                return 0
            try:
                te = THREADENTRY32(); te.dwSize = ctypes.sizeof(THREADENTRY32)
                n = 0
                ok = k32.Thread32First(snap, ctypes.byref(te))
                while ok:
                    if te.th32OwnerProcessID == int(pid):
                        n += 1
                    ok = k32.Thread32Next(snap, ctypes.byref(te))
                return n
            finally:
                k32.CloseHandle(snap)
        out = subprocess.run(['ps', '-M', '-p', str(pid)], capture_output=True, text=True, timeout=2).stdout
        lines = [l for l in out.splitlines() if l.strip()]
        return max(0, len(lines) - 1)                 # one header line, then one line per thread
    except Exception:
        return 0


def _parse_ps_time(txt):
    """'[[DD-]HH:]MM:SS[.ff]' -> seconds."""
    txt = txt.strip()
    if not txt:
        return 0.0
    days = 0
    if '-' in txt:
        d, txt = txt.split('-', 1)
        days = int(d)
    parts = [float(x) for x in txt.split(':')]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return days * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]


def cpu_seconds(pid):
    """CPU time (user + system, all threads) a process has consumed, in seconds; 0 if unreadable."""
    if pid is None:
        return 0.0
    try:
        if sys.platform.startswith('linux'):
            with open(f'/proc/{pid}/stat') as f:
                fields = f.read().rsplit(')', 1)[1].split()
            return (int(fields[11]) + int(fields[12])) / os.sysconf('SC_CLK_TCK')
        if sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            k32 = ctypes.WinDLL('kernel32', use_last_error=True)
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k32.GetProcessTimes.restype = wintypes.BOOL
            k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not h:
                return 0.0
            try:
                c, e, kt, ut = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
                if not k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut)):
                    return 0.0
                to_s = lambda ft: ((ft.dwHighDateTime << 32) + ft.dwLowDateTime) / 1e7
                return to_s(kt) + to_s(ut)
            finally:
                k32.CloseHandle(h)
        out = subprocess.run(['ps', '-o', 'utime=,stime=', '-p', str(pid)], capture_output=True, text=True, timeout=2).stdout
        cols = out.split()
        if len(cols) >= 2:
            return _parse_ps_time(cols[0]) + _parse_ps_time(cols[1])
        out = subprocess.run(['ps', '-o', 'cputime=', '-p', str(pid)], capture_output=True, text=True, timeout=2).stdout
        return _parse_ps_time(out)
    except Exception:
        return 0.0


def child_map():
    """{parent pid: [child pids]} for all processes (best effort; {} if unreadable)."""
    kids = {}
    try:
        if sys.platform.startswith('linux'):
            for name in os.listdir('/proc'):
                if not name.isdigit():
                    continue
                try:
                    with open(f'/proc/{name}/stat') as f:
                        ppid = int(f.read().rsplit(')', 1)[1].split()[1])
                except Exception:
                    continue
                kids.setdefault(ppid, []).append(int(name))
        elif sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes
            TH32CS_SNAPPROCESS = 0x2
            class PROCESSENTRY32(ctypes.Structure):
                _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                            ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)), ('th32ModuleID', wintypes.DWORD),
                            ('cntThreads', wintypes.DWORD), ('th32ParentProcessID', wintypes.DWORD),
                            ('pcPriClassBase', wintypes.LONG), ('dwFlags', wintypes.DWORD), ('szExeFile', ctypes.c_char * 260)]
            k32 = ctypes.WinDLL('kernel32', use_last_error=True)
            k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            k32.Process32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
            k32.Process32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
            if snap and snap != wintypes.HANDLE(-1).value:
                try:
                    pe = PROCESSENTRY32(); pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
                    ok = k32.Process32First(snap, ctypes.byref(pe))
                    while ok:
                        kids.setdefault(int(pe.th32ParentProcessID), []).append(int(pe.th32ProcessID))
                        ok = k32.Process32Next(snap, ctypes.byref(pe))
                finally:
                    k32.CloseHandle(snap)
        else:
            out = subprocess.run(['ps', '-axo', 'pid=,ppid='], capture_output=True, text=True, timeout=2).stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) == 2:
                    kids.setdefault(int(parts[1]), []).append(int(parts[0]))
    except Exception:
        pass
    return kids


def process_tree(pid):
    """The process and all its descendants (a launcher such as juliaup's julia.exe starts the real
    bot as a child, and a bot may fork workers)."""
    kids = child_map()
    seen, todo = [], [int(pid)]
    while todo:
        q = todo.pop()
        if q in seen:
            continue
        seen.append(q)
        todo.extend(kids.get(q, []))
    return seen


class BotSession:
    """One running bot process for one game."""

    def __init__(self, bot):
        self.bot = bot
        self.proc = None
        self.lines = queue.Queue()
        self.stderr_tail = []
        self.stderr_bytes = 0
        self.readers = []

    def start(self):
        try:
            self.proc = subprocess.Popen(self.bot['command'], cwd=self.bot['cwd'],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True, bufsize=1,
                                         encoding='utf-8', errors='replace')
        except OSError as exc:
            raise BotError(f'could not start: {exc}')
        self.readers = [threading.Thread(target=self._read_out, daemon=True),
                        threading.Thread(target=self._read_err, daemon=True)]
        for r in self.readers:
            r.start()

    def _read_out(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _read_err(self):
        for line in self.proc.stderr:      # keep a short tail for error messages
            self.stderr_bytes += len(line)
            self.stderr_tail = (self.stderr_tail + [line])[-20:]

    def ask(self, msg, timeout):
        """Send one message, wait for one JSON reply. Returns (reply, seconds)."""
        start = time.monotonic()
        before_err = self.stderr_bytes
        try:
            self.proc.stdin.write(json.dumps(msg, separators=(',', ':')) + '\n')
            self.proc.stdin.flush()
        except (OSError, ValueError):
            raise BotError('crashed' + self._err_note())
        try:
            line = self.lines.get(timeout=max(0.001, timeout))
        except queue.Empty:
            raise BotError('time')
        elapsed = time.monotonic() - start
        if line is None:
            raise BotError('crashed' + self._err_note())
        if len(line) > OUTPUT_LIMIT or self.stderr_bytes - before_err > OUTPUT_LIMIT:
            raise BotError('too much output')
        try:
            reply = json.loads(line)
        except ValueError:
            raise BotError(f'invalid JSON: {line.strip()[:60]!r}')
        if not isinstance(reply, dict):
            raise BotError('reply must be a JSON object')
        return reply, elapsed

    def _err_note(self):
        tail = ''.join(self.stderr_tail).strip()
        return f' ({tail[-160:]})' if tail else ''

    def close(self):
        if not self.proc:
            return
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        for r in self.readers:
            r.join(timeout=1)
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except OSError:
                pass


HUNTER, PREY = 0, 1
ROLES = ('hunter', 'prey')


def play_game(bots, config, clock, game=1, on_tick=None, delay=0, stop=None, live=None):
    """Play one game. bots = [bot playing hunter, bot playing prey].

    clock: seconds each bot has for this game. Returns a dict with the score
    (ticks), how it ended, the frames and events for the replay, errors and clocks.
    live (optional dict) is kept up to date for the dashboard: live['clocks'] and
    live['since'][role] = when that bot started thinking (None when it is not thinking).
    """
    g = Game(config)
    clocks = [float(clock), float(clock)]
    rss_peak = [0, 0]                # highest resident memory seen per role (bytes)
    rss_last = [0, 0]
    thr_peak = [0, 0]                # most threads seen per role
    thr_last = [0, 0]
    cpu_total = [0.0, 0.0]           # CPU seconds consumed per role (all threads)
    cpu_rate = [0.0, 0.0]            # CPU seconds per second of thinking over the last sample interval (= cores busy)
    cpu_prev = [0.0, 0.0]
    clk_prev = [float(clock), float(clock)]   # budget left at the previous sample
    cpu_base = [0.0, 0.0]            # CPU used before the first tick (startup, compilation): not counted
    rss_at = 0.0
    if live is not None:
        live['clocks'], live['since'] = clocks, [None, None]
        live['rss'], live['rss_peak'] = rss_last, rss_peak
        live['threads'], live['threads_peak'] = thr_last, thr_peak
        live['cpu'], live['cpu_rate'] = cpu_total, cpu_rate

    def sample_rss(force=False):
        nonlocal rss_at
        now = time.monotonic()
        if not force and now - rss_at < 0.5:
            return
        dt = now - rss_at if rss_at else 0.0
        rss_at = now
        for role in (0, 1):
            proc = sessions[role].proc
            if proc is None or proc.poll() is not None:
                continue
            tree = process_tree(proc.pid)                    # the bot may be a child of a launcher
            b = sum(rss_bytes(q) for q in tree)
            if b:
                rss_last[role] = b
                rss_peak[role] = max(rss_peak[role], b)
            n = sum(thread_count(q) for q in tree)
            if n:
                thr_last[role] = n
                thr_peak[role] = max(thr_peak[role], n)
            c = sum(cpu_seconds(q) for q in tree)
            if c:
                thought = clk_prev[role] - clocks[role]          # seconds of budget spent since the last sample
                if thought > 0.02:                               # otherwise it did not think: keep the last rate
                    cpu_rate[role] = max(0.0, (c - cpu_prev[role]) / thought)
                    cpu_prev[role] = c
                    clk_prev[role] = clocks[role]
                elif c - cpu_prev[role] > 0.5 and dt > 0:        # burning CPU while not being asked: show that per wall second
                    cpu_rate[role] = (c - cpu_prev[role]) / dt
                    cpu_prev[role] = c
                    clk_prev[role] = clocks[role]
                cpu_total[role] = max(0.0, c - cpu_base[role])
    errors = [None, None]           # once set, that bot is no longer asked anything
    def frame():
        """After each tick (index = tick): positions, velocities after the move, seconds of budget
        left, then the velocities each was heading with before the move (differ when it bounced)."""
        return [g.hx, g.hy, g.hvx, g.hvy, g.px, g.py, g.pvx, g.pvy, round(clocks[0], 2), round(clocks[1], 2),
                *g.heading_h, *g.heading_p]

    frames = [frame()]
    events = []                     # walls built/removed, rejected requests, errors
    notes_kept = 0
    sessions = [BotSession(b) for b in bots]
    pool = ThreadPoolExecutor(max_workers=2)
    ended = None

    def record(ev):
        nonlocal notes_kept
        if 'notes' in ev:
            if notes_kept >= NOTE_LIMIT:
                ev.pop('notes')
            else:
                notes_kept += len(ev['notes'])
        if len(ev) > 1:
            events.append(ev)

    try:
        def start(role):                  # untimed: launch + start message + ready reply
            s = sessions[role]
            s.start()
            reply, _ = s.ask({'type': 'start', 'protocol_version': PROTOCOL_VERSION,
                              'role': ROLES[role], 'N': g.N, 'M': g.M, 'tick_cap': g.tick_cap,
                              'width': 300, 'height': 300}, STARTUP_LIMIT)
            if reply.get('ready') is not True:
                raise BotError('did not reply {"ready": true} to the start message')

        for role, fut in enumerate([pool.submit(start, r) for r in (0, 1)]):
            try:
                fut.result()
            except BotError as exc:
                errors[role] = f'startup: {exc}'
                events.append({'tick': 0, 'error': {'role': ROLES[role], 'what': errors[role]}})
        for role in (0, 1):                                   # CPU spent starting up is not the bot's thinking
            proc = sessions[role].proc
            if proc is not None and proc.poll() is None:
                cpu_base[role] = cpu_prev[role] = sum(cpu_seconds(q) for q in process_tree(proc.pid))

        while True:
            if errors[HUNTER]:
                ended = 'hunter forfeit'
                break
            if errors[PREY]:
                ended = 'prey failed'
                break
            if g.tick >= g.tick_cap:
                ended = 'tick cap'
                break
            if stop is not None and stop.is_set():
                ended = 'stopped'
                break
            msg = {'type': 'tick'}
            msg.update(g.state())

            def ask(role):
                if errors[role]:
                    return None, None
                if clocks[role] <= 0:
                    return None, 'time'
                if live is not None:
                    live['since'][role] = time.monotonic()
                try:
                    reply, elapsed = sessions[role].ask(msg, clocks[role])
                except BotError as exc:
                    if str(exc) == 'time':
                        clocks[role] = 0.0
                    if live is not None:
                        live['since'][role] = None
                    return None, str(exc)
                clocks[role] = max(0.0, clocks[role] - elapsed)
                if live is not None:
                    live['since'][role] = None
                if clocks[role] == 0.0:
                    return None, 'time'
                return reply, None

            # One bot thinks at a time, so they never compete for the machine: the hunter is
            # asked and answers, then the prey. Neither sees the other's answer; both requests
            # are then carried out in the same step.
            asked = [HUNTER] + ([PREY] if g.prey_moves_now() else [])
            replies = {r: ask(r) for r in asked}
            for role, (reply, err) in replies.items():
                if err and not errors[role]:
                    errors[role] = err
                    events.append({'tick': g.tick, 'error': {'role': ROLES[role], 'what': err}})
            if errors[HUNTER] or errors[PREY]:     # failed while being asked: nobody moves this tick
                # If both fail on the same tick the prey's failure is what counts: it is caught
                # here and the score is this tick (the hunter's forfeit never comes into it).
                ended = 'prey failed' if errors[PREY] else 'hunter forfeit'
                frames.append(frame())             # one more frame, same positions, clocks as they ended
                break

            # Interpret the hunter's request
            remove, create, notes = [], None, []
            hreply = replies[HUNTER][0]
            if isinstance(hreply, dict):
                try:
                    rem = hreply.get('remove') or []
                    if not isinstance(rem, list):
                        raise ValueError('"remove" must be a list of walls')
                    remove = [parse_wall(w) for w in rem]
                    if hreply.get('create') is not None:
                        create = parse_wall(hreply['create'])
                except ValueError as exc:
                    notes.append(f'hunter request ignored: {exc}')
                    remove, create = [], None
            # Interpret the prey's request
            velocity = None
            if PREY in replies and isinstance(replies[PREY][0], dict):
                v = replies[PREY][0].get('velocity')
                if v is not None:
                    try:
                        velocity = parse_prey_velocity(v)
                    except ValueError as exc:
                        notes.append(f'prey request ignored: {exc}')

            ev = g.step(remove, create, velocity)
            if notes:
                ev['notes'] = notes + ev.get('notes', [])
            record(ev)
            frames.append(frame())
            sample_rss()
            if on_tick:
                on_tick(g, ev, clocks)
            if g.caught:
                ended = 'caught'
                break
            d = delay() if callable(delay) else delay      # seconds to linger per tick (for watching live)
            if d:
                if stop is not None:
                    stop.wait(d)
                else:
                    time.sleep(d)
        score = g.tick_cap if ended == 'hunter forfeit' else g.tick
        sample_rss(force=True)
        return {'ticks': score, 'played': g.tick, 'ended': ended, 'caught': g.caught, 'frames': frames,
                'events': events, 'errors': errors, 'clocks': clocks, 'rss_peak': rss_peak, 'threads_peak': thr_peak, 'cpu': [round(x, 2) for x in cpu_total],
                'stderr': [''.join(sessions[r].stderr_tail)[-600:] for r in (0, 1)],   # last lines each bot printed
                'walls': [w.as_list() for w in g.grid.wall_list()]}
    finally:
        for s in sessions:
            s.close()
        pool.shutdown(wait=False)


def play_match(bot1, bot2, config_path, clock=120.0, out_dir=None, on_progress=None,
               delay=0, stop=None, live=None):
    """Two games: bot1 hunts bot2, then bot2 hunts bot1, same parameters.

    Writes results.json and replay.html to out_dir (if given) and returns the
    match state: names, config, both games' replays, and a summary with the scores.
    The team whose hunter needed fewer ticks wins the pairing; equal is a tie. A hunter
    that fails (clock, crash, bad output) is simply scored as the tick cap.
    """
    if isinstance(config_path, dict):                 # typed-in parameters
        config = make_config(config_path.get('N'), config_path.get('M'), config_path.get('tick_cap'))
        config_path = f"N={config['N']} M={config['M']} cap={config['tick_cap']}"
    else:
        config = load_config(ROOT / config_path)
    names = [bot1['name'], bot2['name']]
    state = {'status': 'Starting', 'names': names, 'config': str(config_path), 'params': config,
             'time_budget': clock, 'games': []}
    for game in (1, 2):
        hunter_team = 0 if game == 1 else 1
        players = [(bot1, bot2)[hunter_team], (bot1, bot2)[1 - hunter_team]]
        gstate = {'game': game, 'hunter': players[0]['name'], 'prey': players[1]['name'],
                  'hunter_team': hunter_team, 'tick': 0, 'status': 'starting the bots'}
        state['games'].append(gstate)
        state['status'] = f'Game {game} · starting the bots'
        if on_progress:
            on_progress(state)
        if live is not None:
            live.clear()
            live['game'] = game
            live['hunter_team'] = hunter_team

        def on_tick(g, ev, clocks):
            gstate['tick'] = g.tick
            gstate['status'] = 'playing'
            state['status'] = f'Game {game} · tick {g.tick}'
            if len(ev) > 1:                                  # something happened: keep a short tail for the live view
                gstate['recent'] = (gstate.get('recent') or [])[-19:] + [ev]
            if on_progress:
                on_progress(state, g)

        result = play_game(players, config, clock, game, on_tick=on_tick, delay=delay, stop=stop, live=live)
        gstate.pop('recent', None)
        gstate.update(result, status='finished')
        state['status'] = f'Game {game} finished: {result["ticks"]} ticks ({result["ended"]})'
        if on_progress:
            on_progress(state)
        if stop is not None and stop.is_set():
            break
    scores = {}
    for gs in state['games']:
        if gs.get('status') == 'finished':
            scores[gs['hunter']] = gs['ticks']       # a hunter that failed is scored as the tick cap
    winner = None
    if len(scores) == 2 and scores[names[0]] != scores[names[1]]:
        winner = names[0] if scores[names[0]] < scores[names[1]] else names[1]
    state['status'] = 'Finished'
    state['summary'] = {'type': 'match_over', 'scores': scores, 'winner': winner,
                        'games': [{'game': gs['game'], 'hunter': gs['hunter'], 'prey': gs['prey'],
                                   'ticks': gs.get('ticks'), 'ended': gs.get('ended'),
                                   'errors': gs.get('errors') if any(gs.get('errors') or []) else None}
                                  for gs in state['games']]}
    if out_dir:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state, separators=(',', ':'))
        (out / 'results.json').write_text(payload, encoding='utf-8')
        html = (ROOT / 'dashboard' / 'replay.html').read_text(encoding='utf-8')
        (out / 'replay.html').write_text(html.replace('/* REPLAY_DATA */ null', payload.replace('<', '\\u003c')),
                                         encoding='utf-8')
    return state
