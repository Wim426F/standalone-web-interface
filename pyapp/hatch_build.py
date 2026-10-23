"""Build hook: embed the shared web frontend (../data) into the package.

The frontend lives once at the repo root and serves both the ESP32 firmware
(as a SPIFFS image) and this package. Wheels built from the repo pick it up
from ../data; wheels built from an unpacked sdist find the copy the sdist
hook placed at ./data.
"""

import os

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class DataFrontendHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version, build_data):
        for cand in (os.path.join(self.root, "..", "data"),   # repo checkout
                     os.path.join(self.root, "data")):        # unpacked sdist
            if os.path.isfile(os.path.join(cand, "index.html")):
                src = os.path.normpath(cand)
                dest = "openinverter_web/data" if self.target_name == "wheel" else "data"
                build_data["force_include"][src] = dest
                return
        raise RuntimeError(
            "web frontend not found: expected ../data (repo) or ./data (sdist)")
