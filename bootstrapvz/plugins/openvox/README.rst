OpenVox
-------

Installs the `OpenVox <https://voxpupuli.org/openvox/>`__ agent, the
community-maintained continuation of Puppet by Vox Pupuli, and optionally
applies a manifest inside the chroot. You can also have it copy your
configuration into the image so it is readily available once the image
is booted.

The plugin adds the OpenVox APT repository at `<https://apt.voxpupuli.org/>`__
together with its signing key (fingerprint
``0E26 4299 8700 2418 F951 3F2A 5FB9 99C2 D62F F3D9``, shipped with the plugin)
and installs ``openvox-agent``. OpenVox keeps the Puppet command names and
file locations: the ``puppet`` command is installed in
``/opt/puppetlabs/bin``, the configuration lives in ``/etc/puppetlabs`` and
the agent service is ``puppet.service``.

OpenVox publishes packages for Debian ``bullseye``, ``bookworm`` and
``trixie``.

Rationale and use case in a masterless setup
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You want to use this plugin when you wish to create an image and to be able to
manage that image with OpenVox, with the agent software already installed.
You want it to almost contain everything you need to get it up and running.
This plugin does just that!
While you're at it, throw in some modules from the forge as well!
Want to include your own modules? Include them as assets!

This is primarily useful when you have a very limited collection of nodes you
wish to manage without having to set up an entire OpenVox server
infrastructure. This allows you thus to work "masterless".

You can use this to bootstrap any kind of appliance, like an OpenVox server!

About server/agent setups
~~~~~~~~~~~~~~~~~~~~~~~~~

If you wish to use this plugin in an infrastructure where an OpenVox server is
present, you should evaluate what your setup is. It can be useful to just use
the plugin without any manifests, assets or modules included, and enable the
agent.

Settings
~~~~~~~~

-  ``collection``: The OpenVox release collection (repository component) to
   install from.
   Default: ``openvox8``
   ``optional``
-  ``manifest``: Path to the Puppet manifest that should be applied.
   ``optional``
-  ``assets``: Path to OpenVox assets. The contents will be copied into
   ``/etc/puppetlabs`` on the image. Any existing files will be overwritten.
   ``optional``
-  ``install_modules``: A list of modules you wish to install available from
   `<https://forge.puppet.com/>`__ inside the chroot. It will assume a FORCED
   install of the modules.
   This list is a list of tuples. Every tuple must at least contain the module
   name. A version is optional, when no version is given, it will take the
   latest version available from the forge.
   Format: [module_name (required), version (optional)]
-  ``enable_agent``: Whether the agent service (``puppet.service``) should be
   enabled.
   ``optional - not recommended``. disabled by default. UNTESTED

An example bootstrap-vz manifest is included in the ``KVM`` folder of the
manifests examples directory.

Limitations
~~~~~~~~~~~
(Help is always welcome, feel free to chip in!)

Manifests:

- Running Puppet manifests is not recommended and untested, see below

Assets:

- The assets path must be ABSOLUTE to your manifest file.

install_modules:

- It assumes installing the given list of tuples of modules with the following
  command:
  "... install --force $module_name (--version $version_number)"
  The module name is mandatory, the version is optional. When no version is
  given, it will pick the latest version of the module from
  `<https://forge.puppet.com/>`__
- It assumes the modules are installed into the "production" environment.
  Installing into another environment e.g. develop, is currently not
  implemented.
- You cannot include local modules this way, to include your homebrewn modules,
  You need to inject them through the assets directive.

UNTESTED:

- Enabling the agent and applying the manifest inside the chrooted environment.
  Keep in mind that when applying a manifest when enabling the agent option,
  the system is in a chrooted environment. This can prevent daemons from
  running properly (e.g. listening to ports), they will also need to be shut
  down gracefully (which bootstrap-vz cannot do) before unmounting the
  volume. It is advisable to avoid starting any daemons inside the chroot at
  all.
