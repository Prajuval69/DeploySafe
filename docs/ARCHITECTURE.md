# DeploySafe architecture

The CLI validates inputs and submits an atomic JSON request to a local supervisor inside the selected workspace. The supervisor serializes operations and controls one guardian for each release it starts. A guardian retains the fixture's `Popen` handle and terminates its child when its supervisor pipe closes. The dashboard reads SQLite and a short-lived supervisor observation for the committed release.

~~~mermaid
flowchart LR
    CLI[DeploySafe CLI] -->|Workspace JSON request| Supervisor
    Supervisor --> Manager[Deployment manager]
    Fixtures[Bundled fixtures] --> Manager
    Manager -->|Verified snapshot| Archive[Workspace releases]
    Manager -->|Owns lifeline pipe| Guardian
    Guardian -->|Owns Popen handle| Release[Loopback fixture]
    Manager -->|GET health and version| Release
    Manager --> SQLite[(SQLite audit)]
    Manager --> Pointer[ACTIVE.json]
    SQLite --> Dashboard[Read-only localhost dashboard]
    Supervisor -->|Recent observation| Dashboard
~~~

~~~mermaid
stateDiagram-v2
    [*] --> PENDING
    PENDING --> STAGED
    STAGED --> PREFLIGHT_OK
    PREFLIGHT_OK --> STARTING
    STARTING --> HEALTHY
    HEALTHY --> ACTIVE: pointer replace and post-commit check
    PENDING --> FAILED
    STAGED --> FAILED
    PREFLIGHT_OK --> FAILED
    STARTING --> FAILED
    HEALTHY --> FAILED
    FAILED --> ROLLING_BACK: prior release exists
    ROLLING_BACK --> ROLLED_BACK: prior health verified
    ROLLING_BACK --> RECOVERY_FAILED: recovery could not be verified
    FAILED --> RECOVERY_FAILED: first deployment cleanup could not be verified
~~~

The preflight validates the bundled path, manifest schema, file digests, pinned Python source, app configuration, interpreter, writable workspace, prior service health, and candidate port. It hashes files before staging and again before launch. The service slots alternate between the policy's two distinct ports. The candidate must return HTTP 200 and the expected version on both endpoints. The old process continues serving during the candidate and post-switch checks.

The manager fsyncs a temporary pointer file and atomically replaces `runtime/ACTIVE.json`. After post-switch checks, it commits the `ACTIVE` transition and pointer history in one SQLite transaction. A restart between the file replacement and the SQLite commit restores the last committed row from SQLite and records interrupted attempts. Verified rollback uses the audited archive digest. A failed attempt leaves its evidence and removes its disposable artifact when cleanup succeeds.

SQLite uses WAL, foreign keys, schema migrations, and triggers that prevent update, delete, or replacement of audit rows. Deployments are a mutable state projection with transitions preserved in the event table. The dashboard uses fixed parameterized queries against a read-only connection. The supervisor's kernel-backed locks serialize startup, requests, and deployment mutations. An owner token provides diagnostics, and stale tokens can be reclaimed only after acquiring the kernel guard.

## Workspace contents

| Path | Purpose |
| --- | --- |
| `policy.json` | Effective workspace policy. |
| `staging/` and `releases/` | Attempt-specific staging and verified archived snapshots. |
| `runtime/ACTIVE.json` | Atomic pointer for the selected release. |
| `runtime/*.process.json` | Process provenance and recorded exit data. |
| `runtime/supervisor.json` | Short-lived local status and supervisor session identity. |
| `db/deploysafe.sqlite3` | SQLite audit and deployment projection. |
| `locks/` | Persistent kernel guard files and temporary ownership tokens. |
| `logs/` | Bounded, sanitized fixture output and supervisor diagnostics. |
| `reports/` and `demos/` | Scenario reports and independent scenario workspaces. |

After successful deployment, artifact retention keeps at least the configured number of most recent verified snapshots. Older snapshots become eligible for removal after `retention_days`. Active and tracked artifacts are protected. Audit rows, reports, and logs stay available for review.
