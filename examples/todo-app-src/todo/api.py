"""HTTP routes delegate every write to Store."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import re
from urllib.parse import urlsplit

from .store import Store


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            self.reply(200, {"ok": True})
        elif path == "/todos":
            self.reply(200, self.server.store.list_all())
        else:
            self.reply(404, {"error": "route not found"})

    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            if path == "/todos":
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise ValueError("body must be 1..65536 bytes")
                body = json.loads(self.rfile.read(length))
                self.reply(201, self.server.store.add(body.get("title")))
            elif match := re.fullmatch(r"/todos/([0-9]+)/done", path):
                self.reply(200, self.server.store.transition(int(match[1]), "done"))
            else:
                self.reply(404, {"error": "route not found"})
        except KeyError:
            self.reply(404, {"error": "todo not found"})
        except (ValueError, AttributeError) as error:
            self.reply(409, {"error": str(error)})

    def do_DELETE(self):
        path = urlsplit(self.path).path
        try:
            if match := re.fullmatch(r"/todos/([0-9]+)", path):
                self.reply(200, self.server.store.transition(int(match[1]), "archived"))
            else:
                self.reply(404, {"error": "route not found"})
        except KeyError:
            self.reply(404, {"error": "todo not found"})
        except ValueError as error:
            self.reply(409, {"error": str(error)})


def serve(port, data):
    server = HTTPServer(("127.0.0.1", port), Handler)
    server.store = Store(data)
    try:
        server.serve_forever()
    finally:
        server.server_close()
