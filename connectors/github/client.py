"""Narrow GitHub REST wrapper; runtime still enforces grants and approvals."""
from __future__ import annotations

import base64
import os
import re
from typing import Callable
from urllib.parse import quote
import httpx

from connectors.common import env_redactor
from connectors.github.auth import configured_provider


class GitHub:
    def __init__(self, client: httpx.Client | None = None, token_provider: Callable | None = None,
                 allowed_repos: set[str] | None = None):
        self.client = client or httpx.Client(timeout=20, follow_redirects=False)
        self.tokens = token_provider or configured_provider()
        self.allowed_repos = allowed_repos

    def repo(self, repo: str) -> str:
        allowed = self.allowed_repos
        if allowed is None:
            allowed = {s.strip() for s in os.environ.get('GITHUB_ALLOWED_REPOS', os.environ.get('GITHUB_DEMO_REPO', '')).split(',') if s.strip()}
        if not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', repo) or repo.split('/')[-1] in {'.', '..'} or repo not in allowed:
            raise ValueError('E_RESOURCE_DENIED: repository is outside wrapper scope')
        return f'/repos/{repo}'

    def request(self, method: str, path: str, **kwargs):
        token = self.tokens()
        try:
            r = self.client.request(method, 'https://api.github.com' + path,
                headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json'}, **kwargs)
        except httpx.HTTPError:
            raise RuntimeError('GitHub connector unavailable') from None
        if not 200 <= r.status_code < 300:
            raise RuntimeError(f'GitHub connector HTTP {r.status_code}')
        if r.status_code == 204:
            return {'ok': True}
        try:
            return env_redactor([token])(r.json())
        except ValueError:
            raise RuntimeError('GitHub returned invalid JSON') from None

    @staticmethod
    def number(number: int) -> int:
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise ValueError('Invalid issue or pull request number')
        return number

    def pulls_read(self, repo: str, number: int) -> dict:
        """Read a pull request with redacted file patches."""
        path = f'{self.repo(repo)}/pulls/{self.number(number)}'
        result = self.request('GET', path)
        files = []
        for page in range(1, 31):  # GitHub caps PR files at 3,000.
            batch = self.request('GET', path + '/files', params={'per_page': 100, 'page': page})
            files.extend({'path': f['filename'], 'diff': f.get('patch', ''), 'status': f.get('status')} for f in batch)
            if len(batch) < 100:
                break
        return {**result, 'files': files}

    def pulls_comment(self, repo: str, number: int, body: str) -> dict:
        """Post a PR conversation comment after runtime approval."""
        return self.request('POST', f'{self.repo(repo)}/issues/{self.number(number)}/comments', json={'body': env_redactor()(body)})

    def pulls_merge(self, repo: str, number: int, sha: str) -> dict:
        """Merge a PR with an expected head SHA, subject to runtime policy."""
        return self.request('PUT', f'{self.repo(repo)}/pulls/{self.number(number)}/merge', json={'sha': sha})

    def contents_read(self, repo: str, path: str, ref: str = 'HEAD') -> dict:
        """Read a repository file at a ref and redact decoded text."""
        result = self.request('GET', self.repo(repo) + '/contents/' + self.file_path(path), params={'ref': ref})
        if isinstance(result, dict) and result.get('encoding') == 'base64':
            try:
                result['content'] = env_redactor([self.tokens()])(base64.b64decode(result['content']).decode('utf-8'))
                result['encoding'] = 'utf-8'
            except (ValueError, UnicodeDecodeError):
                return {'path': path, 'binary': True}
        return result

    @staticmethod
    def file_path(path: str) -> str:
        if not path or path.startswith('/') or any(p in {'', '.', '..'} for p in path.split('/')):
            raise ValueError('E_RESOURCE_DENIED: invalid file path')
        return quote(path, safe='/')

    def contents_write(self, repo: str, path: str, content: str, message: str, branch: str, sha: str = '') -> dict:
        """Create or update a file on an explicit branch."""
        payload = {'message': message, 'content': base64.b64encode(content.encode()).decode(), 'branch': branch}
        if sha:
            payload['sha'] = sha
        return self.request('PUT', self.repo(repo) + '/contents/' + self.file_path(path), json=payload)

    def issues_read(self, repo: str, number: int) -> dict:
        """Read an issue."""
        return self.request('GET', f'{self.repo(repo)}/issues/{self.number(number)}')

    def issues_create(self, repo: str, title: str, body: str = '') -> dict:
        """Create an issue after runtime approval."""
        return self.request('POST', self.repo(repo) + '/issues', json={'title': title, 'body': env_redactor()(body)})

    def branches_force_push(self, repo: str, branch: str, sha: str) -> dict:
        """Move an existing branch ref; runtime must authorize destructive risk."""
        return self.request('PATCH', self.repo(repo) + '/git/refs/heads/' + quote(branch, safe=''), json={'sha': sha, 'force': True})

    def repos_delete(self, repo: str) -> dict:
        """Delete a repository only if upstream credentials and runtime permit it."""
        return self.request('DELETE', self.repo(repo))
