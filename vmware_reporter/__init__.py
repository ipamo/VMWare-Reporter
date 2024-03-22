from __future__ import annotations

__prog__ = 'vmware-reporter'

try:
    # Version generated by setuptools_scm during build
    from ._version import __version__, __version_tuple__
except ImportError:
    __version__ = None
    __version_tuple__ = None

__all__ = (
    '__prog__', '__version__', '__version_tuple__',
)
