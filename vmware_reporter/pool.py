"""
Analyze resource pools.
"""
from __future__ import annotations

import logging
import os
import re
from argparse import ArgumentParser
from io import IOBase

from pyVmomi import vim
from zut import Header, tabular_dumper

from . import VCenterClient, get_obj_name, get_obj_ref, settings
from .settings import TABULAR_OUT, OUT_DIR

_logger = logging.getLogger(__name__)


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output table (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

def dump_pools(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', out: os.PathLike|IOBase = TABULAR_OUT, dir: os.PathLike = None):
    """
    Dump resource pools.
    """
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

    with tabular_dumper(out, title='pool', dir=dir or vcenter.data_dir, scope=vcenter.scope, headers=headers, truncate=True, excel=settings.CSV_EXCEL) as t:
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
            
            except Exception:
                _logger.exception(f"Error while analyzing {str(obj)}")

dump_pools.add_arguments = _add_arguments
handle = dump_pools


_cached_cluster_infos: dict[vim.ResourcePool,tuple[str,int]] = {}
_cached_pool_paths: dict[vim.ResourcePool,str] = {}

def get_cached_pool_info(obj: vim.ResourcePool):
    owner = obj.owner
    if owner in _cached_cluster_infos:
        cluster_name, cpu_per_core = _cached_cluster_infos[owner]
    else:
        cluster_name = owner.name
        summary = owner.summary
        cpu_per_core = 0 if summary.numCpuCores == 0 else int(summary.totalCpu / summary.numCpuCores)
        _cached_cluster_infos[owner] = cluster_name, cpu_per_core

    resourcepool_path = get_cached_pool_path(obj)
    return (cluster_name, cpu_per_core, resourcepool_path)


def get_cached_pool_path(obj: vim.ResourcePool):
    if obj is None:
        return None

    if obj in _cached_pool_paths:
        return _cached_pool_paths[obj]

    name = obj.name
    parent = obj.parent
    if isinstance(parent, vim.ComputeResource):
        path = None if name == 'Resources' else name
        _cached_pool_paths[obj] = path
        return path
    else:
        parent_path = get_cached_pool_path(parent)
        path = (f"{parent_path}/" if parent_path else '') + name
        _cached_pool_paths[obj] = path
        return path
