"""
Analyze clusters and domains (compute resources).
"""
from __future__ import annotations

import logging
import os
import re
from argparse import ArgumentParser
from datetime import datetime
from io import IOBase
from time import sleep, time_ns

from pyVmomi import vim
from tabulate import tabulate
from zut import add_command, tabular_dumper, write_live

from . import VCenterClient, get_obj_name, get_obj_ref, settings
from .settings import TABULAR_OUT, OUT_DIR

_logger = logging.getLogger(__name__)


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output table (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")
        
    subparsers = parser.add_subparsers(title='sub commands')
    add_command(subparsers, dump_clusters, name='.')
    add_command(subparsers, display_cluster_stats, name='stat')

def handle(vcenter: VCenterClient, **kwargs):
    dump_clusters(vcenter, **kwargs)

handle.add_arguments = _add_arguments


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output table (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

def dump_clusters(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', out: os.PathLike|IOBase = TABULAR_OUT, dir: os.PathLike = None):
    """
    Dump clusters and domains (compute resources).
    """
    headers = [
        'name',
        'ref',
        'overall_status',
        'config_status',
        'host_count',
        'host_effective_count',
        'cpu_cores',
        'cpu_mhz',
        'cpu_effective_mhz',
        'mem_mib',
        'mem_effective_mib',
        # Perf
        'perf_realtime_refresh_rate',
        # Usage, see: https://vdc-repo.vmware.com/vmwb-repository/dcr-public/dce91b06-cc93-42d6-b277-78fb13a16d6e/7d3494a5-bca7-400f-a18f-f539787ec798/vim.cluster.UsageSummary.html
        'vm_count',
        'vm_poweredoff_count',
        'cpu_demand_mhz', # Sum of CPU demand of all the powered-on VMs in the cluster (= The amount of CPU resources a virtual machine would use if there were no CPU contention or CPU limit).
        'cpu_entitled_mhz', # Current CPU entitlement across the cluster (= CPU resources devoted by the ESXi scheduler to the virtual machines and resource pools).
        'cpu_reservation_mhz', # Sum of CPU reservation of all the Resource Pools and powered-on VMs in the cluster.
        'cpu_poweredoff_reservation_mhz', # Sum of CPU reservation of all the powered-off VMs in the cluster.
        'mem_demand_mib', # Sum of memory demand of all the powered-on VMs in the cluster.
        'mem_entitled_mib', # Current memory entitlement across the cluster (= Amount of host physical memory the VM is entitled to, as determined by the ESXi scheduler).
        'mem_reservation_mib', # Sum of memory reservation of all the Resource Pools and powered-on VMs in the cluster.
        'mem_poweredoff_reservation_mib', # Sum of memory reservation of all the powered-off VMs in the cluster.
    ]

    objs = vcenter.get_objs(vim.ComputeResource, search, normalize=normalize, key=key)
    objs_count = len(objs)

    perf_manager = vcenter.service_content.perfManager

    with tabular_dumper(out, title='cluster', dir=dir or vcenter.data_dir, scope=vcenter.scope, headers=headers, truncate=True, excel=settings.CSV_EXCEL) as t:
        for i, obj in enumerate(objs):
            name = get_obj_name(obj)
            ref = get_obj_ref(obj)
            
            try:
                _logger.info(f"Analyze cluster {i+1:,}/{objs_count:,} ({100 * (i+1)/objs_count:.0f}%): {name} ({ref})")

                summary = obj.summary
                usage: vim.cluster.UsageSummary = summary.usageSummary if isinstance(obj, vim.ClusterComputeResource) else None

                # Perf
                perf_provider_summary = perf_manager.QueryProviderSummary(obj)
                perf_realtime_refresh_rate = perf_provider_summary.refreshRate if perf_provider_summary.currentSupported else None

                t.append([
                    name,
                    ref,
                    obj.overallStatus,
                    obj.configStatus,
                    # Summary, see: https://vdc-download.vmware.com/vmwb-repository/dcr-public/3325c370-b58c-4799-99ff-58ae3baac1bd/45789cc5-aba1-48bc-a320-5e35142b50af/doc/vim.ComputeResource.Summary.html
                    summary.numHosts,
                    summary.numEffectiveHosts,
                    summary.numCpuCores,
                    summary.totalCpu,
                    summary.effectiveCpu, # Effective = aggregated effective resource from all running hosts. Hosts that are in maintenance mode or are unresponsive are not counted. Resources used by the VMware Service Console are not included in the aggregate. This value represents the amount of resources available for the root resource pool for running virtual machines.
                    int(summary.totalMemory / 1024**2),
                    int(summary.effectiveMemory), # Effective = aggregated effective resource from all running hosts. Hosts that are in maintenance mode or are unresponsive are not counted. Resources used by the VMware Service Console are not included in the aggregate. This value represents the amount of resources available for the root resource pool for running virtual machines.                              
                    # Perf
                    perf_realtime_refresh_rate,
                    # Usage, see: https://vdc-repo.vmware.com/vmwb-repository/dcr-public/dce91b06-cc93-42d6-b277-78fb13a16d6e/7d3494a5-bca7-400f-a18f-f539787ec798/vim.cluster.UsageSummary.html
                    usage.totalVmCount if usage else None,
                    usage.poweredOffVmCount if usage else None,
                    usage.cpuDemandMhz,
                    usage.cpuEntitledMhz,
                    usage.cpuReservationMhz,
                    usage.poweredOffCpuReservationMhz,
                    usage.memDemandMB,
                    usage.memEntitledMB,
                    usage.memReservationMB,
                    usage.poweredOffMemReservationMB,
                ])
            
            except:
                _logger.exception(f"Error while analyzing host {name} ({ref})")

dump_clusters.add_arguments = _add_arguments


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('--sleep', type=float, default=1.0, dest='sleep_duration', help="Sleep duration (in seconds) between two invokations.")
    parser.add_argument('--no-pct', action='store_true', help="Do not display percentages.")

    group = parser.add_argument_group('resources')
    group.add_argument('--cpu', action='append_const', const='cpu', dest='resources', help="CPU.")
    group.add_argument('--mem', action='append_const', const='mem', dest='resources', help="Memory.")

def display_cluster_stats(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', no_pct: bool = False, resources: list[str] = None, sleep_duration: float = 1.0, **ignored):
    """
    Display live stats about clusters and domains (compute resources).
    """
    objs = sorted(vcenter.get_objs(vim.ClusterComputeResource, search, normalize=normalize, key=key), key=lambda obj: obj.name)

    previous_tabtext = None
    last_change: datetime = None
    headers = ['name']
    if not no_pct and (not resources or 'cpu' in resources):
        headers.append('cpu_demand_pct')
    if not no_pct and (not resources or 'mem' in resources):
        headers.append('mem_demand_pct')
    if not resources or 'cpu' in resources:
        headers.append('cpu_demand_mhz')
        headers.append('cpu_entitled_mhz')
        headers.append('cpu_effective_mhz')
        headers.append('cpu_mhz')
    if not resources or 'mem' in resources:
        headers.append('mem_demand_mib')
        headers.append('mem_entitled_mib')
        headers.append('mem_effective_mib')
        headers.append('mem_mib')

    while True:
        total_cpu_effective_mhz = 0
        total_mem_effective_mib = 0
        total_cpu_demand_mhz = 0
        total_mem_demand_mib = 0
        total_cpu_entitled_mhz = 0
        total_mem_entitled_mib = 0
        total_cpu_mhz = 0
        total_mem_mib = 0
        
        data = []
        now = datetime.now()
        t0 = time_ns()
        for obj in objs:
            summary = obj.summary
            usage: vim.cluster.UsageSummary = summary.usageSummary

            cpu_effective_mhz = summary.effectiveCpu
            mem_effective_mib = int(summary.effectiveMemory)
            cpu_demand_mhz = usage.cpuDemandMhz
            mem_demand_mib = usage.memDemandMB
            cpu_entitled_mhz = usage.cpuEntitledMhz
            mem_entitled_mib = usage.memEntitledMB
            cpu_mhz = summary.totalCpu
            mem_mib = int(summary.totalMemory / 1024**2)

            total_cpu_effective_mhz += cpu_effective_mhz
            total_mem_effective_mib += mem_effective_mib
            total_cpu_demand_mhz += cpu_demand_mhz
            total_mem_demand_mib += mem_demand_mib
            total_cpu_entitled_mhz += cpu_entitled_mhz
            total_mem_entitled_mib += mem_entitled_mib
            total_cpu_mhz += cpu_mhz
            total_mem_mib += mem_mib

            row = [obj.name]
            if not no_pct and (not resources or 'cpu' in resources):
                row.append(round(100 * cpu_demand_mhz / cpu_effective_mhz, 1))
            if not no_pct and (not resources or 'mem' in resources):
                row.append(round(100 * mem_demand_mib / mem_effective_mib, 1))
            if not resources or 'cpu' in resources:
                row.append(cpu_demand_mhz)
                row.append(cpu_entitled_mhz)
                row.append(cpu_effective_mhz)
                row.append(cpu_mhz)
            if not resources or 'mem' in resources:
                row.append(mem_demand_mib)
                row.append(mem_entitled_mib)
                row.append(mem_effective_mib)
                row.append(mem_mib)
            data.append(row)

        row = ["(total)"]
        if not no_pct and (not resources or 'cpu' in resources):
            row.append(round(100 * total_cpu_demand_mhz / total_cpu_effective_mhz, 1))
        if not no_pct and (not resources or 'mem' in resources):
            row.append(round(100 * total_mem_demand_mib / total_mem_effective_mib, 1))
        if not resources or 'cpu' in resources:
            row.append(total_cpu_demand_mhz)
            row.append(total_cpu_entitled_mhz)
            row.append(total_cpu_effective_mhz)
            row.append(total_cpu_mhz)
        if not resources or 'mem' in resources:
            row.append(total_mem_demand_mib)
            row.append(total_mem_entitled_mib)
            row.append(total_mem_effective_mib)
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

display_cluster_stats.add_arguments = _add_arguments
