"""Small HTTP helpers: MIME table and a multipart/form-data parser.

The multipart parser is a byte-level port of the one in
tests/mock-esp/server.mjs — its only client is the frontend's own FormData
uploads (field "updatefile", filename = target name), so it stays minimal
instead of pulling in a framework. (The stdlib cgi module that used to do
this was removed in Python 3.13.)
"""

import re

MIME = {
    ".html": "text/html",
    ".htm": "text/html",
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
    ".txt": "text/plain",
    ".bin": "application/octet-stream",
}


def guess_type(name):
    dot = name.rfind(".")
    if dot < 0:
        return "application/octet-stream"
    return MIME.get(name[dot:].lower(), "application/octet-stream")


def parse_multipart(body, content_type):
    """Parse a multipart/form-data body into [{name, filename, data}, ...]."""
    m = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not m:
        return []
    boundary = b"--" + m.group(1).encode()
    parts = []
    start = body.find(boundary)
    while start != -1:
        nxt = body.find(boundary, start + len(boundary))
        if nxt == -1:
            break
        segment = body[start + len(boundary) + 2 : nxt - 2]  # trim CRLFs
        header_end = segment.find(b"\r\n\r\n")
        if header_end != -1:
            headers = segment[:header_end].decode("utf-8", "replace")
            nm = re.search(r'name="([^"]*)"', headers)
            fm = re.search(r'filename="([^"]*)"', headers)
            parts.append({
                "name": nm.group(1) if nm else "",
                "filename": fm.group(1) if fm else None,
                "data": segment[header_end + 4 :],
            })
        start = nxt
    return parts
