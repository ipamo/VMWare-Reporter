"""
Analyze datastores or perform operations on datastores.
"""
from __future__ import annotations

import logging
import os
import re
from argparse import ArgumentParser
from contextlib import nullcontext
from datetime import datetime
from http import HTTPStatus
from io import IOBase
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlencode

import requests
from pyVmomi import vim
from zut import (Header, add_command, tabular_dumper)

from . import VCenterClient, get_obj_path, get_obj_ref, settings
from .settings import TABULAR_OUT, OUT_DIR

_logger = logging.getLogger(__name__)

def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output table (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

    subparsers = parser.add_subparsers(title='sub commands')
    add_command(subparsers, dump_datastores, name='.')
    add_command(subparsers, dump_datastores_all, name='all')
    add_command(subparsers, dump_datastore_elements, name='element')    
    add_command(subparsers, dump_datastore_stats, name='stat')
    add_command(subparsers, download_from_datastore, name='download')
    add_command(subparsers, upload_to_datastore, name='upload')
    add_command(subparsers, delete_from_datastore, name='delete')

def handle(vcenter: VCenterClient, **kwargs):
    dump_datastores(vcenter, **kwargs)

handle.add_arguments = _add_arguments


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output table (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

def dump_datastores_all(vcenter: VCenterClient, **kwargs):
    """
    Dump datastores with associated objects (datastore stats).
    """
    dump_datastores(vcenter, **kwargs)
    dump_datastore_stats(vcenter, **kwargs)

dump_datastores_all.add_arguments = _add_arguments


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output table (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

def dump_datastores(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', out: os.PathLike|IOBase = TABULAR_OUT, dir: os.PathLike = None):
    """
    Dump datastores.
    """
    headers = [
        'name',
        'ref',
        'overall_status',
        'config_status',
        Header('capacity', fmt='gib'),
        Header('freespace', fmt='gib'),
        'accessible',
        'maintenance_mode',
        'vmfs_version',
        'url',
        'extent',
        'multiple_host_access',
        'host_access',
    ]

    with tabular_dumper(out, title='datastore', dir=dir or vcenter.data_dir, scope=vcenter.scope, headers=headers, truncate=True, excel=settings.CSV_EXCEL) as t:
        for obj in vcenter.iter_objs(vim.Datastore, search, normalize=normalize, key=key):            
            try:
                _logger.info(f"Analyze datastore {obj.name}")

                t.append([
                    obj.name,
                    get_obj_ref(obj),
                    obj.overallStatus,
                    obj.configStatus,
                    obj.info.vmfs.capacity,
                    obj.info.freeSpace,
                    obj.summary.accessible,
                    obj.summary.maintenanceMode,
                    obj.info.vmfs.version,
                    obj.info.url,
                    parse_datacore_extent(obj.info.vmfs.extent),
                    obj.summary.multipleHostAccess,
                    get_datastore_host_summaries(obj),
                ])
            
            except Exception as err:
                _logger.exception(f"Error while analyzing {str(obj)}")

dump_datastores.add_arguments = _add_arguments


def get_datastore_host_summaries(datastore: vim.Datastore):
    summaries = []
    for host_access in sorted(datastore.host, key=lambda ha: ha.key.name):
        issue = ''

        if not host_access.mountInfo.mounted:
            issue = (', ' if issue else '') + f'notMounted'
        elif not host_access.mountInfo.accessible:
            issue = (', ' if issue else '') + f'notAccessible'

        if host_access.mountInfo.accessMode != 'readWrite':
            issue = (', ' if issue else '') + f'{host_access.mountInfo.accessMode}'

        summaries.append(host_access.key.name + (f' [{issue}]' if issue else ''))

    return summaries


def parse_datacore_extent(extent: list[vim.host.ScsiDisk.Partition]):
    if extent is None:
        return None
    return [part.diskName + ('' if part.partition == 1 else f' (partition {part.partition})') for part in extent]


def iterate_datastore_elements(vcenter: VCenterClient, obj: vim.Datastore, path: str = None, *, pattern: str = None, max_depth: int = None, with_size: bool = True, with_mtime: bool = True, with_owner: bool = True, case_sensitive: bool = False):
    """
    Iterate over datastore elements (files and directories).
    """
    if path:
        path = path.strip("/\\")
        dspath = f"[{obj.name}] {path}"
    else:
        dspath = f"[{obj.name}]"

    search_specs = {}
    search_specs["searchCaseInsensitive"] = not case_sensitive

    if with_size or with_mtime or with_owner:
        details = vim.host.DatastoreBrowser.FileInfo.Details()
        if with_size:
            details.fileSize = True
        if with_owner:
            details.fileOwner = True
        if with_mtime:
            details.modification = True
        search_specs["details"] = details

    if pattern == "#folders":
        search_specs["query"] = [vim.host.DatastoreBrowser.FolderQuery()]
    elif pattern:
        search_specs["matchPattern"] = [pattern]
    
    spec = vim.host.DatastoreBrowser.SearchSpec(**search_specs)

    if max_depth is None:
        task = obj.browser.SearchDatastoreSubFolders_Task(dspath, spec)
        vcenter.wait_for_task(task)
        for result in task.info.result:
            for element in result.file:
                yield DatastoreElement(obj, element, result.folderPath)
    else:
        task = obj.browser.SearchDatastore_Task(dspath, spec)
        vcenter.wait_for_task(task)
        for element in task.info.result.file:
            info = DatastoreElement(obj, element, task.info.result.folderPath)
            yield info
            if info.is_folder and max_depth > 1:
                yield from iterate_datastore_elements(vcenter, obj, info.path, pattern=pattern, max_depth=max_depth-1, with_size=with_size, with_mtime=with_mtime, with_owner=with_owner, case_sensitive=case_sensitive)


def get_datastore_stats(vcenter: VCenterClient, obj: vim.Datastore, path: str = None, *, pattern: str = None, max_depth: int = 1, case_sensitive: bool = False) -> list[DatastoreStat]:
    """
    Return stats about datastore elements (files and directories).
    """
    stats: dict[str,DatastoreStat] = {}

    for info in iterate_datastore_elements(vcenter, obj, path=path, case_sensitive=case_sensitive, pattern=pattern, with_mtime=True, with_owner=True, with_size=True):
        path_split = info.path.split("/")
        stat_path = "/".join(path_split[0:max_depth])
        depth = len(path_split)

        if stat_path in stats:
            stat = stats[stat_path]
        else:
            stat = DatastoreStat(obj, stat_path)
            stats[stat_path] = stat

        stat.size += info.size or 0

        if stat.mtime is None or info.mtime > stat.mtime:
            stat.mtime = info.mtime

        if stat.owner is None:
            stat.owner = info.owner
        elif stat.owner != info.owner:
            stat.owner = "<multi>"

        if depth > stat.depth:
            stat.depth = depth

        if info.path == stat_path:
            stat.nature = info.nature
        
        if info.nature == "Folder":
            stat.dir_count += 1
        elif info.nature == "File":
            stat.file_count += 1
        else:
            stat.other_count += 1

    return sorted(stats.values(), key=lambda stat: stat.path)


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument("--path", help="Detail elements only for the given path.")
    parser.add_argument("--max-depth", type=int, help="Detail elements until the given depth (default: %(default)s).")
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output file (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")
    parser.add_argument('--bytes', action="store_true", help="Display size as bytes.")

def dump_datastore_elements(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', path: str = None, max_depth: int = None, out: os.PathLike|IOBase = TABULAR_OUT, dir: os.PathLike = None, bytes: bool = False):
    """
    Dump datastore elements (files and directories).
    """
    with tabular_dumper(out, headers=DatastoreElement.get_headers(bytes=bytes), title="datastore_element", dir=dir or vcenter.data_dir, scope=vcenter.scope, truncate=True, excel=settings.CSV_EXCEL) as t:
        for obj in vcenter.get_objs(vim.Datastore, search, normalize=normalize, key=key, sort_key='name'):
            _logger.info(f'List datastore elements: {obj.name}')
        
            try:
                for info in sorted(iterate_datastore_elements(vcenter, obj, path=path, max_depth=max_depth), key=lambda info: info.path):
                    t.append(info.as_row())
            except:
                _logger.exception(f'Cannot analyze datastore {obj.name}')

dump_datastore_elements.add_arguments = _add_arguments


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument("--path", help="Detail elements only for the given path.")
    parser.add_argument("--max-depth", type=int, default=1, help="Detail elements until the given depth (default: %(default)s).")
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output file (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")
    parser.add_argument('--bytes', action="store_true", help="Display size as bytes.")

def dump_datastore_stats(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', path: str = None, max_depth: int = 1, out: os.PathLike|IOBase = TABULAR_OUT, dir: os.PathLike = None, bytes: bool = False):
    """
    Dump datastore stats (total number and size of files and directories).
    """
    with tabular_dumper(out, headers=DatastoreStat.get_headers(bytes=bytes), title="datastore_stat", dir=dir or vcenter.data_dir, scope=vcenter.scope, truncate=True, excel=settings.CSV_EXCEL) as t:
        for obj in vcenter.get_objs(vim.Datastore, search, normalize=normalize, key=key, sort_key='name'):
            _logger.info(f'Analyze datastore stats: {obj.name}')

            try:
                for info in get_datastore_stats(vcenter, obj, path=path, max_depth=max_depth):
                    t.append(info.as_row())
            except:
                _logger.exception(f'Cannot analyze datastore {obj.name}')

dump_datastore_stats.add_arguments = _add_arguments


def request_datastore_resource(method: str, vcenter: VCenterClient, datastore: vim.Datastore|str, path: os.PathLike, data: BinaryIO = None):
    datastore_name = datastore.name if isinstance(datastore, vim.Datastore) else datastore

    if not isinstance(path, Path):
        path = Path(path)

    path = "/folder/%s" % path.as_posix()
    params = {"dsName": datastore_name, "dcPath": get_obj_path(vcenter.datacenter, full=True)}
    url = f"https://{vcenter.host}" + path + '?' + urlencode(params)
    
    headers = {}
    if data:
        headers['Content-Type'] = 'application/octet-stream'

    response = requests.request(method, url, data=data, headers=headers, cookies=vcenter.cookie, verify=not vcenter.no_ssl_verify)
    response.raise_for_status()
    return response


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('datastore', help="Name of the datastore.")
    parser.add_argument('path', help="Path of the object to download on the datastore.")
    parser.add_argument('target', nargs='?', default='', help="Target path on the local file system.")

def download_from_datastore(vcenter: VCenterClient, datastore: vim.Datastore|str, path: os.PathLike, target: os.PathLike = ''):
    """
    Download a file from a datastore.
    """
    if isinstance(target, str) and (target == '' or target.endswith(('/', '\\'))):
        target += os.path.basename(path)

    response = request_datastore_resource('GET', vcenter, datastore, path)
    with open(target, 'wb') as fp:
        for chunck in response.iter_content():
            fp.write(chunck)
    
    datastore_name = datastore.name if isinstance(datastore, vim.Datastore) else datastore
    _logger.info("%s %s from datastore %s to %s", 'downloaded' if response.status_code == HTTPStatus.OK else f'{response.status_code} {response.reason}', path, datastore_name, target)

download_from_datastore.add_arguments = _add_arguments


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('source', help="Path of the source data.")
    parser.add_argument('datastore', help="Name of the datastore.")
    parser.add_argument('target', nargs='?', default='', help="Target path on the datastore.")

def upload_to_datastore(vcenter: VCenterClient, source: os.PathLike|BinaryIO, datastore: vim.Datastore|str, target: os.PathLike = ''):
    """
    Upload a file to a datastore.
    """
    if isinstance(target, str) and (target == '' or target.endswith(('/', '\\'))):
        if isinstance(source, IOBase):
            raise ValueError(f"Cannot upload to a directory ({target}): source is not a path")
        target += os.path.basename(source)

    if isinstance(source, IOBase) and hasattr(source, 'encoding'):
        raise ValueError("Cannot send files opened in text mode")

    with nullcontext(source) if isinstance(source, IOBase) else open(source, 'rb') as fp:
        response = request_datastore_resource('PUT', vcenter, datastore, target, data=fp)
    
    datastore_name = datastore.name if isinstance(datastore, vim.Datastore) else datastore
    _logger.info("uploaded %s to datastore %s: %s %s", source, datastore_name, 'created' if response.status_code == HTTPStatus.CREATED else ('updated' if response.status_code == HTTPStatus.OK else f'{response.status_code} {response.reason}'), target)

upload_to_datastore.add_arguments = _add_arguments


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('datastore', help="Name of the datastore.")
    parser.add_argument('path', help="Path of the object to delete on the datastore.")

def delete_from_datastore(vcenter: VCenterClient, datastore: vim.Datastore|str, path: os.PathLike):
    """
    Delete a file from a datastore.
    """
    response = request_datastore_resource('DELETE', vcenter, datastore, path)
    
    datastore_name = datastore.name if isinstance(datastore, vim.Datastore) else datastore
    _logger.info("%s %s from datastore %s", 'deleted' if response.status_code == HTTPStatus.NO_CONTENT else f'{response.status_code} {response.reason}', path, datastore_name)

delete_from_datastore.add_arguments = _add_arguments


def remove_datastore_prefix(obj: vim.Datastore, dspath: str):
    if dspath.startswith(f'[{obj.name}]'):
        dspath = dspath[len(f'[{obj.name}]'):]
        if dspath.startswith(' '):
            dspath = dspath[1:]
    return dspath


class DatastoreElement:
    def __init__(self, obj: vim.Datastore, info: vim.host.DatastoreBrowser.FileInfo, parent_dspath: str):
        self.obj = obj
        parent_dspath = remove_datastore_prefix(obj, parent_dspath)
        self.path: str = parent_dspath + info.path

        if isinstance(info, vim.host.DatastoreBrowser.FolderInfo):
            self.nature = "Folder"
        elif isinstance(info, vim.host.DatastoreBrowser.FileInfo):
            self.nature = "File"
        else:
            # FloppyImageFileInfo, FolderFileInfo, IsoImageFileInfo, VmConfigFileInfo, VmDiskFileInfo, VmLogFileInfo, VmNvramFileInfo, VmSnapshotFileInfo
            self.nature: str = type(info).__name__ 
            if self.nature.endswith('FileInfo'):
                self.nature = self.nature[:-len('FileInfo')]
            
        self.size: int|None = int(info.fileSize) if info.fileSize is not None else None
        self.mtime: datetime|None = info.modification
        self.owner: str|None = info.owner

    @property
    def is_folder(self):
        return self.nature == 'Folder'


    @classmethod
    def get_headers(cls, *, bytes = False):
        return [
            'datastore',
            'path',
            'nature',
            'size' if bytes else Header('size', fmt='gib'),
            'mtime',
            'owner',
        ]

    def as_row(self):
        return [
            self.obj.name,
            self.path,
            self.nature,
            self.size,
            self.mtime,
            self.owner,
        ]


class DatastoreStat:
    def __init__(self, obj: vim.Datastore, path: str):
        self.obj = obj
        self.path = path

        self.nature: str|None = None
        self.size: int = 0
        self.mtime: datetime|None = None
        self.owner: str|None = None

        self.depth: int = 0
        self.dir_count: int = 0
        self.file_count: int = 0
        self.other_count: int = 0

    @property
    def is_folder(self):
        return self.nature == 'Folder'

    @classmethod
    def get_headers(cls, *, bytes = False):
        return [
            'datastore',
            'path',
            'nature',
            'size' if bytes else Header('size', fmt='gib'),
            'mtime',
            'owner',
            'depth',
            'dir_count',
            'file_count',
            'other_count',
        ]

    def as_row(self):
        return [
            self.obj.name,
            self.path,
            self.nature,
            self.size,
            self.mtime,
            self.owner,
            self.depth,
            self.dir_count,
            self.file_count,
            self.other_count,
        ]
