"""
Analyze networking.
"""
from __future__ import annotations

from io import IOBase
import os
import re
from argparse import ArgumentParser

from pyVmomi import vim
from zut import tabular_dumper

from . import VCenterClient, get_obj_ref, get_obj_typename, settings
from .settings import TABULAR_OUT, OUT_DIR


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output Excel or CSV file (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

def dump_nets(vcenter: VCenterClient, *, out: os.PathLike|IOBase = TABULAR_OUT, dir: os.PathLike = None):
    """
    Dump switchs (`vim.dvs.DistributedVirtualSwitch` objects) and networks (`vim.Network` and `vim.dvs.DistributedVirtualPortgroup` objects).
    """
    switchs_headers = [
        'name',
        'ref',
        'overall_status',
        'config_status',
        'uuid',
        'uplinks',
        'default_vlan',
    ]

    with tabular_dumper(out, title='switch', dir=dir or vcenter.data_dir, scope=vcenter.scope, headers=switchs_headers, truncate=True, excel=settings.CSV_EXCEL) as t:
        for obj in vcenter.iter_objs(vim.DistributedVirtualSwitch):
            uplinks = []
            for portgroup in obj.config.uplinkPortgroup:
                uplink = portgroup.name
                uplinks.append(uplink)

            t.append([
                obj.name,
                get_obj_ref(obj),
                obj.overallStatus,
                obj.configStatus,
                obj.uuid,
                uplinks,
                _vlan_repr(obj.config.defaultPortConfig.vlan),
            ])

    networks_headers = [
        'name',
        'ref',
        'overall_status',
        'config_status',
        'type',
        'switch',
        'ports',
        'default_vlan',
    ]

    with tabular_dumper(out, title='network', dir=dir or vcenter.data_dir, scope=vcenter.scope, headers=networks_headers, truncate=True, excel=settings.CSV_EXCEL) as t:
        for obj in sorted(vcenter.iter_objs(vim.Network), key=_network_sortkey):
            if isinstance(obj, vim.dvs.DistributedVirtualPortgroup):
                typename = 'DVP'
                switch = obj.config.distributedVirtualSwitch.name
                vlan = obj.config.defaultPortConfig.vlan
                ports = f'{obj.config.numPorts}: ' + ','.join(_get_portkey_ranges(obj.portKeys))
            elif isinstance(obj, vim.Network):
                typename = 'Network'
                switch = None
                vlan = None
                ports = None
            elif isinstance(obj, vim.Network):
                typename = get_obj_typename(obj)
                switch = None
                vlan = None
                ports = None

            t.append([
                obj.name,
                get_obj_ref(obj),
                obj.overallStatus,
                obj.configStatus,
                typename,
                switch,
                ports,
                _vlan_repr(vlan),
            ])

dump_nets.add_arguments = _add_arguments
handle = dump_nets


def _network_sortkey(obj: vim.Network):
    if isinstance(obj, vim.dvs.DistributedVirtualPortgroup):
        minkey = None
        for key in obj.portKeys:
            if key is not None and re.match(r'^\d+$', key):
                key = int(key)
                if minkey is None or key < minkey:
                    minkey = key

        if minkey is None:
            minkey = 0
        return (1, obj.config.distributedVirtualSwitch.name, minkey ,obj.name)

    else:
        return (2, obj.name)


def _vlan_repr(vlan: vim.dvs.VmwareDistributedVirtualSwitch.VlanIdSpec|vim.dvs.VmwareDistributedVirtualSwitch.TrunkVlanSpec) -> int|str:
    if vlan is None:
        return None
    
    if isinstance(vlan, vim.dvs.VmwareDistributedVirtualSwitch.VlanIdSpec):
        result = 'id: '
    elif isinstance(vlan, vim.dvs.VmwareDistributedVirtualSwitch.TrunkVlanSpec):
        result = 'trunk: '
    else:
        result = f'{type(vlan).__name__}: '
    
    if isinstance(vlan.vlanId, list):
        parts = []
        for spec in vlan.vlanId:
            if isinstance(spec, vim.NumericRange):
                parts.append(f'{spec.start}-{spec.end}')
            else:
                parts.append(f'{spec}')
        result += ','.join(parts)
    elif isinstance(vlan.vlanId, vim.NumericRange):
        result += f'{vlan.vlanId.start}-{vlan.vlanId.end}'
    else:
        result += f'{vlan.vlanId}'

    if vlan.inherited:
        result += ', inherited'

    return result


def _get_portkey_ranges(portkeys: list[int|str]) -> list[str]:
    intkeys = []
    strkeys = []

    for key in portkeys:
        if re.match(r'^\d+$', key):
            intkeys.append(int(key))
        else:
            strkeys.append(str(key))
    
    intkeys.sort()

    results = []

    last_start = None
    last_end = None

    def append_last():
        if last_end is not None:
            if last_end == last_start:
                results.append(str(last_start))
            else:
                results.append(f'{last_start}-{last_end}')

    for key in intkeys:
        if last_end is not None and key == last_end + 1: 
            last_end = key
        else:
            append_last()
            last_start = key
            last_end = key

    append_last()

    for key in strkeys:
        results.append(key)
    return results
