"""Play one match between two bots from bots.json and open the replay.

  python3 start.py match "Box (Python)" "Box (C++)"
  python3 start.py match "Box (C++)" "Box (Julia)" --config params/n50_m10.txt

Two games: Bot 1 hunts Bot 2, then Bot 2 hunts Bot 1. Results go to results/match/
(results.json and replay.html). For many bots at once, use the dashboard: python3 start.py
"""
import argparse
import sys
import webbrowser

from .runner import ROOT, build, load_bots, play_match


def describe(game):
    err = ''
    if game.get('errors'):
        err = '  (' + '; '.join(f'{r}: {e}' for r, e in zip(('hunter', 'prey'), game['errors']) if e) + ')'
    return f"Game {game['game']}: {game['hunter']} hunts {game['prey']}: {game['ticks']} ticks ({game['ended']}){err}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('bot1')
    ap.add_argument('bot2')
    ap.add_argument('--config', default='params/n10_m5.txt')
    ap.add_argument('--clock', type=float, default=120, help='seconds per bot per game')
    ap.add_argument('--registry', default='bots.json')
    ap.add_argument('--output', default='results/match')
    ap.add_argument('--no-browser', action='store_true')
    args = ap.parse_args()
    roster = {b['name']: b for b in load_bots(args.registry)}
    for name in (args.bot1, args.bot2):
        if name not in roster:
            sys.exit(f'Unknown bot {name!r}. In bots.json: {", ".join(roster)}')
        build(roster[name])
    st = play_match(roster[args.bot1], roster[args.bot2], args.config, clock=args.clock, out_dir=ROOT / args.output)
    for g in st['summary']['games']:
        print(describe(g))
    w = st['summary']['winner']
    print('Winner: ' + (w if w else 'tie'))
    replay = ROOT / args.output / 'replay.html'
    print(f'Replay: {replay}')
    if not args.no_browser:
        webbrowser.open(replay.as_uri())


if __name__ == '__main__':
    main()
