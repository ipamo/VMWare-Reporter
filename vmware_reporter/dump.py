from __future__ import annotations

import logging
import os
import re
import sys
from argparse import ArgumentParser
from io import IOBase

from .client import VCenterClient
from .inspect import get_obj_ref, dump_obj

logger = logging.getLogger(__name__)

DEFAULT_OUT_MASK = VCenterClient.DEFAULT_OUT_DIR_MASK.joinpath("dumps", "{type}", "{name} ({ref}).json")


def dump(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', first: bool = False, types: list[type|str]|type|str = None, out: os.PathLike|IOBase = DEFAULT_OUT_MASK):
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
            raise ValueError("out must contain {name} or {ref} placeholder")

    first_types = []

    for obj in vcenter.get_objs(types, search, normalize=normalize, key=key):
        if first:
            if type(obj) in first_types:
                continue

        name = obj.name
        ref = get_obj_ref(obj)
        
        if isinstance(out, IOBase):
            obj_out = out
        else:
            obj_out = vcenter.compile_path_mask(out, type=type(obj).__name__, name=name, ref=ref, parent_mkdir=True)
        
        dump_obj(obj, obj_out, title=f'{name} ({ref})')

        if first:
            first_types.append(type(obj))

def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('-t', '--type', dest='types', metavar='type', help="Managed object type name (example: datastore).")
    parser.add_argument('--first', action='store_true', help="Only handle the first object found for each type.")
    parser.add_argument('-o', '--out', default=DEFAULT_OUT_MASK, help="Output JSON file (default: %(default)s).")

dump.add_arguments = _add_arguments
