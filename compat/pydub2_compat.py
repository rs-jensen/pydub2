import importlib
import os
import sys
from importlib.machinery import ModuleSpec


class Alias:
    off = False
    orig = {}

    def find_spec(self, name, path=None, target=None):
        if self.off or (name != "pydub" and not name.startswith("pydub.")):
            return None
        new = "pydub2" + name[5:]
        try:
            mod = importlib.import_module(new)
        except Exception as e:
            if name == "pydub":
                self.off = True
                try:
                    sys.stderr.write("pydub2-compat: cant import pydub2 (%s), using the real pydub\n" % e)
                except Exception:
                    pass
                return None
            if isinstance(e, ModuleNotFoundError) and e.name == new:
                return None
            raise
        spec = ModuleSpec(name, self)
        spec.loader_state = mod
        return spec

    def create_module(self, spec):
        mod = spec.loader_state
        self.orig[mod.__name__] = mod.__spec__
        return mod

    def get_code(self, name):
        return None

    def exec_module(self, module):
        # the import system sets __spec__ to the alias, put the pydub2 one back
        module.__spec__ = self.orig[module.__name__]


try:
    if os.environ.get("PYDUB2_COMPAT") != "0":
        sys.meta_path.insert(0, Alias())
except Exception:
    pass
