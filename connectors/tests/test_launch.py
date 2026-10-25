from pathlib import Path
import pytest


def test_h3_refuses_skipped_or_failed_checks(tmp_path):
    from connectors import launch
    for counts in ['tests="7" skipped="1" failures="0" errors="0"',
                   'tests="7" skipped="0" failures="1" errors="0"',
                   'tests="0" skipped="0" failures="0" errors="0"']:
        report = tmp_path / 'checks.xml'
        report.write_text(f'<testsuites><testsuite {counts}/></testsuites>')
        with pytest.raises(RuntimeError, match='H3'):
            launch.require_all_passed(report)


def test_h3_accepts_seven_real_passes(tmp_path):
    from connectors import launch
    report = tmp_path / 'checks.xml'
    report.write_text('<testsuites><testsuite tests="7" skipped="0" failures="0" errors="0"/></testsuites>')
    launch.require_all_passed(report)


def test_live_gate_calls_running_mcp_not_a_separate_client(monkeypatch):
    import io
    import json
    from connectors import launch
    monkeypatch.setenv('CONNECTOR_SHARED_SECRET', 'test-runtime-key')
    monkeypatch.setenv('GITHUB_DEMO_REPO', 'csakilan/reagent-demo')
    seen = []
    def response(req, timeout):
        seen.append(req)
        return io.BytesIO(json.dumps({'result': {'content': [{'type': 'text', 'text': json.dumps({
            'files': [{'path': 'demo_service.py', 'diff': '+key=[REDACTED]'}]})}], 'isError': False}}).encode())
    monkeypatch.setattr(launch.request, 'urlopen', response)
    assert hasattr(launch, 'verify_live_github'), 'Missing live MCP call gate'
    launch.verify_live_github('http://localhost:7102/mcp')
    assert seen[0].get_header('Authorization') == 'Bearer test-runtime-key'
    assert json.loads(seen[0].data)['method'] == 'tools/call'
    assert json.loads(seen[0].data)['params']['name'] == 'pulls_read'
