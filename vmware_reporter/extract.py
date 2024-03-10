from __future__ import annotations

import logging
import os
import re
from argparse import ArgumentParser

from pyVmomi import vim

from reporter_utils import get_appdata_paths, tabulate
from reporter_utils.extractions import ExtractFormat, Extraction, get_extrations

from .client import VCenterClient, __prog_modulepath__
from .inspect import dictify_value, dump_obj, get_obj_path, get_obj_ref

logger = logging.getLogger(__name__)

class VMWareExtraction(Extraction):    
    def get_objs(self, vcenter: VCenterClient, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name'):
        return vcenter.list_objs(self.type, search, normalize=normalize, key=key)
        
    def get_obj_name_or_ignore_reason(self, obj: vim.ManagedEntity):
        try:
            return obj.name, None
        except vim.fault.NoPermission:
            return get_obj_ref(obj), "no permission"
    
    def finalize_extracted_obj(self, obj: vim.ManagedObject):
        return dump_obj(obj)
    
    def get_obj_attr(self, obj, attr: str):
        if attr == 'ref':
            return get_obj_ref(obj)
        elif attr == 'path':
            return get_obj_path(obj)
        elif isinstance(obj, dict):
            return obj.get(attr)
        else:
            return dictify_value(getattr(obj, attr))
    
    def search_parent(self, obj: vim.ManagedEntity, params: list[str]):
        if len(params) != 1:
            raise ValueError(f"search_parent() must have exactly one param")

        parent_ype = VCenterClient.OBJ_TYPES[params[0].lower()]
        result = obj
        while not isinstance(result, parent_ype):
            result = result.parent
            if result is None:
                break
        return result

extractions = get_extrations(__prog_modulepath__, cls=VMWareExtraction)


def handle(vcenter: VCenterClient, name: str = None, operation: str = None, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', out: os.PathLike = None, format: ExtractFormat = None, top: int = None):
    """
    Extract structured data from VMWare managed objects as JSON, CSV or table.
    """
    if operation == '--list':
        data = []
        for extraction in extractions.values():
            data.append([extraction.name, extraction.definition_file])
        
        #TODO: out, csv, json
        tabulate(data, ['name', 'definition_file'])

    else:
        extraction = extractions[name]
        extraction.extract(vcenter=vcenter, search=search, normalize=normalize, key=key, out=out, format=format, top=top)


def add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")

    group = parser.add_argument_group(title="Operations")
    group = group.add_mutually_exclusive_group()
    group.add_argument('--list', const='--list', action='store_const', dest='operation', help="List available extractions.")

    if any(extractions):
        group = parser.add_argument_group(title="Extractions")
        group = group.add_mutually_exclusive_group()
        for extraction in extractions.values():
            group.add_argument(f'--{extraction.name}', const=extraction.name, action='store_const', dest='name', help=extraction.help)

    group = parser.add_argument_group(title="Options")
    group.add_argument('-n', '--normalize', action='store_true', help="Normalise search term(s).")
    group.add_argument('-k', '--key', choices=['name', 'ref'], default='name', help="Search key (default: %(default)s).")
    group.add_argument('-o', '--out', help="Output file (default: stdout).")
    me_group = group.add_mutually_exclusive_group()
    me_group.add_argument('--json', action="store_const", const=ExtractFormat.JSON, dest='format', help="Force JSON output (even if out is set to stdout or stderr).")
    me_group.add_argument('--csv', action="store_const", const=ExtractFormat.CSV, dest='format', help="Force CSV output (even if out is set to stdout or stderr).")

    group.add_argument('--top', type=int)


def add_doc():
    paths = get_appdata_paths(__prog_modulepath__, 'extractions/{name}.{py,json}')
    
    result = f"""
    Extractions are defined in the following Python or JSON files (first match is used):"""
    #TODO: python files

    for path in reversed(paths):
        result += f"""
        - {path}"""

    return result

handle.add_arguments = add_arguments
handle.add_doc = add_doc
