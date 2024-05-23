from __future__ import annotations

import logging
import os
import re
from argparse import ArgumentParser

from pyVmomi import vim

from zut import out_table, Literal, get_config_paths
from reporter_utils.extractions import Extraction, get_extractions

import vmware_reporter
from . import VCenterClient, dictify_value, dictify_obj, get_obj_path, get_obj_ref
from .vm import extract_disks, extract_nics

logger = logging.getLogger(__name__)

class VMWareExtraction(Extraction):
    def __init__(self, *args, vcenter: VCenterClient, **kwargs):
        super().__init__(*args, **kwargs)
        self.vcenter = vcenter

    def get_objs(self, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name'):
        return self.vcenter.get_objs(self.type, search, normalize=normalize, key=key)
        
    def get_obj_name_or_ignore_reason(self, obj: vim.ManagedEntity):
        try:
            return obj.name, None
        except vim.fault.NoPermission:
            return get_obj_ref(obj), "no permission"
    
    def finalize_extracted_obj(self, obj: vim.ManagedObject):
        return dictify_obj(obj)
    
    def get_obj_attr(self, obj, attr: str):
        if attr == 'ref':
            return get_obj_ref(obj)
        elif attr == 'path':
            return get_obj_path(obj)
        elif isinstance(obj, dict):
            return obj.get(attr)
        else:
            return dictify_value(getattr(obj, attr))
    
    def search_parent(self, obj: vim.ManagedEntity, *params: str):
        if len(params) != 1:
            raise ValueError(f"search_parent() must have exactly one param")

        parent_type = VCenterClient.OBJ_TYPES[params[0].lower()]
        result = obj
        while not isinstance(result, parent_type):
            result = result.parent
            if result is None:
                break
        return result
    
    def disks(self, obj: vim.VirtualMachine):
        return extract_disks(obj)
    
    def nics(self, obj: vim.VirtualMachine):
        return extract_nics(obj, vcenter=self.vcenter)


_extractions: dict[VCenterClient,dict[str,VMWareExtraction]] = {}

def _get_vmware_extractions(vcenter: VCenterClient):
    if not vcenter in _extractions:
        _extractions[vcenter] = get_extractions(vmware_reporter, cls=VMWareExtraction, vcenter=vcenter)
    return _extractions[vcenter]


def handle(vcenter: VCenterClient, name: str = None, operation: str = None, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = False, key: str = 'name', out: os.PathLike = None, extractfmt: Literal['json', 'csv', 'tabulate'] = None, top: int = None):
    """
    Extract structured data from VMWare managed objects as JSON, CSV or table.
    """
    extractions = _get_vmware_extractions(vcenter)
    if operation == '--list':
        with out_table(out, tablefmt='csv' if extractfmt == 'csv' else None, headers=['name', 'definition_file'], title=False) as t:
            for extraction in extractions.values():
                t.append([extraction.name, extraction.definition_file])

    else:
        extraction = extractions[name]
        extraction.extract(search=search, normalize=normalize, key=key, out=out, extractfmt=extractfmt, top=top)


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('search', nargs='*', help="Search term(s).")

    group = parser.add_argument_group(title="Operations")
    group = group.add_mutually_exclusive_group()
    group.add_argument('--list', const='--list', action='store_const', dest='operation', help="List available extractions.")

    extractions = _get_vmware_extractions(None)
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
    me_group.add_argument('--json', action="store_const", const='json', dest='extractfmt', help="Force JSON output (even if out is set to stdout or stderr).")
    me_group.add_argument('--csv', action="store_const", const='csv', dest='extractfmt', help="Force CSV output (even if out is set to stdout or stderr).")

    group.add_argument('--top', type=int)


def add_doc():
    paths = get_config_paths(vmware_reporter, 'extractions/{name}.{py,json}')
    
    result = f"""
    Extractions are defined in the following Python or JSON files (first match is used):"""
    #TODO: python files

    for path in reversed(paths):
        result += f"""
        - {path}"""

    return result

handle.add_arguments = _add_arguments
handle.add_doc = add_doc
