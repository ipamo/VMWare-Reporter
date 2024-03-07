from __future__ import annotations

import json
import logging
import os
import re
import sys
from argparse import ArgumentParser
from contextlib import nullcontext
from io import IOBase

from .client import VCenterClient
from .inspect import get_obj_name, get_obj_ref, dump_obj
from .utils import ExtendedJSONEncoder

logger = logging.getLogger(__name__)

DEFAULT_OUT_MASK = VCenterClient.DEFAULT_OUT_DIR_MASK.joinpath("dumps", "{type}", "{name} ({ref}).json")


def export_obj_dump(vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', first: bool = False, types: list[type|str]|type|str = None, out: os.PathLike|IOBase = DEFAULT_OUT_MASK):
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

    for obj in vcenter.iterate_objs(types, search, normalize=normalize, key=key):
        if first:
            if type(obj) in first_types:
                continue

        name = get_obj_name(obj)
        ref = get_obj_ref(obj)
        data = dump_obj(obj)

        if isinstance(out, IOBase):
            obj_out = out
            obj_out_name = getattr(obj_out, 'name', '<io>')
        else:
            obj_out = vcenter.compile_path_mask(out, type=type(obj).__name__, name=name, ref=ref, parent_mkdir=True)
            obj_out_name = str(obj_out)
        
        logger.info(f"export {name} ({ref}) to {obj_out_name}")
        with nullcontext(obj_out) if isinstance(obj_out, IOBase) else open(obj_out, 'w', encoding='utf-8') as fp:
            json.dump(data, fp=fp, indent=4, cls=ExtendedJSONEncoder, ensure_ascii=False)

        if first:
            first_types.append(type(obj))


def add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")
    parser.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    parser.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    parser.add_argument('-t', '--type', dest='types', metavar='type', help="Managed object type name (example: datastore).")
    parser.add_argument('--first', action='store_true', help="Only handle the first object found for each type.")
    parser.add_argument('-o', '--out', default=DEFAULT_OUT_MASK, help="Output JSON file (default: %(default)s).")

export_obj_dump.add_arguments = add_arguments
