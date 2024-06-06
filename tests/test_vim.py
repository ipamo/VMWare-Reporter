from unittest import TestCase
from pyVmomi import vim
from vmware_reporter import VCenterClient, OBJ_TYPES

class Case(TestCase):
    def test_types(self):
        self.assertIn('virtualmachine', OBJ_TYPES.keys())
        self.assertEqual(OBJ_TYPES['virtualmachine'], vim.VirtualMachine)
