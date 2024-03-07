import logging
import sys
from argparse import ArgumentParser, RawTextHelpFormatter
from contextlib import nullcontext

from . import (VCenterClient, __doc__, __prog__, __version__,
               delete_from_datastore, download_from_datastore, export_datastore_elements,
               export_datastore_stats, export_inventory, export_obj_dump,
               upload_to_datastore)
from .utils import add_func_command, configure_logging, get_help_text

logger = logging.getLogger(__name__)

def main():
    configure_logging()
    
    vcenter_names = VCenterClient.get_configured_names()

    parser = ArgumentParser(prog=__prog__, description=get_help_text(__doc__), formatter_class=RawTextHelpFormatter, add_help=False, epilog='\n'.join(__doc__.splitlines()[2:]))
    
    group = parser.add_argument_group(title='Options')
    group.add_argument('-e', '--vcenter', '--env', help=f"Name of the vCenter client to use. Available: {', '.join(vcenter_names) if vcenter_names else 'none'}.")
    group.add_argument('-h', '--help', action='help', help=f"Show help message and exit.")
    group.add_argument('--version', action='version', version=f"{__prog__} {__version__ or '?'}", help="Show version information and exit.")

    subparsers = parser.add_subparsers(title='Commands')
    add_func_command(subparsers, export_inventory, name='inventory', need_vcenter=True)
    add_func_command(subparsers, export_obj_dump, name='dump', need_vcenter=True)

    datastore_parser = subparsers.add_parser('datastore', help="Extract data about datastores or perform operations on datastores.")
    datastore_subparsers = datastore_parser.add_subparsers(title='subcommands')
    add_func_command(datastore_subparsers, export_datastore_elements, name='elements', need_vcenter=True)
    add_func_command(datastore_subparsers, export_datastore_stats, name='stats', need_vcenter=True)
    add_func_command(datastore_subparsers, download_from_datastore, name='download', need_vcenter=True)
    add_func_command(datastore_subparsers, upload_to_datastore, name='upload', need_vcenter=True)
    add_func_command(datastore_subparsers, delete_from_datastore, name='delete', need_vcenter=True)

    args = vars(parser.parse_args())
    handle = args.pop('handle', None)
    if not handle:
        logger.error(f"No command provided.")
        sys.exit(1)

    with get_vcenter_context(args):
        handle(**args)
        

def get_vcenter_context(args: dict):
    vcenter_names = VCenterClient.get_configured_names()
    need_vcenter = args.pop('need_vcenter', False)

    if need_vcenter:
        vcenter_name = args.pop('vcenter')
        if not vcenter_name:
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
