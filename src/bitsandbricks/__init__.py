"""The name before the rename: `import bitsandbricks.core` and friends give the `mortar` modules.

Plugins written for bitsandbricks keep working unchanged. Each `bitsandbricks.X` is the very same
module object as `mortar.X` (not a copy), so state such as the current transport is shared.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys

from mortar import __version__  # noqa: F401


class _Alias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path=None, target=None):
        if name.startswith(__name__ + ".") and name != __name__ + ".__main__":  # python -m runs our own
            return importlib.util.spec_from_loader(name, self)
        return None

    def create_module(self, spec):
        return importlib.import_module("mortar" + spec.name[len(__name__):])

    def exec_module(self, module) -> None:
        pass  # already run as mortar.*


sys.meta_path.insert(0, _Alias())
