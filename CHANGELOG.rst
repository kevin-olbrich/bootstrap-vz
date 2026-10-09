Changelog
=========

2026-09-24
----------
Kevin Olbrich:
    * Run on Python 3.13 only: replace removed modules (pipes, distutils, imp via nose),
      pkg_resources and boto 2, and fix Python 3 behavior changes (bytes/str, dict views,
      hashing, set ordering, text encodings)
    * Migrate the test suite from nose to pytest, and replace Travis CI with GitHub Actions
      running every tox env
    * docker_daemon: install Docker CE from download.docker.com, pin versions with APT
      preferences, apply docker_opts with a systemd drop-in, and make pull_images usable
    * Replace the puppet plugin with an openvox plugin that installs openvox-agent from
      apt.voxpupuli.org on bullseye, bookworm and trixie. Manifests must rename the
      ``puppet`` plugin key to ``openvox``
    * pip_install, pip3_install, ansible, GCE and Azure: install Python 3 packages on
      bullseye and newer, and allow pip to install into the system Python on bookworm+
    * salt: download the bootstrap script over HTTPS from its current location
    * google_cloud_repo: install the key without apt-key (removed in trixie)
    * ec2_publish: port to boto3. RegisterAMI: pass KernelId for PV images
    * Cache debootstrap tarballs again (deterministic tarball names)
    * Security: stop disabling the kernel's CPU vulnerability mitigations (mitigations=off,
      nopti, nospectre_v1/v2, mds=off, tsx=on and others) in every grub based image
    * Reject manifests that build Debian trixie or newer for i386, which Debian no longer
      supports (no kernel, no installer), and add Debian 15 (duke) as a release
    * Remove remote bootstrapping (``bootstrap-vz-remote``, ``bootstrap-vz-server`` and the
      Pyro4 dependency). Images are built by running ``bootstrap-vz`` on the build host. The
      system tests build locally and read their settings from ``system-tests.yml``
    * Replace setup.py with pyproject.toml and manage dependencies with uv: ``uv sync`` installs
      bootstrap-vz, ``uv.lock`` pins every dependency, and tox installs its envs from the lockfile
      through tox-uv. The package no longer installs a top-level ``tests`` package
    * Report manifest validation errors at the full path of the offending setting, report volume
      and partition mistakes on the right key, and name a misspelled ``system.release`` in the
      error. The volume schema is stricter: ``backing`` is required, and the lvm backing needs
      both ``volumegroup`` and ``logicalvolume``
    * Reject manifests whose provider has no kernel for the release and architecture (for
      example EC2 arm64 or GCE i386), arm64 with the grub or extlinux bootloader, and VirtualBox
      on arm64 during validation instead of failing during the build
    * Apply the root partition's ``mountopts`` on unpartitioned volumes (partitions type none)
      when mounting and in /etc/fstab instead of silently ignoring them
    * Run tasks without an ordering constraint in a fixed order (by module path and class name),
      so every build of the same manifest runs its tasks in the same order
    * Start every partition on a 1MiB boundary: pass parted the last sector of each partition
      instead of padding with a one-sector gap, which left every partition after the first at an
      odd sector. Without grub, the first partition now starts at 1MiB
    * minimize_size: drop the locales package and skip locale generation only when
      ``dpkg.locales`` is an empty list; the check was inverted, so listing locales left
      ``system.locale`` ungenerated
    * pip3_install: validate the plugin settings against the ``pip3_install`` key instead of
      ``pip_install``, and require ``packages`` in both pip plugins
    * grub: write boolean settings as lowercase true/false in /etc/default/grub, so
      ``GRUB_DISABLE_RECOVERY`` and ``GRUB_HIDDEN_TIMEOUT_QUIET`` take effect and images no
      longer get recovery menu entries
    * apt_proxy: run debootstrap with ``http_proxy`` set to the configured proxy, so the base
      system is downloaded through the cache too
    * Stop copying the build host's mount table to /etc/mtab for bookworm and newer on every
      provider (ec2, gce, azure, oracle, virtualbox, docker), as was already done for kvm
    * Stop touching the build host: kvm ``virtio`` lists its modules in the image's
      /etc/initramfs-tools/modules (before the initramfs is rebuilt) instead of the host's,
      SetLocalTimeCopy copies the image's zoneinfo instead of the host's, and the ClearMOTD
      task, which truncated the host's /var/run/motd and never changed the image, is removed
    * minbase images (all Docker examples) no longer keep the build host's /etc/resolv.conf and
      /etc/hostname, and ``system.hostname`` is no longer ignored for them; only the network
      interface configuration is still skipped for minbase without netbase. openvox applies its
      manifest after ``system.hostname`` is written
    * cloud_init: set the default user in /etc/cloud/cloud.cfg.d/02_bootstrapvz_user.cfg instead
      of editing cloud.cfg with regular expressions, which failed on every build of trixie and
      newer (cloud-init 23.3+); ``groups`` are added to the release's default groups
    * prebootstrapped: create and restore EBS snapshots with boto3, which fixes crashes in both
      EBS modes and logs the created snapshot ID again
    * ec2_launch: port ``deregister_ami`` to boto3; it crashed on every build after launching
      the instance and left the AMI and its snapshot behind
    * ntp: install ntpsec and edit /etc/ntpsec/ntp.conf on bookworm and newer, where ntp is only
      a transitional package, and drop the default ``tos`` lines when ``servers`` are set so
      ntpd also syncs with fewer than three servers

2017-02-20
----------
Hugo Antoniio Sepulveda Manriquez:
    * Updated puppet plugin module:
        * Installs Puppetlabs 4 PC1 agent software from apt.puppetlabs.com
        * Enables you to install modules from forge.puppetlabs.com in the image

    * Important limitations
        * Only works for Wheezy and Jessie for now.
        * If you need puppet 3, just add 'puppet' packages provider list.
        * modules: When installing from forge, it assumes 'install --force'
        * modules: When installing from forge, It assumes master version on forge

2016-06-04
----------
Anders Ingemann
    * Disable persistent network interface names for >=stretch (by @apolloclark)
    * grub defaults and linux boot options are now easier to configure
    * Source ixgbevf driver from intel, not sourceforge (by @justinsb)
    * Use systemd on jessie (by @JamesBromberger)
    * Tune ec2 images (sysctl settings, module blacklisting, nofail in fstab) (by @JamesBromberger)
    * Add enable_modules option for cloud-init (by @JamesBromberger)

2016-06-02
----------
Peter Wagner
    * Added ec2_publish plugin

2016-06-02
----------
Zach Marano:
    * Fix expand-root script to work with newer version of growpart (in jessie-backports and beyond).
    * Overhaul Google Compute Engine image build.
        * Add support for Google Cloud repositories.
        * Google Cloud SDK install uses a deb package from a Google Cloud repository.
        * Google Compute Engine guest software is installed from a Google Cloud repository.
        * Google Compute Engine guest software for Debian 8 is updated to new refactor.
        * Google Compute Engine wheezy and wheezy-backports manifests are deprecated.

2016-03-03
----------
Anders Ingemann:
    * Rename integration tests to system tests

2016-02-23
----------
Nicolas Braud-Santoni:
    * #282, #290: Added 'debconf' plugin
    * #290: Relaxed requirements on plugins manifests

2016-02-10
----------
Manoj Srivastava:
    * #252: Added support for password and static pubkey auth

2016-02-06
----------
Tiago Ilieve:
    * Added Oracle Compute Cloud provider
    * #280: Declared Squeeze as unsupported

2016-01-14
----------
Jesse Szwedko:
    * #269: EC2: Added growpart script extension

2016-01-10
----------
Clark Laughlin:
    * Enabled support for KVM on arm64

2015-12-19
----------
Tim Sattarov:
    * #263: Ignore loopback interface in udev rules (reduces startup of networking by a factor of 10)

2015-12-13
----------
Anders Ingemann:
    * Docker provider implemented (including integration testing harness & tests)
    * minimize_size: Added various size reduction options for dpkg and apt
    * Removed image section in manifest.
      Provider specific options have been moved to the provider section.
      The image name is now specified on the top level of the manifest with "name"
    * Provider docs have been greatly improved. All now list their special options.
    * All manifest option documentation is now accompanied by an example.
    * Added documentation for the integration test providers

2015-11-13
----------
Marcin Kulisz:
    * Exclude docs from binary package

2015-10-20
----------
Max Illfelder:
    * Remove support for the GCE Debian mirror

2015-10-14
----------
Anders Ingemann:
    * Bootstrap azure images directly to VHD

2015-09-28
----------
Rick Wright:
    * Change GRUB_HIDDEN_TIMEOUT to 0 from true and set GRUB_HIDDEN_TIMEOUT_QUIET to true.

2015-09-24
----------
Rick Wright:
    * Fix a problem with Debian 8 on GCE with >2TB disks

2015-09-04
----------
Emmanuel Kasper:
    * Set Virtualbox memory to 512 MB

2015-08-07
----------
Tiago Ilieve:
    * Change default Debian mirror

2015-08-06
----------
Stephen A. Zarkos:
    * Azure: Change default shell in /etc/default/useradd for Azure images
    * Azure: Add boot parameters to Azure config to ease local debugging
    * Azure: Add apt import for backports
    * Azure: Comment GRUB_HIDDEN_TIMEOUT so we can set GRUB_TIMEOUT
    * Azure: Wheezy images use wheezy-backports kernel by default
    * Azure: Change Wheezy image to use single partition
    * Azure: Update WALinuxAgent to use 2.0.14
    * Azure: Make sure we can override grub.ConfigureGrub for Azure images
    * Azure: Add console=tty0 to see kernel/boot messages on local console
    * Azure: Set serial port speed to 115200
    * Azure: Fix error with applying azure/assets/udev.diff

2015-07-30
----------
James Bromberger:
    * AWS: Support multiple ENI
    * AWS: PVGRUB AKIs for Frankfurt region

2015-06-29
----------
Alex Adriaanse:
    * Fix DKMS kernel version error
    * Add support for Btrfs
    * Add EC2 Jessie HVM manifest

2015-05-08
----------
Alexandre Derumier:
    * Fix #219: ^PermitRootLogin regex

2015-05-02
----------
Anders Ingemann:
    * Fix #32: Add image_commands example
    * Fix #99: rename image_commands to commands
    * Fix #139: Vagrant / Virtualbox provider should set ostype when 32 bits selected
    * Fix #204: Create a new phase where user modification tasks can run

2015-04-29
----------
Anders Ingemann:
    * Fix #104: Don't verify default target when adding packages
    * Fix #217: Implement get_version() function in common.tools

2015-04-28
----------
Jonh Wendell:
    * root_password: Enable SSH root login

2015-04-27
----------
John Kristensen:
    * Add authentication support to the apt proxy plugin

2015-04-25
----------
Anders Ingemann (work started 2014-08-31, merged on 2015-04-25):
    * Introduce `remote bootstrapping <bootstrapvz/remote>`__
    * Introduce `integration testing <tests/integration>`__ (for VirtualBox and EC2)
    * Merge the end-user documentation into the sphinx docs
      (plugin & provider docs are now located in their respective folders as READMEs)
    * Include READMEs in sphinx docs and transform their links
    * Docs for integration testing
    * Document the remote bootstrapping procedure
    * Add documentation about the documentation
    * Add list of supported builds to the docs
    * Add html output to integration tests
    * Implement PR #201 by @jszwedko (bump required euca2ools version)
    * grub now works on jessie
    * extlinux is now running on jessie
    * Issue warning when specifying pre/successors across phases (but still error out if it's a conflict)
    * Add salt dependencies in the right phase
    * extlinux now works with GPT on HVM instances
    * Take @ssgelm's advice in #155 and copy the mount table -- df warnings no more
    * Generally deny installing grub on squeeze (too much of a hassle to get working, PRs welcome)
    * Add 1 sector gap between partitions on GPT
    * Add new task: DetermineKernelVersion, this can potentially fix a lot of small problems
    * Disable getty processes on jessie through logind config
    * Partition volumes by sectors instead of bytes
      This allows for finer grained control over the partition sizes and gaps
      Add new Sectors unit, enhance Bytes unit, add unit tests for both
    * Don't require qemu for raw volumes, use `truncate` instead
    * Fix #179: Disabling getty processes task fails half the time
    * Split grub and extlinux installs into separate modules
    * Fix extlinux config for squeeze
    * Fix #136: Make extlinux output boot messages to the serial console
    * Extend sed_i to raise Exceptions when the expected amount of replacements is not met

Jonas Bergler:
    * Fixes #145: Fix installation of vbox guest additions.

Tiago Ilieve:
    * Fixes #142: msdos partition type incorrect for swap partition (Linux)

2015-04-23
----------
Tiago Ilieve:
    * Fixes #212: Sparse file is created on the current directory

2014-11-23
----------
Noah Fontes:
    * Add support for enhanced networking on EC2 images

2014-07-12
----------
Tiago Ilieve:
    * Fixes #96: AddBackports is now a common task

2014-07-09
----------
Anders Ingemann:
    * Allow passing data into the manifest
    * Refactor logging setup to be more modular
    * Convert every JSON file to YAML
    * Convert "provider" into provider specific section

2014-07-02
----------
Vladimir Vitkov:
    * Improve grub options to work better with virtual machines

2014-06-30
----------
Tomasz Rybak:
    * Return information about created image

2014-06-22
----------
Victor Marmol:
    * Enable the memory cgroup for the Docker plugin

2014-06-19
----------
Tiago Ilieve:
    * Fixes #94: allow stable/oldstable as release name on manifest

Vladimir Vitkov:
    * Improve ami listing performance

2014-06-07
----------
Tiago Ilieve:
    * Download `gsutil` tarball to workspace instead of working directory
    * Fixes #97: remove raw disk image created by GCE after build

2014-06-06
----------
Ilya Margolin:
    * pip_install plugin

2014-05-23
----------
Tiago Ilieve:
    * Fixes #95: check if the specified APT proxy server can be reached

2014-05-04
----------
Dhananjay Balan:
    * Salt minion installation & configuration plugin
    * Expose debootstrap --include-packages and --exclude-packages options to manifest

2014-05-03
----------
Anders Ingemann:
    * Require hostname setting for vagrant plugin
    * Fixes #14: S3 images can now be bootstrapped outside EC2.
    * Added enable_agent option to puppet plugin

2014-05-02
----------
Tomasz Rybak:
    * Added Google Compute Engine Provider
