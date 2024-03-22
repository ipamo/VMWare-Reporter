"""
Analyze VM disks or NICs.
"""
from __future__ import annotations

import logging
from argparse import _SubParsersAction, ArgumentParser, RawTextHelpFormatter
from pathlib import Path
import re
from typing import Literal

from pyVmomi import vim
from zut import Header, out_table, gigi_bytes, get_help_text, get_description_text, add_func_command
from zut.excel import openpyxl

from .client import VCenterClient
from .inspect import dictify_obj, dictify_value, get_obj_ref

logger = logging.getLogger(__name__)

def add_vm_commands(commands_subparsers: _SubParsersAction[ArgumentParser], *, name: str):
    parser = commands_subparsers.add_parser(name, help=get_help_text(__doc__), description=get_description_text(__doc__), formatter_class=RawTextHelpFormatter, add_help=False)

    group = parser.add_argument_group(title='Command options')
    group.add_argument('-h', '--help', action='help', help=f"Show this command help message and exit.")

    subparsers = parser.add_subparsers(title='Sub commands')
    add_func_command(subparsers, analyze_disks, name='disks')
    add_func_command(subparsers, analyze_nics, name='nics')


#region Disks

DEFAULT_DISKS_OUT = VCenterClient.DEFAULT_OUT_DIR_MASK.joinpath('disks.xlsx#{title}' if openpyxl else 'disks-{title}.csv')


def analyze_disks(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', out: str = DEFAULT_DISKS_OUT, top: int = None):
    """
    Analyze VM disks.
    """
    vms_headers = [
        'vm',
        'power_state',
        'os_info',
        'os_family',
        'os_name',
        'device_disks',
        'guest_disks',
        'with_mappings',
        'without_mappings',
        'mapped_guest',
        'unmapped_guest',
        Header('capacity', fmt='gib'),
        Header('freespace', fmt='gib'),
        Header('mapped_disks_capacity', fmt='gib'),
        Header('mapped_guests_capacity', fmt='gib'),
        Header('mapped_guests_freespace', fmt='gib'),
        Header('unmapped_disks_capacity', fmt='gib'),
        Header('unmapped_guests_capacity', fmt='gib'),
        Header('unmapped_guests_freespace', fmt='gib'),
        'issues',
    ]

    details_headers = [
        'vm',
        'power_state',
        'os_info',
        'os_family',
        'os_name',
        'key',
        'backing',
        'datastore',
        'filename',
        'diskmode',
        'sharing',
        'remaining_backing_info',
        Header('capacity', fmt='gib'),
        Header('freespace', fmt='gib'),
        'mapping',
        'guests',
        Header('guests_capacity', fmt='gib'),
        Header('guests_freespace', fmt='gib'),
        'capacity_loss_pct',
    ]
    
    with (out_table(out, title='details', vcenter=vcenter.name, headers=details_headers) as t_details,
          out_table(out, title='vms', vcenter=vcenter.name, headers=vms_headers) as t_vms):
        
        for i, vm in enumerate(vcenter.get_objs(vim.VirtualMachine, search, normalize=normalize, key=key)):
            if top is not None and i == top:
                break
            
            try:
                logger.info(f"Analyze {vm.name} disks")

                info = extract_disks(vm)

                os = dictify_value(vm.config.extraConfig).get('guestOS.detailed.data')
                if not os:
                    os = {}
                os_family = os.get('familyName')
                os_name = os.get('prettyName')

                mapped_guests: list[vim.vm.GuestInfo.DiskInfo] = []
                for disk in info.disks:
                    for guest in disk.guests:
                        mapped_guests.append(guest)

                t_vms.append([
                    vm.name, # vm
                    vm.runtime.powerState, # power_state
                    vm.config.guestFullName, # os_info
                    os_family, # os_family
                    os_name, # os_name
                    len(info.disks), # 'device_disks',
                    len(mapped_guests) + len(info.unmapped_guests), # 'guest_disks',
                    len(mapped_guests), # 'with_mappings',
                    len(info.unmapped_guests), # 'without_mappings',
                    sorted(f'{guest.diskPath} ({guest.filesystemType})' if guest.filesystemType else guest.diskPath for guest in mapped_guests), # 'mapped_guest',
                    sorted(f'{guest.diskPath} ({guest.filesystemType})' if guest.filesystemType else guest.diskPath for guest in info.unmapped_guests),# 'unmapped_guests',
                    info.capacity,
                    info.freespace,
                    info.mapped_disks_capacity if mapped_guests else None,
                    info.mapped_guests_capacity if mapped_guests else None,
                    info.mapped_guests_freespace if mapped_guests else None,
                    info.unmapped_disks_capacity if info.unmapped_disks_capacity > 0 else None,
                    info.unmapped_guests_capacity if info.unmapped_guests else None,
                    info.unmapped_guests_freespace if info.unmapped_guests else None,
                    info.issues,
                ])

                for disk in info.disks:
                    device_backing = dictify_obj(disk.device.backing)
                    backing_typename = type(disk.device.backing).__name__
                    if backing_typename.startswith('vim.vm.device.VirtualDisk.'):
                        backing_typename = backing_typename[len('vim.vm.device.VirtualDisk.'):]
                    datastore = device_backing.pop('datastore', None)
                    fileName = device_backing.pop('fileName', None)
                    diskMode = device_backing.pop('diskMode', None)
                    sharing = device_backing.pop('sharing', None)

                    capacity_loss = disk.capacity - disk.guests_capacity if disk.guests else None
                    capacity_loss_pct = 100 * capacity_loss / disk.capacity if disk.guests else None
                    
                    t_details.append([
                        vm.name, # vm
                        vm.runtime.powerState, # power_state
                        vm.config.guestFullName, # os_info
                        os_family, # os_family
                        os_name, # os_name
                        disk.device.key, # key
                        backing_typename, # backing
                        datastore['name'] if datastore else None, # datastore
                        fileName,
                        diskMode,
                        sharing,
                        device_backing, # remaining_backing_info
                        disk.capacity,
                        disk.freespace,
                        disk.guests_mapping,
                        sorted(f'{guest.diskPath} ({guest.filesystemType})' if guest.filesystemType else guest.diskPath for guest in disk.guests), # guests
                        disk.guests_capacity if disk.guests else None,
                        disk.guests_freespace if disk.guests else None,
                        capacity_loss_pct,
                    ])

                for guest in [*info.unmapped_guests, *info.ignored_guests]:
                    t_details.append([
                        vm.name, # vm
                        vm.runtime.powerState, # power_state
                        vm.config.guestFullName, # os_info
                        os_family, # os_family
                        os_name, # os_name
                        None, # key
                        guest.mappings, # backing
                        None, # datastore
                        None, # filename
                        None, # diskMode
                        None, # sharing
                        None, # remaining_backing_info
                        None, # capacity
                        None, # freespace
                        'ignored' if guest in info.ignored_guests else 'unmapped', # mapping
                        f'{guest.diskPath} ({guest.filesystemType})' if guest.filesystemType else guest.diskPath, # guests
                        guest.capacity, # guests_capacity
                        guest.freeSpace, # guests_freespace
                        None, # capacity_loss_pct
                    ])

            except Exception as err:
                logger.exception(f"Error while analyzing {str(vm)} disks: {err}")


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('--top', type=int)
    parser.add_argument('-o', '--out', default=DEFAULT_DISKS_OUT, help="Output Excel or CSV file (default: %(default)s).")

analyze_disks.add_arguments = _add_arguments


def extract_disks(vm: vim.VirtualMachine):
    info = VmDisks(vm)

    # Retrieve disk devices
    disks_per_key: dict[int,VmDisk] = {}
    keys_to_ignore = set()
    for obj in vm.config.hardware.device:
        if not isinstance(obj, vim.vm.device.VirtualDisk):
            continue

        disk = VmDisk(obj)
        info.disks.append(disk)
        info.capacity += disk.capacity

        if obj.key is None:
            info._append_issue(f"Device disk has no key: {obj}")
        elif not isinstance(obj.key, int):
            info._append_issue(f"Device disk has a non-int key: {obj}")
        elif obj.key in disks_per_key:
            info._append_issue(f"Several disk have key {obj.key}")
            keys_to_ignore.add(obj.key)
        else:
            disks_per_key[obj.key] = disk

    for key in keys_to_ignore:
        del disks_per_key[key]

    # Retrieve guest disks
    # Associate them to device disks if a consistent mapping is provided
    for obj in vm.guest.disk:
        mappings = obj.mappings
        if mappings:
            mapping_keys = set()
            for mapping in mappings:
                mapping_keys.add(mapping.key)

            if len(mapping_keys) > 1:
                info._append_issue(f"Guest disk {obj.diskPath} mapped to several device disk keys: {mapping_keys}")
                info.unmapped_guests.append(obj)

            else:
                disk = disks_per_key.get(mapping.key)
                if not disk:
                    info._append_issue(f"Guest disk {obj.diskPath} mapped to unknown device disk key: {mapping.key}")
                    info.unmapped_guests.append(obj)

                else:
                    disk.guests.append(obj)
                    disk.guests_mapping = 'key'
        else:
            info.unmapped_guests.append(obj)

    # Verify consistency of mapped guests capacity
    for disk in info.disks:
        if disk.guests:
            for guest in disk.guests:
                disk.guests_capacity += guest.capacity
                disk.guests_freespace += guest.freeSpace
            if disk.guests_capacity > disk.capacity:
                info._append_issue(f"Inconsistent capacity ({disk.guests_capacity:,}) for guests {', '.join(guest.diskPath for guest in disk.guests)} (device {disk.device.key} capacity: {disk.capacity:,})")

    # Identify guests to ignore
    info._identify_ignored_guests()
    
    # Try to map remaining guests by capacity
    unmapped_guests = sorted([guest for guest in info.unmapped_guests], key=lambda obj: obj.capacity, reverse=True)
    unmapped_disks = sorted([disk for disk in info.disks if not disk.guests], key=lambda disk: disk.capacity, reverse=True)

    confirm_mapping = True
    if len(unmapped_disks) == len(unmapped_guests):
        for i in range(0, len(unmapped_disks)):
            disk = unmapped_disks[i]
            guest = unmapped_guests[i]
            if guest.capacity > disk.capacity:
                confirm_mapping = False
                break

            if i < len(unmapped_disks) - 1:
                next_disk = unmapped_disks[i+1]
                if guest.capacity <= next_disk.capacity: # another disk could match
                    confirm_mapping = False
                    break

        if confirm_mapping:
            for i in range(0, len(unmapped_disks)):
                disk = unmapped_disks[i]
                guest = unmapped_guests[i]
                info.unmapped_guests.remove(guest)
                disk.guests.append(guest)
                disk.guests_capacity += guest.capacity
                disk.guests_freespace += guest.freeSpace
                disk.guests_mapping = 'capacity'

    # Finalize capacity and freespace sums
    for disk in info.disks:
        if disk.guests:
            disk.freespace = disk.guests_freespace + (disk.capacity - disk.guests_capacity)
            info.mapped_guests_capacity += disk.guests_capacity
            info.mapped_guests_freespace += disk.guests_freespace
            info.mapped_disks_capacity += disk.capacity
            info.mapped_disks_freespace += disk.freespace
        else:
            info.unmapped_disks_capacity += disk.capacity

    for guest in info.unmapped_guests:
        info.unmapped_guests_capacity += guest.capacity
        info.unmapped_guests_freespace += guest.freeSpace

    # (estimation of unmapped disk freespace)
    if info.unmapped_disks_capacity > 0:
        if info.unmapped_guests_capacity > info.unmapped_disks_capacity:
            info.unmapped_disks_freespace = info.unmapped_guests_freespace * (info.unmapped_disks_capacity / info.unmapped_guests_capacity)
        else:
            info.unmapped_disks_freespace = info.unmapped_guests_freespace + (info.unmapped_disks_capacity - info.unmapped_guests_capacity)

    info.freespace = info.mapped_disks_freespace + info.unmapped_disks_freespace

    return info


class VmDisks:
    def __init__(self, vm: vim.VirtualMachine):
        self.vm = vm
        self.disks: list[VmDisk] = []
        self.unmapped_guests: list[vim.vm.GuestInfo.DiskInfo] = []
        self.ignored_guests: list[vim.vm.GuestInfo.DiskInfo] = []
        self.issues: list[str] = []

        self.capacity: int = 0
        """ Sum of device capacities. """

        self.freespace: int = 0
        """ Sum of device freespaces. This is sum of mapped device freespaces + an estimation of unmapped freespace. """

        self.mapped_disks_capacity: int = 0
        self.mapped_disks_freespace: int = 0
        self.mapped_guests_capacity: int = 0
        self.mapped_guests_freespace: int = 0
        
        self.unmapped_disks_capacity: int = 0
        self.unmapped_disks_freespace: int = 0
        self.unmapped_guests_capacity: int = 0
        self.unmapped_guests_freespace: int = 0

    def _append_issue(self, message: str):
        logger.warning(f"{self.vm.name}: {message}")
        self.issues.append(message)

    def _identify_ignored_guests(self):
        for guest in list(self.unmapped_guests):
            if self._must_ignore(guest):
                self.unmapped_guests.remove(guest)
                self.ignored_guests.append(guest)

    def _must_ignore(self, guest: vim.vm.GuestInfo.DiskInfo):      
        if guest.diskPath.startswith('C:\\Users\\'):
            return True

        elif '/' in guest.diskPath and guest.diskPath != '/': # Search Linux mount points with the exact same capacity as a parent. Example: /var/lib/rancher/volumes, /var/lib/kubelet.
            parts = [part for part in guest.diskPath.split('/')]
            for n in range(1, len(parts)):
                search_parent = '/' + '/'.join(parts[1:n])
                for parent in self.guests:
                    if parent.diskPath == search_parent and parent.capacity == guest.capacity:
                        return True
                    
        return False

    @property
    def guests(self):
        for disk in self.disks:
            for guest in disk.guests:
                yield guest

        for guest in self.unmapped_guests:
            yield guest

        for guest in self.ignored_guests:
            yield guest

    def to_dict(self):
        data = []

        for disk in self.disks:
            data.append(disk.to_dict())

        if self.unmapped_guests:
            guests_data = []
            data.append({'guests_mapping': 'unmapped', 'guests':  guests_data})
            for guest in self.unmapped_guests:
                guests_data.append(self._get_guest_dict(guest))

        if self.ignored_guests:
            guests_data = []
            data.append({'guests_mapping': 'ignored', 'guests':  guests_data})
            for guest in self.ignored_guests:
                guests_data.append(self._get_guest_dict(guest))

        return data
    
    @classmethod
    def _get_guest_dict(cls, guest: vim.vm.GuestInfo.DiskInfo):
        data = {}
        data['path'] = guest.diskPath
        data['capacity'] = gigi_bytes(guest.capacity)
        data['freespace'] = gigi_bytes(guest.freeSpace)
        if value := guest.filesystemType:
            data['filesystem'] = value
        return data


class VmDisk:
    def __init__(self, device: vim.vm.device.VirtualDisk):
        self.device = device
        self.guests: list[vim.vm.GuestInfo.DiskInfo] = []
        self.guests_mapping: Literal['key','capacity'] = None

        self.capacity: int = device.capacityInBytes
        """ Device capacities. """

        self.freespace: int = 0
        """ Device freespace. This is `guests_freespace + (capacity - guests_capacity)`. """

        self.guests_capacity: int = 0
        """ Sum of guest capacities. """

        self.guests_freespace: int = 0
        """ Sum of guest freespaces. """

    def to_dict(self):
        data = {}
        data['key'] = self.device.key
        data['capacity'] = gigi_bytes(self.capacity)

        if self.guests:
            data['freespace'] = gigi_bytes(self.freespace)

        backing_typename = type(self.device.backing).__name__
        if backing_typename.startswith('vim.vm.device.VirtualDisk.'):
            backing_typename = backing_typename[len('vim.vm.device.VirtualDisk.'):]
        data['backing'] = backing_typename

        datastore: vim.Datastore = getattr(self.device.backing, 'datastore', None)
        if datastore:
            data['datastore'] = {'name': datastore.name, 'ref': get_obj_ref(datastore)}

        filename: str = getattr(self.device.backing, 'fileName', None)
        if filename:
            data['filename'] = filename

        if self.guests_mapping:
            data['guests_mapping'] = self.guests_mapping
        if self.guests:
            data['guests'] = []

            for guest in self.guests:
                data['guests'].append(VmDisks._get_guest_dict(guest))
        
        return data

#endregion


#region NICs

DEFAULT_NICS_OUT = VCenterClient.DEFAULT_OUT_DIR_MASK.joinpath('nics.xlsx#{title}' if openpyxl else 'nics-{title}.csv')


def analyze_nics(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', out: str = DEFAULT_NICS_OUT, top: int = None):
    """
    Analyze VM network interfaces.
    """
    vms_headers = [
        'vm',
        'power_state',
        'os_info',
        'os_family',
        'os_name',
        'device_networks',
        'guest_networks',
        'with_mappings',
        'without_mappings',
        'mapped_guest',
        'unmapped_guest',
        'issues',
    ]

    details_headers = [
        'vm',
        'power_state',
        'os_info',
        'os_family',
        'os_name',
        'backing',
        'network',
        'address_type',
        'key',
        'mac',
        'guests_ips',
        'guests_network_name',
    ]
    
    with (out_table(out, title='details', vcenter=vcenter.name, headers=details_headers) as t_details,
          out_table(out, title='vms', vcenter=vcenter.name, headers=vms_headers) as t_vms):
        
        for i, vm in enumerate(vcenter.get_objs(vim.VirtualMachine, search, normalize=normalize, key=key)):
            if top is not None and i == top:
                break
            
            try:
                logger.info(f"Analyze {vm.name} nics")

                info = extract_nics(vm, vcenter=vcenter)

                os = dictify_value(vm.config.extraConfig).get('guestOS.detailed.data')
                if not os:
                    os = {}
                os_family = os.get('familyName')
                os_name = os.get('prettyName')

                mapped_guests: list[vim.vm.GuestInfo.NicInfo] = []
                for nic in info.nics:
                    for guest in nic.guests:
                        mapped_guests.append(guest)

                t_vms.append([
                    vm.name, # vm
                    vm.runtime.powerState, # power_state
                    vm.config.guestFullName, # os_info
                    os_family, # os_family
                    os_name, # os_name
                    len(info.nics), # 'device_networks',
                    len(mapped_guests) + len(info.unmapped_guests), # 'guest_networks',
                    len(mapped_guests), # 'with_mappings',
                    len(info.unmapped_guests), # 'without_mappings',
                    [guest.macAddress for guest in mapped_guests], # 'mapped_guest',
                    [guest.macAddress for guest in info.unmapped_guests], # 'unmapped_guests',
                    info.issues,
                ])

                for nic in info.nics:
                    backing_typename = type(nic.device.backing).__name__
                    if backing_typename.startswith('vim.vm.device.VirtualEthernetCard.'):
                        backing_typename = backing_typename[len('vim.vm.device.VirtualEthernetCard.'):]

                    if isinstance(nic.device.backing, vim.vm.device.VirtualEthernetCard.DistributedVirtualPortBackingInfo):
                        connection_obj = nic.device.backing.port
                        network_obj = vcenter.get_portgroup_by_key(connection_obj.portgroupKey)
                        if network_obj:
                            network = network_obj.name
                        else:
                            switch_obj = vcenter.get_switch_by_uuid(connection_obj.switchUuid)
                            if switch_obj:
                                network = f"Switch {switch_obj.name} (port {connection_obj.portKey})"
                            else:
                                network = f"Switch {connection_obj.switchUuid} (port {connection_obj.portKey})"
                    elif isinstance(nic.device.backing, vim.vm.device.VirtualEthernetCard.NetworkBackingInfo):
                        network_obj = nic.device.backing.network
                        network = network_obj.name
                    else:
                        network = None

                    ip_addresses = []
                    networks = []
                    for guest in nic.guests:
                        for ip in guest.ipAddress:
                            ip_addresses.append(ip)
                        networks.append(guest.network)
                    
                    t_details.append([
                        vm.name, # vm
                        vm.runtime.powerState, # power_state
                        vm.config.guestFullName, # os_info
                        os_family, # os_family
                        os_name, # os_name
                        backing_typename, # backing
                        network,
                        nic.device.addressType, # address_type
                        nic.device.key, # key
                        nic.device.macAddress.lower(), # mac
                        ip_addresses,
                        networks,
                    ])

                for guest in info.unmapped_guests:
                    t_details.append([
                        vm.name, # vm
                        vm.runtime.powerState, # power_state
                        vm.config.guestFullName, # os_info
                        os_family, # os_family
                        os_name, # os_name
                        None, # backing
                        None, # network
                        None, # address_type
                        guest.deviceConfigId, # key
                        guest.macAddress.lower(), # mac
                        guest.ipAddress,
                        guest.network,
                    ])

            except Exception as err:
                logger.exception(f"Error while analyzing {str(vm)} nics: {err}")


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('--top', type=int)
    parser.add_argument('-o', '--out', default=DEFAULT_NICS_OUT, help="Output Excel or CSV file (default: %(default)s).")

analyze_nics.add_arguments = _add_arguments


def extract_nics(vm: vim.VirtualMachine, *, vcenter: VCenterClient):
    info = VmNics(vm)

    # Retrieve nic devices
    nics_per_key: dict[int,VmNic] = {}
    keys_to_ignore = set()
    for obj in vm.config.hardware.device:
        if not isinstance(obj, vim.vm.device.VirtualEthernetCard):
            continue

        nic = VmNic(obj, vcenter=vcenter)
        info.nics.append(nic)

        if obj.key is None:
            info._append_issue(f"Device NIC has no key: {obj}")
        elif not isinstance(obj.key, int):
            info._append_issue(f"Device NIC has a non-int key: {obj}")
        elif obj.key in nics_per_key:
            info._append_issue(f"Several NIC have key {obj.key}")
            keys_to_ignore.add(obj.key)
        else:
            nics_per_key[obj.key] = nic

    for key in keys_to_ignore:
        del nics_per_key[key]

    # Retrieve guest NICs
    # Associate them to device NICs if a consistent mapping is provided
    for obj in vm.guest.net:
        if obj.deviceConfigId and obj.deviceConfigId != -1:
            nic = nics_per_key.get(obj.deviceConfigId)
            if not nic:
                info._append_issue(f"Guest NIC {obj.macAddress} mapped to unknown device NIC key: {obj.deviceConfigId}")
                info.unmapped_guests.append(obj)
            else:
                nic.guests.append(obj)
        else:
            info.unmapped_guests.append(obj)

    # Verify consistency of mapped guests address MACs
    for nic in info.nics:
        for guest in nic.guests:
            if guest.macAddress.lower() != nic.device.macAddress.lower():
                info._append_issue(f"Inconsistent MAC address ({guest.macAddress.lower()}) for guest {guest.ipAddress} (device {nic.device.key} MAC address: {nic.device.macAddress.lower()})")

    return info


class VmNics:
    def __init__(self, vm: vim.VirtualMachine):
        self.vm = vm
        self.nics: list[VmNic] = []
        self.unmapped_guests: list[vim.vm.GuestInfo.NicInfo] = []
        self.issues: list[str] = []

    def _append_issue(self, message: str):
        logger.warning(f"{self.vm.name}: {message}")
        self.issues.append(message)

    @property
    def guests(self):
        for nic in self.nics:
            for guest in nic.guests:
                yield guest

        for guest in self.unmapped_guests:
            yield guest

    @property
    def network_names(self):
        names = set()

        for nic in self.nics:
            network = nic.network
            if isinstance(network, dict):
                if 'switch' in network:
                    network['switch'] = network['switch'].name
                name = ', '.join(f'{key}: {value}' for key, value in network.items())
            elif network:
                name = network.name
            else:
                name = None

            if name:
                names.add(name)

        return sorted(names)      
    
    def to_dict(self):
        data = []

        for disk in self.nics:
            data.append(disk.to_dict())

        if self.unmapped_guests:
            for guest in self.unmapped_guests:
                data.append({'key': guest.deviceConfigId, 'mac': guest.macAddress.lower(), 'guest':  self._get_guest_dict(guest)})

        return data
    
    @classmethod
    def _get_guest_dict(cls, guest: vim.vm.GuestInfo.NicInfo):
        data = {}
        data['connected'] = guest.connected
        data['ips'] = guest.ipAddress
        data['network_name'] = guest.network
        return data


class VmNic:
    def __init__(self, device: vim.vm.device.VirtualEthernetCard, *, vcenter: VCenterClient):
        self.vcenter = vcenter
        self.device = device
        self.guests: list[vim.vm.GuestInfo.NicInfo] = []

    @property
    def network(self):
        if isinstance(self.device.backing, vim.vm.device.VirtualEthernetCard.DistributedVirtualPortBackingInfo):
            connection_obj = self.device.backing.port
            network_obj = self.vcenter.get_portgroup_by_key(connection_obj.portgroupKey)
            if network_obj:
                return network_obj
            else:
                switch_obj = self.vcenter.get_switch_by_uuid(connection_obj.switchUuid)
                port_key = int(connection_obj.portKey) if re.match(r'^\d+$', connection_obj.portKey) else connection_obj.portKey
                if switch_obj:
                    return {'switch': switch_obj, 'port': port_key}
                else:
                    return {'switch_uuid': connection_obj.switchUuid, 'port': port_key}
        elif isinstance(self.device.backing, vim.vm.device.VirtualEthernetCard.NetworkBackingInfo):
            return self.device.backing.network
        else:
            return None

    def to_dict(self):
        data = {}
        data['key'] = self.device.key
        data['mac'] = self.device.macAddress.lower()

        # vim.vm.device.VirtualEthernetCard.NetworkBackingInfo, vim.vm.device.VirtualEthernetCard.DistributedVirtualPortBackingInfo, etc
        backing_typename = type(self.device.backing).__name__
        if backing_typename.startswith('vim.vm.device.VirtualEthernetCard.'):
            backing_typename = backing_typename[len('vim.vm.device.VirtualEthernetCard.'):]
        data['backing'] = backing_typename

        if network := self.network:
            if isinstance(network, dict) and 'switch' in dict:
                network['switch'] = {'name': network['switch'].name, 'ref': get_obj_ref(network['switch'])}
            else:
                network = {'name': network.name, 'ref': get_obj_ref(network)}
            data['network'] = network

        data['address_type'] = self.device.addressType

        if self.guests:
            data['guests'] = [VmNics._get_guest_dict(guest) for guest in self.guests]
        
        return data

#endregion
