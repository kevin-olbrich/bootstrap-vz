KVM
===

The `KVM <http://www.linux-kvm.org/page/Main_Page>`__ provider creates
virtual images for Linux Kernel-based Virtual Machines. It supports the
installation of `virtio kernel
modules <http://www.linux-kvm.org/page/Virtio>`__ (paravirtualized
drivers for IO operations).
It also supports creating an image with LVM and qcow2 as a disk backend.
``arm64`` images (jessie and later) need ``bootloader: none``, because
the grub and extlinux bootloaders only support ``i386`` and ``amd64``.

On Debian jessie and newer, ``serial-getty@ttyS0.service`` is enabled for serial
logins. The boot configuration no longer adds ``console=ttyS0`` or
``earlyprintk=ttyS0`` kernel arguments.

Manifest settings
-----------------

Provider
~~~~~~~~

-  ``virtio``: Specifies which virtio kernel modules to install. They are listed in
   ``/etc/initramfs-tools/modules`` of the image, so that the initramfs loads them.
   ``optional``
-  ``console``: With GRUB, ``virtual`` adds ``console=tty0`` for startup messages
   and the single-user console. The legacy value ``serial`` remains accepted for
   compatibility but adds no console arguments, as does omitting this setting.
   This setting does not affect the serial login service.
   ``optional``
-  ``logicalvolume``: Specifies the logical volume where the disk image will be built.
-  ``volumegroup``: Specifies the volume group where the logical volume will be stored.
   Both options are required if ``lvm`` was given as a disk backend, and can only be used with it.


Example:

.. code-block:: yaml

    ---
    provider:
      name: kvm
      virtio:
        - virtio_blk
        - virtio_net
      console: virtual
    volume:
      backing: lvm
      logicalvolume: lvtest
      volumegroup: vgtest
