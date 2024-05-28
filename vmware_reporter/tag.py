"""
List tags and categories defined on the system.
"""
from __future__ import annotations

import os
from argparse import _SubParsersAction, ArgumentParser, RawTextHelpFormatter
from io import IOBase

from zut import out_table, get_help_text, get_description_text, add_func_command

from . import VCenterClient

def add_tag_commands(commands_subparsers: _SubParsersAction[ArgumentParser], *, name: str):
    parser = commands_subparsers.add_parser(name, help=get_help_text(__doc__), description=get_description_text(__doc__), formatter_class=RawTextHelpFormatter, add_help=False)

    group = parser.add_argument_group(title='Command options')
    group.add_argument('-h', '--help', action='help', help=f"Show this command help message and exit.")

    subparsers = parser.add_subparsers(title='Sub commands')
    add_func_command(subparsers, list_tags, name='tags')
    add_func_command(subparsers, list_categories, name='categories')


_DEFAULT_OUT = '{title}.csv'

def list_categories(vcenter: VCenterClient, out: os.PathLike|IOBase = _DEFAULT_OUT):
    """
    Export tag categories.
    """
    headers=['uuid', 'name', 'description', 'cardinality', 'associable_types']

    with out_table(out, title="categories", dir=vcenter.out_dir, env=vcenter.env, headers=headers) as t:
        for category in vcenter.get_categories():
            t.append([
                category.uuid,
                category.name,
                category.description,
                category.cardinality,
                category.associable_types,
            ])

def _add_arguments(parser: ArgumentParser):
    parser.add_argument('-o', '--out', default=_DEFAULT_OUT, help="Output table (default: %(default)s).")

list_categories.add_arguments = _add_arguments


def list_tags(vcenter: VCenterClient, out: os.PathLike|IOBase = _DEFAULT_OUT):
    """
    Export tags.
    """
    headers=['uuid', 'name', 'description', 'category']

    with out_table(out, title="tags", dir=vcenter.out_dir, env=vcenter.env, headers=headers) as t:
        for tag in vcenter.get_tags():
            t.append([
                tag.uuid,
                tag.name,
                tag.description,
                tag.category.name,
            ])

def _add_arguments(parser: ArgumentParser):
    parser.add_argument('-o', '--out', default=_DEFAULT_OUT, help="Output table (default: %(default)s).")

list_tags.add_arguments = _add_arguments
