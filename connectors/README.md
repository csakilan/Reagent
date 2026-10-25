# WF4 connector handoff

Run from the repository root in a normal terminal:

```text
.venv/bin/python connectors/launch.py --install --release
```

On Windows use `.venv\Scripts\python.exe` in place of `.venv/bin/python`.
The command starts all three wrappers on registry ports, reads the real demo PR
using the App installation, checks that its synthetic credential is redacted,
runs the seven H3 contract checks, refuses skipped tests, and releases H3 only
after success. Keep the terminal open: Ctrl-C stops the wrappers it launched.
Slack discovery works without a token; Slack actions fail closed until
`SLACK_BOT_TOKEN` is supplied. No Slack messages are sent during verification.

## Runtime integration

MCP tools/list is readable on loopback. For tools/call the runtime must supply
`Authorization: Bearer <CONNECTOR_SHARED_SECRET>` as an HTTP header, loaded from
the shared `.env`; never expose this secret as a tool argument or in a prompt.
The approval/grant middleware remains mandatory; wrapper authentication is not
an approval. The shared credential trusts the runtime, not model-written
identity fields. The runtime alone sets these HTTP headers per root run:

| Header | Meaning |
|---|---|
| `X-Reagent-Auth-Mode` | `service` (default) or `on_behalf_of` |
| `X-Reagent-User-Token` | Root user's upstream token for on_behalf_of; no fallback to service |
| `X-Reagent-User-ID` | Root session principal, required for memory and user auth |
| `X-Reagent-Team-ID` | Root run's team, required for memory |
| `X-Reagent-Run-ID` | Source run for memory writes |
| `X-Reagent-Invocation` | `system` only for runtime-generated episodic writes |

The memory wrapper checks membership and admin status in Postgres rather than
trusting caller-supplied admin claims. Procedural writes remain pending until
the runtime/eval workflow reevaluates them. Search accepts an optional 1536-value
embedding; without one it uses Postgres full text. No embedding API is called.

GitHub uses `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, and
`GITHUB_APP_PRIVATE_KEY_PATH` for automatic hourly installation-token renewal.
`GITHUB_APP_TOKEN` is an optional static-token override. The private key stays
on the wrapper host; runtime callers need only the internal shared secret.
Repository scope defaults to `GITHUB_DEMO_REPO`; an explicit comma-separated
`GITHUB_ALLOWED_REPOS` can configure additional installed repositories.

## Demo coordination

- Repository: `csakilan/reagent-demo`, pull request #1.
- Synthetic exact-match redaction fixture: `AKIAIOSFODNN7EXAMPLE`.
- WF1 must include that value in the redaction exact-match list.
- Existing examples/policies point at `nmc2/infra` and `nmc2/platform-api`.
  WF2 must resolve the actual demo repository through enterprise, team and agent
  layers before the demo; no runtime grant bypass is appropriate.
- No webhook receiver exists in the frozen API. Webhooks remain inactive;
  launch the demo run through HTTP/MCP. A public tunnel alone is insufficient.

## Verification

```text
python -m pytest connectors/tests -q
python -m pytest tests/contract/test_connector_actions.py -q
```

The first suite exercises handlers and the real SDK in process; it is not a
live H3 release. The second requires running wrappers. The launch command also
verifies live GitHub access before releasing H3.
