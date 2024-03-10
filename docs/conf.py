"""
Configuration file for the Sphinx documentation builder.
"""

import os, sys
sys.path.insert(0, os.path.abspath('..'))

from datetime import date

RESET = '\033[0m'
YELLOW = '\033[0;33m'

project = "VMWare Reporter"
copyright = f"2024{f'-{date.today().year}' if date.today().year > 2024 else ''}, Sébastien Hocquet <https://ipamo.net>"
author = "Sébastien Hocquet"

extensions = [
    'myst_parser',
    'sphinx.ext.autodoc',
    'sphinx.ext.autosummary',
    'sphinx_rtd_theme',
]

templates_path = ['templates']
exclude_patterns = ['build', 'Thumbs.db', '.DS_Store']

html_static_path = ['static']
html_logo = "static/logo-100x100.png"

html_theme = 'sphinx_rtd_theme'
html_context = {
    'display_version': True, # appears under the logo on the left menu
}

autosummary_generate = True
autosummary_ignore_module_all = False
autosummary_imported_members = True
