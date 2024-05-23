import os
from argparse import ArgumentParser
from io import IOBase

from zut import files
from zut.excel import is_excel_path, split_excel_path

from . import VCenterClient
from .customfield import list_customfields
from .datastore import list_datastores, list_datastore_stats
from .host import list_hosts
from .net import list_nets
from .vm import list_vms, list_vm_disks, list_vm_nics
from .settings import CONFIG, CONFIG_SECTION

_DEFAULT_OUT = CONFIG.get(CONFIG_SECTION, 'autoreport_out', fallback='autoreport.xlsx#{title}')

def handle(vcenter: VCenterClient, out: os.PathLike|IOBase = _DEFAULT_OUT):
    """
    Export automatic report.
    """
    if is_excel_path(out, accept_table_suffix=True):
        path, _ = split_excel_path(out, dir=vcenter.get_out_dir(), env=vcenter.env, title='__title__')
        target = path.parent.joinpath('archives')
        files.archivate(path, target, missing_ok=True, keep=True)

    list_vms(vcenter, out=out)
    list_vm_disks(vcenter, out=out)
    list_vm_nics(vcenter, out=out)
    list_hosts(vcenter, out=out)
    list_nets(vcenter, out=out)
    list_datastores(vcenter, out=out)
    list_datastore_stats(vcenter, out=out)
    list_customfields(vcenter, out=out)
    
def _add_arguments(parser: ArgumentParser):
    parser.add_argument('-o', '--out', default=_DEFAULT_OUT, help="Output tables (default: %(default)s).")

handle.add_arguments = _add_arguments
