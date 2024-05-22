from zut import out_table
from . import VCenterClient

def handle(vcenter: VCenterClient):
    """
    Export custom fields.
    """
    with out_table(title="custom fields", headers=['key', 'obj_type', 'data_type', 'name']) as t:
        for field in vcenter.service_content.customFieldsManager.field:
            t.append([field.key, field.managedObjectType.__name__, field.type.__name__, field.name])
