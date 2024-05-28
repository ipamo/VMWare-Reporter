"""
Analyze resource pools.
"""
from __future__ import annotations

import logging
import os
import re
from argparse import ArgumentParser, RawTextHelpFormatter, _SubParsersAction
from io import IOBase

from pyVmomi import vim
from zut import (Header, add_func_command, get_description_text, get_help_text,
                 out_table)

from . import VCenterClient, get_obj_name, get_obj_ref, dictify_obj

_logger = logging.getLogger(__name__)


def add_resourcepool_commands(commands_subparsers: _SubParsersAction[ArgumentParser], *, name: str):
    parser = commands_subparsers.add_parser(name, help=get_help_text(__doc__), description=get_description_text(__doc__), formatter_class=RawTextHelpFormatter, add_help=False)

    group = parser.add_argument_group(title='Command options')
    group.add_argument('-h', '--help', action='help', help=f"Show this command help message and exit.")

    subparsers = parser.add_subparsers(title='Sub commands')
    add_func_command(subparsers, list_resourcepool, name='list')


_DEFAULT_OUT = '{title}.csv'


def list_resourcepool(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', out: os.PathLike|IOBase = _DEFAULT_OUT):
    headers = [
        'name',
        'ref',
        'overall_status',
        'config_status',
        'cluster',
        'parent',
        'vm_count',
        'reserved_cpu_mhz',
        'limit_cpu_mhz',
        'used_cpu_mhz',
        Header('reserved_memory', fmt='gib'),
        Header('limit_memory', fmt='gib'),
        Header('used_memory', fmt='gib'),
    ]

    with out_table(out, title='resourcepools', dir=vcenter.get_out_dir(), env=vcenter.env, headers=headers) as t:
        for obj in vcenter.iter_objs(vim.ResourcePool, search, normalize=normalize, key=key):            
            try:
                _logger.info(f"Analyze resource pool {get_obj_name(obj)}")

                t.append([
                    get_obj_name(obj),
                    get_obj_ref(obj),
                    obj.overallStatus,
                    obj.configStatus,
                    obj.owner.name,
                    get_obj_name(obj.parent) if obj.parent != obj.owner else None,
                    len(obj.vm),
                    obj.config.cpuAllocation.reservation,
                    obj.config.cpuAllocation.limit,
                    obj.summary.quickStats.overallCpuUsage,
                    obj.config.memoryAllocation.reservation*1024*1024,
                    obj.config.memoryAllocation.limit*1024*1024,
                    obj.summary.quickStats.hostMemoryUsage*1024*1024,
                ])
            
            except Exception as err:
                _logger.exception(f"Error while analyzing {str(obj)}")

def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('-o', '--out', default=_DEFAULT_OUT, help="Output table (default: %(default)s).")

list_resourcepool.add_arguments = _add_arguments
