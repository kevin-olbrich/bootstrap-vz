Puppet
------

Installs the Puppet agent and optionally applies a manifest inside the
chroot. You can also have it copy your puppet configuration into the
image so it is readily available once the image is booted.

Where the agent comes from depends on the Debian release:

-  ``wheezy``, ``jessie`` and ``stretch``: ``puppet-agent`` (Puppet 4) from
   the Puppetlabs PC1 repository `<http://apt.puppetlabs.com/>`__, using
   the PC1 keyrings shipped with this plugin. It is installed in
   ``/opt/puppetlabs`` and configured in ``/etc/puppetlabs``.
-  ``buster`` and newer: Puppet from Debian itself (``puppet`` on buster and
   bullseye, ``puppet-agent`` from bookworm on), because the PC1
   repository has been discontinued. It is installed in ``/usr/bin`` and
   configured in ``/etc/puppet``.

Rationale and use case in a masterless setup
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You want to use this plugin when you wish to create an image and to be able to
manage that image with Puppet, with the puppet agent software already
installed. You want it to almost contain everything you need to get it up and running 
This plugin does just that!
While you're at it, throw in some modules from the forge as well!
Want to include your own modules? Include them as assets!

This is primarily useful when you have a very limited collection of nodes you 
wish to manage with puppet without to having to set up an entire puppet infra-
structure. This allows you thus to work "masterless". 

You can use this to bootstrap any kind of appliance, like a puppet master!
 

About Master/agent setups
~~~~~~~~~~~~~~~~~~~~~~~~~

If you wish to use this plugin in an infrastructure where a puppet master is 
present, you should evaluate what your setup is. In a puppet OSS server setup 
it can be useful to just use the plugin without any manifests, assets or 
modules included. 
In a puppet PE environment you will probably not need this plugin since the PE 
server console gives you an URL that installs the agent corresponding to your 
PE server. 

Settings
~~~~~~~~

-  ``manifest``: Path to the puppet manifest that should be applied.
   ``optional``
-  ``assets``: Path to puppet assets. The contents will be copied into the
   puppet configuration directory on the image (``/etc/puppetlabs`` on
   wheezy to stretch, ``/etc/puppet`` on buster and newer). Any existing
   files will be overwritten.
   ``optional``
-  ``install_modules``: A list of modules you wish to install available from 
   `<https://forge.puppetlabs.com/>` inside the chroot. It will assume a FORCED
   install of the modules.
   This list is a list of tuples. Every tuple must at least contain the module 
   name. A version is optional, when no version is given, it will take the 
   latest version available from the forge. 
   Format: [module_name (required), version (optional)]
-  ``enable_agent``: Whether the puppet agent service should be enabled
   (with ``systemctl enable``, or ``update-rc.d`` on wheezy).
   ``optional - not recommended``. disabled by default. UNTESTED
   
An example bootstrap-vz manifest is included in the ``KVM`` folder of the 
manifests examples directory.
      
Limitations
~~~~~~~~~~~
(Help is always welcome, feel free to chip in!)
General:

- The Puppet version is the one the PC1 repository or the Debian release
  provides, it cannot be chosen in the manifest.

Manifests:

- Running puppet manifests is not recommended and untested, see below

Assets:

- The assets path must be ABSOLUTE to your manifest file.  

install_modules:

- It assumes installing the given list of tuples of modules with the following 
  command: 
  "... install --force $module_name (--version $version_number)"
  The module name is mandatory, the version is optional. When no version is 
  given, it  will pick the master version of the
  module from `<https://forge.puppetlabs.com/>`
- It assumes the modules are installed into the "production" environment. 
  Installing into another environment e.g. develop, is currently not 
  implemented.
- You cannot include local modules this way, to include you homebrewn modules,
  You need to inject them through the assets directive.

UNTESTED:

- Enabling the agent and applying the manifest inside the chrooted environment.
	Keep in mind that when applying a manifest when enabling the agent option,
	the system is in a chrooted environment. This can prevent daemons from 
	running	properly (e.g. listening to ports), they will also need to be shut 
	down gracefully (which bootstrap-vz cannot do) before unmounting the 
	volume. It is advisable to avoid starting any daemons inside the chroot at 
	all.
