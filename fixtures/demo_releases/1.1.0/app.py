# Tiny offline HTTP demo release. Not DeploySafe itself.
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=8765)
parser.add_argument('--config', default=str(Path(__file__).with_name('app_config.json')))
args = parser.parse_args()
if not 8000 <= args.port <= 8999:
    raise SystemExit('demo port outside safe range')
config = json.loads(Path(args.config).read_text(encoding='utf-8'))
if config.get('bind') != '127.0.0.1':
    raise SystemExit('demo only allows 127.0.0.1')
if config.get('mode') == 'crash':
    raise RuntimeError('Controlled demo startup failure; no host changes')

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ('/health','/version'):
            self.send_error(404)
            return
        if self.path == '/health' and config['mode'] == 'slow_health':
            time.sleep(1.2)
        status = 503 if self.path == '/health' and config['mode'] == 'health_503' else 200
        reported_version = config.get('reported_version',config['version'])
        body = json.dumps({'status':'ok' if status == 200 else 'error', 'version':reported_version}).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
    def log_message(self, fmt, *args):
        sys.stderr.write('[demo] ' + (fmt % args) + '\n')

ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
