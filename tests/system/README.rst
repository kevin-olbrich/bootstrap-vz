System tests
============
`System tests`__ test
bootstrap-vz in its entirety.
This testing includes building images from manifests and
creating/booting said images.

__ http://en.wikipedia.org/wiki/System_testing

Since hardcoding manifests for each test, bootstrapping them and booting the
resulting images is too much code for a single test, a testing harness has
been developed that reduces each test to it's bare essentials:

* Combine available `manifest partials <#manifest-partials>`__ into a single manifest
* Boot an instance from a manifest
* Run tests on the booted instance

The harness bootstraps every image on the machine that runs the tests, so run
them as root on a build host that can build and boot the tested providers.


system-tests.yml
----------------
When running system tests, the framework will look for ``system-tests.yml``
at the root of the repo and raise an error if it is not found.
The file holds the settings of your build host. Keep it out of version control,
it contains credentials.

.. code-block:: yaml

    build_settings:
      guest_additions: /root/images/VBoxGuestAdditions.iso
      apt_proxy:
        address: 127.0.0.1
        port: 3142
      ec2-credentials:
        access-key: AFAKEACCESSKEYFORAWS
        secret-key: thes3cr3tkeyf0ryourawsaccount/FS4d8Qdva
        certificate: /root/manifests/cert.pem
        private-key: /root/manifests/pk.pem
        user-id: 1234-1234-1234
      s3-region: eu-west-1
    run_settings:
      ec2-credentials:
        access-key: AFAKEACCESSKEYFORAWS
        secret-key: thes3cr3tkeyf0ryourawsaccount/FS4d8Qdva
      docker:
        machine: default

The build settings override manifest properties before bootstrapping:

* ``guest_additions`` specifies the path to the VirtualBox guest additions ISO.
* ``apt_proxy`` sets the configuration for the `apt_proxy plugin <../../bootstrapvz/plugins/apt_proxy>`__.
* ``ec2-credentials`` contains all the settings you know from EC2 manifests.
* ``s3-region`` overrides the s3 bucket region when bootstrapping S3 backed images.

The run settings hold information about how to start a bootstrapped image:

* ``ec2-credentials`` contains the access key and secret key used to boot
  an EC2 AMI.
* ``docker.machine`` The docker machine on which an image built for docker
  should run.


Manifest combinations
---------------------
The tests mainly focus on varying key parts of an image
(e.g. partitioning, Debian release, bootloader, ec2 backing, ec2 virtualization method)
that have been problem areas.
Essentially the tests are the cartesian product of these key parts.


Aborting a test
---------------
You can press ``Ctrl+C`` at any time during the testing to abort -
the harness will automatically clean up any temporary resources and shut down
running instances. Pressing ``Ctrl+C`` a second time stops the cleanup and quits
immediately.


Manifest partials
-----------------
Instead of creating manifests from scratch for each single test, reusable parts
are factored out into partials in the manifest folder.
This allows code like this:

.. code-block:: python

    partials = {'vdi': '{provider: {name: virtualbox}, volume: {backing: vdi}}',
                'vmdk': '{provider: {name: virtualbox}, volume: {backing: vmdk}}',
                }

    def test_unpartitioned_extlinux_oldstable():
        std_partials = ['base', 'stable64', 'extlinux', 'unpartitioned', 'root_password']
        custom_partials = [partials['vmdk']]
        manifest_data = merge_manifest_data(std_partials, custom_partials)

The code above produces a manifest for Debian stable 64-bit unpartitioned
virtualbox VMDK image.
``root_password`` is a special partial in that the actual password is
randomly generated on load.


Missing parts
-------------
The system testing harness is in no way complete.

* It still has no support for providers other than Virtualbox, EC2 and Docker.
* Creating an SSH connection to a booted instance is cumbersome and does not
  happen in any of the tests - this would be particularly useful when manifests
  are to be tested beyond whether they boot up.
