The testing framework consists of two parts:
The unit tests and the integration tests.

The `unit tests <unit>`__ are responsible for testing individual
parts of bootstrap-vz, while the `integration tests <integration>`__ test
entire manifests by bootstrapping and booting them.

Selecting tests
---------------
The tests are run with `pytest <https://docs.pytest.org/>`__.
To run one specific test suite simply append its file path to tox after ``--``:

.. code-block:: sh

    $ tox -e unit -- tests/unit/releases_tests.py

Specific tests can be selected by appending the function name with ``::``
to the file path -- to run more than one tests, simply attach more arguments.


.. code-block:: sh

    $ tox -e unit -- tests/unit/releases_tests.py::test_lt tests/unit/releases_tests.py::test_eq
