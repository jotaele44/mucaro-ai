# VIDEO_CLI_PARITY_V1 — governed file edit

`coding.file_edit` closes the source-level `project_file_edit` capability without exposing arbitrary filesystem mutation.

## Contract

Each approved call mutates exactly one UTF-8 repository file using one explicit operation:

- `CREATE` — requires a `MISSING` preimage.
- `REPLACE` — requires an exact `EXISTS` preimage.
- `DELETE` — requires an exact `EXISTS` preimage and carries no replacement content.

Before mutation the transaction revalidates:

- exact repository HEAD;
- canonical POSIX repository-relative path;
- repository boundary and existing parent directory;
- no symlink traversal and no `.git` target;
- exact preimage state, byte size, and SHA-256;
- exact proposed UTF-8 content SHA-256 for create/replace;
- bounded content size;
- absence of pre-existing dirty overlap on the target.

After mutation it verifies:

- HEAD is unchanged;
- the target postimage matches the approved operation/content;
- unrelated pre-existing dirty paths are unchanged;
- no unexpected dirty paths were introduced.

A failed postcondition triggers rollback to the exact preimage. Rollback failure is explicit material residue and is never represented as success.

## Authority model

The tool is local, mutating, and approval-gated. It uses the existing tool-call digest, permission-lease, audit, and terminal `:tool` path. It does not execute shell commands, create directories, follow symlinks, commit, push, or contact a remote service.

Source implementation is distinct from parity certification: VCP-004 can be `IMPLEMENTED` while exact-head interactive execution remains `OPEN`.
