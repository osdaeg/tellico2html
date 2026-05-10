#!/usr/bin/env python3
"""
Tellico2HTML — Servidor HTTP mínimo
Expone /health y sirve los archivos estáticos como fallback.
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
import sys
import subprocess
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("t2h-server")

PORT = int(os.getenv("TELLICO2HTML_INTERNAL_PORT", "8000"))

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # silenciar logs por defecto del servidor

    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"status": "ok", "service": "tellico2html"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = b"Tellico2HTML is running. Access via Nginx at /t2h"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)

def main():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    log.info(f"Servidor escuchando en :{PORT}")
    server.serve_forever()

if __name__ == "__main__":
    main()
