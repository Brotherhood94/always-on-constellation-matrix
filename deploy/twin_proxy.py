"""Expose only one twin's provider grants on its internal model network."""
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os


address = str(ipaddress.ip_address(os.environ['TWIN_PROVIDER_IP']))
grants = set(json.loads(os.environ['TWIN_GRANTS']))


class Proxy(BaseHTTPRequestHandler):
    def do_POST(self):
        auth = self.headers.get('Authorization', '')
        length = self.headers.get('Content-Length', '')
        if self.path.removeprefix('/agents/') not in grants or not self.path.startswith('/agents/') or not auth.startswith('Bearer '):
            return self.send_error(403)
        if not length.isdecimal() or not 0 < int(length) <= 1_800_000:
            return self.send_error(400)
        upstream = HTTPConnection(address, 8082, timeout=360)
        try:
            upstream.request('POST', self.path, self.rfile.read(int(length)), {'Authorization': auth, 'Content-Type': 'application/json'})
            response = upstream.getresponse()
            self.send_response(response.status)
            self.send_header('Content-Type', response.getheader('Content-Type', 'application/json'))
            self.end_headers()
            for line in response:
                self.wfile.write(line)
                self.wfile.flush()
        finally:
            upstream.close()

    def log_message(self, *args):
        pass


ThreadingHTTPServer(('0.0.0.0', 8082), Proxy).serve_forever()
