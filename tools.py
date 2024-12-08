#!/usr/bin/env python
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from argparse import ArgumentParser
from importlib import import_module
from pathlib import Path
from typing import Callable
from zut import Color, slugify_snake
from vmware_reporter import __prog__, __version__

SNAKE_PROG = slugify_snake(__prog__)

PUBLISH_PATTERN = re.compile(r'^' + re.escape(SNAKE_PROG) + r'\-\d+\.\d+\.\d+((a|b)\d+)?+-py3-none-any\.whl$')
PUBLISH_DOCS_PATTERN = re.compile(r'^' + re.escape(SNAKE_PROG) + r'\-docs\-\d+\.\d+\.\d+((a|b)\d+)?+\.zip$')

BASE_DIR = Path(__file__).resolve().parent

def main():
    parser = ArgumentParser()
    subparsers = parser.add_subparsers()

    for command in _commands:
        name = command.__name__
        cmdparser = subparsers.add_parser(name)
        cmdparser.set_defaults(handle=command)
        if add_arguments := getattr(command, 'add_arguments', None):
            add_arguments(cmdparser)

    args = vars(parser.parse_args())
    handle = args.pop('handle', build)
    handle(**args)


_commands: list[Callable] = []

def command(handle = None):
    if handle is None: # used as a decorator with arguments
        def decorator(handle):
            command(handle)
            return handle
        return decorator
    
    _commands.append(handle)
    return handle


# -----------------------------------------------------------------------------
@command
def build():
    clean()
    wheel()


# -----------------------------------------------------------------------------
@command
def clean(path: Path = None):
    if path is None:
        print(f"{Color.YELLOW}Clean{Color.RESET}")

    if not path:
        path = BASE_DIR

    if path.is_dir():
        if path.name == '.venv' or path.name.startswith('.venv.'):
            pass # keep it as is
        elif path.name in ['__pycache__', 'build'] or path.name.endswith('.egg-info') or path.as_posix().endswith('docs/api'):
            print(f'delete {path}')
            shutil.rmtree(path)
        else:
            for sub in path.iterdir():
                clean(sub)
    else:
        pass


# -----------------------------------------------------------------------------
@command
def test():    
    print(f"{Color.YELLOW}Test{Color.RESET}")
    _run('python -m unittest')


# -----------------------------------------------------------------------------
@command
def wheel():
    print(f"{Color.YELLOW}Wheel{Color.RESET}")
    _run('pip wheel --no-deps -w dist .')


# -----------------------------------------------------------------------------
@command
def docs(serve=False):
    _ensure_installed('sphinx')
    _ensure_installed('sphinx_rtd_theme')
    _ensure_installed('myst_parser')
    _ensure_installed('docs_versions_menu')
    
    if os.path.exists('docs/api'):
        shutil.rmtree('docs/api')

    target = f'docs/build/{__prog__}/v{__version__}'
    if os.path.exists(target):
        shutil.rmtree(target)
    
    print(f"{Color.YELLOW}Build docs{Color.RESET}")
    _run(f'sphinx-build docs {target}')
    
    print(f"{Color.YELLOW}Create docs archive{Color.RESET}")
    shutil.make_archive(f'dist/{SNAKE_PROG}-docs-{__version__}', 'zip', f'docs/build/{__prog__}', f'v{__version__}')
    
    print(f"{Color.YELLOW}Update versions.json{Color.RESET}")
    _run('docs-versions-menu --default-branch main --no-downloads-file', cwd=f'docs/build/{__prog__}')

    if serve:
        print(f"Visit {Color.CYAN}http://127.0.0.1:8001/{__prog__}/v{__version__}{Color.RESET}")
        _run(f"python -m http.server -b 127.0.0.1 -d docs/build 8001")
    else:
        print(f"{Color.CYAN}TIP{Color.RESET}: serve docs with: {Color.CYAN}python -m http.server -b 127.0.0.1 -d docs/build 8001{Color.RESET} (or pass option {Color.CYAN}--serve{Color.RESET} to docs command) and visit {Color.CYAN}http://127.0.0.1:8001/{__prog__}/v{__version__}{Color.RESET}")

def _add_arguments(parser: ArgumentParser):
    parser.add_argument('--serve', action='store_true')

docs.add_arguments = _add_arguments


# -----------------------------------------------------------------------------
@command
def publish(path: str|Path, proxy: str = None):
    if not isinstance(path, Path):
        path = Path(path)
    
    if not PUBLISH_PATTERN.match(path.name):
        print(f"{Color.RED}Invalid path name: {path.name}{Color.RESET}")
        exit(1)

    _ensure_installed('twine')
    _ensure_installed('keyring_pass')

    print(f"{Color.YELLOW}Check{Color.RESET}")
    _run(f'twine check {path.as_posix()}')

    env = {**os.environ}
    if sys.platform != 'win32' and os.path.exists('/etc/ssl/certs/ca-certificates.crt'):
        env['REQUESTS_CA_BUNDLE'] = '/etc/ssl/certs/ca-certificates.crt'
    if proxy:
        if not proxy.startswith(('http://','https://')):
            proxy = f'http://{proxy}'
        env['HTTP_PROXY'] = proxy
        env['HTTPS_PROXY'] = proxy

    print(f"{Color.YELLOW}Publish{Color.RESET}")
    _run(f'twine upload {path.as_posix()}', env=env)

def _add_arguments(parser: ArgumentParser):
    parser.add_argument('path')
    parser.add_argument('-x', '--proxy')

publish.add_arguments = _add_arguments


# -----------------------------------------------------------------------------
@command
def publish_docs(path: str|Path, host: str):
    if not isinstance(path, Path):
        path = Path(path)
    
    if not PUBLISH_DOCS_PATTERN.match(path.name):
        print(f"{Color.RED}Invalid path name: {path.name}{Color.RESET}")
        exit(1)

    print(f"{Color.YELLOW}Publish docs{Color.RESET}")
    _run(f"scp {path.as_posix()} {host}:{path.name}")
    _run(f"ssh {host} ./update-docs.sh {path.name}")

def _add_arguments(parser: ArgumentParser):
    parser.add_argument('path')
    parser.add_argument('host', help="SSH host")

publish_docs.add_arguments = _add_arguments


# -----------------------------------------------------------------------------

def _run(args, accept_returncode: int|list[int] = 0, capture_output = False, encoding = 'utf-8', input: str = None, env: dict = None, cwd: os.PathLike = None) -> subprocess.CompletedProcess[str]:
    if isinstance(args, str):
        args = shlex.split(args)
    if not isinstance(accept_returncode, (list,tuple,set)):
        accept_returncode = [accept_returncode]
    
    if capture_output:
        options = {'capture_output': True}
    else:
        options = {'stdout': sys.stdout, 'stderr': sys.stderr, 'stdin': sys.stdin}

    cp = subprocess.run(args, text=True, encoding=encoding, input=input, env=env, cwd=cwd, **options)

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


if __name__ == '__main__':
    main()
