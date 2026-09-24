Pip install
-----------

Install packages from the Python Package Index via pip.

Installs ``build-essential`` and ``python-dev`` debian packages, so
Python extension modules can be built.

Debian dropped Python 2 in bullseye, so from bullseye onward the plugin
installs ``python3-pip`` and ``python3-dev`` and uses ``pip3`` instead
(the same as the `pip3_install <../pip3_install>`__ plugin).
From bookworm onward the system Python is marked as externally managed
(PEP 668), so packages are installed with ``--break-system-packages``.

Settings
~~~~~~~~

-  ``packages``: Python packages to install, a list of strings. The list
   can contain anything that ``pip install`` would accept as an
   argument, for example ``awscli==1.3.13``.
