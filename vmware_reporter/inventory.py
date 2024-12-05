"""
Inventory of VMWare managed objects.
"""
from __future__ import annotations

import logging
import os
import sys
from argparse import ArgumentParser
from contextlib import nullcontext
from io import IOBase

from pyVmomi import vim

from . import VCenterClient, get_obj_name, get_obj_ref
from .settings import INVENTORY_OUT


_logger = logging.getLogger(__name__)

def inventory(vcenter: VCenterClient, assets: list[str] = None, out: os.PathLike|IOBase = INVENTORY_OUT):
    """
    Export an inventory of VMWare managed objects to a YAML file.
    """
    inventory = build_inventory(vcenter, assets=assets)
    
    if not out or out == 'stdout':
        out = sys.stdout
    elif out == 'stderr':
        out = sys.stderr

    if isinstance(out, IOBase):
        out_name = getattr(out, 'name', '<io>')
    else:
        out = os.path.join(vcenter.data_dir, str(out).format(scope=vcenter.scope))
        out_name = str(out)
        
    _logger.info(f"export inventory to {out_name}")
    inventory.to_yaml(out)


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('-o', '--out', default=INVENTORY_OUT, help="Output YAML file (default: %(default)s).")
    parser.add_argument('--asset', nargs='*', dest='assets')

inventory.add_arguments = _add_arguments


def build_inventory(vcenter: VCenterClient, assets: list[str] = None) -> InventoryNode:
    node = InventoryNode(vcenter.scope, nature=VCenterClient)

    if not assets:
        assets = ['folder', 'license', 'authorization']

    for asset in assets:
        if asset == 'folder':
            _logger.info(f"build folder inventory")
            build_folder_inventory(vcenter, parent=node)
            
        elif asset == 'authorization':
            _logger.info(f"build authorization inventory")
            build_authorization_inventory(vcenter, parent=node)
        
        elif asset == 'license':
            _logger.info(f"build license inventory")
            build_license_inventory(vcenter, parent=node)

        else:
            _logger.error(f"Unknown asset: {asset}")

    return node


def build_folder_inventory(vcenter: VCenterClient, parent = None) -> InventoryNode:
    found_by_ref: dict[str,vim.ManagedEntity] = {}

    def recurse_tree(obj: vim.ManagedEntity, parent: InventoryNode):
        ref = get_obj_ref(obj)
        name = get_obj_name(obj)
        found_by_ref[ref] = obj
        node = InventoryNode(name, nature=type(obj), ref=ref, parent=parent)

        if isinstance(obj, vim.Datacenter):
            datastore_node = InventoryNode("(datastores)", parent=node)
            for sub in obj.datastore:
                recurse_tree(sub,datastore_node)
            
            network_node = InventoryNode("(networks)", parent=node)
            for sub in obj.network:
                recurse_tree(sub, network_node)
        
            recurse_tree(obj.datastoreFolder, node)
            recurse_tree(obj.networkFolder, node)
            recurse_tree(obj.hostFolder, node)
            recurse_tree(obj.vmFolder, node)

        elif isinstance(obj, vim.ComputeResource):
            host_node = InventoryNode("(hosts)", parent=node)
            for sub in obj.host:
                recurse_tree(sub, host_node)

            recurse_tree(obj.resourcePool, node)

        if hasattr(obj, 'childEntity'):
            for sub in obj.childEntity:
                recurse_tree(sub, node)

        return node

    # Walk through the tree starting from root folder
    node = recurse_tree(vcenter.service_content.rootFolder, parent)

    # Search through the container view
    view = None
    try:
        view = vcenter.service_content.viewManager.CreateContainerView(vcenter.service_content.rootFolder, recursive=True)
        additional_node = None
        for obj in view.view:
            ref = get_obj_ref(obj)
            if not found_by_ref.pop(ref, None):
                if not additional_node:
                    additional_node = InventoryNode('(found in container view but not in folder tree)', parent=node)
            
                name = get_obj_name(obj)
                InventoryNode(name, nature=type(obj), ref=ref, child_of=f'{get_obj_name(obj.parent)} ({get_obj_ref(obj.parent)})', parent=additional_node)
    finally:
        if view:
            view.Destroy()

    # Show elements missing in the container view
    additional_node = None
    for ref, obj in found_by_ref.items():
        if obj == vcenter.service_content.rootFolder:
            continue

        if not additional_node:
            additional_node = InventoryNode('(found in folder tree but not in container view)', parent=node)
    
        name = get_obj_name(obj)
        InventoryNode(name, nature=type(obj), ref=ref, child_of=f'{get_obj_name(obj.parent)} ({get_obj_ref(obj.parent)})', parent=additional_node)

    return node


def build_authorization_inventory(vcenter: VCenterClient, parent = None) -> InventoryNode:
    node = InventoryNode("(roles)", parent=parent)

    role_nodes = {}
    for role in vcenter.service_content.authorizationManager.roleList:
        role_node = InventoryNode(role.name, nature=type(role), id=role.roleId, parent=node)
        role_node._user_node = None
        role_node._group_node = None
        role_nodes[role.roleId] = role_node

    principal_nodes: dict[str,InventoryNode] = {}
    for permission in vcenter.service_content.authorizationManager.RetrieveAllPermissions():
        role_node = role_nodes[permission.roleId]

        if permission.group:
            if not role_node._group_node:
                role_node._group_node = InventoryNode("(groups)", parent=role_node)
            parent = role_node._group_node
            nature = 'group'
        else:
            if not role_node._user_node:
                role_node._user_node = InventoryNode("(users)", parent=role_node)
            parent = role_node._user_node
            nature = 'user'

        if permission.principal in principal_nodes:
            principal_node = principal_nodes[permission.principal]
        else:
            principal_node = InventoryNode(permission.principal, nature=nature, parent=parent)

        ref = get_obj_ref(permission.entity)
        name = permission.entity.name
        InventoryNode(name, nature=f"{type(permission.entity).__name__} permission", ref=ref, propagate=permission.propagate, parent=principal_node)

    return node


def build_license_inventory(vcenter: VCenterClient, parent = None) -> InventoryNode:
    node = None

    try:
        for license in vcenter.service_content.licenseManager.licenses:
            if not node:
                node = InventoryNode("(licenses)", parent=parent)
            InventoryNode(license.name, nature=type(license), key=license.licenseKey, parent=node)
    except vim.fault.NoPermission:
        _logger.warning("Skip license inventory (no permission)")

    return node


class InventoryNode:
    indent_size = 2

    def __init__(self, name: str, *, nature: type|str = None, parent: InventoryNode = None, **attrs):
        self.name = name        
        self.nature = nature.__name__ if isinstance(nature, type) else (str(nature) if nature else None)
        self.parent = parent
        self.attrs = attrs
        self.children: list[InventoryNode] = []
        if parent:
            parent.children.append(self)

    def __str__(self):
        result = self.name
        need_comma = True
        if self.nature:
            result += f' [{self.nature}]'
            need_comma = False

        for name, value in self.attrs.items():
            result += (', ' if need_comma else ' ') + f'{name}={value}'
            need_comma = True

        return result

    def to_yaml(self, file, depth = 0):
        if not isinstance(file, IOBase):
            if dir := os.path.dirname(file):
                os.makedirs(dir, exist_ok=True)
                
        with nullcontext(file) if isinstance(file, IOBase) else open(file, 'w', encoding='utf-8') as fp:
            print(f"{' ' * self.indent_size * depth + '- ' if depth > 0 else ''}{self}:", file=fp)
            for child in self.children:
                child.to_yaml(fp, depth+1)
