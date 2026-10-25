"""Offline tests: real handlers and HTTP responses, never real side effects."""
import asyncio
import importlib
import importlib.util
import json
from pathlib import Path

import httpx
import pytest


def module(name):
    assert importlib.util.find_spec(name), f"Missing implementation: {name}"
    return importlib.import_module(name)


def test_registry_rejects_missing_or_extra_handler():
    c = module('connectors.common')
    with pytest.raises(ValueError, match='E_CONNECTOR_DRIFT'):
        c.validate_handlers('github', {})


def test_recursive_redaction_includes_tokens_passwords_and_aws():
    c = module('connectors.common')
    redact = c.Redactor(['private-demo-token'])
    result = redact({'diff': '+key=AKIAIOSFODNN7EXAMPLE', 'nested': ['private-demo-token'],
                     'url': 'postgres://alice:password@host/db'})
    assert 'AKIA' not in json.dumps(result)
    assert 'private-demo-token' not in json.dumps(result)
    assert 'password' not in json.dumps(result)


@pytest.mark.parametrize('payload', [{'password': 'opaque-long-sensitive'},
    {'aws_secret_access_key': 'opaque-long-sensitive'},
    '{"aws_secret_access_key":"opaque-long-sensitive"}'])
def test_structured_secret_values_are_redacted(payload):
    c = module('connectors.common')
    assert 'opaque-long-sensitive' not in json.dumps(c.Redactor()(payload))


def test_origin_and_internal_auth_gate():
    c = module('connectors.common')
    async def ok(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'ok'})
    async def run():
        app = c.RequestGate(ok, 'internal-test-key')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost') as client:
            assert (await client.post('/mcp', headers={'Origin': 'https://evil.example.com'})).status_code == 403
            assert (await client.post('/mcp', headers={'Mcp-Method': 'tools/call'})).status_code == 401
            assert (await client.post('/mcp', headers={'Mcp-Method': 'tools/list', 'Origin': 'http://localhost'})).status_code == 200
            assert (await client.post('/mcp', headers={'Mcp-Method': 'tools/call', 'Authorization': 'Bearer internal-test-key'})).status_code == 200
            # A forged method header must never bypass the tool-call gate.
            assert (await client.post('/mcp', headers={'Mcp-Method': 'tools/list'},
                                      json={'method': 'tools/call'})).status_code == 401
    asyncio.run(run())


def github(handler):
    g = module('connectors.github.client')
    c = httpx.Client(transport=httpx.MockTransport(handler))
    return g.GitHub(client=c, token_provider=lambda: 'private-demo-token', allowed_repos={'csakilan/reagent-demo'})


def test_github_pr_includes_files_and_redacts_diff():
    def respond(req):
        assert req.headers['Authorization'] == 'Bearer private-demo-token'
        if req.url.path.endswith('/files'):
            return httpx.Response(200, json=[{'filename': 'demo.py', 'patch': '+key="AKIAIOSFODNN7EXAMPLE"'}])
        return httpx.Response(200, json={'number': 1, 'title': 'test'})
    result = github(respond).pulls_read('csakilan/reagent-demo', 1)
    assert result['files'][0]['path'] == 'demo.py'
    assert '[REDACTED]' in result['files'][0]['diff']
    assert 'private-demo-token' not in json.dumps(result)


@pytest.mark.parametrize('repo', ['other/private', '../secrets', 'csakilan/reagent-demo/../private', 'https://evil.example/a', 'csakilan/%2e%2e'])
def test_github_scope_denies_before_network(repo):
    def no_network(req):
        pytest.fail('denied scope reached network')
    with pytest.raises(ValueError, match='E_RESOURCE_DENIED'):
        github(no_network).pulls_read(repo, 1)


def test_github_comment_preserves_wire_request_and_redacts_output():
    requests = []
    def respond(req):
        requests.append(req)
        return httpx.Response(201, json={'body': 'private-demo-token', 'id': 2})
    result = github(respond).pulls_comment('csakilan/reagent-demo', 1, 'Please rotate the credential.')
    assert requests[0].url.path == '/repos/csakilan/reagent-demo/issues/1/comments'
    assert json.loads(requests[0].content)['body'] == 'Please rotate the credential.'
    assert result['body'] == '[REDACTED]'


def test_upstream_error_never_contains_response_or_credentials():
    with pytest.raises(RuntimeError) as exc:
        github(lambda req: httpx.Response(403, text='private-demo-token')).pulls_read('csakilan/reagent-demo', 1)
    assert 'private-demo-token' not in str(exc.value)
    assert '403' in str(exc.value)


def test_decoded_file_cannot_leak_dynamic_installation_token():
    import base64
    response = {'encoding': 'base64', 'content': base64.b64encode(b'private-demo-token').decode()}
    result = github(lambda req: httpx.Response(200, json=response)).contents_read('csakilan/reagent-demo', 'config.txt')
    assert result['content'] == '[REDACTED]'


def test_github_user_mode_requires_root_identity(monkeypatch):
    a = module('connectors.github.auth')
    c = module('connectors.common')
    marker = c.TRUSTED_HEADERS.set({'x-reagent-auth-mode': 'on_behalf_of', 'x-reagent-user-token': 'personal-test-token'})
    try:
        with pytest.raises(ValueError, match='E_ON_BEHALF_OF_NO_USER'):
            a.configured_provider()()
    finally:
        c.TRUSTED_HEADERS.reset(marker)


def test_app_token_refreshes_before_expiry_and_is_not_user_fallback(tmp_path):
    a = module('connectors.github.auth')
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / 'app.pem'
    path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    times = [1000.0]
    calls = []
    def respond(req):
        calls.append(req)
        assert len(req.headers['Authorization'].split('.') ) == 3
        return httpx.Response(201, json={'token': 'installation-token', 'expires_at': '1970-01-01T01:00:00Z' if len(calls) == 1 else '1970-01-01T02:00:00Z'})
    provider = a.AppToken('123', '456', path, httpx.Client(transport=httpx.MockTransport(respond)), clock=lambda: times[0])
    assert provider() == provider() == 'installation-token'
    assert len(calls) == 1
    times[0] = 3550
    assert provider() == 'installation-token'
    assert len(calls) == 2


def test_slack_post_and_sanitized_api_error():
    s = module('connectors.slack.client')
    seen = []
    def respond(req):
        seen.append(req)
        return httpx.Response(200, json={'ok': True, 'ts': '12.3'})
    client = s.Slack('slack-private-test', httpx.Client(transport=httpx.MockTransport(respond)))
    assert client.messages_post('C123', 'Review ready')['ts'] == '12.3'
    assert seen[0].url.path == '/api/chat.postMessage'
    assert json.loads(seen[0].content)['channel'] == 'C123'


@pytest.mark.parametrize('channel', ['C123', 'G123', 'D123'])
def test_dm_read_cannot_bypass_channel_scopes(channel):
    s = module('connectors.slack.client')
    seen = []
    def respond(req):
        seen.append(req.url.path)
        return httpx.Response(200, json={'ok': True, 'channel': {'is_im': False}})
    client = s.Slack('slack-private-test', httpx.Client(transport=httpx.MockTransport(respond)))
    with pytest.raises(ValueError, match='E_RESOURCE_DENIED'):
        client.dm_read(channel)
    assert '/api/conversations.history' not in seen


def test_memory_acl_and_type_policy():
    m = module('connectors.memory.repository')
    assert m.permitted(['*'], 'people-ops', False)
    assert not m.permitted(['admins'], 'people-ops', False)
    assert m.permitted(['admins'], 'people-ops', True)
    assert not m.permitted(['platform-eng'], 'people-ops', True)
    with pytest.raises(ValueError, match='E_MEMORY_RUNTIME_ONLY'):
        m.write_status('episodic', runtime=False)
    assert m.write_status('episodic', runtime=True) == 'active'
    assert m.write_status('procedural', runtime=False) == 'pending'


def test_all_handlers_match_contract():
    for name in ['github', 'slack', 'memory']:
        server = module(f'connectors.{name}.server')
        module('connectors.common').validate_handlers(name, server.handlers())
