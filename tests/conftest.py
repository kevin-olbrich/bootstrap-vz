import importlib
import pkgutil
import bootstrapvz

# Import every module before any test runs. Many modules bind helpers such as log_check_call
# when they are imported (from bootstrapvz.common.tools import log_check_call). A module imported
# for the first time while a test has patched such a helper would keep the patched function for
# the rest of the test run, so the result of a test would depend on the tests that ran before it.
for module in pkgutil.walk_packages(bootstrapvz.__path__, bootstrapvz.__name__ + '.'):
    importlib.import_module(module.name)
