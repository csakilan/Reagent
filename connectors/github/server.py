from connectors.common import action_map, serve
from connectors.github.client import GitHub


def handlers() -> dict:
    client = GitHub()
    # The contract is the name mapping; no dot-to-underscore transform.
    actions = {'pulls.read': client.pulls_read, 'pulls.comment': client.pulls_comment,
               'pulls.merge': client.pulls_merge, 'contents.read': client.contents_read,
               'contents.write': client.contents_write, 'issues.read': client.issues_read,
               'issues.create': client.issues_create, 'branches.force_push': client.branches_force_push,
               'repos.delete': client.repos_delete}
    return {a['mcp_tool']: actions[a['action']] for a in action_map('github')}


if __name__ == '__main__':
    serve('github', handlers())
