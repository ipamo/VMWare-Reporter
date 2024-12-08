"""
Manage virtual machines.
"""
from __future__ import annotations

from contextlib import nullcontext
import json
import logging
import os
import re
from argparse import ArgumentParser
from datetime import datetime
from io import IOBase
from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path
from time import sleep
from typing import Any, Callable, Literal

from pyVmomi import vim
from zut import Header, add_command, gigi_bytes, tabular_dumper, slugify
from zut.excel import ExcelRow, ExcelWorkbook, split_excel_path

from . import (Tag, VCenterClient, dictify_obj,
               dictify_value, get_obj_name, get_obj_path, get_obj_ref, settings)
from .settings import TABULAR_OUT, OUT_DIR, EXTRACT_TAG_CATEGORIES, EXTRACT_CUSTOM_VALUES
from .pool import get_cached_pool_info

_logger = logging.getLogger(__name__)


def handle(vcenter: VCenterClient, **kwargs):
    dump_vms(vcenter, **kwargs)

def _add_arguments(parser: ArgumentParser):    
    subparsers = parser.add_subparsers(title='sub commands')
    # Dumps
    add_command(subparsers, dump_vms, name='.')
    add_command(subparsers, dump_vms_all, name='all')
    add_command(subparsers, dump_vm_disks, name='disk')
    add_command(subparsers, dump_vm_nics, name='nic')
    add_command(subparsers, dump_vm_cdroms, name='cdrom')
    # Operations
    add_command(subparsers, start_vms, name='start')
    add_command(subparsers, stop_vms, name='stop')
    add_command(subparsers, suspend_vms, name='suspend')
    add_command(subparsers, reconfigure_vms, name='reconfigure')

handle.add_arguments = _add_arguments


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output table (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

def dump_vms_all(vcenter: VCenterClient, *, per_vm = False, **kwargs):
    """
    Dump VMs with associated objects (VM disks and VM nics).
    """
    dump_vms(vcenter, **kwargs)
    dump_vm_disks(vcenter, per_vm=per_vm, **kwargs)
    dump_vm_nics(vcenter, per_vm=per_vm, **kwargs)
    dump_vm_cdroms(vcenter, **kwargs)

dump_vms_all.add_arguments = _add_arguments


#region VM

def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output table (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

def dump_vms(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', out: os.PathLike|IOBase = TABULAR_OUT, dir: os.PathLike = None):
    """
    Dump VMs.
    """
    headers = [
        'name',
        'ref',
        'overall_status',
        'config_status',
        'template',
        # Config
        'cpu_cores',
        'cpu_cores_per_socket',
        'cpu_mhz',
        'cpu_max_mhz',
        'cpu_reservation_mhz',
        'mem_mib',
        'mem_reservation_mib',
        Header('disk_capacity', fmt='gib'),
        Header('disk_freespace', fmt='gib'),
        'disk_count',
        'nic_count',
        # Dossier / rangement
        'folder',
    ]

    for cat in EXTRACT_TAG_CATEGORIES:
        headers.append(cat)

    headers += [
        'other_tags' if EXTRACT_TAG_CATEGORIES else 'tags',
    ]

    for cv in EXTRACT_CUSTOM_VALUES:
        headers.append(cv)
    
    headers += [
        'other_custom_values' if EXTRACT_CUSTOM_VALUES else 'custom_values',
        'annotation',
        'create_date',
        # Config details
        'hostname',
        'network',
        'disk',
        # ResourcePool / runtime
        'cluster',
        'pool',
        'host',
        'power_state',
        'paused',
        'connection_state',
        'boot_time',
        'hw_version',
        # Guest info
        'os_family',
        'os_version',
        'os_distro_version',
        'os_kernel_version',
        'tools_status',
        'tools_running_status',
        'tools_version_number',
        'tools_version',
        'guestinfo_publish_time',
        # Performance info
        'perf_realtime_refresh_rate',
        # Quick stats, see: https://developer.broadcom.com/xapis/virtual-infrastructure-json-api/latest/data-structures/VirtualMachineQuickStats/
        # Not available for templates and powered-off VMs
        'cpu_demand_mhz', # overallCpuDemand
        'cpu_ready_pct', # overallCpuReadiness: Percentage of time that the virtual machine was ready, but could not get scheduled to run on the physical CPU. - TODO: verify 'cpu_ready_pct' is actually percentages
        'cpu_usage_mhz', # overallCpuUsage
        'cpu_entitled_mhz', # distributedCpuEntitlement = This is the amount of CPU resource, in MHz, that this VM is entitled to, as calculated by DRS.
        'cpu_static_entitlement_mhz', # staticCpuEntitlement = The static CPU resource entitlement for a virtual machine (doesn't take into account current usage = worst case allocation for this virtual machine, if all VMs went to maximum consumption).
        'mem_guest_usage_mib', # guestMemoryUsage = also known as active guest memory
        'mem_host_usage_mib',  # hostMemoryUsage = also known as consumed host memory
        'mem_granted_mib', # grantedMemory = Amount of host physical memory that is mapped for a virtual machine, in MB.
        'mem_private_mib', # privateMemory = The portion of memory that is granted to this VM from non-shared host memory.
        'mem_shared_mib', # sharedMemory = The portion of memory that is granted to this VM from host memory that is shared between VMs.
        'mem_active_mib', # activeMemory = The amount of memory that was recently touched by the VM.
        'mem_consumed_overhead_mib', # consumedOverheadMemory = The amount of consumed overhead memory for this VM.
        'mem_swapped_mib',  # swappedMemory = The portion of memory that is granted to this VM from the host's swap space. This is a sign that there is memory pressure on the host.
        'mem_ballooned_mib', # balloonedMemory = The size of the balloon driver. The host will inflate the balloon driver to reclaim physical memory from the VM. This is a sign that there is memory pressure on the host.
        'mem_compressed_mib', # compressedMemory = The amount of compressed memory currently consumed by VM.
        'mem_entitled_mib', # distributedMemoryEntitlement = This is the amount of memory that this VM is entitled to, as calculated by DRS.
        'mem_static_entitlement_mib', # staticMemoryEntitlement = The static memory resource entitlement for a virtual machine (doesn't take into account current usage = worst case allocation for this virtual machine, if all VMs went to maximum consumption).
    ]

    objs = vcenter.get_objs(vim.VirtualMachine, search, normalize=normalize, key=key)
    objs_count = len(objs)

    perf_manager = vcenter.service_content.perfManager

    with tabular_dumper(out, title='vm', dir=dir or vcenter.data_dir, scope=vcenter.scope, headers=headers, after1970=True, truncate=True, excel=settings.CSV_EXCEL) as t:
        for i, obj in enumerate(objs):
            name = get_obj_name(obj)
            ref = get_obj_ref(obj)

            try:
                _logger.info(f"Analyze vm {i+1:,}/{objs_count:,} ({100 * (i+1)/objs_count:.0f}%): {name} ({ref})")

                summary = obj.summary
                config_summary = summary.config
                quickstats = summary.quickStats
                quickstats_available = quickstats.overallCpuReadiness != -1

                config = obj.config
                guest = obj.guest
                runtime = obj.runtime
                host = runtime.host
                        
                if obj.resourcePool: # not for templates
                    cluster_name, cpu_per_core, pool_path = get_cached_pool_info(obj.resourcePool) # ~ 5.2 ms
                else:
                    cluster_name = None
                    cpu_per_core = None
                    pool_path = None

                disks = extract_vm_disks(obj)
                nics = extract_vm_nics(obj, vcenter=vcenter)
                extra_config = dictify_value(config.extraConfig) if config is not None else {}

                row = [
                    obj.name,
                    get_obj_ref(obj),
                    obj.overallStatus,
                    obj.configStatus,
                    config_summary.template if config_summary is not None else None,
                    # Config
                    config_summary.numCpu if config_summary is not None else None,
                    config.hardware.numCoresPerSocket if config is not None else None,
                    config_summary.numCpu * cpu_per_core if config_summary is not None and cpu_per_core is not None else None, # cpu_mhz,
                    runtime.maxCpuUsage if runtime is not None else None, # cpu_max_mhz - NOTE: sometimes empty, 0 or lower than 'cpu_mhz' (for a few VMs, not only templates)
                    config_summary.cpuReservation if config_summary is not None else None,
                    config_summary.memorySizeMB if config_summary is not None else None,
                    config_summary.memoryReservation if config_summary is not None else None,
                    disks.capacity if disks is not None else None,
                    disks.freespace if disks is not None else None,
                    config_summary.numVirtualDisks,
                    config_summary.numEthernetCards,
                    # Dossier / rangement
                    get_obj_path(obj.parent),
                ]

                tags = vcenter.get_obj_tags(obj)
                for category_name in EXTRACT_TAG_CATEGORIES:
                    category_tags: list[Tag] = []
                    for tag in tags:
                        if tag.category.name == category_name:
                            category_tags.append(tag)
                    for tag in category_tags:
                        tags.remove(tag)
                    row.append([tag.name for tag in category_tags])

                # other tags
                row.append([f"{tag.name} ({tag.category.name})" for tag in tags])
                
                custom_values = get_custom_values(obj)
                for cv in EXTRACT_CUSTOM_VALUES:
                    row.append(custom_values.pop(cv, None))
                other_custom_values = ' | '.join(f"{key}: {value}" for key, value in custom_values.items())

                # Perf
                perf_provider_summary = perf_manager.QueryProviderSummary(obj)
                perf_realtime_refresh_rate = perf_provider_summary.refreshRate if perf_provider_summary.currentSupported else None

                row += [
                    other_custom_values,
                    config_summary.annotation if config_summary is not None else None,
                    config.createDate if config is not None else None,
                    # Config details
                    guest.hostName,
                    nics.to_summary(ip_version=4) if nics is not None else None,
                    disks.to_summary() if disks is not None else None,
                    # ResourcePool / runtime
                    cluster_name,
                    pool_path,
                    host.name if host is not None else None,
                    runtime.powerState,
                    runtime.paused,
                    runtime.connectionState,
                    runtime.bootTime,
                    config.version if config is not None else None,
                    # Guest info
                    get_os_family(extra_config, config_summary.guestFullName) if config_summary is not None else None,
                    get_os_version(extra_config),
                    get_os_distro_version(extra_config),
                    get_os_kernel_version(extra_config),
                    guest.toolsStatus,  # nominal: toolsOk
                    guest.toolsRunningStatus,  # nominal: guestToolsRunning
                    get_tools_version_number(guest.toolsVersion),  # version number
                    get_tools_version(extra_config, guest.toolsVersion),      
                    get_guestinfo_publish_time(extra_config),
                    # Perf
                    perf_realtime_refresh_rate,
                    # Quick stats
                    quickstats.overallCpuDemand if quickstats_available else None,
                    quickstats.overallCpuReadiness if quickstats_available else None,
                    quickstats.overallCpuUsage if quickstats_available else None,
                    quickstats.distributedCpuEntitlement if quickstats_available else None,
                    quickstats.staticCpuEntitlement if quickstats_available else None,
                    quickstats.guestMemoryUsage if quickstats_available else None,
                    quickstats.hostMemoryUsage if quickstats_available else None,
                    quickstats.grantedMemory if quickstats_available else None,
                    quickstats.privateMemory if quickstats_available else None,
                    quickstats.sharedMemory if quickstats_available else None,
                    quickstats.activeMemory if quickstats_available else None,
                    quickstats.consumedOverheadMemory if quickstats_available else None,
                    quickstats.swappedMemory if quickstats_available else None,
                    quickstats.balloonedMemory if quickstats_available else None,
                    quickstats.compressedMemory if quickstats_available else None,
                    quickstats.distributedMemoryEntitlement if quickstats_available else None,
                    quickstats.staticMemoryEntitlement if quickstats_available else None,
                ]
                
                t.append(row)
            
            except:
                _logger.exception(f"Error while analyzing host {name} ({ref})")

dump_vms.add_arguments = _add_arguments

#endregion


#region Disks

def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('--per-vm', action='store_true', help="Make a second output with the summary for each VM (one row per VM).")
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output Excel or CSV file (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

def dump_vm_disks(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', detailed = True, per_vm = False, out: str = TABULAR_OUT, dir: os.PathLike = None):
    """
    Dump VM disks.
    """
    disks_per_vm_headers = [
        'vm',
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

    disks_headers = [
        'vm',
        'key',
        'backing',
        'datastore',
        'filename',
        'diskmode',
        'sharing',
        'thin_provisioned',
        'compatibility_mode',
        'identification',
        'remaining_backing_info',
        Header('capacity', fmt='gib'),
        Header('freespace', fmt='gib'),
        'mapping',
        'guests',
        Header('guests_capacity', fmt='gib'),
        Header('guests_freespace', fmt='gib'),
    ]
    
    with (tabular_dumper(out, title='vm_disk', headers=disks_headers, dir=dir or vcenter.data_dir, scope=vcenter.scope, truncate=True, excel=settings.CSV_EXCEL) if detailed else nullcontext() as t_detailed,
          tabular_dumper(out, title='vm_disk_per_vm', headers=disks_per_vm_headers, dir=dir or vcenter.data_dir, scope=vcenter.scope, truncate=True, excel=settings.CSV_EXCEL) if per_vm else nullcontext() as t_per_vm):
        
        for i, vm in enumerate(vcenter.iter_objs(vim.VirtualMachine, search, normalize=normalize, key=key)):
            try:
                _logger.info(f"Analyze vm {vm.name} disks")

                info = extract_vm_disks(vm)

                mapped_guests: list[vim.vm.GuestInfo.DiskInfo] = []
                if info is not None:
                    for disk in info.disks:
                        for guest in disk.guests:
                            mapped_guests.append(guest)

                if per_vm:
                    t_per_vm.append([
                        vm.name, # vm
                        len(info.disks) if info is not None else None, # 'device_disks',
                        len(mapped_guests) + len(info.unmapped_guests) if info is not None else None, # 'guest_disks',
                        len(mapped_guests) if info is not None else None, # 'with_mappings',
                        len(info.unmapped_guests) if info is not None else None, # 'without_mappings',
                        sorted((guest.diskPath.rstrip(':\\') + f" ({guest.filesystemType})") if guest.filesystemType else guest.diskPath.rstrip(':\\') for guest in mapped_guests) if info is not None else None, # 'mapped_guest',
                        sorted((guest.diskPath.rstrip(':\\') + f" ({guest.filesystemType})") if guest.filesystemType else guest.diskPath.rstrip(':\\') for guest in info.unmapped_guests) if info is not None else None, # 'unmapped_guests',
                        info.capacity if info is not None else None,
                        info.freespace if info is not None else None,
                        info.mapped_disks_capacity if info is not None and mapped_guests else None,
                        info.mapped_guests_capacity if info is not None and mapped_guests else None,
                        info.mapped_guests_freespace if info is not None and mapped_guests else None,
                        info.unmapped_disks_capacity if info is not None and info.unmapped_disks_capacity > 0 else None,
                        info.unmapped_guests_capacity if info is not None and info.unmapped_guests else None,
                        info.unmapped_guests_freespace if info is not None and info.unmapped_guests else None,
                        info.issues if info is not None else None,
                    ])

                if detailed and info is not None:
                    for disk in info.disks:
                        device_backing = dictify_obj(disk.device.backing)
                        backing_typename = type(disk.device.backing).__name__
                        if backing_typename.startswith('vim.vm.device.VirtualDisk.'):
                            backing_typename = backing_typename[len('vim.vm.device.VirtualDisk.'):]
                        datastore = device_backing.pop('datastore', None)
                        fileName = device_backing.pop('fileName', None)
                        diskMode = device_backing.pop('diskMode', None)
                        sharing = device_backing.pop('sharing', None)
                        thinProvisioned = device_backing.pop('thinProvisioned', None)
                        compatibilityMode = device_backing.pop('compatibilityMode', None)
                        
                        identification = {}
                        if value := device_backing.pop('uuid', None):
                            identification['uuid'] = value
                        if value := device_backing.pop('contentId', None):
                            identification['contentId'] = value
                        if value := device_backing.pop('changeId', None):
                            identification['changeId'] = value
                        if value := device_backing.pop('parent', None):
                            identification['parent'] = value
                        if value := device_backing.pop('deviceName', None): # Raw
                            identification['deviceName'] = value
                        if value := device_backing.pop('lunUuid', None): # Raw
                            identification['lunUuid'] = value
                        
                        t_detailed.append([
                            vm.name, # vm
                            disk.device.key, # key
                            backing_typename, # backing
                            datastore['name'] if datastore else None, # datastore
                            fileName,
                            diskMode,
                            sharing,
                            thinProvisioned,
                            compatibilityMode,
                            identification,
                            device_backing, # remaining_backing_info
                            disk.capacity,
                            disk.freespace,
                            disk.guests_mapping,
                            sorted((guest.diskPath.rstrip(':\\') + f" ({guest.filesystemType})") if guest.filesystemType else guest.diskPath.rstrip(':\\') for guest in disk.guests), # guests
                            disk.guests_capacity if disk.guests else None,
                            disk.guests_freespace if disk.guests else None,
                        ])

                    for guest in [*info.unmapped_guests, *info.ignored_guests]:
                        t_detailed.append([
                            vm.name, # vm
                            None, # key
                            guest.mappings, # backing
                            None, # datastore
                            None, # filename
                            None, # diskMode
                            None, # sharing
                            None, # thin_provisioned
                            None, # compatibility_mode
                            None, # identification'
                            None, # remaining_backing_info
                            None, # capacity
                            None, # freespace
                            'ignored' if guest in info.ignored_guests else 'unmapped', # mapping
                            (guest.diskPath.rstrip(':\\') + f" ({guest.filesystemType})") if guest.filesystemType else guest.diskPath.rstrip(':\\'), # guests
                            guest.capacity, # guests_capacity
                            guest.freeSpace, # guests_freespace
                        ])

            except Exception as err:
                _logger.exception(f"Error while analyzing {str(vm)} disks: {err}")

dump_vm_disks.add_arguments = _add_arguments


def extract_vm_disks(vm: vim.VirtualMachine):
    config = vm.config
    if config is None:
        # VM is not yet configured
        return None
    
    info = VmDisks(vm)

    # Retrieve disk devices
    disks_per_key: dict[int,VmDisk] = {}
    keys_to_ignore = set()
    for obj in config.hardware.device:
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
                guest_list = ', '.join(guest.diskPath.rstrip(':\\') for guest in disk.guests)
                info._append_issue(f"Inconsistent capacity ({disk.guests_capacity:,}) for guests {guest_list} (device {disk.device.key} capacity: {disk.capacity:,})")

    # Identify guests to ignore
    info._identify_ignored_guests()
    
    # Try to map remaining guests automatically
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
                disk.guests_mapping = 'auto'

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
            info.unmapped_disks_freespace = int(info.unmapped_guests_freespace * (info.unmapped_disks_capacity / info.unmapped_guests_capacity))
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
        _logger.warning(f"{self.vm.name}: {message}")
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

    def to_dict(self, bytes=False):
        data = []

        for disk in self.disks:
            data.append(disk.to_dict(bytes=bytes))

        if self.unmapped_guests:
            guests_data = []
            data.append({'guests_mapping': 'unmapped', 'guests':  guests_data})
            for guest in self.unmapped_guests:
                guests_data.append(self._get_guest_dict(guest, bytes=bytes))

        if self.ignored_guests:
            guests_data = []
            data.append({'guests_mapping': 'ignored', 'guests':  guests_data})
            for guest in self.ignored_guests:
                guests_data.append(self._get_guest_dict(guest, bytes=bytes))

        return data
    
    def to_summary(self):
        result = ''

        for disk in self.disks:
            result += (' | ' if result else '') + disk.to_summary()

        if self.unmapped_guests:
            result += (' | ' if result else '') + f"Unmapped guests ("
            for i, guest in enumerate(self.unmapped_guests):
                if i > 0:
                    result += ', '
                result += self._get_guest_summary(guest)
            result += ')'

        return result
    
    @classmethod
    def _get_guest_dict(cls, guest: vim.vm.GuestInfo.DiskInfo, bytes=False):
        data = {}
        data['path'] = guest.diskPath
        data['capacity'] = guest.capacity if bytes else gigi_bytes(guest.capacity)
        data['freespace'] = guest.freeSpace if bytes else gigi_bytes(guest.freeSpace)
        if value := guest.filesystemType:
            data['filesystem'] = value
        return data
    
    @classmethod
    def _get_guest_summary(cls, guest: vim.vm.GuestInfo.DiskInfo):
        path = guest.diskPath.rstrip(':\\')
        return f'{path}: {gigi_bytes(guest.freeSpace):.1f}/{gigi_bytes(guest.capacity):.1f} GiB'


class VmDisk:
    def __init__(self, device: vim.vm.device.VirtualDisk):
        self.device = device
        self.guests: list[vim.vm.GuestInfo.DiskInfo] = []
        self.guests_mapping: Literal['key','auto'] = None

        self.capacity: int = device.capacityInBytes
        """ Device capacities. """

        self.freespace: int = 0
        """ Device freespace. This is `guests_freespace + (capacity - guests_capacity)`. """

        self.guests_capacity: int = 0
        """ Sum of guest capacities. """

        self.guests_freespace: int = 0
        """ Sum of guest freespaces. """

    def to_dict(self, bytes=False):
        data = {}
        data['key'] = self.device.key
        data['capacity'] = self.capacity if bytes else gigi_bytes(self.capacity)

        if self.guests:
            data['freespace'] = self.freespace if bytes else gigi_bytes(self.freespace)

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
                data['guests'].append(VmDisks._get_guest_dict(guest, bytes=bytes))
        
        return data
    
    def to_summary(self):
        filename: str = getattr(self.device.backing, 'fileName', None)
        if filename:
            identifier = filename
        else:
            identifier = f'#{self.device.key}'

        result = f'{identifier}: {gigi_bytes(self.freespace):.1f}/{gigi_bytes(self.capacity):.1f} GiB'

        if self.guests:
            result += f' ('
            for i, guest in enumerate(self.guests):
                if i > 0:
                    result += ', '
                result += VmDisks._get_guest_summary(guest)
            result += ')'
        return result

#endregion


#region NICs

def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output Excel or CSV file (default: %(default)s).")
    parser.add_argument('--per-vm', action='store_true', help="Make a second output with the summary for each VM (one row per VM).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

def dump_vm_nics(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', detailed = True, per_vm = False, out: str = TABULAR_OUT, dir: os.PathLike = None):
    """
    Dump VM network interfaces.
    """
    nics_per_vm_headers = [
        'vm',
        'device_networks',
        'guest_networks',
        'with_mappings',
        'without_mappings',
        'mapped_guest',
        'unmapped_guest',
        'issues',
    ]

    nics_headers = [
        'vm',
        'backing',
        'network',
        'address_type',
        'key',
        'mac',
        'guests_ipv4',
        'guests_ipv6',
        'guests_network_name',
    ]
    
    with (tabular_dumper(out, title='vm_nic', headers=nics_headers, dir=dir or vcenter.data_dir, scope=vcenter.scope, truncate=True, excel=settings.CSV_EXCEL) if detailed else nullcontext() as t_detailed,
          tabular_dumper(out, title='vm_nic_per_vm', headers=nics_per_vm_headers, dir=dir or vcenter.data_dir, scope=vcenter.scope, truncate=True, excel=settings.CSV_EXCEL) if per_vm else nullcontext() as t_per_vm):
        
        for i, vm in enumerate(vcenter.iter_objs(vim.VirtualMachine, search, normalize=normalize, key=key)):
            try:
                _logger.info(f"Analyze vm {vm.name} nics")

                info = extract_vm_nics(vm, vcenter=vcenter)

                mapped_guests: list[vim.vm.GuestInfo.NicInfo] = []
                if info is not None:
                    for nic in info.nics:
                        for guest in nic.guests:
                            mapped_guests.append(guest)

                if per_vm:
                    t_per_vm.append([
                        vm.name, # vm
                        len(info.nics) if info is not None else None, # 'device_networks',
                        len(mapped_guests) + len(info.unmapped_guests) if info is not None else None, # 'guest_networks',
                        len(mapped_guests) if info is not None else None, # 'with_mappings',
                        len(info.unmapped_guests) if info is not None else None, # 'without_mappings',
                        [guest.macAddress for guest in mapped_guests] if info is not None else None, # 'mapped_guest',
                        [guest.macAddress for guest in info.unmapped_guests] if info is not None else None, # 'unmapped_guests',
                        info.issues if info is not None else None,
                    ])

                if detailed and info is not None:
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

                        ipv4 = []
                        ipv6 = []
                        networks = []
                        for guest in nic.guests:
                            for ip in guest.ipAddress:
                                ip = ip_address(ip)
                                if ip.version == 4:
                                    ipv4.append(ip)
                                else:
                                    ipv6.append(ip)
                            networks.append(guest.network)
                        
                        t_detailed.append([
                            vm.name, # vm
                            backing_typename, # backing
                            network,
                            nic.device.addressType, # address_type
                            nic.device.key, # key
                            nic.device.macAddress.lower(), # mac
                            ipv4,
                            ipv6,
                            networks,
                        ])

                    for guest in info.unmapped_guests:
                        ipv4 = []
                        ipv6 = []
                        for ip in guest.ipAddress:
                            ip = ip_address(ip)
                            if ip.version == 4:
                                ipv4.append(ip)
                            else:
                                ipv6.append(ip)

                        t_detailed.append([
                            vm.name, # vm
                            None, # backing
                            None, # network
                            None, # address_type
                            guest.deviceConfigId, # key
                            guest.macAddress.lower(), # mac
                            ipv4,
                            ipv6,
                            guest.network,
                        ])

            except Exception as err:
                _logger.exception(f"Error while analyzing {str(vm)} nics: {err}")

dump_vm_nics.add_arguments = _add_arguments


def extract_vm_nics(vm: vim.VirtualMachine, *, vcenter: VCenterClient):
    config = vm.config
    if config is None:
        # VM is not yet configured
        return None
    
    info = VmNics(vm)

    # Retrieve nic devices
    nics_per_key: dict[int,VmNic] = {}
    keys_to_ignore = set()
    for obj in config.hardware.device:
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
        _logger.warning(f"{self.vm.name}: {message}")
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
            name = nic.network_name
            if name:
                names.add(name)

        return sorted(names)      
    
    def to_dict(self):
        data = []

        for nic in self.nics:
            data.append(nic.to_dict())

        if self.unmapped_guests:
            for guest in self.unmapped_guests:
                data.append({'key': guest.deviceConfigId, 'mac': guest.macAddress.lower(), 'guest':  self._get_guest_dict(guest)})

        return data
    
    def to_summary(self, ip_version: int = None):
        result = ''
        
        for nic in self.nics:
            result += (' | ' if result else '') + nic.to_summary(ip_version=ip_version)

        if self.unmapped_guests:
            for guest in self.unmapped_guests:
                result += (' | ' if result else '') + f"unmapped {guest.macAddress.lower()}: {guest.network} (" + VmNics._get_guest_summary(guest, ip_version=ip_version) + ")"

        return result
    
    @classmethod
    def _get_guest_dict(cls, guest: vim.vm.GuestInfo.NicInfo):
        data = {}
        data['ips'] = guest.ipAddress
        data['connected'] = guest.connected
        data['network_name'] = guest.network
        return data
    
    @classmethod
    def _get_guest_summary(cls, guest: vim.vm.GuestInfo.NicInfo, ip_version: int = None):        
        ip_addresses: list[IPv4Address|IPv6Address] = []
        for ip_str in guest.ipAddress:
            ip = ip_address(ip_str)
            if ip_version is None or ip.version == ip_version:
                ip_addresses.append(ip)

        ip_addresses.sort()
                
        result = ''
        for ip in ip_addresses:
            result += (', ' if result else '') + ip.compressed
        result += (', ' if result else '') + ('connected' if guest.connected else 'notConnected')
        return result


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
                port_key = int(connection_obj.portKey) if connection_obj.portKey is not None and re.match(r'^\d+$', connection_obj.portKey) else connection_obj.portKey
                if switch_obj:
                    return {'switch': switch_obj, 'port': port_key}
                else:
                    return {'switch_uuid': connection_obj.switchUuid, 'port': port_key}
        elif isinstance(self.device.backing, vim.vm.device.VirtualEthernetCard.NetworkBackingInfo):
            return self.device.backing.network
        else:
            return None
        
    @property
    def network_name(self):
        network = self.network
        if not network:
            return None
        elif isinstance(network, (vim.dvs.DistributedVirtualPortgroup, vim.Network)):
            return network.name
        elif isinstance(network, dict):
            result = ''
            for key, value in network.items():
                if isinstance(value, vim.ManagedEntity):
                    value = value.name
                result += (', ' if result else '') + f"{key}: {value}"
            return str(network)
        else:
            return str(network)

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
    
    def to_summary(self, ip_version: int = None):
        result = f"{self.device.macAddress.lower()}: {self.network_name}"
        if self.guests:
            result += " ("
            for i, guest in enumerate(self.guests):
                if i > 0:
                    result += ', '
                result += VmNics._get_guest_summary(guest, ip_version=ip_version)
            result += ")"
        return result

#endregion


#region ISO files

def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output table (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

def dump_vm_cdroms(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', out: str = TABULAR_OUT, dir: os.PathLike = None):
    """
    Dump VM virtual CD-ROM devices.
    """
    headers = [
        'vm',
        'key',
        'status',
        'connected',
        'start_connected',
        'backing',
        'target',
    ]

    with tabular_dumper(out, title='vm_cdrom', headers=headers, dir=dir or vcenter.data_dir, scope=vcenter.scope, truncate=True, excel=settings.CSV_EXCEL) as t:
        for vm in vcenter.get_objs(vim.VirtualMachine, search, normalize=normalize, key=key):
            _logger.info(f"Analyze VM {vm.name} cdroms")

            for dev in vm.config.hardware.device:
                if isinstance(dev, vim.vm.device.VirtualCdrom):
                    backing_typename = type(dev.backing).__name__
                    if backing_typename.startswith('vim.vm.device.VirtualCdrom.'):
                        backing_typename = backing_typename[len('vim.vm.device.VirtualCdrom.'):]

                    if isinstance(dev.backing, vim.vm.device.VirtualCdrom.IsoBackingInfo):
                        target = dev.backing.fileName
                    elif isinstance(dev.backing, (vim.vm.device.VirtualCdrom.PassthroughBackingInfo,vim.vm.device.VirtualCdrom.RemotePassthroughBackingInfo,vim.vm.device.VirtualCdrom.AtapiBackingInfo,vim.vm.device.VirtualCdrom.RemoteAtapiBackingInfo)):
                        target = dev.backing.deviceName
                    else:
                        target = None
                        
                    t.append([
                        vm.name,
                        dev.key,
                        dev.connectable.status,
                        dev.connectable.connected,
                        dev.connectable.startConnected,
                        backing_typename,
                        target if target else None,
                    ])
            
dump_vm_cdroms.add_arguments = _add_arguments


#endregion


#region Start, stop, suspend, reconfigure

_DEFAULT_START_BATCH = 10
_DEFAULT_BATCH_PAUSE = 60.0

def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='+', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('-b', '--batch', type=int, default=_DEFAULT_START_BATCH, help="Batch size: maximum number of VMs to start at the same time (default: %(default)s).")
    parser.add_argument('--batch-pause', type=float, default=_DEFAULT_BATCH_PAUSE, help="Batch pause: duration of pauses between batchs, in seconds (default: %(default)s).")

def start_vms(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern, *, normalize: bool = False, key: str = 'name', batch: int = _DEFAULT_START_BATCH, batch_pause: float = _DEFAULT_BATCH_PAUSE):
    """
    Start VMs.
    """
    _invoke_and_track('PowerOn', 'poweredOn', vcenter, search, normalize=normalize, key=key, batch=batch, batch_pause=batch_pause)

start_vms.add_arguments = _add_arguments


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='+', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('-f', '--force', action='store_true', help="Force (use PowerOff operation instead of ShutdownGuest).")

def stop_vms(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern, *, normalize: bool = False, key: str = 'name', force: bool = False):
    """
    Stop VMs.
    """
    if force:
        _invoke_and_track('PowerOff', 'poweredOff', vcenter, search, normalize=normalize, key=key)
    else:
        _invoke_and_track('ShutdownGuest', 'poweredOff', vcenter, search, normalize=normalize, key=key)

stop_vms.add_arguments = _add_arguments


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='+', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('-f', '--force', action='store_true', help="Force (use Suspend operation instead of StandbyGuest).")

def suspend_vms(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern, *, normalize: bool = False, key: str = 'name', force: bool = False):
    """
    Suspend VMs.
    """
    if force:
        _invoke_and_track('Suspend', 'suspended', vcenter, search, normalize=normalize, key=key)
    else:
        _invoke_and_track('StandbyGuest', 'suspended', vcenter, search, normalize=normalize, key=key)

suspend_vms.add_arguments = _add_arguments


def _invoke_and_track(op: str, target_status: str, vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern, *, normalize: bool = False, key: str = 'name', batch: int = None, batch_pause: float = _DEFAULT_BATCH_PAUSE):
    # Idea: https://github.com/reubenur-rahman/vmware-pyvmomi-examples/blob/master/vm_power_ops.py
    task_helpers: dict[vim.Task,ReportHelper] = {}
    
    def task_success_callback(task):
        helper = task_helpers[task]
        if helper.row:
            helper.row['status'] = 'SUCCESS'
            helper.row['details'] = None

    def task_error_callback(task, details):
        helper = task_helpers[task]
        if helper.row:
            helper.row['status'] = 'ERROR'
            helper.row['details'] = details

    tasks: dict[vim.VirtualMachine,str] = {}
    loop_objs: dict[vim.VirtualMachine,ReportHelper] = {}
    for i, vm in enumerate(vcenter.iter_objs(vim.VirtualMachine, search, normalize=normalize, key=key)):
        if batch is not None and i > 0 and i % batch == 0:
            _logger.info(f"Handled {i} VMs, wait for {batch_pause} seconds")
            sleep(batch_pause)

        log_prefix = f"{vm.name} ({get_obj_ref(vm)})"
        _logger.info(f"{op} {log_prefix}...")
        func = getattr(vm, op)
        helper = ReportHelper(log_prefix=log_prefix)
        try:
            task = func()
            if task is not None:
                tasks[task] = helper.log_prefix
                task_helpers[task] = helper
            else:
                loop_objs[vm] = helper
        except vim.fault.InvalidPowerState as err:
            helper.error(f"Invalid current state \"{err.existingState}\" (expected \"{err.requestedState}\")")
        except vim.fault.ToolsUnavailable as err:
            helper.error(f"VMWare Tools is not running")
        except Exception as err:
            helper.error(f"{err}")

    if tasks:
        vcenter.wait_for_task(tasks, success_callback=task_success_callback, error_callback=task_error_callback)

    total_seconds = 60
    remaining_seconds = total_seconds
    while len(loop_objs) > 0 and remaining_seconds > 0:
        if remaining_seconds % 10 == 0:
            _logger.info("Wait %s entities to reach status %s (%s seconds max)", len(loop_objs), target_status, remaining_seconds)
        sleep(1)

        vms_to_remove = []
        for vm, helper in loop_objs.items():
            if vm.runtime.powerState == target_status:
                helper.success()
                vms_to_remove.append(vm)

        for vm in vms_to_remove:
            del loop_objs[vm]

        remaining_seconds -= 1

    for vm, helper in loop_objs.items():
        helper.error(f"VM still not {target_status} after {total_seconds} seconds")


DEFAULT_RECONFIGURE_FILE = 'vm_reconfigure.xlsx'
DEFAULT_RECONFIGURE_TABLE = 'vm'

def _add_arguments(parser: ArgumentParser):
    parser.add_argument('path', nargs='?', default=DEFAULT_RECONFIGURE_FILE, help="Path to Excel file describing VMs to reconfigure (default: %(default)s).")
    parser.add_argument('--top', type=int, help="Stop after the given number of VMs (usefull for tests).")
    parser.add_argument('--ignore-invalid-current', action='store_true')
    parser.add_argument('--dir', help=f"Directory of Excel file describing VMs to reconfigure (default: {OUT_DIR}).")

def reconfigure_vms(vcenter: VCenterClient, path: str|Path = DEFAULT_RECONFIGURE_FILE, *, top: int = None, ignore_invalid_current: bool = False, dir = None):
    """
    Reconfigure VMs (vcpu, memory, annotation, custom fields) as a mass operation, using an Excel file.
    """
      
    class FieldDef:
        def __init__(self, name: str, type: type, getter: Callable[[vim.VirtualMachine],Any]|Literal['__customvalue__'], target_key:str|int, target_formatter: Callable[[vim.VirtualMachine],Any] = None):
            """
            - `key`: if str, this is the reconfigure ConfigSpec argument to use. If int, this is the custom_value key to use.
            """
            self.name = name
            self.type = type
            self.getter = getter
            self.target_key = target_key
            self.target_formatter = target_formatter

        def get_from_vm(self, vm: vim.VirtualMachine):
            if self.getter == '__customvalue__':
                for kv in vm.customValue:
                    if kv.key == self.target_key:
                        return kv.value
            else:
                return self.getter(vm)

        def get_from_row(self, row: ExcelRow, suffix: str = None):
            value = row.get(f"{self.name}_{suffix}" if suffix else self.name)
            if value is None or value == '':
                return None
            if self.type == str and value == '-':
                return ''
            return self.type(value)
        
        def format_for_target(self, value):
            if not self.target_formatter:
                return value
            return self.target_formatter(value)

    fielddefs: list[FieldDef] = [
        FieldDef('vcpu', int, lambda vm: vm.config.hardware.numCPU, 'numCPUs'),
        FieldDef('memory', float, lambda vm: vm.config.hardware.memoryMB / 1024.0, 'memoryMB', lambda val: int(val * 1024.0)),
        FieldDef('annotation', str, lambda vm: vm.config.annotation, 'annotation'),
    ]

    for cf in vcenter.service_content.customFieldsManager.field:
        if cf.managedObjectType == vim.VirtualMachine:
            fielddefs.append(FieldDef(cf.name, str, '__customvalue__', cf.key))
    
    path, tablename = split_excel_path(path, default_table_name=DEFAULT_RECONFIGURE_TABLE, dir=dir or vcenter.data_dir, scope=vcenter.scope)

    workbook = ExcelWorkbook(path)
    table = workbook.get_or_create_table(tablename)
    
    helpers: dict[str,ReportHelper] = {}
    search_by_ref = 'ref' in table.column_names
    for row in table:
        if search_by_ref:
            if row['ref']:
                if row.get('status') == 'IGNORE':
                    _logger.info(f"Ignore {row['ref']}")
                    continue
                helpers[row['ref']] = ReportHelper(row=row)
        else:
            if row['name']:
                if row.get('status') == 'IGNORE':
                    _logger.info(f"Ignore {row['name']}")
                    continue
                helpers[slugify(row['name'])] = ReportHelper(row=row)
        row['status'] = None
        row['details'] = None
    
    n = 0
    for vm in vcenter.iter_objs(vim.VirtualMachine):
        if top is not None and n == top:
            _logger.warning(f"Stop after {n} VMs")
            break

        key = get_obj_ref(vm) if search_by_ref else slugify(vm.name)
        helper = helpers.get(key)
        if not helper:
            continue
        
        if helper.found:
            _logger.error(f"VM {vm.name}: ignore (several VMs with key \"{key}\")")
            continue
        helper.found = True

        n += 1        
        _logger.info(f"Handle {vm.name} ({get_obj_ref(vm)})")
        row = helper.row

        try:
            if search_by_ref and slugify(vm.name) != slugify(row['name']):
                helper.error(f"Invalid VM name: expected {vm.name}")
                continue

            if vm.config.template:
                helper.error(f"This is a template")
                continue

            if vm.runtime.powerState != 'poweredOff':
                helper.error(f"Not powered off")
                continue

            config_previous: dict[str|int,Any] = {}
            config_targets: dict[str|int,Any] = {}
            invalid_current = ''

            # Prepare reconfiguration
            change_version = vm.config.changeVersion  # used to guard against updates that have happened between when configInfo is read and when it is applied

            for fielddef in fielddefs:
                target = fielddef.get_from_row(row, 'target')
                if target is None:
                    continue

                current = fielddef.get_from_vm(vm)
                current_expected = fielddef.get_from_row(row)
                if current_expected is not None:
                    if current_expected != current and not ignore_invalid_current:
                        invalid_current += (', ' if invalid_current else '') + f"current {fielddef.name}: {'(empty)' if current == '' else current}"   
                        continue

                if target != current:
                    config_previous[fielddef.target_key] = fielddef.format_for_target(current)
                    config_targets[fielddef.target_key] = fielddef.format_for_target(target)

            if invalid_current:
                helper.error(f"Invalid {invalid_current}")
                continue

            if not config_targets:
                helper.success("Nothing to do")
                continue

            config_targets_reconfigure = {}
            config_targets_customfields = {}
            for key, value in config_targets.items():
                if isinstance(key, str):
                    config_targets_reconfigure[key] = value
                else:
                    config_targets_customfields[key] = value

            # Perform the reconfiguration
            if config_targets_reconfigure:
                _logger.debug("Reconfigure VM: %s", config_targets_reconfigure)
                configspec = vim.vm.ConfigSpec(**config_targets_reconfigure, changeVersion=change_version)
                task = vm.ReconfigVM_Task(configspec)
                vcenter.wait_for_task(task)

            for key, value in config_targets_customfields.items():
                _logger.debug("Set custom field %s: %s", key, value)
                vcenter.service_content.customFieldsManager.SetField(vm, key, value)

            # Verification
            success = True
            details = ''

            for fielddef in fielddefs:
                if not fielddef.target_key in config_targets:
                    continue # field not modified

                previous_value = config_previous[fielddef.target_key]
                target_value = config_targets[fielddef.target_key]
                new_value = fielddef.format_for_target(fielddef.get_from_vm(vm))
                details += (', ' if details else '') + f"{fielddef.target_key}: {'(empty)' if previous_value == '' else previous_value} -> {'(empty)' if new_value == '' else new_value}"

                if new_value != target_value:
                    success = False
                    details += ' [ERROR]'

            if success:
                helper.success(details)
            else:
                helper.error(details)
        
        except Exception as err:
            _logger.exception(str(err))
            row['status'] = type(err).__name__
            row['details'] = str(err)

    # Report not found
    for key, helper in helpers.items():
        if not helper.found:
            helper.error(f"VM {key} not found")

    workbook.close()

reconfigure_vms.add_arguments = _add_arguments


class ReportHelper:
    def __init__(self, *, log_prefix: str, row: ExcelRow = None, found: bool = False):
        self.log_prefix = log_prefix
        self.row = row
        self.found = found

    def success(self, details: str = None):
        _logger.info((f"{self.log_prefix }: " if self.log_prefix else '') + f"SUCCESS{f': {details}' if details else ''}")
        if self.row:
            self.row['status'] = 'SUCCESS'
            self.row['details'] = details

    def error(self, details: str):
        _logger.error((f"{self.log_prefix }: " if self.log_prefix else '') + f"{details}")
        if self.row:
            self.row['status'] = 'ERROR'
            self.row['details'] = details

#endregion


def get_tools_version_number(toolsversion: str):
    if toolsversion is None:
        return None
    elif re.match(r'^\d+$', toolsversion):
        toolsversion = int(toolsversion)
        if toolsversion == 0 or toolsversion == 2147483647:
            return None
        return toolsversion    
    elif toolsversion:
        return str(toolsversion)
    else:
        return None


def get_os_family(extra_config: dict, configured_fullname: str):
    def get_family_from_configured(fullname: str):
        if m := re.match(r'^(.+) \(\d\d\-bit\)$', fullname):
            fullname = m[1]
            
        lower_fullname = fullname.lower()
        if 'windows' in lower_fullname:
            return "Windows"
        elif 'linux' in lower_fullname or 'centos' in lower_fullname:
            return "Linux"
        elif lower_fullname == 'other':
            return None
        else:
            return fullname
      
    if 'guestInfo.detailed.data' in extra_config:
        line = extra_config['guestInfo.detailed.data']
        if m := re.match(r".*familyName='([^']+)'.*", line):
            return m[1]
    elif 'guestOS.detailed.data' in extra_config:
        data = extra_config.get("guestOS.detailed.data")
        if isinstance(data, dict) and 'familyName' in data:
            return data['familyName']

    if configured_fullname:
        return get_family_from_configured(configured_fullname)


def get_os_version(extra_config: dict):
    if 'guestInfo.detailed.data' in extra_config:
        line = extra_config['guestInfo.detailed.data']
        if m := re.match(r".*prettyName='([^']+)'.*", line):
            return m[1]
        else:
            return line
    elif 'guestOS.detailed.data' in extra_config:
        data = extra_config.get("guestOS.detailed.data")
        if isinstance(data, dict) and 'prettyName' in data:
            return data['prettyName']
        else:
            return str(data)
    else:
        return None


def get_os_distro_version(extra_config: dict):
    if 'guestInfo.detailed.data' in extra_config:
        line = extra_config['guestInfo.detailed.data']
        if m := re.match(r".*distroVersion='([^']+)'.*", line):
            return m[1]
    if 'guestOS.detailed.data' in extra_config:
        data = extra_config.get("guestOS.detailed.data")
        if isinstance(data, dict) and 'distroVersion' in data:
            return data['distroVersion']


def get_os_kernel_version(extra_config: dict):
    if 'guestInfo.detailed.data' in extra_config:
        line = extra_config['guestInfo.detailed.data']
        if m := re.match(r".*kernelVersion='([^']+)'.*", line):
            return m[1]
    if 'guestOS.detailed.data' in extra_config:
        data = extra_config.get("guestOS.detailed.data")
        if isinstance(data, dict) and 'kernelVersion' in data:
            return data['kernelVersion']
        

def get_guestinfo_publish_time(extra_config: dict):
    if not 'guestinfo.appInfo' in extra_config:
        return
    
    appinfo_str = extra_config['guestinfo.appInfo']
    if not isinstance(appinfo_str, str):
        return str(appinfo_str)
    
    if appinfo_str == "":
        return
    
    try:
        appinfo = json.loads(appinfo_str)
    except json.decoder.JSONDecodeError as err:
        return f"Cannot parse \"{appinfo_str}\": {err}"

    if 'publishTime' in appinfo:
        return datetime.fromisoformat(appinfo['publishTime'])


def get_tools_version(extra_config: dict, version_number: str):
    if 'guestinfo.vmtools.versionNumber' in extra_config and 'guestinfo.vmtools.description' in extra_config:
        if extra_config['guestinfo.vmtools.versionNumber'] == version_number:
            return extra_config['guestinfo.vmtools.description']


def get_custom_values(vm: vim.VirtualMachine):
    available_fields = {}
    custom_values = {}

    for field in vm.availableField:
        available_fields[field.key] = field.name

    for value in vm.value:
        custom_values[available_fields[value.key]] = value.value
        
    for value in vm.customValue:
        custom_values[available_fields[value.key]] = value.value

    return custom_values
