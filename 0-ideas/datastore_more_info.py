from __future__ import annotations
from argparse import ArgumentParser
import logging, re, os
from typing import Iterable
from pyVmomi import vim
from datetime import datetime
from .utils import tabulate
from .inspect import get_obj_ref
from .vcenter import vcenter

logger = logging.getLogger(__name__)


def add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="name(s) to search")
    parser.add_argument('--ref', action='store_true', dest='search_by_ref', help="search ref(s) instead of name(s)")
    parser.add_argument('--stat', action='store_true', help="extract statistics about top-level directories and VMDKs")
    parser.add_argument('-o', '--out', action="store", help=f"output CSV or Excel file")


def handle(search: list[str] = None, search_by_ref: bool = False, stat: bool = False, out: str = None):
    """
    List datastores.
    """
    with tabulate(out, table='Datastores') as o:
        o.print_title("Datastores")

        for obj in vcenter.enumerate_objs(vim.Datastore, search=search, search_by_ref=search_by_ref):
            ds = Datastore(obj)        
            logger.info(f"found datastore {ds.name}")

            o.append({
                'ref': ds.ref,
                'name': ds.name,      
                'capacity': ds.capacity,
                'freespace': ds.freespace,
                'scsi_device': ds.scsi_device, # permet de faire le lien avec les objets VirtualDisk de l'API Datacore.
                'scsi_partition': ds.scsi_partition,
                'other_disk': ds.other_disk,
            })

    if stat:
        # NOTE: we should export/compare with VMs data. See keys:
        # "backing"
        # "diskFile"
        # "files": {
        #     "logDirectory": "[VMFS-PROD-17] SVTRAMGTCSVR004/",
        #     "snapshotDirectory": "[VMFS-PROD-17] SVTRAMGTCSVR004/",
        #     "suspendDirectory": "[VMFS-PROD-17] SVTRAMGTCSVR004/",
        #     "vmPathName": "[VMFS-PROD-17] SVTRAMGTCSVR004/SVTRAMGTCSVR004.vmx"
        # },
        # "layout"
        # "layoutEx"
        # "perDatastoreUsage"

        # Extract paths
        paths: list[PathInfo] = []
        for ds in Datastore.instances.values():
            logger.info(f"extract paths from {ds.name}")
            try:
                paths += ds.extract_paths()
            except:
                logger.exception(f"cannot extract from {ds.name}")
                continue

        # Analyze towards DirInfo
        logger.info(f"analyze paths")
        for pi in paths:
            DirInfo.fetch(pi)

        # Exports
        with tabulate(out, table='Directories') as o:
            o.print_title("Directories")
            for dirstat in sorted(DirInfo.instances.values(), key=lambda e: e.path):
                o.append({
                    'datastore': dirstat.datastore.name,
                    'path': dirstat.path,
                    'vmdk_count': len(dirstat.vmdks),
                    'vmdk_size':  sum_size(dirstat.vmdks),
                    'vswps_count': len(dirstat.vswps),
                    'vswp_size':  sum_size(dirstat.vswps),
                    'snapshots_count': len(dirstat.snapshots),
                    'snapshots_size':  sum_size(dirstat.snapshots),
                    'logs_count': len(dirstat.logs),
                    'logs_size':  sum_size(dirstat.logs),
                    'sfs_count': len(dirstat.sfs),
                    'sfs_size':  sum_size(dirstat.sfs),
                    'others_count': len(dirstat.others),
                    'others_size': sum_size(dirstat.others),
                })


        with tabulate(out, table='VMDKs') as o:
            o.print_title("VMDKs")
            for vmdk in VmdkInfo.instances.values():
                o.append({
                    'datastore': vmdk.datastore.name,
                    'main_path': vmdk.main.path if vmdk.main else None,
                    'total_size': vmdk.size,
                    'data_size': vmdk.data.size if vmdk.data else None,
                    'main_size': vmdk.main.size if vmdk.main else None,
                    'other_size': sum_size(vmdk.others),
                    'data_path': os.path.basename(vmdk.data.path) if vmdk.data else None,
                    'other_paths': ', '.join(os.path.basename(other.path) for other in sorted(vmdk.others, key=lambda p: p.path)),
                })
                


class Datastore:
    instances: dict[str,Datastore] = {}  # key: ref
    scsi_devices: list[str] = []

    def __init__(self, obj: vim.Datastore):
        self.obj = obj

        self.ref = get_obj_ref(obj)
        if self.ref in self.instances:
            logger.warning(f"ref {self.ref} already registered for datastore {self.instances[self.ref].name}")
        else:
            self.instances[self.ref] = self
        
        self.name: str            
        try:
            self.name = obj.name
        except vim.fault.NoPermission as err:
            logger.error(f"cannot read name of {obj}: {type(err).__name__}")
            self.name = f"<{self.ref}>"
        except Exception as err:
            logger.error(f"cannot read name of {obj}: {err}")
            self.name = f"<{self.ref}>"

        self.capacity: int = int(obj.info.vmfs.capacity)
        self.freespace: int = int(obj.info.freeSpace)

        self.scsi_device: str = None
        self.scsi_partition: int = None
        self.other_disk: str = None

        if len(obj.info.vmfs.extent):
            extent = obj.info.vmfs.extent[0]
            if m:= re.match(r'^naa\.([0-9a-f]{32})$', extent.diskName):
                self.scsi_device = m.group(1)
                self.scsi_partition = extent.partition
                
                if self.scsi_device in Datastore.scsi_devices:
                    logger.warning(f"datastore {self.name}: SCSI device {self.scsi_device} already used by another datastore")
                Datastore.scsi_devices.append(self.scsi_device)

        if not self.scsi_device:
            self.other_disk = ', '.join(f'ex.diskName (partition {ex.partition})' for ex in obj.info.vmfs.extent)
            logger.warning(f"datastore {self.name} disk: {self.other_disk}")


    def extract_paths(self) -> list[PathInfo]:
        dspath_prefix = f'[{self.obj.name}]'
        details = vim.host.DatastoreBrowser.FileInfo.Details(fileSize=True, fileOwner=True, modification=True)
        search_spec = vim.host.DatastoreBrowser.SearchSpec(matchPattern=[], details=details)

        paths: list[PathInfo] = []

        task = self.obj.browser.SearchDatastoreSubFolders_Task(dspath_prefix, search_spec)
        vcenter.wait_for_task(task)
        for result in task.info.result:
            for file in result.file:
                paths.append(PathInfo(self.obj, result, file))

        return paths


class PathInfo:
    def __init__(self, datastore: vim.Datastore, result: vim.host.DatastoreBrowser.SearchResults, info: vim.host.DatastoreBrowser.FileInfo):
        self.datastore = datastore

        self.path = f'{result.folderPath}{info.path}'            

        # Remove datastore prefix from path
        dspath_prefix = f'[{self.datastore.name}]'            
        if self.path.startswith(dspath_prefix):
            self.path = self.path[len(dspath_prefix):].strip()
        else:
            logger.warning(f'invalid path: {self.path}, expected to start with {dspath_prefix}')

        self.is_dir = isinstance(info, vim.host.DatastoreBrowser.FolderInfo)
                
        self.size: int|None = int(info.fileSize) if info.fileSize is not None else None
        self.mtime: datetime|None = info.modification
        self.owner: str|None = info.owner

    def __str__(self):
        return self.path

# -----------------------------------------------------------------------------
# Get stats about top-level directories
#
def sum_size(elements: Iterable[PathInfo]):
    return sum(element.size for element in elements)


class VmdkInfo:
    instances: dict[str,VmdkInfo] = {}  # key: datastoreprefix + main path

    def __init__(self):
        self.main: PathInfo = None
        self.data: PathInfo = None
        self.others: list[PathInfo] = []  # snapshots, ctk

    @property
    def datastore(self) -> vim.Datastore:
        if self.main:
            return self.main.datastore
        if self.data:
            return self.data.datastore
        for other in self.others:
            return other.datastore

    @property
    def size(self):
        return (self.main.size if self.main else 0) + (self.data.size if self.data else 0) + sum_size(self.others)

    @property
    def count(self):
        return (1 if self.main else 0) + (1 if self.data else 0) + len(self.others)


    @classmethod
    def fetch(cls, pi: PathInfo):
        if m := re.match(r'^(.+)\-(flat|rdmp|ctk|sesparse|delta)\.vmdk$', pi.path):
            main = m.group(1)
            if m2 := re.match(r'^(.+)\-\d{6}$', main):
                mainpath = m2.group(1) + '.vmdk'
            else:
                mainpath = main + '.vmdk'
            nature = m.group(2)
        elif m := re.match(r'^(.+)\-\d{6}\.vmdk$',  pi.path):
            mainpath = m.group(1) + '.vmdk'
            nature = '<6-digits>'
        else:
            mainpath = pi.path
            nature = None

        # Search VmdkInfo        
        key = f'[{pi.datastore.name}] {mainpath}'
        if key in cls.instances:
            vmdk = cls.instances[key]
        else:
            vmdk = cls()
            cls.instances[key] = vmdk

        # Update depending on nature
        if nature is None:
            vmdk.main = pi
        elif nature in ['flat', 'rdmp']:
            if vmdk.data:
                logger.warning(f'{pi.path}: data file already appended for this VMDK')
                vmdk.others.append(pi)
            else:
                vmdk.data = pi
        elif nature in ['ctk', 'sesparse', 'delta', '<6-digits>']:
            vmdk.others.append(pi)
        else:
            logger.warning(f'{pi.path}: unknown nature "{nature}"')
            vmdk.others.append(pi)

        return vmdk


class DirInfo:
    instances: dict[str,DirInfo] = {}  # key: datastoreprefix + toplevel directory name

    def __init__(self, datastore: vim.Datastore, path: str):
        self.datastore = datastore
        self.path = path
        self.vmdks: list[VmdkInfo] = []
        self.vswps: list[PathInfo] = []
        self.snapshots: list[PathInfo] = []
        self.sfs: list[PathInfo] = [] # system files, directories
        self.logs: list[PathInfo] = []
        self.others: list[PathInfo] = []


    @classmethod
    def fetch(cls, pi: PathInfo):
        parts = pi.path.split('/')

        if len(parts) <= 1 or parts[0] in ['.dvsData', '.vSphere-HA'] or parts[0].startswith('.naa.'):
            path = '<root>'
        else:
            path = parts[0]

        key = f'[{pi.datastore.name}]' + (f' {path}' if path else '')
        if key in cls.instances:
            dirinfo = cls.instances[key]
        else:
            dirinfo = cls(pi.datastore, path)
            cls.instances[key] = dirinfo

        if pi.is_dir:
            dirinfo.sfs.append(pi)
        elif pi.path in ['.fbb.sf', '.fdc.sf', '.pbc.sf', '.sbc.sf', '.vh.sf', '.sdd.sf', '.pb2.sf', '.jbc.sf']:
            dirinfo.sfs.append(pi)
        elif parts[0] in ['.dvsData', '.vSphere-HA'] or parts[0].startswith('.naa.'):
            dirinfo.sfs.append(pi)
        elif pi.path.endswith('.vmdk'):
            vmdk = VmdkInfo.fetch(pi)
            if not vmdk in dirinfo.vmdks:
                dirinfo.vmdks.append(vmdk)
        elif pi.path.endswith('.vswp'):
            dirinfo.vswps.append(pi)
        elif pi.path.endswith(('.vmsn', '.vmsd', '.vmem')):
            dirinfo.snapshots.append(pi)
        elif pi.path.endswith('.log'):
            dirinfo.logs.append(pi)
        else:
            dirinfo.others.append(pi)

        return dirinfo
