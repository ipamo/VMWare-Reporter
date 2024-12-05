"""
Export all available data about VMWare managed objects.
"""
from __future__ import annotations

import os
import re
import sys
from argparse import ArgumentParser
from io import IOBase

from . import VCenterClient, get_obj_name, get_obj_ref, get_obj_typename, export_obj
from .settings import EXPORT_OUT


def export(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', first: bool = False, types: list[type|str]|type|str = None, out: os.PathLike|IOBase = EXPORT_OUT):
    """
    Export all available data about VMWare managed objects to JSON files.
    """
    if not out or out == 'stdout':
        out = sys.stdout
    elif out == 'stderr':
        out = sys.stderr
    elif not isinstance(out, IOBase):
        if not isinstance(out, str):
            out = str(out)
        if not '{name}' in out and not '{ref}' in out:
            raise ValueError("out must contain at least {name} or {ref} placeholder")

    first_types = []

    for obj in vcenter.iter_objs(types, search, normalize=normalize, key=key):
        if first:
            if type(obj) in first_types:
                continue

        name = get_obj_name(obj)
        ref = get_obj_ref(obj)
        
        if isinstance(out, IOBase):
            obj_out = out
        else:
            obj_out = os.path.join(vcenter.data_dir, str(out).format(typename=get_obj_typename(obj), name=name, ref=ref, scope=vcenter.scope))
            obj_out_dir = os.path.dirname(obj_out)
            if obj_out_dir:
                os.makedirs(obj_out_dir, exist_ok=True)
        
        export_obj(obj, obj_out, title=f'{name} ({ref})')

        if first:
            first_types.append(type(obj))

def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('--first', action='store_true', help="Only handle the first object found for each type.")
    parser.add_argument('-t', '--type', dest='types', metavar='type', help="Managed object type name (example: datastore).")
    parser.add_argument('-o', '--out', default=EXPORT_OUT, help="Output JSON file (default: %(default)s).")

export.add_arguments = _add_arguments
