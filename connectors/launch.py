"""Start wrappers, verify live GitHub and H3, optionally release the handoff.

python connectors/launch.py --install --release
Keeps all wrappers running until Ctrl-C. Never releases on skipped checks.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
from urllib import request, error
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def require_all_passed(report: Path) -> None:
    suites = list(ET.parse(report).getroot().iter('testsuite'))
    if sum(int(s.get('tests', 0)) for s in suites) < 7 or any(
            int(s.get(k, 0)) for s in suites for k in ('skipped', 'failures', 'errors')):
        raise RuntimeError('H3 not released: every live contract check must pass, with zero skips')


def discover(url: str) -> None:
    payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {'_meta': {
        'io.modelcontextprotocol/protocolVersion': '2026-07-28',
        'io.modelcontextprotocol/clientCapabilities': {}}}}
    req = request.Request(url, data=json.dumps(payload).encode(), headers={
        'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
        'MCP-Protocol-Version': '2026-07-28', 'Mcp-Method': 'tools/list', 'Origin': 'http://localhost'})
    with request.urlopen(req, timeout=2) as response:
        data = json.loads(response.read())
    if 'tools' not in data.get('result', {}):
        raise RuntimeError('Connector discovery failed')


def verify_live_github(url: str) -> None:
    repo = os.environ.get('GITHUB_DEMO_REPO', '')
    if not repo:
        raise RuntimeError('GITHUB_DEMO_REPO missing')
    body = {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
        'name': 'pulls_read', 'arguments': {'repo': repo, 'number': int(os.environ.get('GITHUB_DEMO_PR', '1'))},
        '_meta': {'io.modelcontextprotocol/protocolVersion': '2026-07-28',
                  'io.modelcontextprotocol/clientCapabilities': {}}}}
    req = request.Request(url, data=json.dumps(body).encode(), headers={
        'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
        'MCP-Protocol-Version': '2026-07-28', 'Mcp-Method': 'tools/call', 'Mcp-Name': 'pulls_read',
        'Authorization': 'Bearer ' + os.environ['CONNECTOR_SHARED_SECRET']})
    try:
        with request.urlopen(req, timeout=60) as response:
            payload = json.loads(response.read())
        result = payload.get('result', {})
        if payload.get('error') or result.get('isError'):
            raise ValueError()
        data = result.get('structuredContent')
        if data is None:
            data = json.loads(next(c['text'] for c in result.get('content', []) if c.get('type') == 'text'))
        if not any(f.get('path') == 'demo_service.py' and '[REDACTED]' in f.get('diff', '') for f in data['files']):
            raise ValueError()
    except (ValueError, KeyError, StopIteration, TypeError, OSError):
        raise RuntimeError('Live MCP GitHub PR read/redaction failed; H3 not released') from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true', help='Install dependencies using the current Python')
    parser.add_argument('--release', action='store_true', help='Release H3 after live checks and a real PR read')
    args = parser.parse_args()
    if args.install:
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', str(ROOT / 'connectors' / 'requirements.txt')], check=True)
    from dotenv import load_dotenv, set_key
    from connectors.common import registry
    load_dotenv(ROOT / '.env')
    if not os.environ.get('CONNECTOR_SHARED_SECRET'):
        value = secrets.token_urlsafe(32)
        set_key(ROOT / '.env', 'CONNECTOR_SHARED_SECRET', value)
        os.environ['CONNECTOR_SHARED_SECRET'] = value
    children, logs = [], []
    logdir = ROOT / '.secrets' / 'connectors'
    logdir.mkdir(parents=True, exist_ok=True)
    try:
        for name in ('github', 'slack', 'memory'):
            url = registry(name)['transport']['url']
            try:
                discover(url)
            except (OSError, ValueError, RuntimeError):
                log = (logdir / f'{name}.log').open('w', encoding='utf-8')
                logs.append(log)
                child = subprocess.Popen([sys.executable, '-m', f'connectors.{name}.server'],
                    cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                children.append(child)
                deadline = time.monotonic() + 20
                while True:
                    if child.poll() is not None:
                        raise RuntimeError(f'{name} wrapper failed to start; inspect .secrets/connectors/{name}.log')
                    try:
                        discover(url)
                        break
                    except (OSError, ValueError, RuntimeError):
                        if time.monotonic() >= deadline:
                            raise RuntimeError(f'{name} wrapper did not become ready') from None
                        time.sleep(0.2)
            print(f'{name}: MCP ready at {url}', flush=True)
        # Verify the actual running server, its internal authentication, App
        # credentials and redaction. Discovery alone cannot establish H3.
        verify_live_github(registry('github')['transport']['url'])
        print('GitHub App: real demo PR read succeeded; credential fixture redacted.', flush=True)
        with tempfile.TemporaryDirectory(prefix='reagent-h3-') as tmp:
            report = Path(tmp) / 'h3.xml'
            subprocess.run([sys.executable, '-m', 'pytest', 'tests/contract/test_connector_actions.py', '-q',
                            f'--junitxml={report}'], cwd=ROOT, check=True)
            require_all_passed(report)
        print('H3: all seven live checks passed, zero skips.', flush=True)
        if args.release:
            subprocess.run([sys.executable, 'scripts/handoff.py', 'ready', 'H3', '--yes'], cwd=ROOT, check=True)
            print('H3 released. wf/runtime must run: python scripts/handoff.py wait H3', flush=True)
        print('Wrappers remain running. Press Ctrl-C to stop.', flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(1)
        raise RuntimeError('A wrapper exited unexpectedly')
    except KeyboardInterrupt:
        return 0
    except (RuntimeError, ValueError, subprocess.CalledProcessError, OSError) as exc:
        # Never print upstream exception strings or credential-bearing URLs.
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print('Stopped; H3 readiness must not be assumed:', message, flush=True)
        return 1
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for log in logs:
            log.close()


if __name__ == '__main__':
    raise SystemExit(main())
