"""
List custom values defined on the system.
"""
import os
from argparse import ArgumentParser
from io import IOBase

from zut import out_table

from . import VCenterClient

_DEFAULT_OUT = 'stdout'

def list_custom_values(vcenter: VCenterClient, out: os.PathLike|IOBase = _DEFAULT_OUT):
    """
    Export custom fields.
    """
    headers=['name', 'key', 'obj_type', 'data_type']

    with out_table(out, title="customvalues", dir=vcenter.get_out_dir(), env=vcenter.env, headers=headers) as t:
        for field in vcenter.service_content.customFieldsManager.field:
            t.append([field.name, field.key, field.managedObjectType.__name__, field.type.__name__])

def _add_arguments(parser: ArgumentParser):
    parser.add_argument('-o', '--out', default=_DEFAULT_OUT, help="Output table (default: %(default)s).")

list_custom_values.add_arguments = _add_arguments
