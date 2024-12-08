"""
Dump tag and category definitions.
"""
from __future__ import annotations

import os
from argparse import _SubParsersAction, ArgumentParser, RawTextHelpFormatter
from io import IOBase

from zut import tabular_dumper, get_help_text, get_description_text, add_command

from . import VCenterClient, settings
from .settings import TABULAR_OUT, OUT_DIR


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output table (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

    subparsers = parser.add_subparsers(title='sub commands')
    add_command(subparsers, dump_tag_values, name='value')
    add_command(subparsers, dump_tag_categories, name='category')

def dump_tags(vcenter: VCenterClient, out: os.PathLike|IOBase = TABULAR_OUT, dir: os.PathLike = None):
    """
    Dump tag category and value definitions.
    """
    dump_tag_values(vcenter, out=out, dir=dir)
    dump_tag_categories(vcenter, out=out, dir=dir)

dump_tags.add_arguments = _add_arguments
handle = dump_tags


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output table (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

def dump_tag_categories(vcenter: VCenterClient, *, out: os.PathLike|IOBase = TABULAR_OUT, dir: os.PathLike = None):
    """
    Dump tag category definitions.
    """
    headers=['uuid', 'name', 'description', 'cardinality', 'associable_types']

    with tabular_dumper(out, title="tag_category", dir=dir or vcenter.data_dir, scope=vcenter.scope, headers=headers, truncate=True, excel=settings.CSV_EXCEL) as t:
        for category in vcenter.get_categories():
            t.append([
                category.uuid,
                category.name,
                category.description,
                category.cardinality,
                category.associable_types,
            ])

dump_tag_categories.add_arguments = _add_arguments


def _add_arguments(parser: ArgumentParser):
    parser.add_argument('-o', '--out', default=TABULAR_OUT, help="Output table (default: %(default)s).")
    parser.add_argument('--dir', help=f"Output directory (default: {OUT_DIR}).")

def dump_tag_values(vcenter: VCenterClient, *, out: os.PathLike|IOBase = TABULAR_OUT, dir: os.PathLike = None):
    """
    Dump tag value definitions.
    """
    headers=['uuid', 'name', 'description', 'category']

    with tabular_dumper(out, title="tag_value", dir=dir or vcenter.data_dir, scope=vcenter.scope, headers=headers, truncate=True, excel=settings.CSV_EXCEL) as t:
        for tag in vcenter.get_tags():
            t.append([
                tag.uuid,
                tag.name,
                tag.description,
                tag.category.name,
            ])

dump_tag_values.add_arguments = _add_arguments
