import os
from argparse import ArgumentParser
from io import IOBase

from zut import files
from zut.excel import is_excel_path, split_excel_path

from . import VCenterClient
from .cluster import dump_clusters
from .customvalue import dump_customvalues
from .datastore import dump_datastores_all
from .host import dump_hosts
from .net import dump_nets
from .pool import dump_pools
from .vm import dump_vms_all
from .tag import dump_tags
from .perf import dump_perf_all
from .settings import OUT, ARCHIVATE, OUT_DIR

def _add_arguments(parser: ArgumentParser):
    parser.add_argument('-o', '--out', default=OUT, help="Output tables (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

def handle(vcenter: VCenterClient, out: os.PathLike|IOBase = OUT, dir: os.PathLike = None):
    """
    Dump all definitions.
    """
    if is_excel_path(out, accept_table_suffix=True) and ARCHIVATE:
        path, _ = split_excel_path(out, dir=dir or vcenter.data_dir, scope=vcenter.scope, title='__title__')
        files.archivate(path, ARCHIVATE, missing_ok=True, keep=True)

    dump_vms_all(vcenter, out=out, dir=dir)
    dump_hosts(vcenter, out=out, dir=dir)
    dump_nets(vcenter, out=out, dir=dir)
    dump_datastores_all(vcenter, out=out, dir=dir)
    dump_clusters(vcenter, out=out, dir=dir)
    dump_pools(vcenter, out=out, dir=dir)
    dump_perf_all(vcenter, out=out, dir=dir)
    dump_tags(vcenter, out=out, dir=dir)
    dump_customvalues(vcenter, out=out, dir=dir)

handle.add_arguments = _add_arguments
