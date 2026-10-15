# Directory Permissions

Mucaro AI can ingest project-local files under the repository root without an
extra grant. Direct filesystem ingestion outside the project root is gated by a
local directory permission ledger.

## CLI Flow

```bash
mucaro permissions grant ~/Documents/mucaro-sources --yes --reason "research corpus"
mucaro permissions check ~/Documents/mucaro-sources/source.md
mucaro ingest ~/Documents/mucaro-sources
mucaro permissions revoke ~/Documents/mucaro-sources
```

For interactive terminal use:

```bash
mucaro ingest ~/Documents/mucaro-sources --grant-directory-access
```

Without a grant, external path ingestion fails closed with
`ERROR_DIRECTORY_PERMISSION_REQUIRED`.

## API Flow

```text
GET  /permissions/directories
POST /permissions/directories/check
POST /permissions/directories/grant
POST /permissions/directories/revoke
POST /ingest-path
```

The `/ingest` upload route remains separate and copies uploaded files into
`data/input`. The `/ingest-path` route is for direct local filesystem access and
therefore requires a matching grant when the path is outside the project root.

## Browser Flow

The local frontend exposes the same contract in the Directory Access panel with
one canonical intake action:

1. Enter the folder path and optional reason.
2. Choose **Check access & ingest**.
3. If an existing grant covers the resolved path, ingestion proceeds through the
   normal server-side permission check.
4. If a grant is required, verify the resolved granted root and choose
   **Grant access & ingest**. Cancel leaves the grant unchanged and does not ingest.
5. Manage an active grant only from **Granted directories**, where **Revoke** is
   the single grant-management action.

The UI intentionally does not expose separate Check, Ingest, Grant, and
Grant-and-Ingest controls for the same workflow. Browser directory access still
goes through the backend `/permissions/directories/*` and `/ingest-path`
routes, so consolidating the UI does not bypass or weaken the server-side
permission gate.

## Local State

The ledger lives at:

```text
data/processed/directory_permissions.json
```

It records the requested path, canonical granted root, grant time, caller, and
optional reason. The ledger is runtime privacy state and is ignored by Git.

Filesystem root grants are rejected with `ERROR_DIRECTORY_PERMISSION_TOO_BROAD`.
