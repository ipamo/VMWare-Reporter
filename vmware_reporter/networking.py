"""
Analyze networks.

List switchs (`vim.dvs.DistributedVirtualSwitch` objects) and networks (`vim.Network` and `vim.dvs.DistributedVirtualPortgroup` objects).
"""
from __future__ import annotations

import re
from argparse import ArgumentParser, _SubParsersAction

from pyVmomi import vim
from zut import add_func_command, out_table
from zut.excel import openpyxl

from . import VCenterClient, get_obj_ref


def add_networking_commands(commands_subparsers: _SubParsersAction[ArgumentParser], *, name: str):
    add_func_command(commands_subparsers, analyze_networks, name=name, doc=__doc__)


DEFAULT_OUT = 'networking.xlsx#{title}' if openpyxl else 'networking-{title}.csv'

def analyze_networks(vcenter: VCenterClient, *, out: str = DEFAULT_OUT):
    with out_table(out, title='switchs', dir=vcenter.get_out_dir()) as t:
        for obj in vcenter.iter_objs(vim.DistributedVirtualSwitch):
            uplinks = []
            for portgroup in obj.config.uplinkPortgroup:
                uplink = portgroup.name
                uplinks.append(uplink)

            t.append({
                'ref': get_obj_ref(obj),
                'name': obj.name,
                'uuid': obj.uuid,
                'uplinks': uplinks,
                'default_vlan': _vlan_repr(obj.config.defaultPortConfig.vlan),
            })

    with out_table(out, title='networks', dir=vcenter.get_out_dir()) as t:
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
                typename = type(obj).__name__
                switch = None
                vlan = None
                ports = None

            t.append({
                'ref': get_obj_ref(obj),
                'name': obj.name,
                'type': typename,
                'switch': switch,
                'ports': ports,
                'default_vlan': _vlan_repr(vlan),
            })


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('-o', '--out', default=DEFAULT_OUT, help="Output Excel or CSV file (default: %(default)s).")

analyze_networks.add_arguments = _add_arguments


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
