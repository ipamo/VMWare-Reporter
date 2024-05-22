VMWare-reporter
===============

Interact easily with your VMWare clusters.


## Installation

VMWare-reporter package is published [on PyPI](https://pypi.org/project/vmware-reporter/):

```sh
pip install vmware-reporter
```


## Configuration

Create file `C:\Users\$USER\AppData\Local\vmware-reporter\vmware-reporter.conf` (`/home/$USER/.config/vmware-reporter/vmware-reporter.conf` on Linux). Example:

```ini
[vmware-reporter]
host = myvcenter.example.org
user = reporter@vsphere.local
password = ...
no_ssl_verify = True
```


## Usage examples

See also [full documentation](https://ipamo.net/vmware-reporter) (including [API reference](https://ipamo.net/vmware-reporter/latest/api-reference.html)).

VMWare-reporter may be used as a library in your Python code:

```py
from vmware_reporter import VCenterClient
with VCenterClient() as vcenter:
    for vm in vcenter.iterate_objs('vm'):
        print(vm.name)
```

VMWare-reporter may also be invoked as a command-line application (the `vmware-reporter` executable is installed with the package). Examples:

- Export inventory of VMWare managed objects to a YAML file:

```sh
vmware-reporter inventory
```

- Export all available information about VMWare managed objects to JSON files:

```sh
vmware-reporter dump
```

- Reconfigure VMs (mass operation): copy `templates/vms_reconfigure.xlsx` to `data/vms_reconfigure.xlsx`, fill-in this file, then:

```sh
vmware-reporter vm reconfigure
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
