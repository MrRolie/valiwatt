"""Serve live/site privately (bind address from argv, the Tailscale one), never cached: the page and its data change
every 10 minutes and a cached copy shows stale code or numbers (seen 2026-11-13)."""
import functools
import http.server
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent / "site"


class NoCache(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def list_directory(self, path):  # no directory listings
        self.send_error(404)


if __name__ == "__main__":
    host, port = sys.argv[1], int(sys.argv[2])
    http.server.ThreadingHTTPServer((host, port), functools.partial(NoCache, directory=str(SITE))).serve_forever()
