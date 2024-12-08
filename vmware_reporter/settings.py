from configparser import RawConfigParser
from zut import get_variable, get_list_variable, get_bool_variable
from .utils import load_dotenvs

load_dotenvs('vmware-reporter')

# Define global configuration directives
OUT_DIR = get_variable('VMWARE_OUT_DIR', 'data/{scope}')
OUT = get_variable('VMWARE_OUT', 'report.xlsx:{title}')
ARCHIVATE = get_variable('VMWARE_ARCHIVATE', '0')
if (_state := RawConfigParser.BOOLEAN_STATES.get(ARCHIVATE.lower())) is not None:
    ARCHIVATE = _state

TABULAR_OUT = get_variable('VMWARE_TABULAR_OUT', '{title}.csv')
EXPORT_OUT = get_variable('VMWARE_REPORT_OUT', 'export/{typename}/{name} ({ref}).json')
INVENTORY_OUT = get_variable('VMWARE_INVENTORY_OUT', 'inventory.yml')

COUNTERS = get_list_variable('VMWARE_COUNTERS')

EXTRACT_TAG_CATEGORIES = get_list_variable('VMWARE_EXTRACT_TAG_CATEGORIES')
EXTRACT_CUSTOM_VALUES = get_list_variable('VMWARE_EXTRACT_CUSTOM_VALUES')

CSV_EXCEL = get_bool_variable('CSV_EXCEL')
