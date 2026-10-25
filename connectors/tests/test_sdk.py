"""Real MCP SDK integration, in process (separate from live H3 checks)."""
import importlib
import json
import pytest
from starlette.testclient import TestClient

from connectors import common


@pytest.mark.parametrize('name', ['github', 'slack', 'memory'])
def test_sdk_discovery_and_origin_validation(name):
    handlers = importlib.import_module(f'connectors.{name}.server').handlers()
    assert hasattr(common, 'build_app'), 'Missing testable SDK app factory'
    app = common.build_app(name, handlers, shared_secret='test-runtime-key')
    with TestClient(app, base_url='http://localhost') as client:
        body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list',
                'params': {'_meta': {'io.modelcontextprotocol/protocolVersion': '2026-07-28'}}}
        headers = {'MCP-Protocol-Version': '2026-07-28', 'Mcp-Method': 'tools/list',
                   'Accept': 'application/json, text/event-stream', 'Origin': 'http://localhost'}
        r = client.post('/mcp', json=body, headers=headers)
        assert r.status_code == 200, r.text
        names = {t['name'] for t in r.json()['result']['tools']}
        assert names == {a['mcp_tool'] for a in common.action_map(name)}
        assert client.post('/mcp', json=body, headers={**headers, 'Origin': 'https://evil.example.com'}).status_code == 403
        call = {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': next(iter(names)), 'arguments': {}}}
        assert client.post('/mcp', json=call, headers=headers).status_code == 401


def test_authenticated_sdk_call_reaches_github_and_redacts():
    import httpx
    from connectors.github.client import GitHub
    from connectors.github.server import handlers
    def upstream(req):
        if req.url.path.endswith('/files'):
            return httpx.Response(200, json=[{'filename': 'demo.py', 'patch': '+key=AKIAIOSFODNN7EXAMPLE'}])
        return httpx.Response(200, json={'number': 1})
    github = GitHub(httpx.Client(transport=httpx.MockTransport(upstream)), lambda: 'dynamic-private', {'csakilan/reagent-demo'})
    tools = handlers()
    tools['pulls_read'] = github.pulls_read
    app = common.build_app('github', tools, shared_secret='test-runtime-key')
    with TestClient(app, base_url='http://localhost') as client:
        r = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {
            'name': 'pulls_read', 'arguments': {'repo': 'csakilan/reagent-demo', 'number': 1},
            '_meta': {'io.modelcontextprotocol/protocolVersion': '2026-07-28',
                      'io.modelcontextprotocol/clientCapabilities': {}}}}, headers={
            'MCP-Protocol-Version': '2026-07-28', 'Mcp-Method': 'tools/call', 'Mcp-Name': 'pulls_read',
            'Accept': 'application/json, text/event-stream', 'Authorization': 'Bearer test-runtime-key'})
        assert r.status_code == 200, r.text
        payload = r.json()
        assert not payload.get('error'), payload
        result = payload['result']
        assert not result.get('isError'), result
        assert '[REDACTED]' in json.dumps(result)
        assert 'AKIA' not in json.dumps(result)
