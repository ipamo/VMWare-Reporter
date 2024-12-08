"""
Manage ESXi hosts.
"""
from __future__ import annotations

import logging
import os
import re
from argparse import ArgumentParser
from datetime import datetime
from io import IOBase
from time import sleep, time_ns
from typing import Iterable

from pyVmomi import vim
from tabulate import tabulate
from zut import add_command, tabular_dumper, write_live

from . import VCenterClient, dictify_value, get_obj_name, get_obj_ref, get_obj_typename, settings
from .settings import TABULAR_OUT, OUT_DIR


_logger = logging.getLogger(__name__)


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output table (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")
        
    subparsers = parser.add_subparsers(title='sub commands')
    add_command(subparsers, dump_hosts, name='list')
    add_command(subparsers, display_host_stats, name='stat')

def handle(vcenter: VCenterClient, **kwargs):
    dump_hosts(vcenter, **kwargs)

handle.add_arguments = _add_arguments


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output table (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

def dump_hosts(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', out: os.PathLike|IOBase = TABULAR_OUT, dir: os.PathLike = None):
    headers = [
        'name',
        'ref',
        'overall_status',
        'config_status',
        'cpu_cores',
        'cpu_mhz_per_core',
        'mem_mib',
        'cluster',
        'state',        
        'power_state',
        'standby_mode',
        'connection_state',
        'maintenance_mode',
        'quarantine_mode',
        'reboot_required',
        'boot_time',
        'vmware_product',
        'vendor',
        'model',
        'serial',
        'enclosure',
        'cpu_sockets',
        'cpu_model',
        'cpu_threads',
        'hyperthreading',
        # Quick stats, see: https://vdc-repo.vmware.com/vmwb-repository/dcr-public/d1902b0e-d479-46bf-8ac9-cee0e31e8ec0/07ce8dbd-db48-4261-9b8f-c6d3ad8ba472/vim.host.Summary.QuickStats.html
        # Overall usage
        'cpu_overall_mhz',
        'mem_overall_mib',
    ]

    objs = vcenter.get_objs(vim.HostSystem, search, normalize=normalize, key=key)
    objs_count = len(objs)

    with tabular_dumper(out, title='host', dir=dir or vcenter.data_dir, scope=vcenter.scope, headers=headers, after1970=True, truncate=True, excel=settings.CSV_EXCEL) as t:
        for i, obj in enumerate(objs):
            name = get_obj_name(obj)
            ref = get_obj_ref(obj)
            
            try:
                _logger.info(f"Analyze host {i+1:,}/{objs_count:,} ({100 * (i+1)/objs_count:.0f}%): {name} ({ref})")

                config = obj.config
                summary = obj.summary
                runtime = summary.runtime
                hardware = summary.hardware
                quickstats = summary.quickStats
                oii = dictify_value(hardware.otherIdentifyingInfo)

                t.append([
                    name,
                    ref,
                    obj.overallStatus,
                    obj.configStatus,
                    hardware.numCpuCores,
                    hardware.cpuMhz,
                    int(hardware.memorySize / 1024**2),
                    obj.parent.name if obj.parent and get_obj_typename(obj.parent) == 'ClusterComputeResource' else None,
                    runtime.dasHostState.state if runtime.dasHostState else None,                                        
                    runtime.powerState,
                    runtime.standbyMode,
                    runtime.connectionState,
                    runtime.inMaintenanceMode,
                    runtime.inQuarantineMode,
                    runtime.bootTime,
                    summary.rebootRequired,
                    config.product.fullName,
                    hardware.vendor,
                    hardware.model,
                    oii.get('SerialNumberTag'),
                    oii.get('EnclosureSerialNumberTag'),
                    hardware.numCpuPkgs,
                    hardware.cpuModel,
                    hardware.numCpuThreads,
                    config.hyperThread.active if config.hyperThread else None,
                    # Quickstats
                    quickstats.overallCpuUsage,
                    quickstats.overallMemoryUsage,
                ])
            
            except:
                _logger.exception(f"Error while analyzing host {name} ({ref})")

dump_hosts.add_arguments = _add_arguments


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('--sleep', type=float, default=1.0,  dest='sleep_duration', help="Sleep duration (in seconds) between two invokations.")
    parser.add_argument('--running', action='store_true', help="Only show running hosts.")
    parser.add_argument('--no-pct', action='store_true', help="Do not display percentages.")
    
    group = parser.add_argument_group('resources')
    group.add_argument('--cpu', action='append_const', const='cpu', dest='resources', help="CPU.")
    group.add_argument('--mem', action='append_const', const='mem', dest='resources', help="Memory.")

def display_host_stats(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', running: bool = False, no_pct: bool = False, resources: list[str] = None, sleep_duration: float = 1.0, **ignored):    
    objs: Iterable[vim.HostSystem] = sorted(vcenter.get_objs(vim.HostSystem, search, normalize=normalize, key=key), key=lambda obj: (obj.parent.name, obj.name))

    previous_tabtext = None
    last_change: datetime = None
    headers = [
        'name',
        'cluster',
        'state',
    ]
    if not no_pct and (not resources or 'cpu' in resources):
        headers.append('cpu_overall_pct')
    if not no_pct and (not resources or 'mem' in resources):
        headers.append('mem_overall_pct')
    if not resources or 'cpu' in resources:
        headers.append('cpu_overall_mhz')
        headers.append('cpu_mhz')
    if not resources or 'mem' in resources:
        headers.append('mem_overall_mib')
        headers.append('mem_mib')

    while True:
        total_cpu_mhz = 0
        total_mem_mib = 0
        total_cpu_overall_mhz = 0
        total_mem_overall_mib = 0

        last_cluster_ref = None
        last_cluster_name = None
        cluster_cpu_mhz = 0
        cluster_mem_mib = 0
        cluster_cpu_overall_mhz = 0
        cluster_mem_overall_mib = 0
        
        data = []
        now = datetime.now()
        t0 = time_ns()
        for obj in objs:
            summary = obj.summary
            runtime = summary.runtime
            
            if runtime.dasHostState:
                state = runtime.dasHostState.state
            elif runtime.inMaintenanceMode:
                state = 'maintenance'
            elif runtime.inQuarantineMode:
                state = 'quarantine'
            elif runtime.standbyMode:
                state = 'standby'
            else:
                state = runtime.powerState

            if running and not state in ['master', 'connectedToMaster']:
                continue

            hardware = summary.hardware
            quickstats = summary.quickStats

            cpu_mhz = hardware.numCpuCores*hardware.cpuMhz
            mem_mib = int(hardware.memorySize/1024**2)
            cpu_overall_mhz = quickstats.overallCpuUsage
            mem_overall_mib = quickstats.overallMemoryUsage

            total_cpu_mhz += cpu_mhz
            total_mem_mib += mem_mib
            total_cpu_overall_mhz += cpu_overall_mhz
            total_mem_overall_mib += mem_overall_mib

            cluster = obj.parent
            cluster_name = cluster.name
            cluster_ref = get_obj_ref(cluster)

            if last_cluster_ref is not None and cluster_ref != last_cluster_ref:
                row = [
                    "(cluster)",
                    last_cluster_name,
                    "-",
                ]
                if not no_pct and (not resources or 'cpu' in resources):
                    row.append(round(100 * cluster_cpu_overall_mhz / cluster_cpu_mhz, 1))
                if not no_pct and (not resources or 'mem' in resources):
                    row.append(round(100 * cluster_mem_overall_mib / cluster_mem_mib, 1))
                if not resources or 'cpu' in resources:            
                    row.append(cluster_cpu_overall_mhz)
                    row.append(cluster_cpu_mhz)
                if not resources or 'mem' in resources:
                    row.append(cluster_mem_overall_mib)
                    row.append(cluster_mem_mib)
                data.append(row)
                
                cluster_cpu_mhz = 0
                cluster_mem_mib = 0
                cluster_cpu_overall_mhz = 0
                cluster_mem_overall_mib = 0

            last_cluster_ref = cluster_ref
            last_cluster_name = cluster_name
            cluster_cpu_mhz += cpu_mhz
            cluster_mem_mib += mem_mib
            cluster_cpu_overall_mhz += cpu_overall_mhz
            cluster_mem_overall_mib += mem_overall_mib

            row = [
                obj.name,
                cluster_name,
                state,
            ]
            if not no_pct and (not resources or 'cpu' in resources):
                row.append(round(100 * cpu_overall_mhz / cpu_mhz, 1))
            if not no_pct and (not resources or 'mem' in resources):
                row.append(round(100 * mem_overall_mib / mem_mib, 1))
            if not resources or 'cpu' in resources:            
                row.append(cpu_overall_mhz)
                row.append(cpu_mhz)
            if not resources or 'mem' in resources:
                row.append(mem_overall_mib)
                row.append(mem_mib)
            data.append(row)

        row = [
            "(cluster)",
            last_cluster_name,
            "-",
        ]
        if not no_pct and (not resources or 'cpu' in resources):
            row.append(round(100 * cluster_cpu_overall_mhz / cluster_cpu_mhz, 1))
        if not no_pct and (not resources or 'mem' in resources):
            row.append(round(100 * cluster_mem_overall_mib / cluster_mem_mib, 1))
        if not resources or 'cpu' in resources:            
            row.append(cluster_cpu_overall_mhz)
            row.append(cluster_cpu_mhz)
        if not resources or 'mem' in resources:
            row.append(cluster_mem_overall_mib)
            row.append(cluster_mem_mib)
        data.append(row)
    
        row = [
            "(total)",
            "-",
            "-",
        ]
        if not no_pct and (not resources or 'cpu' in resources):
            row.append(round(100 * total_cpu_overall_mhz / total_cpu_mhz, 1))
        if not no_pct and (not resources or 'mem' in resources):
            row.append(round(100 * total_mem_overall_mib / total_mem_mib, 1))
        if not resources or 'cpu' in resources:            
            row.append(total_cpu_overall_mhz)
            row.append(total_cpu_mhz)
        if not resources or 'mem' in resources:
            row.append(total_mem_overall_mib)
            row.append(total_mem_mib)
        data.append(row)
        
        duration_ms = (time_ns() - t0) / 10**6

        tabtext = tabulate(data, headers, intfmt=',', floatfmt='.1f')
        if previous_tabtext is not None and tabtext != previous_tabtext:
            last_change = now
        text = f"Updated: {now.strftime('%Y-%m-%d %H:%M:%S')} (duration: {duration_ms:,.0f} ms), last changed: {last_change.strftime('%Y-%m-%d %H:%M:%S') if last_change else '-'}\n"
        text += tabtext
        write_live(text)
        previous_tabtext = tabtext
        sleep(sleep_duration)

display_host_stats.add_arguments = _add_arguments
