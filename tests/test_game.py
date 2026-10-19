"""Tests for the rules, the bot runner and the bot templates.

Run from the repository root:  python3 -m unittest discover -s tests -v
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from referee.engine import Game, Grid, Wall, bounce, cells_between, load_config, parse_prey_velocity  # noqa: E402
from referee.runner import build, load_bots, play_game, play_match  # noqa: E402

TEMPLATES = ROOT / 'bots' / 'templates'
SAMPLES = ROOT / 'bots' / 'samples'


def grid(*walls):
    g = Grid()
    for w in walls:
        g.add(Wall(*w))
    return g


class Bouncing(unittest.TestCase):
    """The worked examples from the rules page."""

    def test_reflects_off_a_horizontal_wall(self):
        g = grid((10, 200, 300 - 1, 200))
        self.assertEqual(bounce(100, 199, 1, 1, g.blocked), (101, 199, 1, -1))

    def test_passes_the_narrow_end(self):
        g = grid((10, 200, 299, 200))
        self.assertEqual(bounce(9, 200, 1, 1, g.blocked), (10, 201, 1, 1))

    def test_corner_counts_as_the_wall(self):
        g = grid((10, 200, 299, 200))
        self.assertEqual(bounce(9, 199, 1, 1, g.blocked), (10, 199, 1, -1))

    def test_two_parallel_walls_act_as_a_vertical_wall(self):
        g = grid((0, 40, 40, 40), (0, 41, 40, 41))
        self.assertEqual(bounce(41, 41, -1, -1, g.blocked), (41, 40, 1, -1))

    def test_arena_sides_and_corners(self):
        b = Grid().blocked
        self.assertEqual(bounce(0, 5, -1, 1, b), (0, 6, 1, 1))
        self.assertEqual(bounce(5, 299, 1, 1, b), (6, 299, 1, -1))
        self.assertEqual(bounce(0, 0, -1, -1, b), (0, 0, 1, 1))
        self.assertEqual(bounce(299, 299, 1, 1, b), (299, 299, -1, -1))

    def test_straight_mover_bounces_back_in_place(self):
        g = grid((0, 50, 100, 50))
        self.assertEqual(bounce(20, 49, 0, 1, g.blocked), (20, 49, 0, -1))
        g = grid((50, 0, 50, 100))
        self.assertEqual(bounce(49, 20, 1, 0, g.blocked), (49, 20, -1, 0))

    def test_lone_cell_is_a_corner(self):
        g = grid((50, 50, 50, 50))
        self.assertEqual(bounce(49, 49, 1, 1, g.blocked), (49, 49, -1, -1))

    def test_standing_still(self):
        self.assertEqual(bounce(10, 10, 0, 0, Grid().blocked), (10, 10, 0, 0))


class WallShapes(unittest.TestCase):
    def test_end_points_may_be_given_either_way_round(self):
        self.assertEqual(Wall(9, 3, 2, 3).key, (2, 3, 9, 3))
        self.assertEqual(Wall(4, 9, 4, 2).key, (4, 2, 4, 9))
        self.assertEqual(Wall(4, 9, 4, 2), Wall(4, 2, 4, 9))

    def test_cells(self):
        self.assertEqual(Wall(2, 3, 4, 3).cells, {(2, 3), (3, 3), (4, 3)})
        self.assertEqual(Wall(7, 7, 7, 7).cells, {(7, 7)})

    def test_only_horizontal_and_vertical(self):
        for bad in [(0, 0, 5, 5), (0, 5, 5, 0), (0, 0, 5, 3), (-1, 0, 5, 0), (0, 0, 0, 300), (0, 0, 300, 0)]:
            with self.assertRaises(ValueError, msg=bad):
                Wall(*bad)
        with self.assertRaises(ValueError):
            Wall(1.0, 0, 5, 0)


class WallRules(unittest.TestCase):
    def game(self, **kw):
        cfg = {'N': 10, 'M': 2, 'tick_cap': 1000}
        cfg.update(kw)
        return Game(cfg)

    def test_wall_must_touch_hunter(self):
        g = self.game()
        self.assertIn('touch the hunter', g.wall_problem(Wall(5, 0, 5, 100)))
        self.assertIsNone(g.wall_problem(Wall(0, 0, 0, 100)))

    def test_cooldown_and_limit(self):
        g = self.game(M=2)
        g.step(create=Wall(0, 0, 0, 100))
        self.assertEqual(len(g.grid.walls), 1)
        self.assertEqual(g.last_wall, 0)
        self.assertIn('too soon', g.wall_problem(Wall(1, 1, 1, 100)))
        for _ in range(9):
            g.step()
        self.assertEqual(g.tick, 10)
        self.assertTrue(g.can_build())
        ev = g.step(create=Wall(10, 10, 10, 100))
        self.assertEqual(ev['created'], [10, 10, 10, 100])
        for _ in range(9):
            g.step()
        self.assertIn('already 2 walls', g.wall_problem(Wall(g.hx, g.hy, g.hx, 100)))
        # remove then create in one step
        ev = g.step(remove=[Wall(0, 0, 0, 100)], create=Wall(20, 20, 20, 100))
        self.assertEqual(ev['removed'], [[0, 0, 0, 100]])
        self.assertEqual(ev['created'], [20, 20, 20, 100])

    def test_wall_may_not_hit_prey_or_cross_a_wall(self):
        g = self.game()
        g.hx, g.hy = 230, 100
        self.assertIn('hit the prey', g.wall_problem(Wall(230, 0, 230, 250)))
        g.grid.add(Wall(0, 150, 299, 150))
        self.assertIn('through another wall', g.wall_problem(Wall(230, 0, 230, 199)))
        self.assertIsNone(g.wall_problem(Wall(230, 0, 230, 149)))     # touching end to end is fine

    def test_squished_hunter(self):
        g = self.game()
        g.hx, g.hy, g.hvx, g.hvy = 50, 50, 1, 1
        g.grid.add(Wall(0, 51, 299, 51))            # a wall just above: the hunter bounces this tick
        self.assertIn('squish', g.wall_problem(Wall(0, 50, 299, 50)))
        self.assertIsNone(g.wall_problem(Wall(50, 0, 50, 50)))

    def test_rejected_wall_is_noted_not_penalised(self):
        g = self.game()
        ev = g.step(create=Wall(5, 0, 5, 100))
        self.assertIn('not built', ev['notes'][0])
        self.assertIsNone(g.last_wall)
        self.assertEqual((g.hx, g.hy), (1, 1))

    def test_removing_a_missing_wall_is_noted(self):
        g = self.game()
        ev = g.step(remove=[Wall(5, 5, 5, 9)])
        self.assertIn('no such wall', ev['notes'][0])


class Stepping(unittest.TestCase):
    def test_prey_moves_every_other_tick(self):
        g = Game({'N': 5, 'M': 1})
        g.step(prey_velocity=(1, 0))                 # tick 0: hunter only; velocity request ignored
        self.assertEqual((g.px, g.py, g.pvx, g.pvy), (230, 200, 0, 0))
        g.step(prey_velocity=(1, 0))                 # tick 1: both move
        self.assertEqual((g.px, g.py), (231, 200))
        g.step()                                     # tick 2: hunter only
        self.assertEqual((g.px, g.py), (231, 200))
        g.step()                                     # tick 3: prey keeps going
        self.assertEqual((g.px, g.py), (232, 200))
        self.assertEqual((g.hx, g.hy), (4, 4))
        self.assertEqual(g.tick, 4)

    def test_catch_distance_and_line_of_sight(self):
        g = Game({'N': 5, 'M': 1})
        g.hx, g.hy, g.px, g.py = 100, 100, 104, 100
        self.assertTrue(g.is_caught())
        g.px = 105
        self.assertFalse(g.is_caught())
        g.px, g.py = 103, 103                        # distance 4.24
        self.assertFalse(g.is_caught())
        g.px, g.py = 102, 103                        # distance 3.6, wall between
        g.grid.add(Wall(101, 90, 101, 110))
        self.assertFalse(g.is_caught())
        g.grid.remove(Wall(101, 90, 101, 110))
        self.assertTrue(g.is_caught())

    def test_cells_between(self):
        self.assertEqual(cells_between(0, 0, 3, 0), [(1, 0), (2, 0)])
        self.assertEqual(cells_between(0, 0, 0, 0), [])
        self.assertEqual(cells_between(0, 0, 2, 2), [(1, 1)])

    def test_velocity_validation(self):
        self.assertEqual(parse_prey_velocity([1, -1]), (1, -1))
        for bad in [[2, 0], [1], [1.0, 0], 'up', [True, 0], None]:
            with self.assertRaises(ValueError):
                parse_prey_velocity(bad)

    def test_config_files(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'c.txt'
            p.write_text('# comment\n10\n5\n', encoding='utf-8')
            self.assertEqual(load_config(p), {'N': 10, 'M': 5, 'tick_cap': 24000})
            p.write_text('50 10 3000\n', encoding='utf-8')
            self.assertEqual(load_config(p), {'N': 50, 'M': 10, 'tick_cap': 3000})
            p.write_text('0 10\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                load_config(p)


def _write_bot(folder, hunter_body='return {}', prey_body='return None'):
    """A Python bot with the given strategy bodies, built from the template."""
    src = (TEMPLATES / 'python' / 'bot.py').read_text(encoding='utf-8')
    src = src.replace('    # TODO: your hunter strategy here\n    return {}', '    ' + hunter_body)
    src = src.replace('    # TODO: your prey strategy here\n    return None', '    ' + prey_body)
    (folder / 'bot.py').write_text(src, encoding='utf-8')
    return {'name': folder.name, 'cwd': str(folder), 'command': [sys.executable, 'bot.py']}


class Runner(unittest.TestCase):
    def test_template_bots_play_a_game(self):
        with tempfile.TemporaryDirectory() as d:
            bot = _write_bot(Path(d))
            r = play_game([bot, bot], {'N': 10, 'M': 3, 'tick_cap': 400}, clock=60)
            self.assertEqual(r['errors'], [None, None])
            self.assertEqual(len(r['frames']), r['ticks'] + 1)
            self.assertIn(r['ended'], ('caught', 'tick cap'))

    def test_hunter_walls_and_prey_moves_reach_the_engine(self):
        with tempfile.TemporaryDirectory() as d:
            h = Path(d) / 'h'; h.mkdir()
            p = Path(d) / 'p'; p.mkdir()
            hunter = _write_bot(h, hunter_body="return {'create': longest_wall(s, 'v')} if can_build(s) else {}")
            prey = _write_bot(p, prey_body='return (0, -1)')
            r = play_game([hunter, prey], {'N': 50, 'M': 2, 'tick_cap': 60}, clock=60)
            self.assertEqual(r['errors'], [None, None])
            built = [e for e in r['events'] if 'created' in e]
            self.assertEqual([e['tick'] for e in built], [0, 50])
            self.assertEqual(r['frames'][-1][5], 200 - 30)        # prey went up 30 times in 60 ticks

    def test_crashes_prey_is_caught_hunter_forfeits(self):
        with tempfile.TemporaryDirectory() as d:
            h = Path(d) / 'h'; h.mkdir()
            p = Path(d) / 'p'; p.mkdir()
            crasher = _write_bot(h, hunter_body='if s.tick == 5: raise SystemExit(1)\n    return {}',
                                 prey_body='if s.tick == 5: raise SystemExit(1)\n    return None')
            fine = _write_bot(p)
            r = play_game([crasher, fine], {'N': 10, 'M': 1, 'tick_cap': 30}, clock=60)
            self.assertTrue(r['errors'][0].startswith('crashed'))
            self.assertEqual(r['ended'], 'hunter forfeit')
            self.assertEqual(r['ticks'], 30)                        # scored as the tick cap
            self.assertEqual(r['played'], 5)
            r = play_game([fine, crasher], {'N': 10, 'M': 1, 'tick_cap': 30}, clock=60)
            self.assertTrue(r['errors'][1].startswith('crashed'))
            self.assertEqual(r['ended'], 'prey failed')
            self.assertEqual(r['ticks'], 5)

    def test_failed_hunter_scores_the_cap(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / 'a'; a.mkdir()
            b = Path(d) / 'b'; b.mkdir()
            cfg = Path(d) / 'cfg.txt'
            cfg.write_text('10 2 40\n', encoding='utf-8')
            # a's hunter crashes; b's hunter is merely useless (reaches the cap): both score the cap, a tie
            bot_a = _write_bot(a, hunter_body='if s.tick == 3: raise SystemExit(1)\n    return {}')
            bot_b = _write_bot(b)
            st = play_match(bot_a, bot_b, cfg, clock=60)
            self.assertEqual([g['ended'] for g in st['summary']['games']], ['hunter forfeit', 'tick cap'])
            self.assertEqual(st['summary']['scores'], {'a': 40, 'b': 40})
            self.assertIsNone(st['summary']['winner'])
            # a's hunter crashes; b's prey stands still and is caught: b wins on ticks
            bot_b = _write_bot(b, hunter_body="return {'create': longest_wall(s, 'v')} if can_build(s) else {}")
            st = play_match(bot_b, bot_a, cfg, clock=60)
            self.assertEqual(st['summary']['winner'], 'b' if st['summary']['scores']['b'] < 40 else None)
            # both hunters crash: tie
            bot_b = _write_bot(b, hunter_body='if s.tick == 3: raise SystemExit(1)\n    return {}')
            st = play_match(bot_a, bot_b, cfg, clock=60)
            self.assertEqual([g['ended'] for g in st['summary']['games']], ['hunter forfeit', 'hunter forfeit'])
            self.assertIsNone(st['summary']['winner'])

    def test_both_fail_on_the_same_tick_counts_as_prey_caught(self):
        with tempfile.TemporaryDirectory() as d:
            h = Path(d) / 'h'; h.mkdir()
            p = Path(d) / 'p'; p.mkdir()
            # both crash the first time the prey is asked (tick 1): hunter on tick 1, prey on tick 1
            hunter = _write_bot(h, hunter_body='if s.tick == 1: raise SystemExit(1)\n    return {}')
            prey = _write_bot(p, prey_body='raise SystemExit(1)')
            r = play_game([hunter, prey], {'N': 10, 'M': 1, 'tick_cap': 30}, clock=60)
            self.assertTrue(r['errors'][0] and r['errors'][1])
            self.assertEqual(r['ended'], 'prey failed')
            self.assertEqual(r['ticks'], 1)

    def test_bad_requests_are_ignored_not_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            h = Path(d) / 'h'; h.mkdir()
            p = Path(d) / 'p'; p.mkdir()
            hunter = _write_bot(h, hunter_body="return {'create': [1, 2, 3], 'remove': 'nope'}")
            prey = _write_bot(p, prey_body='return (5, 5)')
            r = play_game([hunter, prey], {'N': 10, 'M': 1, 'tick_cap': 4}, clock=60)
            self.assertEqual(r['errors'], [None, None])
            notes = [n for e in r['events'] for n in e.get('notes', [])]
            self.assertTrue(any('hunter request ignored' in n for n in notes))
            self.assertTrue(any('prey request ignored' in n for n in notes))

    def test_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'p'; p.mkdir()
            h = Path(d) / 'h'; h.mkdir()
            slow = _write_bot(p, prey_body='import time; time.sleep(0.3); return None')
            fine = _write_bot(h)
            r = play_game([fine, slow], {'N': 10, 'M': 1, 'tick_cap': 100}, clock=0.5)
            self.assertEqual(r['errors'][1], 'time')
            self.assertEqual(r['ended'], 'prey failed')
            self.assertLess(r['ticks'], 10)

    def test_match_scores_and_winner(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / 'a'; a.mkdir()
            b = Path(d) / 'b'; b.mkdir()
            cfg = Path(d) / 'cfg.txt'
            cfg.write_text('10 2 80\n', encoding='utf-8')
            # a's prey runs away; b's prey stands still
            bot_a = _write_bot(a, prey_body='return (1, 0) if s.hunter.x < s.prey.x else (-1, 0)')
            bot_b = _write_bot(b)
            st = play_match(bot_a, bot_b, cfg, clock=60, out_dir=Path(d) / 'out')
            s = st['summary']
            self.assertEqual(set(s['scores']), {'a', 'b'})
            self.assertEqual(len(st['games']), 2)
            self.assertTrue((Path(d) / 'out' / 'replay.html').exists())
            data = json.loads((Path(d) / 'out' / 'results.json').read_text(encoding='utf-8'))
            self.assertEqual(data['summary'], s)


class Roster(unittest.TestCase):
    def test_local_roster_is_merged(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'a').mkdir(); (d / 'b').mkdir()
            (d / 'bots.json').write_text(json.dumps([{'name': 'A', 'cwd': 'a', 'command': ['x']}]), encoding='utf-8')
            self.assertEqual([b['name'] for b in load_bots(d / 'bots.json')], ['A'])
            (d / 'bots.local.json').write_text(json.dumps([{'name': 'B', 'cwd': 'b', 'command': ['y']}]), encoding='utf-8')
            self.assertEqual([b['name'] for b in load_bots(d / 'bots.json')], ['A', 'B'])

    def test_dropped_in_folders_are_discovered(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'bots' / 'carol').mkdir(parents=True); (d / 'bots' / 'carol' / 'bot.jl').write_text('')
            (d / 'bots' / 'dave').mkdir(); (d / 'bots' / 'dave' / 'bot.cpp').write_text('')
            (d / 'bots' / 'notes').mkdir(); (d / 'bots' / 'notes' / 'readme.txt').write_text('')
            (d / 'bots' / 'samples' / 'x').mkdir(parents=True); (d / 'bots' / 'samples' / 'x' / 'bot.py').write_text('')
            (d / 'bots.json').write_text(json.dumps([{'name': 'X', 'cwd': 'bots/samples/x', 'command': ['{python}', 'bot.py']}]), encoding='utf-8')
            bots = {b['name']: b for b in load_bots(d / 'bots.json')}
            self.assertEqual(sorted(bots), ['X', 'carol', 'dave'])
            self.assertEqual(bots['carol']['command'][0], 'julia')
            self.assertEqual(bots['dave']['command'][0].split('/')[-1], 'bot')
            self.assertIn('build', bots['dave'])


class Samples(unittest.TestCase):
    def test_roster_and_python_sample(self):
        bots = {b['name']: b for b in load_bots('bots.json')}
        py = bots['Box (Python)']
        r = play_game([py, py], {'N': 20, 'M': 8, 'tick_cap': 1500}, clock=60)
        self.assertEqual(r['errors'], [None, None])
        self.assertTrue(any('created' in e for e in r['events']))

    @unittest.skipUnless(shutil.which('c++') or shutil.which('g++') or shutil.which('clang++'), 'no C++ compiler')
    def test_cpp_sample_matches_python_sample(self):
        bots = {b['name']: b for b in load_bots('bots.json')}
        build(bots['Box (C++)'])
        cfg = {'N': 20, 'M': 8, 'tick_cap': 800}
        r1 = play_game([bots['Box (Python)'], bots['Box (Python)']], cfg, clock=60)
        r2 = play_game([bots['Box (C++)'], bots['Box (C++)']], cfg, clock=60)
        self.assertEqual(r2['errors'], [None, None])
        self.assertEqual([f[:8] for f in r1['frames']], [f[:8] for f in r2['frames']])   # clocks differ, play must not

    @unittest.skipUnless(shutil.which('julia'), 'Julia not installed')
    def test_julia_sample_matches_python_sample(self):
        bots = {b['name']: b for b in load_bots('bots.json')}
        cfg = {'N': 20, 'M': 8, 'tick_cap': 800}
        r1 = play_game([bots['Box (Python)'], bots['Box (Python)']], cfg, clock=120)
        r2 = play_game([bots['Box (Julia)'], bots['Box (Julia)']], cfg, clock=120)
        self.assertEqual(r2['errors'], [None, None])
        self.assertEqual([f[:8] for f in r1['frames']], [f[:8] for f in r2['frames']])   # clocks differ, play must not

    def test_templates_answer_the_protocol(self):
        for lang, cmd in (('python', [sys.executable, 'bot.py']),):
            proc = subprocess.run(cmd, cwd=TEMPLATES / lang, text=True, capture_output=True, timeout=30,
                                  input='{"type":"start","protocol_version":1,"role":"prey","N":10,"M":5,"tick_cap":100,"width":300,"height":300}\n'
                                        '{"type":"tick","tick":1,"hx":1,"hy":1,"hvx":1,"hvy":1,"last_wall":-1,"px":230,"py":200,"pvx":0,"pvy":0,"walls":[]}\n')
            self.assertEqual(proc.stdout.split(), ['{"ready":true}', '{}'], proc.stderr)


if __name__ == '__main__':
    unittest.main()
