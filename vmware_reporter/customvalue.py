"""
Dump custom value definitions.
"""
import os
from argparse import ArgumentParser
from io import IOBase

from zut import tabular_dumper

from . import VCenterClient, settings
from .settings import TABULAR_OUT, OUT_DIR


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output table (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

def dump_customvalues(vcenter: VCenterClient, out: os.PathLike|IOBase = TABULAR_OUT, dir: os.PathLike = None):
    headers=['name', 'key', 'obj_type', 'data_type']

    with tabular_dumper(out, title="customvalue", dir=dir or vcenter.data_dir, scope=vcenter.scope, headers=headers, truncate=True, excel=settings.CSV_EXCEL) as t:
        for field in vcenter.service_content.customFieldsManager.field:
            t.append([field.name, field.key, field.managedObjectType.__name__, field.type.__name__])

dump_customvalues.add_arguments = _add_arguments
handle = dump_customvalues
