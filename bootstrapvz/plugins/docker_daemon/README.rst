Docker daemon
-------------

Install the `Docker <https://www.docker.com/>`__ Engine in the image
from Docker's official APT repository at ``download.docker.com``.
The plugin adds the repository together with Docker's signing key
(fingerprint ``9DC8 5822 9FC7 DD38 854A E2D8 8D81 803C 0EBF CD88``,
shipped with the plugin) and installs ``docker-ce``, ``docker-ce-cli``
and ``containerd.io``.

Docker publishes packages for Debian ``buster``, ``bullseye``,
``bookworm`` and ``trixie`` on the ``amd64`` and ``arm64``
architectures supported by this plugin.

Settings
~~~~~~~~

-  ``version``: Docker Engine version to install, e.g. ``29.8.1``.
   The version is pinned with an APT preference, so ``apt-get upgrade``
   keeps it. To install the latest version simply omit this setting.
   ``optional``
-  ``docker_opts``: Additional command line options for ``dockerd``,
   e.g. ``--dns 8.8.8.8``. They are added to the ``docker.service``
   systemd unit with a drop-in. Settings that ``/etc/docker/daemon.json``
   supports can also be placed there with the file_copy plugin.
   ``optional``
-  ``pull_images``: Images to add to the image store at build time, a
   list of image names to ``docker pull`` or paths to ``.tar.gz``/``.tgz``
   archives to ``docker load``. The Docker daemon from the image is run
   on the build host for this, so the host needs the same architecture
   as the image.
   ``optional``
-  ``pull_images_retries``: How many seconds to wait for that temporary
   Docker daemon to start.
   Default: ``10``
   ``optional``
