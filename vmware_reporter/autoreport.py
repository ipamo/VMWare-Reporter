import os
from argparse import ArgumentParser
from io import IOBase

from zut import files
from zut.excel import is_excel_path, split_excel_path

from . import VCenterClient
from .cluster import cluster_list
from .customvalue import list_custom_values
from .datastore import list_datastores, list_datastore_stats
from .host import host_list
from .net import list_nets
from .pool import list_pool
from .vm import vm_list, vm_disks, vm_nics
from .tag import list_categories, list_tags
from .perf import perf_counter_list, perf_interval_list, perf_metric_list
from .settings import AUTOREPORT_OUT, ARCHIVES_DIR

def handle(vcenter: VCenterClient, out: os.PathLike|IOBase = AUTOREPORT_OUT):
    """
    Export automatic report.
    """
    if is_excel_path(out, accept_table_suffix=True):
        path, _ = split_excel_path(out, dir=vcenter.out_dir, env=vcenter.env, title='__title__')
        target = files.join(files.dirname(path), ARCHIVES_DIR)
        files.archivate(path, target, missing_ok=True, keep=True)

    vm_list(vcenter, out=out)
    vm_disks(vcenter, out=out)
    vm_nics(vcenter, out=out)
    host_list(vcenter, out=out)
    list_nets(vcenter, out=out)
    list_datastores(vcenter, out=out)
    list_datastore_stats(vcenter, out=out)
    cluster_list(vcenter, out=out)
    list_pool(vcenter, out=out)
    perf_interval_list(vcenter, out=out)
    perf_counter_list(vcenter, out=out)
    perf_metric_list(vcenter, first=True, out=out)
    list_categories(vcenter, out=out)
    list_tags(vcenter, out=out)
    list_custom_values(vcenter, out=out)
    
def _add_arguments(parser: ArgumentParser):
    parser.add_argument('-o', '--out', default=AUTOREPORT_OUT, help="Output tables (default: %(default)s).")

handle.add_arguments = _add_arguments
