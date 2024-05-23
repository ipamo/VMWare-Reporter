"""
Manage virtual machines.
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

logger = logging.getLogger(__name__)

def add_vm_commands(commands_subparsers: _SubParsersAction[ArgumentParser], *, name: str):
    parser = commands_subparsers.add_parser(name, help=get_help_text(__doc__), description=get_description_text(__doc__), formatter_class=RawTextHelpFormatter, add_help=False)

    group = parser.add_argument_group(title='Command options')
    group.add_argument('-h', '--help', action='help', help=f"Show this command help message and exit.")

    subparsers = parser.add_subparsers(title='Sub commands')
    add_func_command(subparsers, list_vms, name='list')
    add_func_command(subparsers, analyze_disks, name='disks')
    add_func_command(subparsers, analyze_nics, name='nics')
    
    # Operations
    add_func_command(subparsers, start_vms, name='start')
    add_func_command(subparsers, stop_vms, name='stop')
    add_func_command(subparsers, suspend_vms, name='suspend')    
    add_func_command(subparsers, reconfigure_vms, name='reconfigure')
   

DEFAULT_OUT = 'vms.xlsx#{title}' if openpyxl else 'vms-{title}.csv'


#region List

def list_vms(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', out: os.PathLike|IOBase = DEFAULT_OUT):
    headers = [
        'name',
        'ref',
        'overall_status',
        'config_status',
        'template',
        # Config
        'vcpu',
        Header('memory', fmt='gib'),
        Header('disk_capacity', fmt='gib'),
        Header('disk_freespace', fmt='gib'),
        'disk_count',
        'nic_count',
        # Dossier / rangement
        'folder',
        'assignment',
        'annotation',
        'create_date',
        'change_date',
        # Config details
        'hostname',
        'network',
        'disk',
        # ResourcePool / runtime
        'resource_pool',
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
    ]

    with out_table(out, title='vms', dir=vcenter.get_out_dir(), headers=headers, after1970=True) as t:
        for obj in vcenter.iter_objs(vim.VirtualMachine, search, normalize=normalize, key=key):  
            try:
                logger.info(f"Analyze {obj.name}")

                disks = extract_disks(obj)
                nics = extract_nics(obj, vcenter=vcenter)

                custom_values = get_custom_values(obj)
                assignment = custom_values.pop('Appartenance', None)
                annotations = obj.config.annotation
                for key, value in custom_values.items():
                    annotations += (' | ' if annotations else '') + f"{key}: {value}"

                extra_config = dictify_value(obj.config.extraConfig)

                t.append([
                    obj.name,
                    get_obj_ref(obj),
                    obj.overallStatus,
                    obj.configStatus,
                    obj.config.template,
                    # Config
                    obj.config.hardware.numCPU,
                    obj.config.hardware.memoryMB * 1024 * 1024,
                    disks.capacity,
                    disks.freespace,
                    obj.summary.config.numVirtualDisks,
                    obj.summary.config.numEthernetCards,
                    # Dossier / rangement
                    get_obj_path(obj.parent),
                    assignment,
                    annotations,
                    obj.config.createDate,
                    datetime.fromisoformat(obj.config.changeVersion),
                    # Config details
                    obj.guest.hostName,
                    nics.to_summary(ip_version=4),
                    disks.to_summary(),
                    # ResourcePool / runtime
                    get_obj_path(obj.resourcePool),
                    obj.runtime.host.name,
                    obj.runtime.powerState,
                    obj.runtime.paused,
                    obj.runtime.connectionState,
                    obj.runtime.bootTime,
                    obj.config.version,
                    # Guest info
                    get_os_family(extra_config, obj.config.guestFullName),
                    get_os_version(extra_config),
                    get_os_distro_version(extra_config),
                    get_os_kernel_version(extra_config),
                    obj.guest.toolsStatus,  # nominal: toolsOk
                    obj.guest.toolsRunningStatus,  # nominal: guestToolsRunning
                    get_tools_version_number(obj.guest.toolsVersion),  # version number
                    get_tools_version(extra_config, obj.guest.toolsVersion),      
                    get_guestinfo_publish_time(extra_config),
                ])
            
            except Exception as err:
                logger.exception(f"Error while analyzing {str(obj)}")


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('-o', '--out', default=DEFAULT_OUT, help="Output table (default: %(default)s).")

list_vms.add_arguments = _add_arguments

#endregion


#region Start, stop, suspend

def start_vms(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', **options):
    _invoke_and_track('PowerOn', vcenter, search, normalize=normalize, key=key)

def stop_vms(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', force: bool = False):
    if force:
        _invoke_and_track('PowerOff', vcenter, search, normalize=normalize, key=key)
    else:
        _invoke_and_track('ShutdownGuest', vcenter, search, normalize=normalize, key=key)

def suspend_vms(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', force: bool = False):
    if force:
        _invoke_and_track('Suspend', vcenter, search, normalize=normalize, key=key)
    else:
        _invoke_and_track('StandbyGuest', vcenter, search, normalize=normalize, key=key)

def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('-f', '--force', action='store_true', help="Force operation.")

start_vms.add_arguments = _add_arguments
stop_vms.add_arguments = _add_arguments
suspend_vms.add_arguments = _add_arguments


def _invoke_and_track(op: str, vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name'):
    # Idea: https://github.com/reubenur-rahman/vmware-pyvmomi-examples/blob/master/vm_power_ops.py
    tasks = {}
    for vm in vcenter.iter_objs(vim.VirtualMachine, search, normalize=normalize, key=key):
        log_prefix = f"{vm.name} ({get_obj_ref(vm)})"
        logger.info(f"{op} {log_prefix}...")
        func = getattr(vm, op)
        try:
            task = func()
            if task is not None:
                tasks[task] = log_prefix
        except Exception as err:
            logger.error(f"{log_prefix}: {str(err)}")

    if tasks:
        vcenter.wait_for_task(tasks)


#endregion


#region Reconfigure

DEFAULT_RECONFIGURE_FILE = 'vms_reconfigure.xlsx'
DEFAULT_RECONFIGURE_TABLE = 'vms_reconfigure'

def reconfigure_vms(vcenter: VCenterClient, path: str|Path = DEFAULT_RECONFIGURE_FILE, top: int = None):
    """
    Reconfigure VMs (vcpu, memory, annotation, custom fields) as a mass operation, using an Excel file.
    """
    from zut.excel import ExcelWorkbook, ExcelRow, split_excel_path

    class Helper:
        def __init__(self, row: ExcelRow, found: bool = False):
            self.row = row
            self.found = found

        def report_ok(self, details: str):
            logger.info(f"OK: {details}")
            self.row['status'] = 'OK'
            self.row['details'] = details

        def report_error(self, details: str):
            logger.error(details)
            self.row['status'] = 'ERROR'
            self.row['details'] = details
            
    class FieldDef:
        def __init__(self, name: str, type: type, getter: Callable[[vim.VirtualMachine],Any]|Literal['__customvalue__'], target_key:str|int, target_formatter: Callable[[vim.VirtualMachine],Any] = None):
            """
            - `key`: if str, this is the reconfigure ConfigSpec argument to use. If int, this is the customfield key to use.
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

        def get_from_row(self, row: ExcelRow, suffix: str):
            value = row.get(f"{self.name}_{suffix}")
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

    for customfield in vcenter.service_content.customFieldsManager.field:
        if customfield.managedObjectType == vim.VirtualMachine:
            fielddefs.append(FieldDef(customfield.name, str, '__customvalue__', customfield.key))
    
    path, tablename = split_excel_path(path, default_table_name=DEFAULT_RECONFIGURE_TABLE, dir=vcenter.get_out_dir())

    workbook = ExcelWorkbook(path)
    table = workbook.get_or_create_table(tablename)
    
    helpers: dict[str,Helper] = {}
    search_by_ref = 'ref' in table.column_names
    for row in table:
        if search_by_ref:
            if row['ref']:
                helpers[row['ref']] = Helper(row)
        else:
            if row['name']:
                helpers[slugify(row['name'])] = Helper(row)
        row['status'] = None
        row['details'] = None
    
    n = 0
    for vm in vcenter.iter_objs(vim.VirtualMachine):
        if top is not None and n == top:
            logger.warning(f"Stop after {n} VMs")
            break

        key = get_obj_ref(vm) if search_by_ref else slugify(vm.name)
        helper = helpers.get(key)
        if not helper:
            continue
        
        if helper.found:
            logger.error(f"VM {vm.name}: ignore (several VMs with key \"{key}\")")
            continue
        helper.found = True

        n += 1        
        logger.info(f"Handle {vm.name} ({get_obj_ref(vm)})")
        row = helper.row

        try:
            if search_by_ref and slugify(vm.name) != slugify(row['name']):
                helper.report_error(f"Invalid VM name: expected {vm.name}")
                continue

            if vm.config.template:
                helper.report_error(f"This is a template")
                continue

            if vm.runtime.powerState != 'poweredOff':
                helper.report_error(f"Not powered off")
                continue

            config_previous: dict[str|int,Any] = {}
            config_targets: dict[str|int,Any] = {}
            prepare_error = ''

            # Prepare reconfiguration
            change_version = vm.config.changeVersion  # used to guard against updates that have happened between when configInfo is read and when it is applied

            for fielddef in fielddefs:
                target = fielddef.get_from_row(row, 'target')
                if target is None:
                    continue

                current = fielddef.get_from_vm(vm)
                current_expected = fielddef.get_from_row(row, 'current')
                if current_expected is not None:
                    if current_expected != current:
                        prepare_error += (', ' if prepare_error else '') + f"current {fielddef.name}: {'(empty)' if current == '' else current}"   
                        continue

                if target != current:
                    config_previous[fielddef.target_key] = fielddef.format_for_target(current)
                    config_targets[fielddef.target_key] = fielddef.format_for_target(target)

            if prepare_error:
                helper.report_error(f"Invalid {prepare_error}")
                continue

            if not config_targets:
                helper.report_ok("Nothing to do")
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
                logger.debug("Reconfigure VM: %s", config_targets_reconfigure)
                configspec = vim.vm.ConfigSpec(**config_targets_reconfigure, changeVersion=change_version)
                task = vm.ReconfigVM_Task(configspec)
                vcenter.wait_for_task(task)

            for key, value in config_targets_customfields.items():
                logger.debug("Set custom field %s: %s", key, value)
                vcenter.service_content.customFieldsManager.SetField(vm, key, value)

            # Verification
            status_ok = True
            details = ''

            for fielddef in fielddefs:
                if not fielddef.target_key in config_targets:
                    continue # field not modified

                previous_value = config_previous[fielddef.target_key]
                target_value = config_targets[fielddef.target_key]
                new_value = fielddef.get_from_vm(vm)
                details += (', ' if details else '') + f"{fielddef.target_key}: {previous_value} -> {'(empty)' if new_value == '' else new_value}"

                if new_value != target_value:
                    status_ok = False
                    details += ' [ERROR]'

            if status_ok:
                helper.report_ok(details)
            else:
                helper.report_error(details)
        
        except Exception as err:
            logger.exception(str(err))
            row['status'] = type(err).__name__
            row['details'] = str(err)

    # Report not found
    for key, helper in helpers.items():
        if not helper.found:
            helper.report_error(f"VM {key} not found")

    workbook.close()


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('path', nargs='?', default=DEFAULT_RECONFIGURE_FILE, help="Path to Excel file describing VMs to reconfigure.")
    parser.add_argument('--top', type=int)

reconfigure_vms.add_arguments = _add_arguments


#endregion


#region Disks

def analyze_disks(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', out: str = DEFAULT_OUT, top: int = None):
    """
    Analyze VM disks.
    """
    disks_per_vm_headers = [
        'vm',
        'power_state',
        'os_family',
        'os_version',
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
        'power_state',
        'os_family',
        'os_version',
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
    
    with (out_table(out, title='disks', headers=disks_headers, dir=vcenter.get_out_dir()) as t_disks,
          out_table(out, title='disks_per_vm', headers=disks_per_vm_headers, dir=vcenter.get_out_dir()) as t_disks_per_vm):
        
        for i, vm in enumerate(vcenter.iter_objs(vim.VirtualMachine, search, normalize=normalize, key=key)):
            if top is not None and i == top:
                break
            
            try:
                logger.info(f"Analyze {vm.name} disks")

                info = extract_disks(vm)

                extra_config = dictify_value(vm.config.extraConfig)
                os_family = get_os_family(extra_config, vm.config.guestFullName)
                os_version = get_os_version(extra_config)

                mapped_guests: list[vim.vm.GuestInfo.DiskInfo] = []
                for disk in info.disks:
                    for guest in disk.guests:
                        mapped_guests.append(guest)

                t_disks_per_vm.append([
                    vm.name, # vm
                    vm.runtime.powerState, # power_state
                    os_family,
                    os_version,
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
                    
                    t_disks.append([
                        vm.name, # vm
                        vm.runtime.powerState, # power_state
                        os_family,
                        os_version,
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
                    t_disks.append([
                        vm.name, # vm
                        vm.runtime.powerState, # power_state
                        os_family,
                        os_version,
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
    parser.add_argument('-o', '--out', default=DEFAULT_OUT, help="Output Excel or CSV file (default: %(default)s).")

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
    def _get_guest_dict(cls, guest: vim.vm.GuestInfo.DiskInfo):
        data = {}
        data['path'] = guest.diskPath
        data['capacity'] = gigi_bytes(guest.capacity)
        data['freespace'] = gigi_bytes(guest.freeSpace)
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

def analyze_nics(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', out: str = DEFAULT_OUT, top: int = None):
    """
    Analyze VM network interfaces.
    """
    nics_per_vm_headers = [
        'vm',
        'power_state',
        'os_family',
        'os_version',
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
        'power_state',
        'os_family',
        'os_version',
        'backing',
        'network',
        'address_type',
        'key',
        'mac',
        'guests_ips',
        'guests_network_name',
    ]
    
    with (out_table(out, title='nics', headers=nics_headers, dir=vcenter.get_out_dir()) as t_nics,
          out_table(out, title='nics_per_vm', headers=nics_per_vm_headers, dir=vcenter.get_out_dir()) as t_nics_per_vm):
        
        for i, vm in enumerate(vcenter.iter_objs(vim.VirtualMachine, search, normalize=normalize, key=key)):
            if top is not None and i == top:
                break
            
            try:
                logger.info(f"Analyze {vm.name} nics")

                info = extract_nics(vm, vcenter=vcenter)

                extra_config = dictify_value(vm.config.extraConfig)
                os_family = get_os_family(extra_config, vm.config.guestFullName)
                os_version = get_os_version(extra_config)

                mapped_guests: list[vim.vm.GuestInfo.NicInfo] = []
                for nic in info.nics:
                    for guest in nic.guests:
                        mapped_guests.append(guest)

                t_nics_per_vm.append([
                    vm.name, # vm
                    vm.runtime.powerState, # power_state
                    os_family,
                    os_version,
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
                    
                    t_nics.append([
                        vm.name, # vm
                        vm.runtime.powerState, # power_state
                        os_family,
                        os_version,
                        backing_typename, # backing
                        network,
                        nic.device.addressType, # address_type
                        nic.device.key, # key
                        nic.device.macAddress.lower(), # mac
                        ip_addresses,
                        networks,
                    ])

                for guest in info.unmapped_guests:
                    t_nics.append([
                        vm.name, # vm
                        vm.runtime.powerState, # power_state
                        os_family,
                        os_version,
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
    parser.add_argument('-o', '--out', default=DEFAULT_OUT, help="Output Excel or CSV file (default: %(default)s).")

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


def get_tools_version_number(toolsversion: str):
    if re.match(r'^\d+$', toolsversion):
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
