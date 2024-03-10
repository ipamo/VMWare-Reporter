import inspect
import logging
import sys
from argparse import ArgumentParser, RawTextHelpFormatter
from contextlib import nullcontext
from types import FunctionType

from reporter_utils import add_func_command, configure_logging, get_help_text

from . import (VCenterClient, __doc__, __prog__, __version__,
               export_inventory, export_obj_dump)

from .datastore import add_datastore_commands
from .extract import handle as extract_handle

logger = logging.getLogger(__name__)

def main():
    configure_logging()
    
    vcenter_names = VCenterClient.get_configured_names()

    parser = ArgumentParser(prog=__prog__, description=get_help_text(__doc__), formatter_class=RawTextHelpFormatter, add_help=False, epilog='\n'.join(__doc__.splitlines()[2:]))
    
    group = parser.add_argument_group(title='General options')
    group.add_argument('-e', '--vcenter', '--env', help=f"Name of the vCenter client to use. Available: {', '.join(vcenter_names) if vcenter_names else 'none'}.")
    group.add_argument('-h', '--help', action='help', help=f"Show this program help message and exit.")
    group.add_argument('--version', action='version', version=f"{__prog__} {__version__ or '?'}", help="Show version information and exit.")

    subparsers = parser.add_subparsers(title='Commands')
    add_func_command(subparsers, export_inventory, name='inventory')
    add_func_command(subparsers, export_obj_dump, name='dump')
    add_func_command(subparsers, extract_handle, name='extract')
    add_datastore_commands(subparsers, name='datastore')

    args = vars(parser.parse_args())
    handle = args.pop('handle', None)
    if not handle:
        logger.error(f"No command provided.")
        sys.exit(1)

    with get_vcenter_context(handle, args):
        handle(**args)
        

def get_vcenter_context(handle: FunctionType, args: dict):
    vcenter_name = args.pop('vcenter')
    need_vcenter = 'vcenter' in inspect.signature(handle).parameters

    if need_vcenter:        
        if not vcenter_name:
            vcenter_names = VCenterClient.get_configured_names()
            if len(vcenter_names) > 1:
                logger.error(f"Name of the vCenter client to use must be provided (option --vcenter). Available: {', '.join(vcenter_names) if vcenter_names else 'none'}.")
                sys.exit(1)
            elif len(vcenter_names) == 1:
                vcenter_name = vcenter_names[0]
            elif 'vcenter' in args:
                logger.error(f"No vCenter configured.")
                sys.exit(1)
        
        context = VCenterClient(vcenter_name)
        args['vcenter'] = context
    
    else:
        context = nullcontext()

    return context

if __name__ == '__main__':
    main()
