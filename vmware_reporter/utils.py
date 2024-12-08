import os
import sys
from typing import Iterator
from dotenv import load_dotenv


def walk_to_root(path: str) -> Iterator[str]:
    """
    Yield directories starting from the given directory up to the root
    """
    if not os.path.exists(path):
        raise IOError('Starting path not found')

    if os.path.isfile(path):
        path = os.path.dirname(path)

    last_dir = None
    current_dir = os.path.abspath(path)
    while last_dir != current_dir:
        yield current_dir
        parent_dir = os.path.abspath(os.path.join(current_dir, os.path.pardir))
        last_dir, current_dir = current_dir, parent_dir


def load_dotenvs(prog: str):
    # NOTE: order matters so that current working directory gets precedence over system configuration file

    # Load configuration file from the current working directory
    cwd_env = os.path.abspath('.env')
    if os.path.exists(cwd_env):
        load_dotenv(cwd_env)

    # Load environment file where the Python module is installed
    install_env = None
    for _dirname in walk_to_root(__file__):
        install_env = os.path.join(_dirname, '.env')
        if os.path.isfile(install_env):
            if install_env != cwd_env:
                load_dotenv(install_env)
            break

    # Load system configuration file
    sys_env = f'C:\\ProgramData\\{prog}\\.env' if sys.platform == 'win32' else f'/etc/{prog}/.env'
    if os.path.isfile(sys_env) and sys_env != cwd_env and sys_env != install_env:
        load_dotenv(sys_env)
