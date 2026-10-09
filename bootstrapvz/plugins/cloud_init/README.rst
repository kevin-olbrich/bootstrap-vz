cloud-init
----------

This plugin installs and configures
`cloud-init <https://packages.debian.org/wheezy-backports/cloud-init>`__
on the system. Depending on the release it installs it from either
backports or the main repository.

cloud-init is only compatible with Debian wheezy and upwards.

Settings
~~~~~~~~

-  ``username``: The username of the account to create. The plugin sets it as
   the cloud-init default user in
   ``/etc/cloud/cloud.cfg.d/02_bootstrapvz_user.cfg``, together with
   passwordless sudo and ``/bin/bash`` as the login shell.
   ``required``
-  ``groups``: A list of strings specifying which additional groups the account
   should be added to, on top of the default groups in the
   ``/etc/cloud/cloud.cfg`` of the cloud-init package.
   ``optional``
-  ``disable_modules``: A list of strings specifying which cloud-init
   modules should be disabled.
   ``optional``
-  ``metadata_sources``: A string that sets the
   `datasources <http://cloudinit.readthedocs.org/en/latest/topics/datasources.html>`__
   that cloud-init should try fetching metadata from (corresponds to
   debconf-set-selections values). The source is
   automatically set when using the ec2 provider.
   ``optional``
