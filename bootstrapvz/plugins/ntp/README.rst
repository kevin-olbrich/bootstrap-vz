NTP
---

This plugins installs the Network Time Protocol daemon and optionally
defines which time servers it should use.

Debian replaced ``ntp`` with ``ntpsec`` in bookworm, so from bookworm
onward the plugin installs ``ntpsec`` instead of ``ntp`` and configures
it in ``/etc/ntpsec/ntp.conf`` instead of ``/etc/ntp.conf``.

Settings
~~~~~~~~

-  ``servers``: A list of strings specifying which servers should be
   used to synchronize the machine clock. They replace the Debian pool
   servers of the default configuration. The ``tos`` lines are removed
   as well, because the ``minsane 3`` of the default ``ntpsec``
   configuration would keep ntpd from setting the clock with fewer than
   three servers.
   ``optional``
