"""Evasion:  python3 start.py [command] [options]

  python3 start.py                          start the dashboard (http://127.0.0.1:8091)
  python3 start.py match "Bot 1" "Bot 2"    play one match (both hunt once) and open its replay
  python3 start.py tournament               play every pair of bots, no browser
  python3 start.py params --out params/x.txt   make a parameter file (N, M, tick cap)

Add --help after a command to see its options.
"""
import sys

from referee import make_params, match, server, tournament

COMMANDS = {'serve': server.main, 'match': match.main, 'tournament': tournament.main,
            'params': make_params.main}


def main():
    args = sys.argv[1:]
    if args and args[0] in ('-h', '--help', 'help'):
        print(__doc__)
        return
    name = args[0] if args and args[0] in COMMANDS else 'serve'
    if args and args[0] in COMMANDS:
        args = args[1:]
    sys.argv = [f'python3 start.py {name}'] + args
    try:
        COMMANDS[name]()
    except KeyboardInterrupt:
        print('\nStopped.')
    except (OSError, ValueError) as exc:
        sys.exit(f'Error: {exc}')


if __name__ == '__main__':
    main()
