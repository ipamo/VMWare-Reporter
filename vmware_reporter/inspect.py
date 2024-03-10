"""
Analyze VMWare manage objects.
"""
from __future__ import annotations
from datetime import date

import logging
import re
from types import BuiltinFunctionType, BuiltinMethodType, FunctionType, MethodType

from pyVmomi import vim

logger = logging.getLogger(__name__)


def get_obj_ref(obj: vim.ManagedObject) -> str:
    """
    Get the value of the Managed Object Reference (MOR) of the given object.

    See: https://vdc-repo.vmware.com/vmwb-repository/dcr-public/1ef6c336-7bef-477d-b9bb-caa1767d7e30/82521f49-9d9a-42b7-b19b-9e6cd9b30db1/mo-types-landing.html
    """
    text = str(obj)
    m = re.match(r"^'(.*)\:(.*)'$", text)
    if not m:
        raise ValueError(f'Invalid object identifier: {text}')
    
    expected_type = type(obj).__name__
    if m.group(1) != expected_type:
        raise ValueError(f'Invalid type for object identifier: {text}, expected: {expected_type}')
    return m.group(2)


def get_obj_path(obj: vim.ManagedEntity) -> str:
    """ Return the full path of the given vim managed entity. """
    if isinstance(obj, vim.Datacenter):
        return obj.name
    else:
        return get_obj_path(obj.parent) + "/" + obj.name
    

def dictify_value(data: list|str):
    """
    Return a dict if data is a list of OptionValue, StringValue or SystemIdentificationInfo objects, or if it is a string like guestOS.detailed.data.
    Otherwise leave as is.
    """
    def allinstance(enumerable_instance, element_type):
        any = False
        for element in enumerable_instance:
            any = True
            if not isinstance(element, element_type):
                return False
        return any
        
    if isinstance(data, list):
        if allinstance(data, vim.option.OptionValue) or allinstance(data, vim.CustomFieldsManager.StringValue): #example for vm: config.extraConfig, summary.config.customValue
            result = {}
            for ov in data:
                key = ov.key
                value = ov.value
                if key == "guestOS.detailed.data":
                    value = dictify_value(value)
                result[key] = value
            return result

        elif allinstance(data, vim.host.SystemIdentificationInfo): #example for host: summary.hardware.otherIdentifyingInfo
            result = {}
            for ov in data:
                key = ov.identifierType.key
                value = ov.identifierValue
                result[key] = value
            return result

        else:
            return data
    
    if isinstance(data, str):
        if matches := re.findall(r"([a-zA-Z0-9]+)='([^']+)'", data): # example: guestOS.detailed.data
            result = {}
            for m in matches:
                key = m[0]
                value = m[1]
                if key == 'bitness' and re.match(r'^\d+$', value):
                    value = int(value)
                result[key] = value
            return result

        else:
            return data       
        
    else:
        return data


def dump_obj(obj: vim.ManagedEntity, *, object_types=False, exclude_keys=[], max_depth=None) -> dict:
    """
    Export all available information about the given VMWare managed object to a dictionnary.
    """    
    for key in ['dynamicProperty', 'recentTask']:
        if not key in exclude_keys:
            exclude_keys.append(key)
    exclude_keys_containing = ['capability', 'alarm']
    keypath = []

    def keypath_str():
        s = ''
        for key in keypath:
            s += ('.' if s and not isinstance(key, int) else '') + (f"[{key}]" if isinstance(key, int) else key)
        return s

    def forward(key: str):
        keypath.append(key)

    def backward():
        del keypath[-1]

    def dump_object(obj: object):
        result = { '_type': type(obj).__name__ } if object_types else {}
        any = False
        for key in dir(obj):
            ignore = False
            if key.startswith('_') or key in exclude_keys:
                ignore = True
            else:
                for containing in exclude_keys_containing:
                    if containing in key.lower():
                        ignore = True
                        break

            if ignore:
                continue

            forward(key)
            
            try:
                value = getattr(obj, key)
            except: # problem getting the data (e.g. invalid/not-supported accessor)
                _key = keypath_str()
                if _key not in ['configManagerEnabled', 'environmentBrowser']:
                    logger.error('Cannot read attribute: %s', _key)
                value = "!error:cannot_read"
            
            value = dump_any(value)

            if value is not None:
                result[key] = value
                any = True

            backward()

        if any:
            return result

    def dump_dict(data: dict):
        result = {}
        any = False
        for key in data:
            forward(key)
            value = dump_any(data[key])
            if value is not None:
                result[key] = value
                any = True
            backward()

        if any:
            return result

    def dump_list(data: list):
        result = dictify_value(data)
        if isinstance(result, dict):
            return result

        # general case
        result = []
        any = False
        for i, value in enumerate(data):
            forward(i)
            extracted = dump_any(value)
            if extracted is not None:
                result.append(extracted)
                any = True
            backward()

        if any:
            return result

    def dump_any(data):
        if data is None or isinstance(data, (type, FunctionType, MethodType, BuiltinMethodType, BuiltinFunctionType)):
            return None
        
        elif isinstance(data, (str, int, float, complex)):
            return data

        elif isinstance(data, date):
            if data.year == 1970 and data.month == 1 and data.day == 1:
                return None
            return data

        elif isinstance(data, vim.ManagedObject):
            if not keypath: # depth == 0
                result = identify_obj(data)
                for key, value in dump_object(data).items():
                    result[key] = value
                return result
            else:
                return identify_obj(data)

        elif max_depth and len(keypath) >= max_depth:
            logger.error('Reached max depth: %s', type(data).__name__)
            return f"!error:max_depth({type(data).__name__})"

        elif isinstance(data, dict):
            return dump_dict(data)

        elif isinstance(data, list):
            return dump_list(data)
            
        else:
            return dump_object(data)

    return dump_any(obj)


def identify_obj(obj: vim.ManagedObject) -> dict:
    if obj is None:
        return None

    if not isinstance(obj, vim.ManagedObject):
        raise ValueError(f'invalid type: {type(obj)}')

    data = {
        "_type": type(obj).__name__, # managed object type
        "ref": get_obj_ref(obj),
    }

    try:
        if name := getattr(obj, 'name', None):
            data["name"] = name            
    except vim.fault.NoPermission:
        data["name"] = '!error:no_permission'

    if 'name' in data and data["name"] == "Resources" and isinstance(obj, vim.ResourcePool) and hasattr(obj, 'parent') and isinstance(obj.parent, vim.ClusterComputeResource):
        # root resource pool of a cluster (named 'Resources'): let's prepend cluster name
        data["name"] = obj.parent.name + "/" + data["name"]

    return data
