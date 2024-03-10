from __future__ import annotations
import inspect

import logging
import os
import re
from pathlib import Path
from collections.abc import Callable, Iterator
from typing import Literal, TypeVar, overload
from urllib.parse import urlparse
from uuid import UUID

from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim, vmodl
from pyVmomi.VmomiSupport import _managedDefMap

from reporter_utils import Filters, get_config, resolve_host
from .inspect import get_obj_ref
from .utils import __prog__, __prog_modulepath__

T_Obj = TypeVar('T_Obj', bound=vim.ManagedEntity)


class VCenterClient:
    """
    Main entry point of the library to retrieve VMWare managed objects and interact with them. 
    """
    def __init__(self, name: str = None, *, host: str = None, user: str = None, password: str = None, no_ssl_verify: bool = None):
        """
        Create a new vCenter client.

        If `host`, `user`, `password` or `no_ssl_verify` options are not provided, they are read from configuration file
        in section `[vmware-reporter]` (or `[vmware-reporter:{name}]` if `name` is given).

        :param name: An optional name to distinguish between several vCenters.
        :param host: Host name of the vCenter.
        :param user: Name of the vCenter user having access to the API.
        :param password: Password of the vCenter user having access to the API.
        """
        self.name = name or 'default'
        
        config = get_config(__prog_modulepath__)
        section = __prog__ if self.name == 'default' else f'{__prog__}:{self.name}'
            
        self.host = host if host is not None else config.get(section, 'host')
        self.user = user if user is not None else config.get(section, 'user')
        self.password = password if password is not None else config.get(section, 'password')
        self.no_ssl_verify = no_ssl_verify if no_ssl_verify is not None else config.getboolean(section, 'no_ssl_verify', fallback=False)
        
        self.logger = logging.getLogger(f'{self.__class__.__module__}.{self.__class__.__qualname__}.{self.name}')


    #region Enter/connect and exit/close

    def __enter__(self):
        self.connect()
        return self


    def __exit__(self, exc_type = None, exc_value = None, exc_traceback = None):
        self.close()


    def connect(self):
        addrs = resolve_host(self.host, timeout=2.0)
        if not addrs:
            raise ValueError(f"Cannot resolve host name \"{self.host}\"")
        
        addr = addrs[0]
        self.logger.debug(f"Connect to {addr} ({self.host}) with user {self.user}")

        options = {}
        if 'httpConnectionTimeout' in inspect.signature(SmartConnect).parameters:
            # Introduced in pyVmomi 8.0.0.1 (see https://github.com/vmware/pyvmomi/issues/627)
            options['httpConnectionTimeout'] = 5.0

        self._service_instance = SmartConnect(host=self.host, user=self.user, pwd=self.password, disableSslCertValidation=self.no_ssl_verify, **options)


    def close(self):
        try:
            Disconnect(self._service_instance)
        except AttributeError:
            pass
    

    @property
    def service_instance(self) -> vim.ServiceInstance:
        try:
            return self._service_instance
        except AttributeError:
            pass

        self.connect()
        return self._service_instance


    @property
    def service_content(self) -> vim.ServiceInstanceContent:
        try:
            return self._service_content
        except AttributeError:
            pass

        self._service_content = self.service_instance.RetrieveContent()
        return self._service_content

    #endregion


    #region Retrieve managed objects

    @property
    def datacenter(self):
        try:
            return self._datacenter
        except AttributeError:
            pass

        datacenters = self.list_objs(vim.Datacenter)
        if not datacenters:
            raise ValueError(f"Datacenter not found")
        if len(datacenters) > 1:
            raise ValueError(f"Several datacenter found")
        self._datacenter = datacenters[0]
        return self._datacenter


    def get_obj(self, type: type[T_Obj], search: list[str|re.Pattern]|str|re.Pattern|UUID, *, normalize: bool = False, key: Literal['name', 'ref', 'uuid', 'bios_uuid'] = 'name') -> T_Obj:
        """
        Find a single VMWare managed object.

        Raise KeyError if not found or several found.
        """
        if key in ['uuid', 'bios_uuid']:
            if not isinstance(search, (UUID,str)):
                raise TypeError(f"specs must be UUID or str for key {key}, got {type(search).__name__}")
            
            if isinstance(search, UUID):
                uuid = search
            else:
                uuid = UUID(search)

            obj = None
            
            if key == 'bios_uuid':
                # NOTE: uuid is "BIOS UUID". Seems to match the end of `sudo cat /sys/class/dmi/id/product_uuid`.
                if type == vim.VirtualMachine:
                    obj = self._find_by_uuid(uuid, for_vm=True, instance_uuid=False)
                else:
                    raise ValueError(f"key '{key}' can be used only for virtual machines")
                
            else:
                if type == vim.VirtualMachine:
                    obj = self._find_by_uuid(uuid, for_vm=True, instance_uuid=True)
                elif type == vim.HostSystem:
                    obj = self._find_by_uuid(uuid, for_vm=False, instance_uuid=False)
                else:
                    raise ValueError(f"key '{key}' can be used only for virtual machines or host systems")

            if obj:
                return obj
            else:
                raise KeyError(f"Not found: {search} (type: {type.__name__})")

        else:
            iterator = self.get_objs(types=type, search=search, normalize=normalize, key=key)
            try:
                found = next(iterator)
            except StopIteration:
                raise KeyError(f"Not found: {search} (type: {type.__name__})")
            
            try:
                next(iterator)
                raise KeyError(f"Several found: {search} (type: {type.__name__})")
            except StopIteration:
                pass
            return found
            

    def _find_by_uuid(self, uuid: UUID|str, for_vm: bool, instance_uuid: bool):
        if isinstance(uuid, UUID):
            uuid = str(uuid)
        
        for datacenter in self.get_objs(vim.Datacenter):
            obj = self.service_content.searchIndex.FindByUuid(datacenter, uuid, vmSearch=for_vm, instanceUuid=instance_uuid)
            if obj:
                return obj


    @overload
    def list_objs(self, types: type[T_Obj], search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = None, key: Literal['name', 'ref'] = 'name', sort_key: str|list[str]|Callable = None) -> list[T_Obj]:
        ...

    def list_objs(self, types: list[type|str]|type|str = None, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = None, key: Literal['name', 'ref'] = 'name', sort_key: str|list[str]|Callable = None):        
        """
        List VMWare managed objects matching the given search.
        """
        objs = [obj for obj in self.get_objs(types, search, normalize=normalize, key=key)]

        if sort_key:
            if isinstance(sort_key, str):
                sort_key = [sort_key]

            if isinstance(sort_key, list):
                sort_func = lambda obj: [getattr(obj, attr) for attr in sort_key]
            else:
                sort_func = sort_key

            objs.sort(key=sort_func)

        return objs


    @overload
    def get_objs(self, types: type[T_Obj], search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = None, key: Literal['name', 'ref'] = 'name') -> Iterator[T_Obj]:
        ...

    def get_objs(self, types: list[type|str]|type|str = None, search: list[str|re.Pattern]|str|re.Pattern = None, *, normalize: bool = None, key: Literal['name', 'ref'] = 'name'):
        """
        Iterate over VMWare managed objects matching the given search.
        """

        # Prepare value filter
        filters = Filters(search, normalize=normalize)

        # Prepare types
        if not types:
            types = []
        elif isinstance(types, (str,type)):
            types = [types]
        
        types = [self.parse_obj_type(_type) for _type in types]

        # Search using a container view
        view = None
        try:
            view = self.service_content.viewManager.CreateContainerView(self.service_content.rootFolder, types, recursive=True)

            for obj in view.view:
                if self._obj_matches(obj, key, filters):
                    yield obj
        finally:
            if view:
                view.Destroy()


    def _obj_matches(self, obj: vim.ManagedEntity, key: Literal['name', 'ref'], filters: Filters):
        if not filters:
            return True
        
        if key == 'name':
            try:
                value = obj.name
            except vim.fault.NoPermission:
                return False
            
        elif key == 'ref':
            value = get_obj_ref(obj)
            
        else:
            raise ValueError(f"key not supported: {key}")
        
        return filters.matches(value)

    #endregion


    #region Instance helpers

    @property
    def cookie(self) -> dict:
        try:
            return self._cookie
        except AttributeError:
            pass
    
        # Get the cookie built from the current session
        client_cookie = self.service_instance._stub.cookie

        # Break apart the cookie into it's component parts
        cookie_name = client_cookie.split("=", 1)[0]
        cookie_value = client_cookie.split("=", 1)[1].split(";", 1)[0]
        cookie_path = client_cookie.split("=", 1)[1].split(";", 1)[1].split(
            ";", 1)[0].lstrip()
        cookie_text = " " + cookie_value + "; $" + cookie_path

        # Make a cookie
        self._cookie = dict()
        self._cookie[cookie_name] = cookie_text
        return self._cookie


    def wait_for_task(self, *tasks: vim.Task):
        """
        Given a service instance and tasks, return after all the tasks are complete.
        """
        property_collector = self.service_instance.content.propertyCollector
        task_list = [str(task) for task in tasks]
        # Create filter
        obj_specs = [vmodl.query.PropertyCollector.ObjectSpec(obj=task) for task in tasks]
        property_spec = vmodl.query.PropertyCollector.PropertySpec(type=vim.Task, pathSet=[], all=True)
        filter_spec = vmodl.query.PropertyCollector.FilterSpec()
        filter_spec.objectSet = obj_specs
        filter_spec.propSet = [property_spec]
        pc_filter = property_collector.CreateFilter(filter_spec, True)
        try:
            version, state = None, None
            # Loop looking for updates till the state moves to a completed state.
            while task_list:
                update = property_collector.WaitForUpdates(version)
                for filter_set in update.filterSet:
                    for obj_set in filter_set.objectSet:
                        task = obj_set.obj
                        for change in obj_set.changeSet:
                            if change.name == 'info':
                                state = change.val.state
                            elif change.name == 'info.state':
                                state = change.val
                            else:
                                continue

                            if not str(task) in task_list:
                                continue

                            if state == vim.TaskInfo.State.success:
                                # Remove task from taskList
                                task_list.remove(str(task))
                            elif state == vim.TaskInfo.State.error:
                                raise task.info.error
                # Move to next version
                version = update.version
        finally:
            if pc_filter:
                pc_filter.Destroy()


    DEFAULT_OUT_DIR_MASK = Path('data/vmware-{vcenter}')

    def compile_path_mask(self, path: os.PathLike, *, parent_mkdir = False, mkdir = False, **attrs):
        path = Path(str(path).format(vcenter=self.name, **attrs))
        if mkdir:
            path.mkdir(parents=True, exist_ok=True)
        elif parent_mkdir:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    #endregion


    #region Class helpers

    @classmethod
    def get_configured_names(cls) -> list[str]:
        try:
            return cls._configured_names
        except AttributeError:
            pass

        cls._configured_names = []

        config = get_config(__prog_modulepath__)    
        for section in config.sections():
            if m := re.match(r'^' + re.escape(__prog__) + r'(?:\:(.+))?', section):
                name = m[1]
                if name == 'default':
                    raise ValueError(f"Invalid configuration section \"{section}\": name \"default\" is reserved")
                if not name:
                    name = 'default'
                if not name in cls._configured_names:
                    cls._configured_names.append(name)

        return cls._configured_names
        
    @classmethod
    def parse_obj_type(cls, value: str|type|vim.ManagedEntity) -> type[vim.ManagedEntity]:
        if not value:
            raise ValueError(f"name cannot be blank")
        
        elif isinstance(value, type):
            if not issubclass(value, vim.ManagedEntity):
                raise TypeError(f"type {value} is not a subclass of vim.ManagedEntity")
            
            return value
        
        elif isinstance(value, vim.ManagedEntity):
            return type(value)
        
        elif not isinstance(value, str):
            raise TypeError(f"invalid type for name: {value}")
        
        else:
            lower = value.lower()

            # Search in types
            if lower in cls.OBJ_TYPES:
                return cls.OBJ_TYPES[lower]

            # Handle aliases            
            if lower == 'vm':
                return vim.VirtualMachine
            if lower == 'host':
                return vim.HostSystem
            if lower == 'net':
                return vim.Network
            if lower == 'dvs':
                return vim.DistributedVirtualSwitch
            if lower == 'dvp':
                return vim.dvs.DistributedVirtualPortgroup
            if lower == 'ds':
                return vim.Datastore
            if lower == 'dc':
                return vim.Datacenter
            if lower == 'cluster':
                return vim.ClusterComputeResource

            raise KeyError(f"vim managed object type not found for name {value}")

    def _build_obj_types() -> dict[str,type[vim.ManagedEntity]]:
        types = {}

        for key in _managedDefMap.keys():
            if not key.startswith('vim.'):
                continue

            attr = key[len('vim.'):]
            _type = getattr(vim, attr)
            if not issubclass(_type, vim.ManagedEntity):
                continue

            lower = attr.lower()
            if lower in types:
                continue

            types[lower] = _type

        return types
            
    OBJ_TYPES = _build_obj_types()
    
    #endregion
