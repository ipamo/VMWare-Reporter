#!/usr/bin/env python
from __future__ import annotations
from importlib import import_module

import ctypes
import os
import re
import shlex
import shutil
import subprocess
import sys
from argparse import ArgumentParser
from pathlib import Path
from vmware_reporter import __prog__

BASE_DIR = Path(__file__).resolve().parent

def main():
    parser = ArgumentParser()
    subparsers = parser.add_subparsers()

    for command in _commands:
        name = command.handle.__name__
        cmdparser = subparsers.add_parser(name)
        cmdparser.set_defaults(handle=command.handle)
        if command.add_arguments:
            command.add_arguments(cmdparser)

    args = vars(parser.parse_args())
    handle = args.pop('handle', build)
    handle(**args)


class Command:
    def __init__(self, handle, *, add_arguments=None):
        self.handle = handle
        self.add_arguments = add_arguments

_commands: list[Command] = []

def command(handle = None, *, add_arguments = None):
    if handle is None: # used as a decorator with arguments
        def decorator(handle):
            command(handle, add_arguments=add_arguments)
            return handle
        return decorator
    
    _commands.append(Command(handle, add_arguments=add_arguments))
    return handle


@command
def build():
    clean()
    test()
    docs()
    wheel()


@command
def clean(path: Path = None):
    if path is None:
        print(f"{Color.YELLOW}Clean{Color.RESET}")

    if not path:
        path = BASE_DIR

    if path.is_dir():
        if path.name == '.venv':
            pass # keep it as is
        elif path.name in ['__pycache__', 'build'] or path.name.endswith('.egg-info'):
            print(f'delete {path}')
            shutil.rmtree(path)
        else:
            for sub in path.iterdir():
                clean(sub)
    else:
        pass


@command
def test():    
    print(f"{Color.YELLOW}Test{Color.RESET}")
    _run('python -m unittest')


@command
def wheel():
    print(f"{Color.YELLOW}Wheel{Color.RESET}")
    _run('pip wheel --no-deps -w dist .')


def docs_add_arguments(parser: ArgumentParser):
    parser.add_argument('--serve', action='store_true')

@command(add_arguments=docs_add_arguments)
def docs(serve=False):
    _ensure_installed('sphinx')
    _ensure_installed('sphinx_rtd_theme')
    _ensure_installed('myst_parser')
    
    print(f"{Color.YELLOW}Docs{Color.RESET}")
    _run('rm -rf docs/build')
    _run('sphinx-build docs docs/build')

    if serve:
        _run(f"python -m http.server -b 127.0.0.1 -d docs/build 8001")
    else:
        print(f"{Color.CYAN}TIP{Color.RESET}: serve docs with: {Color.CYAN}python -m http.server -b 127.0.0.1 -d docs/build 8001{Color.RESET} (or pass option {Color.CYAN}--serve{Color.RESET} to docs command)")


def publish_add_arguments(parser: ArgumentParser):
    parser.add_argument('path')

@command(add_arguments=publish_add_arguments)
def publish(path: str|Path):
    if not isinstance(path, Path):
        path = Path(path)
    
    if not re.match(r'^' + re.escape(__prog__) + r'-\d+\.\d+\.\d+((a|b)\d+)?+-py3-none-any.whl$', path.name):
        print(f"{Color.RED}Invalid path name: {path.name}{Color.RESET}")
        exit(1)

    _ensure_installed('twine')

    print(f"{Color.YELLOW}Check{Color.RESET}")
    _run(f'twine check {path}')

    print(f"{Color.YELLOW}Publish{Color.RESET}")
    _run(f'twine upload {path}')


def _run(args, accept_returncode: int|list[int] = 0, capture_output = False, encoding = 'utf-8', input: str = None) -> subprocess.CompletedProcess[str]:
    if isinstance(args, str):
        args = shlex.split(args)
    if not isinstance(accept_returncode, (list,tuple,set)):
        accept_returncode = [accept_returncode]
    
    if capture_output:
        options = {'capture_output': True}
    else:
        options = {'stdout': sys.stdout, 'stderr': sys.stderr, 'stdin': sys.stdin}

    cp = subprocess.run(args, text=True, encoding=encoding, input=input, **options)

    if capture_output:
        cp.stdout = cp.stdout.rstrip()
        cp.stderr = cp.stderr.rstrip()

    if not cp.returncode in accept_returncode:
        message = f"${args[1] if args[0] == 'sudo' else args[0]} returned code {cp.returncode}"
        if capture_output:
            if cp.stderr:
                message += f"\n{cp.stderr}"
            if cp.stdout:
                message += f"\n{cp.stdout}"
        
        print(f"{Color.RED}{message}{Color.RESET}")
        exit(1)
    
    return cp


def _ensure_installed(*packages: str, check_module: str = None):
    if not check_module:
        check_module = packages[0]
    
    try:
        import_module(check_module)
        return
    except ImportError:
        print(f"{Color.YELLOW}Install: {' '.join(packages)}{Color.RESET}")

    if sys.executable.startswith(os.path.expanduser('~')):
        _run(f"pip install {' '.join(packages)}")
    else:
        _run(f"sudo -i pip install {' '.join(packages)}")


class Color:
    RESET = '\033[0m'
    BLACK = '\033[0;30m'
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[0;33m'
    BLUE = '\033[0;34m'
    PURPLE = '\033[0;35m'
    CYAN = '\033[0;36m'
    WHITE = '\033[0;37m'

    # Disable coloring if environment variable NO_COLOR is set to 1
    NO_COLOR = False
    if (os.environ.get('NO_COLOR') or '0').lower() in ['1', 'yes', 'true', 'on']:
        NO_COLOR = True
        for _ in dir():
            if isinstance(_, str) and _[0] != '_' and _ not in ['DISABLED']:
                locals()[_] = ''

    # Set Windows console in VT mode
    if not NO_COLOR and sys.platform == 'win32':
        _kernel32 = ctypes.windll.kernel32
        _kernel32.SetConsoleMode(_kernel32.GetStdHandle(-11), 7)
        del _kernel32


if __name__ == '__main__':
    main()
