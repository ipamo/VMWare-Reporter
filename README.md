VMWare Reporter
===============

Extract data easily from your VMWare clusters.

See [git repository](https://github.com/ipamo/vmware-reporter) on _GitHub_ and [full documentation](https://vmware-reporter.readthedocs.io/) (including [API reference](https://vmware-reporter.readthedocs.io/en/latest/api-reference.html)) on _Read the Docs_.

[![Documentation Status](https://readthedocs.org/projects/vmware-reporter/badge/?version=latest)](https://vmware-reporter.readthedocs.io/en/latest/?badge=latest)

## Installation

Package `vmware-reporter` is [available on PyPI.org](https://pypi.org/project/vmware-reporter/):

```sh
pip install vmware-reporter
```


## Usage

VMWare Reporter may be used as a library in your Python code:

```py
from vmware_reporter import VCenterClient
with VCenterClient() as vcenter:
    for vm in vcenter.iterate_objs('vm'):
        print(vm.name)
```

It may also be invoked as a command-line application (`vmware-reporter` executable is installed with the package). Examples:

- Export inventory of VMWare managed objects to a YAML file:

```sh
vmware-reporter inventory
```

- Export all available information about VMWare managed objects to JSON files:

```sh
vmware-reporter dump
```

Complete help about command-line usage may be displayed by typing:

```sh
vmware-reporter --help
```


## Credits

This library leverages [pyvmomi](https://github.com/vmware/pyvmomi), the vSphere API Python bindings provided by VMWare.


## Legal

This project is licensed under the terms of the [MIT license](https://raw.githubusercontent.com/ipamo/vmware-reporter/main/LICENSE.txt).

This project is not affiliated in any way with VMWare or Broadcom.
