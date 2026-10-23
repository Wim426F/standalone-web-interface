"""Static guard for the firmware's permission router (npm run test:scripts).

Every port-80 endpoint must be registered through route() — whose
permission comes from weblogic::ROUTES in src/web_logic.h — so that no
handler can run without the sign-in check. This fails if the firmware calls
server.on()/onNotFound() directly, re-adds HTTPUpdateServer (whose handlers
bypass the router), or registers a path/method missing from the table.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INO = (ROOT / "esp32-web-interface.ino").read_text(encoding="utf-8")
WEB_LOGIC = (ROOT / "src" / "web_logic.h").read_text(encoding="utf-8")

BITS = {"M_GET": 1, "M_POST": 2, "M_PUT": 4, "M_DELETE": 8, "M_OTHER": 16, "M_ANY": 31}


def code_only(src):
    """Source without // and /* */ comments or string contents."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    return src


def table():
    block = WEB_LOGIC[WEB_LOGIC.index("static const RouteRule ROUTES[]"):WEB_LOGIC.index("static const size_t ROUTE_COUNT")]
    out = {}
    for path, methods in re.findall(r'\{\s*"([^"]+)",\s*([A-Z_| ]+?),\s*PERM_[A-Z]+\s*\}', block):
        mask = 0
        for m in methods.split("|"):
            mask |= BITS[m.strip()]
        out[path] = out.get(path, 0) | mask
    return out


class RouteGuard(unittest.TestCase):
    def test_no_direct_handler_registration(self):
        code = code_only(INO)
        for bad in ("server.on(", "server.onNotFound(", "server.serveStatic(", "HTTPUpdateServer", "updater.setup("):
            self.assertNotIn(bad, code, bad + " bypasses the permission router; use route()")

    def test_router_is_installed_first(self):
        code = code_only(INO)
        self.assertIn("server.addHandler(&authRouter);", code)
        # No handler may be added before it (the first matching handler wins)
        self.assertEqual(code.count("server.addHandler("), 1)

    def test_every_route_is_in_the_permission_table(self):
        known = table()
        routes = re.findall(r'\broute\("([^"]+)",\s*([a-zA-Z_:| ]+?),', code_only(INO))
        self.assertGreater(len(routes), 30)
        for path, methods in routes:
            mask = 0
            for m in methods.split("|"):
                mask |= BITS[m.strip().replace("weblogic::", "")]
            self.assertIn(path, known, path + " is not in weblogic::ROUTES")
            self.assertEqual(known[path] & mask, mask, path + ": method not covered by weblogic::ROUTES")

    def test_users_never_live_in_spiffs(self):
        # Accounts are in NVS; nothing may write them to the filesystem
        code = code_only(INO)
        self.assertIn('p.begin("oiauth"', code)
        self.assertNotRegex(code, r'SPIFFS\.open\([^)]*user', "user records must stay out of SPIFFS")


if __name__ == "__main__":
    unittest.main()
