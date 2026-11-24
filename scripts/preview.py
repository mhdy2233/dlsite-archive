#!/usr/bin/env python3
"""Local preview server that mimics GitHub Pages' 404.html fallback."""
import http.server
import os
import socketserver


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path != "/" and not os.path.exists("." + path):
            self.path = "/404.html"
        return super().do_GET()

    def log_message(self, *a):
        pass


socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("127.0.0.1", 8899), Handler) as httpd:
    print("preview on http://127.0.0.1:8899")
    httpd.serve_forever()
