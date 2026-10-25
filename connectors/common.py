"""Registry-driven SDK hosting and credential-safe connector boundaries."""
from __future__ import annotations

from contextvars import ContextVar
import hmac
import json
import os
from pathlib import Path
import re
from typing import Any, Callable
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
TRUSTED_HEADERS: ContextVar[dict[str, str]] = ContextVar('connector_headers', default={})
ALLOWED_ORIGINS = {'http://localhost', 'http://127.0.0.1', 'http://localhost:8000',
                   'http://localhost:3000', 'http://127.0.0.1:8000', 'http://127.0.0.1:3000'}


def registry(name: str) -> dict:
    return yaml.safe_load((ROOT / 'spec' / 'examples' / 'registry' / 'connectors' /
                           f'{name}.connector.yaml').read_text(encoding='utf-8'))['spec']


def action_map(name: str) -> list[dict]:
    return json.loads((ROOT / 'contracts' / 'connector_actions.json').read_text())['connectors'][name]['actions']


def validate_handlers(name: str, handlers: dict[str, Callable]) -> None:
    expected = {a['mcp_tool'] for a in action_map(name)}
    declared = {a['name'] for a in registry(name)['actions']}
    if set(handlers) != expected or declared != {a['action'] for a in action_map(name)}:
        raise ValueError(f'E_CONNECTOR_DRIFT: {name} handlers do not match frozen registry')


class Redactor:
    SENSITIVE_KEYS = {'password', 'apikey', 'secretkey', 'awssecretkey', 'awssecretaccesskey',
                      'accesstoken', 'refreshtoken', 'authorization', 'privatekey', 'token'}
    def __init__(self, secrets: list[str] | None = None):
        self.secrets = sorted({s for s in (secrets or []) if s}, key=len, reverse=True)

    def __call__(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {self(str(k)): '[REDACTED]' if re.sub(r'[^a-z]', '', str(k).lower()) in self.SENSITIVE_KEYS
                    else self(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self(v) for v in value]
        if not isinstance(value, str):
            return value
        if value.lstrip().startswith(('{', '[')):
            try:
                decoded = json.loads(value)
            except (ValueError, RecursionError):
                pass
            else:
                if isinstance(decoded, (dict, list)):
                    return json.dumps(self(decoded))
        for secret in self.secrets:
            value = value.replace(secret, '[REDACTED]')
        value = re.sub(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b', '[REDACTED]', value)
        value = re.sub(r'\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-or-v1-[A-Za-z0-9]+|xox[baprs]-[A-Za-z0-9-]+)\b', '[REDACTED]', value)
        value = re.sub(r'(\w+://[^\s:/]+:)[^\s@]+@', r'\1[REDACTED]@', value)
        value = re.sub(r'(?i)((?:aws_secret_access_key|aws_secret_key|password|api_key|secret_key)[\"\x27]?\s*[=:]\s*[\"\x27]?)[^\s\"\x27,]+', r'\1[REDACTED]', value)
        return value


def env_redactor(extra: list[str] | None = None) -> Redactor:
    return Redactor([v for k, v in os.environ.items()
                     if any(w in k for w in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY'))] + (extra or []))


class RequestGate:
    """Only authenticated runtime calls may invoke tools or supply identity.

    Discovery remains public on loopback for contract checks. Parse the body as
    well as the header, so a forged tools/list header cannot invoke tools/call.
    """
    def __init__(self, app, shared_secret: str):
        self.app, self.secret = app, shared_secret

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get('headers', [])}
        async def reject(status):
            await send({'type': 'http.response.start', 'status': status, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'Request denied'})
        if headers.get('origin') and headers['origin'] not in ALLOWED_ORIGINS:
            return await reject(403)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            size += len(message.get('body', b''))
            if size > 4 * 1024 * 1024:
                return await reject(413)
            chunks.append(message)
            if not message.get('more_body'):
                break
        try:
            body = json.loads(b''.join(m.get('body', b'') for m in chunks))
        except (ValueError, UnicodeDecodeError):
            body = {}
        discovery = isinstance(body, dict) and body.get('method', headers.get('mcp-method')) in {'tools/list', 'ping'}
        authorized = bool(self.secret) and hmac.compare_digest(headers.get('authorization', ''), 'Bearer ' + self.secret)
        if not discovery and not authorized:
            return await reject(401)
        # The frozen project discovery client predates the SDK's mandatory
        # clientCapabilities envelope. Treat omission on discovery as an empty
        # capability set; this grants nothing and leaves tool calls untouched.
        if discovery and isinstance(body, dict):
            meta = body.get('params', {}).get('_meta', {}) if isinstance(body.get('params'), dict) else {}
            if isinstance(meta, dict) and meta.get('io.modelcontextprotocol/protocolVersion') == '2026-07-28' and 'io.modelcontextprotocol/clientCapabilities' not in meta:
                meta['io.modelcontextprotocol/clientCapabilities'] = {}
                encoded = json.dumps(body).encode()
                chunks = [{'type': 'http.request', 'body': encoded, 'more_body': False}]
                scope = {**scope, 'headers': [(k, v) for k, v in scope.get('headers', []) if k.lower() != b'content-length'] + [(b'content-length', str(len(encoded)).encode())]}
        token = TRUSTED_HEADERS.set(headers if authorized else {})
        async def replay():
            if chunks:
                return chunks.pop(0)
            return await receive()
        try:
            await self.app(scope, replay, send)
        finally:
            TRUSTED_HEADERS.reset(token)


def build_app(name: str, handlers: dict[str, Callable], shared_secret: str | None = None):
    from mcp.server import MCPServer
    from mcp.server.transport_security import TransportSecuritySettings
    validate_handlers(name, handlers)
    server = MCPServer(f'{name}-connector')
    for tool, handler in handlers.items():
        server.tool(name=tool)(handler)
    url = urlsplit(registry(name)['transport']['url'])
    if url.hostname not in {'localhost', '127.0.0.1'}:
        raise ValueError('Connector must bind to loopback')
    security = TransportSecuritySettings(
        allowed_hosts=['localhost', '127.0.0.1', f'localhost:{url.port}', f'127.0.0.1:{url.port}'],
        allowed_origins=sorted(ALLOWED_ORIGINS))
    return RequestGate(server.streamable_http_app(streamable_http_path=url.path, json_response=True, transport_security=security),
                       shared_secret if shared_secret is not None else os.environ.get('CONNECTOR_SHARED_SECRET', ''))


def serve(name: str, handlers: dict[str, Callable]) -> None:
    from dotenv import load_dotenv
    import uvicorn
    load_dotenv(ROOT / '.env')
    app = build_app(name, handlers)
    url = urlsplit(registry(name)['transport']['url'])
    uvicorn.run(app, host='127.0.0.1', port=url.port, access_log=False)
