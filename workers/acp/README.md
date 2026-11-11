# ACP Worker Profiles

Run one worker process and persistent worker root per user, project, and agent trust boundary. The worker is a private execution service; an OpenShell service URL alone does not authenticate application requests. Every job and control request requires the central Ed25519 delegation in `X-AOG-Context`, bound to its invocation, owner, project, and audience.

## Jobs and Native Controls

- `POST /jobs`: existing invocation body plus `Idempotency-Key`; returns a durable job handle immediately.
- `GET /jobs/{job_id}`: status, negotiated capabilities, native session reference, pending permissions, and terminal result.
- `POST /jobs/{job_id}/permissions/{permission_id}` with `{"option_id":"native-option-id"}`: resolves the original live ACP permission request. Only options supplied by that harness are accepted.
- `POST /jobs/{job_id}/cancel`: sends ACP `session/cancel`. Status stays `cancelling` until the native prompt response confirms its stop reason.
- `POST /jobs/{job_id}/resolve`: requires an operator claim in the signed delegation, `outcome` (`completed` or `failed`), `output`, and `external_execution_stopped: true`. This records the operator's external verification; it does not independently prove that external effects stopped.
- `POST /invoke`: compatible blocking call over the same persisted job.

All controls require a fresh signed context for the same invocation. A disconnected browser does not cancel a job. After a worker process restart, unfinished jobs become `needs_attention` and are not replayed. Native approval futures cannot be restored across that process loss. Uncertain jobs block subsequent turns in their session until an operator resolves them. A new explicitly requested invocation can resume a supported, reconciled native session. Session turns are serialized in the worker. Use `--workers 1`; multiple worker processes sharing this SQLite root are not supported.

`AOG_AGENT_APPROVAL_POLICY=interactive` is the default: the task waits for its owner to select a native option. `deny` rejects permission requests. `container` automatically selects `allow_once` when available and is only for a container boundary the operator explicitly authorizes.

## Provider Connections

The optional `provider_connection` contains `protocol`, `base_url`, and `model`; it never contains a secret. The central service sends the selected provider credential separately through its private authenticated worker request. Codex explicitly requires `responses` and uses a worker-owned native provider configuration. Claude requires `messages` and uses native base URL plus auth token settings. Schema-1 Codex/Claude official API-key behavior remains available when no provider profile is supplied.

OpenCode also supports an explicit model/base URL using the matching native provider package; the model and provider must actually support that protocol. Chat Completions, Responses, and Messages are different protocols. The worker does not translate them. A model provider must implement the selected protocol and its tool/streaming requirements.

## Native Login Profiles

Set `AOG_NATIVE_LOGIN_PROFILES` to an operator-mounted JSON file using [the example](native-profiles.example.json). Each opaque reference identifies an existing mounted home and must match the worker owner, project, and harness. A request cannot supply a filesystem path or combine a native login with a provider API key.

`AOG_AGENT_PROVIDER` chooses the installed template: `codex`, `claude`, `hermes`, `kimi`, or `opencode`. Kimi and OpenCode launch their native `acp` command; their binaries are not installed by the default Codex/Claude worker image. Kimi requires a provisioned native profile. OpenCode accepts either a native profile or an explicit ProviderConnection and model ID. Its v1.19.5 template selects `@ai-sdk/openai-compatible`, `@ai-sdk/openai`, or `@ai-sdk/anthropic` according to the declared protocol. This is native configuration, not protocol translation. Configure Hermes/Kimi providers in their own profiles. Native login/token refresh stays with the harness. Persistent volumes containing those profiles are private credentials and must not be committed.

## OpenShell Authentication

[The SDK authentication example](openshell-auth.example.json) uses renewable client credentials. Configure native OpenShell roles and workspace membership for this service principal. It is a central operator identity, not the requesting user's delegated identity. A shared local mTLS certificate with auth-only roles is not user isolation. The SDK also accepts an operator-specified `bearer_token_env` callback for externally rotated access tokens; do not combine it with `client_credentials`.

## Relay

Hermes native Relay observation remains opt-in via `AOG_RELAY_ENABLED=1`. Local ATOF configuration suppresses full payloads and the central response contains only category counts. Correlation records name the job and native session but explicitly do not attest a trace link. This profile does not implement Relay policy enforcement or central-disconnection egress revocation. Relay middleware enforcement requires a separately tested native integration and network policy; it is not enabled by an observation flag.

## Required Worker Settings

| Variable | Meaning |
|---|---|
| AOG_WORKER_OWNER | Exact central user email |
| AOG_WORKER_PROJECT | Central/native team ID, or empty for a private service |
| AOG_WORKER_AUDIENCE | Exact registered worker invocation URL |
| AOG_WORKER_PUBLIC_KEY | Central Ed25519 public key in base64; no central signing secret |
| AOG_WORKER_ROOT | Persistent private worker root for jobs, sessions, files and telemetry |
| AOG_AGENT_PROVIDER | Installed harness template |
| AOG_NATIVE_LOGIN_PROFILES | Operator-mounted opaque-profile mapping, when native login is used |
| AOG_WORKER_EGRESS | Actual verified network policy: isolated or unrestricted |
| AOG_AGENT_APPROVAL_POLICY | interactive (default), deny, or explicitly authorized container |
| AOG_AGENT_TIMEOUT | Native prompt timeout; align with registered service/runtime limits |

The central native Tool catalog registers `/jobs` and the private `/job-control` RPC behind the public managed service. `/invoke` remains a blocking compatibility path. These APIs do not replace OpenShell ingress authentication. Keep the worker private and preserve `X-AOG-Context` and `Idempotency-Key` through its proxy.

Dynamic provider configuration, approval and job/session behavior are covered by contract tests using fake ACP processes. Actual paid inference, OpenCode/Kimi/Hermes binaries, OpenShell sandbox deployment and DGX hardware require separate end-to-end verification. A template list is not an installed-runtime inventory.
