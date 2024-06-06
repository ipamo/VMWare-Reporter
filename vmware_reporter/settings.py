import os
import vmware_reporter
from zut import get_config

CONFIG = get_config(os.environ.get('VMWARE_REPORTER_CONFIG') or vmware_reporter)
CONFIG_SECTION = os.environ.get('VMWARE_REPORTER_CONFIG_SECTION') or 'vmware-reporter'

OUT_DIR = CONFIG.get(CONFIG_SECTION, 'out_dir', fallback='data/{env}')
OUT = CONFIG.get(CONFIG_SECTION, 'out', fallback='{title}.csv')
DUMP_OUT = CONFIG.get(CONFIG_SECTION, 'dump_out', fallback='dumps/{typename}/{name} ({ref}).json')
INVENTORY_OUT = CONFIG.get(CONFIG_SECTION, 'inventory_out', fallback='inventory.yml')
AUTOREPORT_OUT = CONFIG.get(CONFIG_SECTION, 'autoreport_out', fallback='autoreport.xlsx#{title}')
ARCHIVES_DIR = CONFIG.get(CONFIG_SECTION, 'archives_dir', fallback='archives')

COUNTERS = CONFIG.getlist(CONFIG_SECTION, 'counters', fallback=None, delimiter=',')
