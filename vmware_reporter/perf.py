"""
Analyze performance counters and metrics, and export performance data.
"""
from __future__ import annotations

from datetime import datetime
import logging
import os
import re
from argparse import ArgumentParser, RawTextHelpFormatter, _SubParsersAction
from io import IOBase

from pyVmomi import vim
from zut import (Header, add_func_command, get_description_text, get_help_text,
                 out_table, is_naive, make_aware)

from . import VCenterClient, get_obj_name, get_obj_ref, dictify_obj, get_obj_typename
from .settings import CONFIG, CONFIG_SECTION

_logger = logging.getLogger(__name__)


def add_perf_commands(commands_subparsers: _SubParsersAction[ArgumentParser], *, name: str):
    parser = commands_subparsers.add_parser(name, help=get_help_text(__doc__), description=get_description_text(__doc__), formatter_class=RawTextHelpFormatter, add_help=False)

    group = parser.add_argument_group(title='Command options')
    group.add_argument('-h', '--help', action='help', help=f"Show this command help message and exit.")

    subparsers = parser.add_subparsers(title='Sub commands')
    add_func_command(subparsers, list_perf_counters, name='counters')
    add_func_command(subparsers, list_perf_intervals, name='intervals')
    add_func_command(subparsers, list_perf_metrics, name='metrics')    
    add_func_command(subparsers, extract_perf_data, name='data')


_DEFAULT_OUT = '{title}.csv'


def list_perf_counters(vcenter: VCenterClient, group: str = None, level: int = None, out: os.PathLike|IOBase = _DEFAULT_OUT):
    headers = [
        'key', 'group', 'name', 'rollup_type', 'stats_type', 'unit', 'level', 'per_device_level'
    ]

    pm = vcenter.service_content.perfManager

    with out_table(out, title='perf_counters', dir=vcenter.out_dir, env=vcenter.env, headers=headers) as t:
        for counter in sorted(pm.perfCounter, key=lambda counter: (counter.groupInfo.key, counter.nameInfo.key, counter.rollupType)):
            if group is not None and counter.groupInfo.key != group:
                continue
            if level is not None and counter.level > level:
                continue        
            t.append([counter.key, counter.groupInfo.key, counter.nameInfo.key, counter.rollupType, counter.statsType, counter.unitInfo.key, counter.level, counter.perDeviceLevel])

def _add_arguments(parser: ArgumentParser):
    parser.add_argument('-g', '--group', help="Group of the counters (example: cpu)")
    parser.add_argument('-l', '--level', type=int, help="Max level of the counters (from 1 to 4)")
    parser.add_argument('-o', '--out', default=_DEFAULT_OUT, help="Output table (default: %(default)s).")

list_perf_counters.add_arguments = _add_arguments


def list_perf_intervals(vcenter: VCenterClient, out: os.PathLike|IOBase = _DEFAULT_OUT):
    headers = [
        'key', 'name', 'enabled', 'level', 'sampling_period'
    ]

    pm = vcenter.service_content.perfManager

    with out_table(out, title='perf_intervals', dir=vcenter.out_dir, env=vcenter.env, headers=headers) as t:
        for interval in sorted(pm.historicalInterval, key=lambda interval: interval.samplingPeriod):
            t.append([interval.key, interval.name, interval.enabled, interval.level, interval.samplingPeriod])


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('-o', '--out', default=_DEFAULT_OUT, help="Output table (default: %(default)s).")

list_perf_intervals.add_arguments = _add_arguments


def list_perf_metrics(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', first: bool = None, types: list[type|str]|type|str = None, out: os.PathLike|IOBase = _DEFAULT_OUT):
    """
    List available metrics per entity objects.
    """
    if first is None and not search:
        first = True
    first_types = []

    pm = vcenter.service_content.perfManager
    counters_by_key: dict[str,vim.PerformanceManager.CounterInfo] = {}
    for counter in sorted(pm.perfCounter, key=lambda counter: (counter.groupInfo.key, counter.nameInfo.key, counter.rollupType)):
        counters_by_key[counter.key] = counter
        
    headers = [
        'entity_name', 'entity_ref', 'entity_type', 'instance', 'key', 'group', 'name', 'rollup_type', 'stats_type', 'unit', 'level', 'per_device_level'
    ]

    with out_table(out, title='perf_metrics', dir=vcenter.out_dir, env=vcenter.env, headers=headers) as t:
        for obj in vcenter.iter_objs(types, search=search, normalize=normalize, key=key):
            if isinstance(obj, (vim.Folder, vim.Network)):
                continue # No performance data for these types

            if first:
                if type(obj) in first_types:
                    continue
                else:                    
                    first_types.append(type(obj))

            name = get_obj_name(obj)
            ref = get_obj_ref(obj)        
            _logger.info(f"List {name} ({ref}) metrics")
    
            for metric in pm.QueryAvailablePerfMetric(entity=obj):
                counter = counters_by_key[metric.counterId]
                t.append([
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
                    counter.perDeviceLevel
                ])


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('--first', action='store_true', default=None, help="Only handle the first object found for each type.")
    parser.add_argument('-t', '--type', dest='types', metavar='type', help="Managed object type name (example: datastore).")
    parser.add_argument('-o', '--out', default=_DEFAULT_OUT, help="Output table (default: %(default)s).")

list_perf_metrics.add_arguments = _add_arguments


_DEFAULT_INTERVAL = 1800
_DEFAULT_INSTANCE = '*'

def extract_perf_data(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, counters: list[str] = None, instance: str = _DEFAULT_INSTANCE, interval: int = _DEFAULT_INTERVAL, start: datetime|str = None, end: datetime|str = None, normalize: bool = False, key: str = 'name', first: bool = None, types: list[type|str]|type|str = None, out: os.PathLike|IOBase = _DEFAULT_OUT):
    if isinstance(start, str):
        start = datetime.fromisoformat(start)
        if is_naive(start):
            start = make_aware(start)
    
    if isinstance(end, str):
        end = datetime.fromisoformat(end)
        if is_naive(end):
            end = make_aware(end)
    
    first_types = []
    excluded_types = []

    pm = vcenter.service_content.perfManager
    counters_by_key: dict[str,vim.PerformanceManager.CounterInfo] = {}
    for counter in sorted(pm.perfCounter, key=lambda counter: (counter.groupInfo.key, counter.nameInfo.key, counter.rollupType)):
        counters_by_key[counter.key] = counter

    class SeriesInfo:
        def __init__(self, counter: vim.PerformanceManager.CounterInfo, series_index, headers_index):
            self.counter = counter
            self.series_index = series_index
            self.headers_index = headers_index
            self.is_percent = counter.unitInfo.key == 'percent'

        def convert_value(self, value):
            if self.is_percent:
                return value / 10000
            else:
                return value
    
    all_counters = []
    if counters:
        all_counters = counters
    else:
        for option in CONFIG.options(CONFIG_SECTION):
            if option.endswith('_counters'):
                for counter in CONFIG.getlist(CONFIG_SECTION, option, fallback=[], delimiter=','):
                    if not counter in all_counters:
                        all_counters.append(counter)
 
    headers = [
        'entity_name', 'entity_ref', 'entity_type', 'instance', 'interval', 'timestamp', *all_counters
    ]

    with out_table(out, title='perf_data', dir=vcenter.out_dir, env=vcenter.env, headers=headers) as t:
        for obj in vcenter.iter_objs(types, search=search, normalize=normalize, key=key):
            if isinstance(obj, (vim.Folder, vim.Network)):
                continue # No performance data for these types

            if first:
                if type(obj) in first_types:
                    continue
                else:
                    first_types.append(type(obj))

            obj_counters = []
            if counters:
                obj_counters = list(counters)
            else:
                obj_counters = CONFIG.getlist(CONFIG_SECTION, f"{type(obj).__name__.split('.')[-1].lower()}_counters", fallback=[], delimiter=',')
            
            if not obj_counters:
                if not type(obj) in excluded_types:
                    _logger.warning(f"Exclude type {type(obj).__name__}: no counter defined")
                    excluded_types.append(type(obj))
                continue

            try:
                name = get_obj_name(obj)
                ref = get_obj_ref(obj)        
                _logger.info(f"Extract {name} ({ref}) perf data")
                
                # Determine metrics
                metrics = []
                headers_index_per_counter_key: dict[str,int] = {}
                for metric in pm.QueryAvailablePerfMetric(entity=obj):
                    counter = counters_by_key[metric.counterId]                
                    counter_name = f"{counter.groupInfo.key}.{counter.nameInfo.key}"
                    counter_qualifiedname = f"{counter_name}:{counter.rollupType}"
                    if counter_name in obj_counters:
                        obj_counters.remove(counter_name)
                        headers_index_per_counter_key[metric.counterId] = headers.index(counter_name)
                        metrics.append(vim.PerformanceManager.MetricId(counterId=metric.counterId, instance=instance))
                    elif counter_qualifiedname in obj_counters:
                        obj_counters.remove(counter_qualifiedname)
                        headers_index_per_counter_key[metric.counterId] = headers.index(counter_qualifiedname)
                        metrics.append(vim.PerformanceManager.MetricId(counterId=metric.counterId, instance=instance))

                if obj_counters:
                    _logger.warning(f"Counter(s) not available for {name} ({ref}): {', '.join(obj_counters)}")          
                            
                # Query stats
                spec = vim.PerformanceManager.QuerySpec(entity=obj, metricId=metrics, intervalId=interval, startTime=start, endTime=end)

                for result in pm.QueryStats([spec]):
                    # Analyze result series
                    series_by_instance: dict[str,list[SeriesInfo]] = {}
                    for series_index, series in enumerate(result.value):
                        metric: vim.PerformanceManager.MetricId = series.id
                        headers_index = headers_index_per_counter_key[metric.counterId]
                        info = SeriesInfo(counters_by_key[metric.counterId], series_index, headers_index)
                        if instance_series := series_by_instance.get(metric.instance):
                            instance_series.append(info)
                        else:
                            series_by_instance[metric.instance] = [info]
                        
                    # Export data
                    for metric_instance, seriesinfo_list in series_by_instance.items():
                        for sample_index, sample_info in enumerate(result.sampleInfo):
                            row = [
                                name,
                                ref,
                                get_obj_typename(obj),
                                metric_instance,
                                sample_info.interval,
                                sample_info.timestamp,
                            ]
                            while len(row) < len(headers):
                                row.append(None)

                            for info in seriesinfo_list:
                                value = result.value[info.series_index].value[sample_index]
                                row[info.headers_index] = info.convert_value(value)
                            
                            t.append(row)

            except Exception as err:
                _logger.exception(f"Error while extracting {str(obj)} perf data: {err}")

def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-c', '--counters', nargs='*', help="Counter(s).")
    parser.add_argument('--instance', default=_DEFAULT_INSTANCE, help="Metric instance (default: %(default)s).")
    parser.add_argument('-i', '--interval', type=int, default=_DEFAULT_INTERVAL, help="Interval sampling period (see `perf intervals` command). Default: 1800 (Past week).")
    parser.add_argument('--start', help="The server time from which to obtain counters (the specified start time is EXCLUDED from the returned samples).")
    parser.add_argument('--end', help="The server time up to which statistics are retrieved (the specified end time is INCLUDED in the returned samples).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('--first', action='store_true', default=None, help="Only handle the first object found for each type.")
    parser.add_argument('-t', '--type', dest='types', metavar='type', help="Managed object type name (example: datastore).")
    parser.add_argument('-o', '--out', default=_DEFAULT_OUT, help="Output table (default: %(default)s).")

extract_perf_data.add_arguments = _add_arguments
