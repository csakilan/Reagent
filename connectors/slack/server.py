from connectors.common import action_map, serve
from connectors.slack.client import Slack


def handlers() -> dict:
    client = Slack()
    actions = {'channels.list': client.channels_list, 'channels.archive': client.channels_archive,
               'messages.read': client.messages_read, 'messages.search': client.messages_search,
               'messages.post': client.messages_post, 'reactions.add': client.reactions_add,
               'users.read': client.users_read, 'dm.read': client.dm_read, 'dm.send': client.dm_send}
    return {a['mcp_tool']: actions[a['action']] for a in action_map('slack')}


if __name__ == '__main__':
    serve('slack', handlers())
