"""
Interact easily with your VMWare clusters.
"""
from __future__ import annotations

import os
from argparse import ArgumentParser, RawTextHelpFormatter, _SubParsersAction
from contextlib import nullcontext
from inspect import signature
from types import FunctionType

from zut import add_command, configure_logging, exec_command, get_help_text

from . import (VCenterClient, __prog__, __version__, cluster,
               customvalue, datastore, host, net, perf, pool, report, tag, vm, settings)
from .export import export
from .inventory import inventory


def main():
    configure_logging()

    parser = init_parser(__prog__, __version__, __doc__)

    subparsers = parser.add_subparsers(title='Commands')
    add_commands(subparsers)
    
    parse_and_exec_command(parser)
    

def init_parser(prog: str = None, version: str = None, doc: str = None):
    parser = ArgumentParser(prog=prog, description=get_help_text(doc), formatter_class=RawTextHelpFormatter, add_help=False, epilog='\n'.join(doc.splitlines()[2:]) if doc else None)
    
    scopes = VCenterClient.get_available_scopes()

    group = parser.add_argument_group(title='General options')
    group.add_argument('-s', '--scope', default=os.environ.get('VMWARE_DEFAULT_SCOPE'), help=f"VCenter scope to use. Available: {', '.join(scopes) if scopes else 'none'}.")
    group.add_argument('-x', '--csv-excel', action='store_true', help="Format CSV outputs for easy display with Excel.")
    group.add_argument('-h', '--help', action='help', help=f"Show this program help message and exit.")
    group.add_argument('--version', action='version', version=f"{prog} {version or '?'}", help="Show version information and exit.")

    return parser


def add_commands(subparsers: _SubParsersAction[ArgumentParser]):
    add_command(subparsers, report)

    add_command(subparsers, cluster)
    add_command(subparsers, pool)
    add_command(subparsers, datastore)
    add_command(subparsers, net)
    add_command(subparsers, host)
    add_command(subparsers, vm)
    add_command(subparsers, customvalue)
    add_command(subparsers, tag)
    
    add_command(subparsers, inventory, name='inventory')
    add_command(subparsers, export, name='export')
    
    add_command(subparsers, perf)
        

def get_vcenter(handle: FunctionType, args: dict):
    if handle and 'vcenter' in signature(handle).parameters:
        scope = args.pop('scope', None)
        vcenter = VCenterClient(scope)
        args['vcenter'] = vcenter    
    else:
        vcenter = nullcontext()

    return vcenter
        

def parse_and_exec_command(parser: ArgumentParser):
    args = vars(parser.parse_args())
    handle = args.pop('handle', None)
    csv_excel = args.pop('csv_excel', None)
    if csv_excel is not None:
        settings.CSV_EXCEL = csv_excel

    with get_vcenter(handle, args):
        exec_command(handle, args)


if __name__ == '__main__':
    main()
