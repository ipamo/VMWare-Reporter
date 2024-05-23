"""
Manage ESXi hosts.
"""
from __future__ import annotations

from datetime import datetime
from io import IOBase
import json
import logging
import os
import re
from argparse import ArgumentParser, RawTextHelpFormatter, _SubParsersAction
from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path
from typing import Any, Callable, Literal

from pyVmomi import vim
from zut import (Header, add_func_command, get_description_text, get_help_text,
                 gigi_bytes, out_table)
from zut import slugify
from zut.excel import openpyxl

from . import VCenterClient, dictify_obj, dictify_value, get_obj_ref, get_obj_path

_logger = logging.getLogger(__name__)

def add_host_commands(commands_subparsers: _SubParsersAction[ArgumentParser], *, name: str):
    parser = commands_subparsers.add_parser(name, help=get_help_text(__doc__), description=get_description_text(__doc__), formatter_class=RawTextHelpFormatter, add_help=False)

    group = parser.add_argument_group(title='Command options')
    group.add_argument('-h', '--help', action='help', help=f"Show this command help message and exit.")

    subparsers = parser.add_subparsers(title='Sub commands')
    add_func_command(subparsers, list_hosts, name='list')


_DEFAULT_OUT = 'hosts.xlsx#{title}' if openpyxl else 'hosts-{title}.csv'


#region List

def list_hosts(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', out: os.PathLike|IOBase = _DEFAULT_OUT):
    headers = [
        'name',
        'ref',
        'overall_status',
        'config_status',
        Header('memory', fmt='gib'),
        'cpu_packages',
        'cpu_cores',
        'model',
        'vendor',
        'serial',
        'enclosure',
        'boot_time',
        'vmware_product',
        'cluster',
        'state',
    ]

    with out_table(out, title='hosts', dir=vcenter.get_out_dir(), headers=headers, after1970=True) as t:
        for obj in vcenter.iter_objs(vim.HostSystem, search, normalize=normalize, key=key):  
            try:
                _logger.info(f"Analyze {obj.name}")

                oii = dictify_value(obj.hardware.systemInfo.otherIdentifyingInfo)

                t.append([
                    obj.name,
                    get_obj_ref(obj),
                    obj.overallStatus,
                    obj.configStatus,
                    obj.hardware.memorySize,
                    obj.hardware.cpuInfo.numCpuPackages,
                    obj.hardware.cpuInfo.numCpuCores,
                    obj.hardware.systemInfo.model,
                    obj.hardware.systemInfo.vendor,
                    oii.get('SerialNumberTag'),
                    oii.get('EnclosureSerialNumberTag'),
                    obj.runtime.bootTime,
                    obj.config.product.fullName,
                    obj.parent.name,
                    obj.runtime.dasHostState.state if obj.runtime.dasHostState else None,
                ])
            
            except Exception as err:
                _logger.exception(f"Error while analyzing {str(obj)}")


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('-o', '--out', default=_DEFAULT_OUT, help="Output table (default: %(default)s).")

list_hosts.add_arguments = _add_arguments

#endregion
