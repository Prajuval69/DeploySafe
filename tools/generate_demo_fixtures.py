#!/usr/bin/env python3
"""Regenerate deterministic, safe, loopback-only sample release apps (stdlib only)."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'fixtures' / 'demo_releases'

APP_CODE = r"""# Tiny offline HTTP demo release. Not DeploySafe itself.
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
"""

SCENARIOS = [
    ('1.0.0', 'healthy', None, True),
    ('1.1.0', 'healthy', None, True),
    ('1.2.0-healthfail', 'health_503', None, True),
    ('1.3.0-badconfig', 'healthy', None, False),
    ('1.4.0-timeout', 'slow_health', None, True),
    ('1.5.0-tampered', 'healthy', None, True),
    ('1.6.0-crash', 'crash', None, True),
    ('1.7.0-wrongversion', 'healthy', '9.9.9', True),
]

def create():
    OUT.mkdir(parents=True,exist_ok=True)
    for release, mode, forced_version, valid_bind in SCENARIOS:
        folder=OUT/release
        folder.mkdir(parents=True,exist_ok=True)
        app_file=folder/'app.py'
        config_file=folder/'app_config.json'
        app_file.write_text(APP_CODE,encoding='utf-8')
        cfg={'version':release,'mode':mode,'bind':'127.0.0.1' if valid_bind else '0.0.0.0'}
        if forced_version: cfg['reported_version']=forced_version
        config_file.write_text(json.dumps(cfg,indent=2,sort_keys=True)+'\n',encoding='utf-8')
        hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (app_file,config_file)}
        manifest={'schema_version':1,'release_id':release,'entrypoint':'app.py',
                  'config_file':'app_config.json','sha256':hashes,
                  'description':'Deterministic local-only test release'}
        (folder/'release.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n',encoding='utf-8')
        if release == '1.5.0-tampered':
            with app_file.open('a',encoding='utf-8') as f:
                f.write('# Intentional checksum mismatch fixture; never execute this release.\n')
    print('Generated',len(SCENARIOS),'offline demo release folders at', OUT.relative_to(ROOT))

if __name__=='__main__':create()
