"""
Dump performance definitions or data.
"""
from __future__ import annotations

import logging
import os
import re
from argparse import ArgumentParser
from datetime import datetime, timedelta
from io import IOBase
from time import sleep, time_ns

from pyVmomi import vim, vmodl
from tabulate import tabulate
from zut import add_command, is_naive, make_aware, tabular_dumper, write_live

from . import (VCenterClient, get_obj_name, get_obj_ref, get_obj_refprefix,
               get_obj_typename, settings)
from .settings import TABULAR_OUT, OUT_DIR, COUNTERS

_logger = logging.getLogger(__name__)


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output table (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

    subparsers = parser.add_subparsers(title='sub commands')
    add_command(subparsers, dump_perf_all, name='all')
    add_command(subparsers, dump_perf_intervals, name='interval')
    add_command(subparsers, dump_perf_counters, name='counter')
    add_command(subparsers, dump_perf_metrics, name='metric')
    add_command(subparsers, dump_perf_providers, name='provider')
    add_command(subparsers, dump_perf_data, name='data')
    add_command(subparsers, display_perf_data, name='live')

def handle(vcenter: VCenterClient, **kwargs):
    dump_perf_all(vcenter, **kwargs)

handle.add_arguments = _add_arguments


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output table (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

def dump_perf_all(vcenter: VCenterClient, **kwargs):
    """
    Dump performance definitions (intervals, counters, and metrics of the first managed entity).
    """
    dump_perf_intervals(vcenter, **kwargs)
    dump_perf_counters(vcenter, **kwargs)
    dump_perf_metrics(vcenter, first=True, **kwargs)

dump_perf_all.add_arguments = _add_arguments


#region Perf data

def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='+', help="Search term(s).")
    parser.add_argument('-c', '--counter', dest='counters', nargs='*', help="Counter(s).")
    parser.add_argument('--instance', help="Metric instance.")
    parser.add_argument('--consolidate', action='store_true')
    parser.add_argument('-i', '--interval', help="Interval sampling period (see `perf interval` command).")
    parser.add_argument('--start', help="The server time from which to obtain counters (the specified start time is EXCLUDED from the returned samples).")
    parser.add_argument('--end', help="The server time up to which statistics are retrieved (the specified end time is INCLUDED in the returned samples).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('--first', action='store_true', help="Only handle the first object found for each type.")
    parser.add_argument('-t', '--type', dest='types', metavar='type', help="Managed object type name (example: datastore).")
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output table (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

def dump_perf_data(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern, *, counters: list[int|str] = None, instance: str = None, consolidate = False, interval: str|int = None, start: datetime|str = None, end: datetime|str = None, normalize: bool = False, key: str = 'name', first: bool = None, types: list[type|str]|type|str = None, out: os.PathLike|IOBase = TABULAR_OUT, dir: os.PathLike = None):
    """
    Dump performance data for managed entities.
    """
    objs = vcenter.get_objs(types, search=search, normalize=normalize, key=key, first=first)

    if counters and (counters == '*' or '*' in counters):
        counters = None
    elif not counters:
        counters = COUNTERS

    if isinstance(start, str):
        start = datetime.fromisoformat(start)
        if is_naive(start):
            start = make_aware(start)
    
    if isinstance(end, str):
        end = datetime.fromisoformat(end)
        if is_naive(end):
            end = make_aware(end)

    handler = PerfHandler(vcenter, consolidate=consolidate)
    if not interval or interval in {20, 'realtime'}:
        if start or end or consolidate:
            handler.extract_realtime(objs, counters=counters, instance=instance, start=start, end=end)
        else:
            handler.extract_realtime(objs, counters=counters, instance=instance, max_sample=1)
    else:
        handler.extract(objs, counters=counters, instance=instance, interval=interval, start=start, end=end)
    handler.export_multi(out, counter_details=True, with_entity_ref=True, translate_percent=True)

dump_perf_data.add_arguments = _add_arguments


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='+', help="Search term(s).")
    parser.add_argument('-c', '--counter', dest='counters', nargs='*', help="Counter(s).")
    parser.add_argument('-i', '--instances', action='store_true', dest='with_instances')
    parser.add_argument('--consolidate', action='store_true')
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('--first', action='store_true', help="Only handle the first object found for each type.")
    parser.add_argument('-t', '--type', dest='types', metavar='type', help="Managed object type name (example: datastore).")

def display_perf_data(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, counters: list[int|str] = None, with_instances = False, consolidate = False, normalize: bool = False, key: str = 'name', first: bool = None, types: list[type|str]|type|str = None, **ignored):
    """
    Display live performance data about management entities.
    """    
    objs = vcenter.get_objs(types, search, normalize=normalize, key=key, first=first)

    if not counters:
        if not COUNTERS:
            raise ValueError("Counters must be provided")
        counters = COUNTERS

    handler = PerfHandler(vcenter, consolidate=consolidate)
    while True:
        handler.clear()

        now = datetime.now()
        t0 = time_ns()
        handler.extract_realtime(objs, instance='*' if with_instances else '', counters=counters, max_sample=12)        
        duration_ms = (time_ns() - t0) / 10**6
        
        headers, rows = handler.export_single(translate_percent=100)
        tabtext = tabulate(rows, headers, intfmt=',', floatfmt=',.0f')
        text = f"Updated: {now.strftime('%Y-%m-%d %H:%M:%S')} (duration: {duration_ms:,.0f} ms)\n"
        text += tabtext
        write_live(text, newline=False)
        sleep(1.0)

display_perf_data.add_arguments = _add_arguments

#endregion


#region Perf definitions

def _add_arguments(parser: ArgumentParser):
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output table (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

def dump_perf_intervals(vcenter: VCenterClient, out: os.PathLike|IOBase = TABULAR_OUT, dir: os.PathLike = None, **ignored):
    """
    Dump performance intervals configured on the system.
    """
    headers = [
        'key', 'name', 'enabled', 'level', 'sampling_period'
    ]

    with tabular_dumper(out, title='perf_interval', dir=dir or vcenter.data_dir, scope=vcenter.scope, headers=headers, truncate=True, excel=settings.CSV_EXCEL) as t:
        for interval in sorted(vcenter.perf_intervals_by_name.values(), key=lambda interval: interval.samplingPeriod):
            t.append([interval.key, interval.name, interval.enabled, interval.level, interval.samplingPeriod])

dump_perf_intervals.add_arguments = _add_arguments


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('-g', '--group', help="Group of the counters (example: cpu)")
    parser.add_argument('-l', '--level', type=int, help="Max level of the counters (from 1 to 4)")
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output table (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

def dump_perf_counters(vcenter: VCenterClient, group: str = None, level: int = None, out: os.PathLike|IOBase = TABULAR_OUT, dir: os.PathLike = None, **ignored):
    """
    Dump performance counters configured on the system.
    """

    headers = [
        'key', 'group', 'name', 'rollup_type', 'stats_type', 'unit', 'level', 'per_device_level', 'label', 'summary'
    ]

    pm = vcenter.service_content.perfManager
    
    with tabular_dumper(out, title='perf_counter', dir=dir or vcenter.data_dir, scope=vcenter.scope, headers=headers, truncate=True, excel=settings.CSV_EXCEL) as t:
        for counter in sorted(pm.perfCounter, key=lambda counter: (counter.groupInfo.key, counter.nameInfo.key, counter.rollupType)):
            if group is not None and counter.groupInfo.key != group:
                continue
            if level is not None and counter.level > level:
                continue        
            t.append([counter.key, counter.groupInfo.key, counter.nameInfo.key, counter.rollupType, counter.statsType, counter.unitInfo.key, counter.level, counter.perDeviceLevel, counter.nameInfo.label, counter.nameInfo.summary])

dump_perf_counters.add_arguments = _add_arguments


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('--first', action='store_true', help="Only handle the first object found for each type.")
    parser.add_argument('-t', '--type', dest='types', metavar='type', help="Managed object type name (example: datastore).")
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output table (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

def dump_perf_providers(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', first: bool = None, types: list[type|str]|type|str = None, out: os.PathLike|IOBase = TABULAR_OUT, dir: os.PathLike = None, **ignored):
    """
    Dump available performance providers (managed entities only).
    """
    if first is None and not search:
        first = True

    pm = vcenter.service_content.perfManager

    counters_by_key: dict[str,vim.PerformanceManager.CounterInfo] = {}
    for counter in sorted(pm.perfCounter, key=lambda counter: (counter.groupInfo.key, counter.nameInfo.key, counter.rollupType)):
        counters_by_key[counter.key] = counter
        
    headers = [
        'entity_name', 'entity_ref', 'entity_type', 'history', 'realtime', 'realtime_refresh_rate'
    ]

    objs = vcenter.get_objs(types, search=search, normalize=normalize, key=key, first=first)
    objs_count = len(objs)
    t0 = None

    with tabular_dumper(out, title='perf_provider', dir=dir or vcenter.data_dir, scope=vcenter.scope, headers=headers, truncate=True, excel=settings.CSV_EXCEL) as table:
        for i, obj in enumerate(objs):
            name = get_obj_name(obj)
            ref = get_obj_ref(obj)
            try:
                t = time_ns()
                if t0 is None or (t - t0) >= 10**9 or i == objs_count - 1:
                    _logger.info(f"List providers {i+1:,}/{objs_count:,} ({100 * (i+1)/objs_count:.0f}%): {name} ({ref})")
                    t0 = t
                
                provider_summary = pm.QueryProviderSummary(entity=obj)
                table.append([
                    name,
                    ref,
                    get_obj_typename(obj),
                    provider_summary.summarySupported,
                    provider_summary.currentSupported,
                    provider_summary.refreshRate if provider_summary.currentSupported else None, # seconds
                ])
            except vmodl.fault.InvalidArgument:
                # this is not a provider
                pass
            except:
                _logger.exception(f"Error while listing providers for {name} ({ref})")   

dump_perf_providers.add_arguments = _add_arguments


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('--first', action='store_true', help="Only handle the first object found for each type.")
    parser.add_argument('-t', '--type', dest='types', metavar='type', help="Managed object type name (example: datastore).")
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output table (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

def dump_perf_metrics(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', first: bool = None, types: list[type|str]|type|str = None, out: os.PathLike|IOBase = TABULAR_OUT, dir: os.PathLike = None, **ignored):
    """
    Dump available performance metrics (managed entities and physical or virtual devices associated with managed entities).
    """
    if first is None and not search:
        first = True
    
    pm = vcenter.service_content.perfManager

    counters_by_key: dict[str,vim.PerformanceManager.CounterInfo] = {}
    for counter in sorted(pm.perfCounter, key=lambda counter: (counter.groupInfo.key, counter.nameInfo.key, counter.rollupType)):
        counters_by_key[counter.key] = counter
        
    headers = [
        'entity_name', 'entity_ref', 'entity_type', 'instance', 'key', 'group', 'name', 'rollup_type', 'stats_type', 'unit', 'level', 'per_device_level', 'label', 'summary'
    ]

    objs = vcenter.get_objs(types, search=search, normalize=normalize, first=first, key=key)
    objs_count = len(objs)
    t0 = None

    with tabular_dumper(out, title='perf_metric', dir=dir or vcenter.data_dir, scope=vcenter.scope, headers=headers, truncate=True, excel=settings.CSV_EXCEL) as table:
        for i, obj in enumerate(objs):
            if isinstance(obj, (vim.Folder, vim.Network)):
                continue # No performance data for these types

            name = get_obj_name(obj)
            ref = get_obj_ref(obj)
            
            try:
                t = time_ns()
                if t0 is None or (t - t0) >= 10**9 or i == objs_count - 1:
                    _logger.info(f"List metrics {i+1:,}/{objs_count:,} ({100 * (i+1)/objs_count:.0f}%): {name} ({ref})")
                    t0 = t
        
                for metric in pm.QueryAvailablePerfMetric(entity=obj):
                    counter = counters_by_key[metric.counterId]
                    table.append([
                        name,
                        ref,
                        get_obj_typename(obj),
                        metric.instance,
                        counter.key,
                        counter.groupInfo.key,
                        counter.nameInfo.key,
                        counter.rollupType,
                        counter.statsType,
                        counter.unitInfo.key,
                        counter.level,
                        counter.perDeviceLevel,
                        counter.nameInfo.label,
                        counter.nameInfo.summary,
                    ])            
            except:
                _logger.exception(f"Error while listing metrics for {name} ({ref})")

dump_perf_metrics.add_arguments = _add_arguments

#endregion


class PerfHandler:
    def __init__(self, vcenter: VCenterClient, *, consolidate = False):
        self._vcenter = vcenter
        self._perf_manager = vcenter.service_content.perfManager

        self._consolidate = consolidate

        self._rows: dict[str,dict[vim.ManagedEntity,dict[str,dict[str,dict[datetime,dict[int,PerfResultRow]]]]]] = {}
        """ Row containers: `refprefix` -> `entity` ->`instance` (empty for entity) -> `counter_group` (empty for entity) -> `timestamp` -> `interval` """

        # clearable caches (for this set of data)
        self._row_count = 0
        self._intervals: set[int] = set()
        self._used_counters: dict[str,dict[str,dict[int,vim.PerformanceManager.CounterInfo|int]]] = {}
        """ Used counter containers: `refprefix` -> `counter_group` (empty for entity) -> `counter_key` -> `counter` """

        # persistent caches (general purpose)
        self._all_counters_by_key = {counter.key: counter for counter in self._perf_manager.perfCounter}
        self._available_counter_keys_by_type: dict[type,list[int]] = {}
        self._given_counter_keys: list[int] = [] # ordered


    def clear(self):
        self._rows.clear()
        self._row_count = 0
        self._intervals.clear()
        self._used_counters.clear()


    def extract(self, objs: list[vim.ManagedEntity]|vim.ManagedEntity = None, *, counters: list[int|str] = None, instance: str = None, interval: str|int = None, start: datetime = None, end: datetime = None):
        if objs is None:
            objs = self._vcenter.get_objs()
        elif isinstance(objs, vim.ManagedEntity):
            objs = [objs]

        if not objs:
            raise ValueError("No entities.")

        if not interval and not start and not end:
            interval = self._vcenter.perf_intervals_by_name['Past day'].samplingPeriod
            start = datetime.now() - timedelta(minutes=15)
            end = None        
        else:
            if isinstance(interval, str):
                if re.match(r'^\d+$', interval):
                    interval = int(interval)
                else:
                    interval = self._vcenter.perf_intervals_by_name[f'Past {interval}'].samplingPeriod
            elif not interval:
                interval = self._vcenter.perf_intervals_by_name['Past day'].samplingPeriod

        for type_objs, counter_keys in self._get_counter_keys_by_type(objs, counters):
            results = self.query(type_objs, counters=counter_keys, instance=instance, interval=interval, start=start, end=end)
            self.add_results(results)


    def extract_realtime(self, objs: list[vim.ManagedEntity]|vim.ManagedEntity = None, *, counters: list[int|str] = None, instance: str = None, max_sample: int = None, start: datetime = None, end: datetime = None):
        if objs is None:
            objs = self._vcenter.get_objs()
        elif isinstance(objs, vim.ManagedEntity):
            objs = [objs]

        objs_by_refresh_rate: dict[int,list[vim.ManagedEntity]] = {}
        for obj in objs:
            try:
                provider_summary = self._perf_manager.QueryProviderSummary(obj)
            except vmodl.fault.InvalidArgument:
                continue # not a perf provider

            if not provider_summary.currentSupported:
                continue # perf provider does not support realtime stats

            refresh_rate = provider_summary.refreshRate
            if refresh_rate_objs := objs_by_refresh_rate.get(refresh_rate):
                refresh_rate_objs.append(obj)
            else:
                objs_by_refresh_rate[refresh_rate] = [obj]

        if not objs_by_refresh_rate:
            raise ValueError("No entities supporting realtime perfs.")

        for refresh_rate, refresh_rate_objs in objs_by_refresh_rate.items():
            for type_objs, counter_keys in self._get_counter_keys_by_type(refresh_rate_objs, counters):
                results = self.query(type_objs, counters=counter_keys, instance=instance, interval=refresh_rate, max_sample=max_sample, start=start, end=end)
                self.add_results(results)


    def _get_counter_keys_by_type(self, objs: list[vim.ManagedEntity], counters: list[int|str]) -> list[tuple[list[vim.ManagedEntity], list[int]]]:
        if not counters:
            return [(objs, None)]
     
        requested_counter_keys = set()
        for counter in counters:
            if isinstance(counter, str):
                if re.match(r'^\d+$', counter):
                    counter_key = int(counter)
                    requested_counter_keys.add(counter_key)                        
                    if not counter_key in self._given_counter_keys:
                        self._given_counter_keys.append(counter_key)
                else:
                    for counter_key in self._find_counter_keys(counter):
                        requested_counter_keys.add(counter_key)
                        if not counter_key in self._given_counter_keys:
                            self._given_counter_keys.append(counter_key)
            else:
                requested_counter_keys.add(counter)
                if not counter in self._given_counter_keys:
                    self._given_counter_keys.append(counter)
        
        counter_keys_by_type = {}
        for obj in objs:
            if type(obj) in counter_keys_by_type:
                continue

            # Get type_counter_keys from cache (or query it if not in cache)
            if type(obj) in self._available_counter_keys_by_type:
                type_counter_keys = self._available_counter_keys_by_type[type(obj)]
            else:
                try:
                    if _logger.isEnabledFor(logging.DEBUG):
                        _logger.debug(f"start QueryAvailablePerfMetric (entity={get_obj_ref(obj)})...")
                        t0 = time_ns()
                        
                    metrics = self._perf_manager.QueryAvailablePerfMetric(entity=obj)
                    
                    if _logger.isEnabledFor(logging.DEBUG):
                        _logger.debug(f"QueryAvailablePerfMetric (entity={get_obj_ref(obj)}): done, {len(metrics)} counters ({(time_ns() - t0)/10**6:,.1f} ms)")

                except vmodl.fault.InvalidArgument as err:
                    if err.invalidProperty == 'entity': # not a performance provider
                        counter_keys_by_type[type(obj)] = None
                        continue
                    raise

                type_counter_keys = set(metric.counterId for metric in metrics)
                self._available_counter_keys_by_type[type(obj)] = type_counter_keys

            counter_keys_by_type[type(obj)] = type_counter_keys.intersection(requested_counter_keys)
        
        results = []
        for obj_type, counter_keys in counter_keys_by_type.items():
            if not counter_keys:
                continue
            type_objs = [obj for obj in objs if type(obj) == obj_type]
            results.append((type_objs, counter_keys))
            
        return results


    def query(self, objs: list[vim.ManagedEntity], *, counters: list[int|vim.PerformanceManager.CounterInfo] = None, instance: str = None, interval: str|int = None, max_sample: int = None, start: datetime|str = None, end: datetime|str = None):
        metrics = None          
        if counters:
            metrics = []        
            for counter in counters:
                metrics.append(vim.PerformanceManager.MetricId(counterId=counter if isinstance(counter, int) else counter.key, instance=instance if instance is not None else '*'))

        if _logger.isEnabledFor(logging.DEBUG):
            _logger.debug(f"start QueryPerf (interval={interval}, {len(objs)} entities [{get_obj_typename(objs[0]) if objs else '?'} ...], {len(metrics) if metrics is not None else None} metrics)...")
            t0 = time_ns()
        
        results = self._perf_manager.QueryPerf([vim.PerformanceManager.QuerySpec(entity=obj, startTime=start, endTime=end, intervalId=interval, maxSample=max_sample, metricId=metrics) for obj in objs])
        
        if _logger.isEnabledFor(logging.DEBUG):
            _logger.debug(f"QueryPerf (interval={interval}): done ({(time_ns() - t0)/10**6:,.1f} ms)")

        return results


    def _find_counter_keys(self, name: str):
        counter_keys = []

        for counter in sorted(self._all_counters_by_key.values(), key=lambda c: (c.groupInfo.key, c.nameInfo.key, c.level, c.rollupType, c.statsType)):
            shortname = f"{counter.groupInfo.key}.{counter.nameInfo.key}"
            if name == shortname:
                counter_keys.append(counter.key)
            else:
                fullname = f"{shortname}:{counter.rollupType}"
                if name == fullname:
                    counter_keys.append(counter.key)

        if not counter_keys:
            raise ValueError(f"Counter not found: {name}")
        return counter_keys


    def add_results(self, results: list[vim.PerformanceManager.EntityMetric]):
        if _logger.isEnabledFor(logging.DEBUG):
            t0 = time_ns()

        entity_results: vim.PerformanceManager.EntityMetric
        for entity_results in results:
            samples = entity_results.sampleInfo
            for series in entity_results.value:
                if not isinstance(series, vim.PerformanceManager.IntSeries):
                    raise ValueError(f"Invalid series type: {type(series).__name__}")
                
                for i, value in enumerate(series.value):
                    sample = samples[i]
                    self._add_value(entity_results.entity, series.id.instance, series.id.counterId, sample.timestamp, sample.interval, value)

        if _logger.isEnabledFor(logging.DEBUG):
            _logger.debug(f"add_perf_results: done ({(time_ns() - t0)/10**6:,.1f} ms)")
        
    
    def _add_value(self, entity: vim.ManagedEntity, instance: str, counter_key: int, timestamp: datetime, interval: int, value: int):
        if value == -1:
            return
        
        if self._consolidate:
            target_timestamp = self._consolidate_timestamp(timestamp)
        else:
            target_timestamp = timestamp

        row = self._fetch_row(entity, instance, counter_key, target_timestamp, interval)
        row.add_value(counter_key, timestamp, value)
    
        
    def _consolidate_timestamp(self, timestamp: datetime):
        """
        Defaults to a 2-minute consolidation.

        May be overriden by subclass.
        """
        base_timestamp = timestamp.replace(minute=timestamp.minute - (1 if timestamp.minute % 2 == 1 else 0), second=0, microsecond=0)
        if timestamp == base_timestamp:
            return base_timestamp
        else:
            return base_timestamp + timedelta(minutes=2)


    def _fetch_row(self, entity: vim.ManagedEntity, instance: str, counter_key: int, timestamp: datetime, interval: int):
        #refprefix -> entity -> instance ('' for entity) -> counter_group ('' for entity) -> timestamp -> interval

        refprefix = get_obj_refprefix(entity)
        refprefix_rows = self._rows.get(refprefix)
        if refprefix_rows is None:
            refprefix_rows = {}
            self._rows[refprefix] = refprefix_rows

        entity_rows = refprefix_rows.get(entity)
        if entity_rows is None:
            entity_rows = {}
            refprefix_rows[entity] = entity_rows

        instance_rows = entity_rows.get(instance)
        if instance_rows is None:
            instance_rows = {}
            entity_rows[instance] = instance_rows

        counter = self._all_counters_by_key.get(counter_key)
        if instance == '':
            counter_group = ''
        else:
            counter_group = counter.groupInfo.key if counter else 'unknown'        
        self._report_used_counter(refprefix, counter_group, counter)
        group_rows = instance_rows.get(counter_group)
        if group_rows is None:
            group_rows = {}
            instance_rows[counter_group] = group_rows

        timestamp_rows = group_rows.get(timestamp)
        if timestamp_rows is None:
            timestamp_rows = {}
            group_rows[timestamp] = timestamp_rows

        row = timestamp_rows.get(interval)
        if row is None:
            row = PerfResultRow(entity, instance, timestamp, interval)
            self._row_count += 1
            timestamp_rows[interval] = row
            self._intervals.add(interval)

        return row


    def _report_used_counter(self, refprefix: str, counter_group: str, counter: int|vim.PerformanceManager.CounterInfo):
        # refprefix -> counter group ('' for entity) -> counter key -> counter

        refprefix_counters = self._used_counters.get(refprefix)
        if refprefix_counters is None:
            refprefix_counters = {}
            self._used_counters[refprefix] = refprefix_counters

        group_counters = refprefix_counters.get(counter_group)
        if group_counters is None:
            group_counters = {}
            refprefix_counters[counter_group] = group_counters

        counter_key = counter if isinstance(counter, int) else counter.key
        group_counters[counter_key] = counter


    def _counter_sort_key(self, counter: vim.PerformanceManager.CounterInfo|int):
        counter_key = counter if isinstance(counter, int) else counter.key
        try:
            index = self._given_counter_keys.index(counter_key)
            return (index, '')
        except ValueError:
            return (len(index), get_counter_fullname(counter))


    @property
    def row_count(self):
        return self._row_count
    

    def iter_rows(self):
        #refprefix -> entity -> instance ('' for entity) -> counter_group ('' for entity) -> timestamp -> interval

        for refprefix_rows in self._rows.values():
            for entity_rows in refprefix_rows.values():
                for instance_rows in entity_rows.values():
                    for group_rows in instance_rows.values():
                        for timestamp_rows in group_rows.values():
                            for row in timestamp_rows.values():
                                yield row
    

    def iter_used_counters(self):
        # refprefix -> counter group ('' for entity) -> counter key -> counter

        for refprefix_counters in self._used_counters.values():
            for group_counters in refprefix_counters.values():
                for counter in group_counters.values():
                    yield counter


    def export_single(self, *, with_entity_ref=False, translate_percent=False):
        """
        Export to a tabulated text.
        """
        with_interval = len(self._intervals) > 1
        with_instance = False

        counters: set[vim.PerformanceManager.CounterInfo|int] = set()
        for refprefix_counters in self._used_counters.values():
            for group, group_counters in refprefix_counters.items():
                if group != '':
                    with_instance = True
                for counter in group_counters.values():
                    counters.add(counter)

        counters: list[vim.PerformanceManager.CounterInfo|int] = sorted(counters, key=self._counter_sort_key)

        headers = ['entity_name']
        if with_entity_ref:
            headers.append('entity_ref')
            headers.append('entity_type')
        if with_instance:
            headers.append('instance')
        headers.append('timestamp')
        if with_interval:
            headers.append('interval')
        if self._consolidate:
            headers.append('consolidated')
        
        consolidate_counters = set()
        for counter in counters:
            headers.append(get_counter_fullname(counter))
            if self._consolidate and counter.rollupType == 'average' and not any(get_counter_fullname(counter) == f"{counter.groupInfo.key}.{counter.nameInfo.key}:maximum" for counter in counters):
                consolidate_counters.add(counter)
                headers.append(f"{counter.groupInfo.key}.{counter.nameInfo.key}:max")

        rows = []
        for row in self.iter_rows():
            # Prepare output row
            outrow = [get_obj_name(row.entity, use_cache=True)]
            if with_entity_ref:
                outrow.append(get_obj_ref(row.entity))
                outrow.append(get_obj_typename(row.entity))

            if with_instance:
                outrow.append(row.instance)

            outrow.append(row.timestamp)
            if with_interval:
                outrow.append(row.interval)
    
            if self._consolidate:
                outrow.append(row.values_count)

            for counter in counters:
                value = row.get_value_by_counter(counter, translate_percent)
                outrow.append(value)

                if self._consolidate and counter.rollupType == 'average' and counter in consolidate_counters:
                    value = row.get_maximum_value_by_counter(counter, translate_percent)
                    outrow.append(value)

            rows.append(outrow)

            # sort by: entity_name, instance (if given), timestamp
            rows.sort(key=lambda row: (
                row[0], # entity_name
                row[1 + (2 if with_entity_ref else 0)] if with_instance else '', # instance
                row[1 + (2 if with_entity_ref else 0) + (1 if with_instance else 0)], # timestamp
            ))
            return headers, rows
            

    def export_multi(self, out: os.PathLike|IOBase = TABULAR_OUT, *, dir: os.PathLike = None, counter_details=False, with_entity_ref=False, translate_percent=False):
        """
        Export to entity-and-instance-specific tables.
        """
        if _logger.isEnabledFor(logging.DEBUG):
            t0 = time_ns()
        
        for refprefix in sorted(self._rows.keys()):
            for group in sorted(self._used_counters[refprefix].keys()):
                counters = sorted(self._used_counters[refprefix][group].values(), key=self._counter_sort_key)
                        
                headers = ['entity_name']
                if with_entity_ref:
                    headers.append('entity_ref')

                if group == '':
                    title = f'perfdata_{refprefix}'
                else:
                    headers.append('instance')
                    title = f'perfdata_{refprefix}_instance_{group.lower()}'

                headers.append('timestamp')
                headers.append('interval')
                if self._consolidate:
                    headers.append('consolidated')
                    
                preheaders_before_counters = [None] * len(headers)

                consolidate_counters = set()
                for counter in counters:
                    headers.append(get_counter_fullname(counter))
                    if self._consolidate and counter.rollupType == 'average' and not any(get_counter_fullname(counter) == f"{counter.groupInfo.key}.{counter.nameInfo.key}:maximum" for counter in counters):
                        consolidate_counters.add(counter)
                        headers.append(f"{counter.groupInfo.key}.{counter.nameInfo.key}:max")

                with tabular_dumper(out, title=title, dir=dir or self._vcenter.data_dir, scope=self._vcenter.scope, truncate=True, excel=settings.CSV_EXCEL) as table:
                    if counter_details:
                        preheaders_before_counters[0] = '(counter_key)'
                        row = preheaders_before_counters + [counter if isinstance(counter, int) else counter.key for counter in counters]
                        table.append(row)

                        preheaders_before_counters[0] = '(rollup_type)'
                        row = preheaders_before_counters + [None if isinstance(counter, int) else counter.rollupType for counter in counters]
                        table.append(row)

                        preheaders_before_counters[0] = '(stats_type)'
                        row = preheaders_before_counters + [None if isinstance(counter, int) else counter.statsType for counter in counters]
                        table.append(row)

                        preheaders_before_counters[0] = '(unit)'
                        row = preheaders_before_counters + [None if isinstance(counter, int) else ("ratio(percent)" if counter.unitInfo.key == "percent" and translate_percent else counter.unitInfo.key) for counter in counters]
                        table.append(row)

                        preheaders_before_counters[0] = '(label)'
                        row = preheaders_before_counters + [None if isinstance(counter, int) else counter.nameInfo.label for counter in counters]
                        table.append(row)

                        preheaders_before_counters[0] = '(summary)'
                        row = preheaders_before_counters + [None if isinstance(counter, int) else counter.nameInfo.summary for counter in counters]
                        table.append(row)

                    table.append(headers)

                    for obj, obj_rows in self._rows[refprefix].items():
                        for instance in obj_rows.keys() if group else ['']:
                            group_rows = obj_rows.get(instance)
                            if group_rows:
                                timestamp_rows = group_rows.get(group)
                                if timestamp_rows:
                                    for timestamp in sorted(timestamp_rows.keys(), reverse=True):
                                        interval_rows = timestamp_rows[timestamp]
                                        for interval, row in interval_rows.items():
                                            # Prepare output row
                                            outrow = [get_obj_name(obj, use_cache=True)]
                                            if with_entity_ref:
                                                outrow.append(get_obj_ref(obj))
                                                
                                            if group != '':
                                                outrow.append(instance)

                                            outrow.append(timestamp)
                                            outrow.append(interval)
                                    
                                            if self._consolidate:
                                                outrow.append(row.values_count)
                                            
                                            for counter in counters:
                                                value = row.get_value_by_counter(counter, translate_percent)
                                                outrow.append(value)
                                                                            
                                                if self._consolidate and counter.rollupType == 'average' and counter in consolidate_counters:
                                                    value = row.get_maximum_value_by_counter(counter, translate_percent)
                                                    outrow.append(value)

                                            table.append(outrow)
    
        if _logger.isEnabledFor(logging.DEBUG):
            _logger.debug(f"export: done ({(time_ns() - t0)/10**6:,.1f} ms)")


class PerfResultRow:
    def __init__(self, entity: vim.ManagedEntity, instance: str, timestamp: datetime, interval: int):
        self.entity = entity
        self.instance = instance
        self.timestamp = timestamp
        self.interval = interval
        self._values_by_counter_key: dict[int,dict[datetime,int]] = {}

    def add_value(self, counter_key: int, actual_timestamp: int, value: int):
        values = self._values_by_counter_key.get(counter_key)
        if values is None:
            values = {}
            self._values_by_counter_key[counter_key] = values
        values[actual_timestamp] = value

    @property
    def values_count(self):
        values_count_min = None
        for values in self._values_by_counter_key.values():
            if values_count_min is None or len(values) < values_count_min:
                values_count_min = len(values)
        return values_count_min
    
    def _convert_value(self, value: int|float, translate_percent: bool):
        if not isinstance(value, int):
            value = int(value)

        if translate_percent:
            if translate_percent == 100:
                return value / 100
            else:
                return value / 10000
            
        return value

    def get_value_by_counter(self, counter: vim.PerformanceManager.CounterInfo|int, translate_percent: bool):
        values_by_timestamp = self._values_by_counter_key.get(counter if isinstance(counter, int) else counter.key)
        if values_by_timestamp is None or len(values_by_timestamp) == 0:
            return None

        translate_percent = translate_percent if is_percent_counter(counter) else False
        if len(values_by_timestamp) == 1:
            return self._convert_value(list(values_by_timestamp.values())[0], translate_percent)
        
        values = values_by_timestamp.values()
        if isinstance(counter, int) or counter.rollupType == 'average' or counter.rollupType == 'none':
            average = sum(values) / len(values)
            return self._convert_value(average, translate_percent)
        elif counter.rollupType == 'summation':
            return self._convert_value(sum(values), translate_percent)
        elif counter.rollupType == 'maximum':
            return self._convert_value(max(values), translate_percent)
        elif counter.rollupType == 'minimum':
            return self._convert_value(max(values), translate_percent)
        else: # latest
            latest_timestamp = max(values_by_timestamp.keys())
            return self._convert_value(values_by_timestamp[latest_timestamp], translate_percent)

    def get_maximum_value_by_counter(self, counter: vim.PerformanceManager.CounterInfo|int, translate_percent: bool):
        values_by_timestamp = self._values_by_counter_key.get(counter if isinstance(counter, int) else counter.key)
        if values_by_timestamp is None or len(values_by_timestamp) == 0:
            return None
        
        translate_percent = translate_percent if is_percent_counter(counter) else False
        return self._convert_value(max(values_by_timestamp.values()), translate_percent)
    

def get_counter_fullname(counter: vim.PerformanceManager.CounterInfo):
    if isinstance(counter, int):
        return f'!unknown.{counter}'
    return f'{counter.groupInfo.key}.{counter.nameInfo.key}:{counter.rollupType}'
    

def is_percent_counter(counter: vim.PerformanceManager.CounterInfo):
    if isinstance(counter, int):
        return False
    return counter.unitInfo.key == 'percent'
