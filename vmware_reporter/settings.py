import os
import vmware_reporter
from zut import get_config

CONFIG = get_config(os.environ.get('VMWARE_REPORTER_CONFIG') or vmware_reporter)
CONFIG_SECTION = os.environ.get('VMWARE_REPORTER_CONFIG_SECTION') or 'vmware-reporter'
