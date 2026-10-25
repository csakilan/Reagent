"""Short-lived GitHub App installation tokens. Never uses a user's CLI token."""
from __future__ import annotations

import base64
from datetime import datetime
import json
import os
from pathlib import Path
import threading
import time
from typing import Callable

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
import httpx

from connectors.common import ROOT, TRUSTED_HEADERS


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


class AppToken:
    def __init__(self, app_id: str, installation_id: str, key_path: Path,
                 client: httpx.Client | None = None, clock: Callable = time.time):
        if not app_id.isdigit() or not installation_id.isdigit():
            raise ValueError('GitHub App and installation IDs must be numeric')
        self.app_id, self.installation_id = app_id, installation_id
        self.key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        self.client = client or httpx.Client(timeout=20, follow_redirects=False)
        self.clock, self.token, self.expires = clock, '', 0.0
        self.lock = threading.Lock()

    def __call__(self) -> str:
        with self.lock:
            now = self.clock()
            if self.token and now < self.expires - 60:
                return self.token
            header = b64(b'{"alg":"RS256","typ":"JWT"}')
            claims = b64(json.dumps({'iat': int(now) - 60, 'exp': int(now) + 540,
                                     'iss': self.app_id}).encode())
            signing = f'{header}.{claims}'.encode()
            signature = self.key.sign(signing, padding.PKCS1v15(), hashes.SHA256())
            jwt = f'{header}.{claims}.{b64(signature)}'
            try:
                result = self.client.post(f'https://api.github.com/app/installations/{self.installation_id}/access_tokens',
                    headers={'Authorization': f'Bearer {jwt}', 'Accept': 'application/vnd.github+json'}, json={})
            except httpx.HTTPError:
                raise RuntimeError('GitHub installation authentication unavailable') from None
            if result.status_code != 201:
                raise RuntimeError(f'GitHub installation authentication failed (HTTP {result.status_code})')
            try:
                data = result.json()
                token = data['token']
                expires = datetime.fromisoformat(data['expires_at'].replace('Z', '+00:00')).timestamp()
                if not isinstance(token, str) or not token or expires <= now + 60:
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                raise RuntimeError('GitHub returned an invalid installation token') from None
            self.token, self.expires = token, expires
            return self.token


def configured_provider() -> Callable[[], str]:
    provider = None
    def token() -> str:
        nonlocal provider
        mode = TRUSTED_HEADERS.get().get('x-reagent-auth-mode', 'service')
        if mode == 'on_behalf_of':
            # Runtime forwards the root user's token in a trusted HTTP header,
            # never in tool arguments or the model's context.
            value = TRUSTED_HEADERS.get().get('x-reagent-user-token')
            if not value or not TRUSTED_HEADERS.get().get('x-reagent-user-id'):
                raise ValueError('E_ON_BEHALF_OF_NO_USER')
            return value
        if mode != 'service':
            raise ValueError('E_AUTH_MODE')
        if os.environ.get('GITHUB_APP_TOKEN'):
            return os.environ['GITHUB_APP_TOKEN']
        if provider is None:
            path = Path(os.environ.get('GITHUB_APP_PRIVATE_KEY_PATH', '.secrets/github-app.pem'))
            provider = AppToken(os.environ.get('GITHUB_APP_ID', ''),
                                os.environ.get('GITHUB_APP_INSTALLATION_ID', ''),
                                path if path.is_absolute() else ROOT / path)
        return provider()
    return token
