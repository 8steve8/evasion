"""Write a parameter file: N (ticks between walls), M (most walls at once), tick cap.

  python3 start.py params --n 10 --m 5 --out params/my_game.txt
  python3 start.py params --n 50 --m 10 --cap 24000 --out params/competition.txt
"""
import argparse
from pathlib import Path

from .engine import DEFAULT_TICK_CAP, load_config
from .runner import ROOT


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--n', type=int, required=True, help='N: ticks the hunter must wait between walls')
    ap.add_argument('--m', type=int, required=True, help='M: most walls standing at once')
    ap.add_argument('--cap', type=int, default=DEFAULT_TICK_CAP, help=f'tick cap (default {DEFAULT_TICK_CAP})')
    ap.add_argument('--out', required=True, help='file to write, e.g. params/my_game.txt')
    args = ap.parse_args()
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f'# N (ticks between walls), M (most walls at once), tick cap\n{args.n}\n{args.m}\n{args.cap}\n',
                   encoding='utf-8')
    load_config(out)
    print(f'Wrote {out}')


if __name__ == '__main__':
    main()
