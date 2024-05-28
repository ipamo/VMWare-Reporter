"""
Interact easily with your VMWare clusters.
"""
from __future__ import annotations

import logging
import os
from argparse import ArgumentParser, RawTextHelpFormatter, _SubParsersAction
from configparser import ConfigParser
from contextlib import nullcontext
from inspect import signature
from types import FunctionType

from zut import (OutTable, add_func_command, add_module_command,
                 configure_logging, exec_command, get_help_text,
                 register_locale)

from . import VCenterClient, __prog__, __version__, autoreport, customvalue
from .cluster import add_cluster_commands
from .resourcepool import add_resourcepool_commands
from .datastore import add_datastore_commands
from .dump import dump
from .host import add_host_commands
from .inventory import export_inventory
from .net import add_net_commands
from .tag import add_tag_commands
from .vm import add_vm_commands
from .perf import add_perf_commands

logger = logging.getLogger(__name__)

def main():
    configure_logging()
    register_locale(use_excel_csv=(os.environ.get('USE_EXCEL_CSV') or '1').lower() in ['1', 'yes', 'true', 'on'])
    OutTable.DEFAULT_EXCEL_ATEXIT = (os.environ.get('DEFAULT_EXCEL_ATEXIT') or '1').lower() in ['1', 'yes', 'true', 'on']

    parser = init_parser(__prog__, __version__, __doc__)

    subparsers = parser.add_subparsers(title='Commands')
    add_commands(subparsers)
    
    parse_and_exec_command(parser)
    

def init_parser(prog: str = None, version: str = None, doc: str = None, *, config: ConfigParser = None, section: str = None):
    parser = ArgumentParser(prog=prog, description=get_help_text(doc), formatter_class=RawTextHelpFormatter, add_help=False, epilog='\n'.join(doc.splitlines()[2:]) if doc else None)
    
    envs = VCenterClient.get_configured_envs(config=config, section=section)

    group = parser.add_argument_group(title='General options')
    group.add_argument('-e', '--env', default=os.environ.get('VMWARE_DEFAULT_ENV'), help=f"Name of the vCenter to use. Available: {', '.join(envs) if envs else 'none'}.")
    group.add_argument('-h', '--help', action='help', help=f"Show this program help message and exit.")
    group.add_argument('--version', action='version', version=f"{prog} {version or '?'}", help="Show version information and exit.")

    return parser


def add_commands(subparsers: _SubParsersAction[ArgumentParser]):
    add_func_command(subparsers, export_inventory, name='inventory')
    add_func_command(subparsers, dump, name='dump')

    add_cluster_commands(subparsers, name='cluster')
    add_resourcepool_commands(subparsers, name='resourcepool')
    add_datastore_commands(subparsers, name='datastore')
    add_net_commands(subparsers, name='net')
    add_host_commands(subparsers, name='host')
    add_vm_commands(subparsers, name='vm')
    add_func_command(subparsers, customvalue.list_custom_values, name='customvalue')
    add_tag_commands(subparsers, name='tag')
    
    add_perf_commands(subparsers, name='perf')

    add_module_command(subparsers, autoreport)
        

def get_vcenter(handle: FunctionType, args: dict, *, config: ConfigParser = None, section: str = None):
    if 'vcenter' in signature(handle).parameters:
        env = args.pop('env', None)
        vcenter = VCenterClient(env, config=config, section=section)
        args['vcenter'] = vcenter    
    else:
        vcenter = nullcontext()

    return vcenter
        

def parse_and_exec_command(parser: ArgumentParser, *, config: ConfigParser = None, section: str = None):    
    args = vars(parser.parse_args())
    handle = args.pop('handle', None)

    with get_vcenter(handle, args, config=config, section=section):
        exec_command(handle, args)


if __name__ == '__main__':
    main()
