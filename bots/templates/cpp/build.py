"""Compiles bot.cpp into ./bot (bot.exe on Windows).

Works on Mac, Linux and Windows. The referee runs it once before the tournament
(untimed), and again only when your files change. You can also run it yourself:
    python3 build.py
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
exe = here / ('bot.exe' if os.name == 'nt' else 'bot')
sources = ['bot.cpp']

for cxx in (os.environ.get('CXX'), 'c++', 'g++', 'clang++'):
    if cxx and shutil.which(cxx):
        cmd = [cxx, '-std=c++17', '-O2', *sources, '-o', str(exe)]
        break
else:
    if shutil.which('cl'):                      # Visual Studio's compiler (Developer Command Prompt)
        cmd = ['cl', '/nologo', '/std:c++17', '/O2', '/EHsc', *sources, f'/Fe:{exe}']
    else:
        sys.exit('No C++ compiler found. Mac: run  xcode-select --install.  '
                 'Windows: install MinGW-w64 (g++) or Visual Studio Build Tools.  Linux: install g++.')

r = subprocess.run(cmd, cwd=here)
if r.returncode == 0:
    sys.exit(0)

# On a Mac after a macOS update the default SDK can be newer than the linker understands
# ("unknown architecture ... .tbd"). Try the other installed SDKs, newest first.
if sys.platform == 'darwin' and not os.environ.get('SDKROOT'):
    sdks = Path('/Library/Developer/CommandLineTools/SDKs')
    candidates = sorted((p for p in sdks.glob('MacOSX*.*.sdk') if p.is_dir()),
                        key=lambda p: [int(x) for x in p.name[6:-4].split('.')], reverse=True) if sdks.is_dir() else []
    for sdk in candidates:
        print(f'Retrying with SDKROOT={sdk}', file=sys.stderr)
        env = dict(os.environ, SDKROOT=str(sdk))
        if subprocess.run(cmd, cwd=here, env=env, capture_output=True).returncode == 0:
            print(f'Built with {sdk.name}. To make this permanent: export SDKROOT={sdk}', file=sys.stderr)
            sys.exit(0)
sys.exit(r.returncode)
