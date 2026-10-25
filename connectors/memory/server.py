from connectors.common import action_map, serve
from connectors.memory.repository import Memory


def handlers() -> dict:
    memory = Memory()
    actions = {'read': memory.read, 'search': memory.search, 'write': memory.write}
    return {a['mcp_tool']: actions[a['action']] for a in action_map('memory')}


if __name__ == '__main__':
    serve('memory', handlers())
