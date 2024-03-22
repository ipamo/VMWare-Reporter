"""
Configuration file for the Sphinx documentation builder.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath('..'))

from vmware_reporter import __prog__, __version__, __version_tuple__

RESET = '\033[0m'
YELLOW = '\033[0;33m'

project = __prog__
author = "Sébastien Hocquet"
copyright = f"2024{f'-{date.today().year}' if date.today().year > 2024 else ''}, {author} <dev@ipamo.net>"

version = f"v{'.'.join(str(part) for part in __version_tuple__[0:3])}"
release = f"v{__version__}"

extensions = [
    'myst_parser',
    'sphinx.ext.autodoc',
    'sphinx.ext.autosummary',
    'sphinx_rtd_theme',
    'docs_versions_menu',
]

templates_path = ['templates']
exclude_patterns = ['build', 'Thumbs.db', '.DS_Store']

html_static_path = ['static']
html_logo = "static/logo-100x100.png"
html_favicon = "static/logo-100x100.png"

html_theme = 'sphinx_rtd_theme'
html_context = {
    'display_version': True, # appears under the logo on the left menu
}

html_copy_source = False
html_show_sourcelink = False

autosummary_generate = True
autosummary_ignore_module_all = False
autosummary_imported_members = False

docs_versions_menu_conf = {
    'github_project_url': 'https://github.com/ipamo/vmware-reporter',
    'current_folder': f'v{__version__}',
}
