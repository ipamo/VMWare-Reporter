VMWare-reporter
===============

Interact easily with your VMWare clusters.


## Installation

VMWare-reporter package is published [on PyPI](https://pypi.org/project/vmware-reporter/):

```sh
pip install vmware-reporter
```

Optionaly, use specifier `[excel]` to also install dependencies for reading and writing Excel files:

```sh
pip install vmware-reporter[excel]
```

## Configuration

Create file `C:\Users\$USER\AppData\Local\vmware-reporter\vmware-reporter.conf` (`/home/$USER/.config/vmware-reporter/vmware-reporter.conf` on Linux).

Example:

```ini
[vmware-reporter]
host = myvcenter.example.org
user = reporter@vsphere.local
password = ...
no_ssl_verify = True
```

Several environments may be distinguished. Example for two environments named `ENV1` and `ENV2`:

```ini
[vmware-reporter:ENV1]
host = myvcenter.env1.example.org
user = reporter@vsphere.local
password = ...
no_ssl_verify = True

[vmware-reporter:ENV2]
host = myvcenter.env2.example.org
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

- Reconfigure VMs (mass operation): copy template [vm_reconfigure.xlsx](https://ipamo.net/vmware-reporter/latest/_static/templates/vm_reconfigure.xlsx) to `data/vm_reconfigure.xlsx`, fill-in this file, then:

```sh
vmware-reporter vm reconfigure
```

- If you use several environments, put files in `data/ENV1/` instead of `data/` and specify environment on the command line. Example:

```sh
vmware-reporter -e ENV1 vm reconfigure
```

Complete help about command-line usage may be displayed by typing:

```sh
vmware-reporter --help
```


## Run every day (using a systemd timer)

Copy [vmware-reporter.service](https://ipamo.net/vmware-reporter/latest/_static/vmware-reporter.service), [vmware-reporter.timer](https://ipamo.net/vmware-reporter/latest/_static/vmware-reporter.timer) and [notify-email@.service](https://ipamo.net/vmware-reporter/latest/_static/notify-email@.service)
in `/etc/systemd/system` and adapt them to your configuration.

Run:

    sudo systemctl daemon-reload
    sudo systemctl enable vmware-reporter.timer
    sudo systemctl start vmware-reporter.timer


## Credits

This library leverages [pyvmomi](https://github.com/vmware/pyvmomi), the vSphere API Python bindings provided by VMWare.


## Legal

This project is licensed under the terms of the [MIT license](https://raw.githubusercontent.com/ipamo/vmware-reporter/main/LICENSE.txt).

This project is not affiliated in any way with VMWare or Broadcom.
