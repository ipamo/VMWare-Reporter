from unittest import TestCase
from pyVmomi import vim
from vmware_reporter import VCenterClient

class Case(TestCase):
    def test_types(self):
        self.assertIn('virtualmachine', VCenterClient.OBJ_TYPES.keys())
        self.assertEqual(VCenterClient.OBJ_TYPES['virtualmachine'], vim.VirtualMachine)
