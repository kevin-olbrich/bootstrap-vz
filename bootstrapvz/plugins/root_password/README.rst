root password
-------------

Sets the root password. This plugin removes the task that disables the
SSH password authentication. Neither the password nor its hash is written
to the log.

Settings
~~~~~~~~
``oneOf``

-  ``password``: The password for the root user.
-  ``password-crypted``: The password for the root user[crypt(3) hash]

The following command (available from the **whois** package) can be used
to generate a SHA-512 based crypt(3) hash for a password:

.. code-block:: shell

  mkpasswd -m sha-512

