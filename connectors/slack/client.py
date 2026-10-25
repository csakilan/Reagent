"""Fixed Slack Web API surface with wrapper-only credentials."""
from __future__ import annotations
import os
import re
import httpx
from connectors.common import TRUSTED_HEADERS, env_redactor


class Slack:
    def __init__(self, token: str = '', client: httpx.Client | None = None):
        self.token = token
        self.client = client or httpx.Client(timeout=20, follow_redirects=False)

    def request(self, method: str, payload: dict) -> dict:
        headers = TRUSTED_HEADERS.get()
        mode = headers.get('x-reagent-auth-mode', 'service')
        if mode == 'on_behalf_of':
            token = headers.get('x-reagent-user-token', '')
            if not token or not headers.get('x-reagent-user-id'):
                raise ValueError('E_ON_BEHALF_OF_NO_USER')
        elif mode == 'service':
            token = self.token or os.environ.get('SLACK_BOT_TOKEN', '')
        else:
            raise ValueError('E_AUTH_MODE')
        if not token:
            raise RuntimeError('Slack service credential is not configured')
        try:
            r = self.client.post('https://slack.com/api/' + method,
                headers={'Authorization': 'Bearer ' + token}, json=env_redactor([token])(payload))
        except httpx.HTTPError:
            raise RuntimeError('Slack connector unavailable') from None
        if r.status_code != 200:
            raise RuntimeError(f'Slack connector HTTP {r.status_code}')
        try:
            result = r.json()
        except ValueError:
            raise RuntimeError('Slack returned invalid JSON') from None
        if not result.get('ok'):
            raise RuntimeError('Slack rejected the action')
        return env_redactor([token])(result)

    def channels_list(self, cursor: str = '') -> dict:
        """List accessible channels; use next_cursor to paginate."""
        return self.request('conversations.list', {'cursor': cursor, 'limit': 100, 'types': 'public_channel,private_channel'})

    def channels_archive(self, channel: str) -> dict:
        """Archive a channel after runtime authorization."""
        return self.request('conversations.archive', {'channel': channel})

    def messages_read(self, channel: str, cursor: str = '') -> dict:
        """Read channel history."""
        return self.request('conversations.history', {'channel': channel, 'limit': 100, 'cursor': cursor})

    def messages_search(self, channel: str, query: str) -> dict:
        """Search only within one channel."""
        # Filter returned matches as well: Slack's query grammar must not allow
        # an injected in: selector or OR expression to widen the channel scope.
        result = self.request('search.messages', {'query': f'in:{channel} {query}', 'count': 100})
        messages = result.get('messages', {})
        messages['matches'] = [m for m in messages.get('matches', [])
            if channel in {m.get('channel', {}).get('id'), '#' + m.get('channel', {}).get('name', '')}]
        result['messages'] = messages
        return result

    def messages_post(self, channel: str, text: str) -> dict:
        """Post a message after runtime approval."""
        return self.request('chat.postMessage', {'channel': channel, 'text': text})

    def reactions_add(self, channel: str, timestamp: str, name: str) -> dict:
        """Add a reaction."""
        return self.request('reactions.add', {'channel': channel, 'timestamp': timestamp, 'name': name})

    def users_read(self, user: str) -> dict:
        """Read a Slack user profile."""
        return self.request('users.info', {'user': user})

    def dm_read(self, channel: str, cursor: str = '') -> dict:
        """Read a DM conversation."""
        if not re.fullmatch(r'D[A-Z0-9]+', channel):
            raise ValueError('E_RESOURCE_DENIED: dm.read requires a direct conversation')
        info = self.request('conversations.info', {'channel': channel})
        if not info.get('channel', {}).get('is_im'):
            raise ValueError('E_RESOURCE_DENIED: conversation is not a direct message')
        return self.request('conversations.history', {'channel': channel, 'limit': 100, 'cursor': cursor})

    def dm_send(self, user: str, text: str) -> dict:
        """Open a DM and send a message after runtime approval."""
        channel = self.request('conversations.open', {'users': user})['channel']['id']
        return self.messages_post(channel, text)
