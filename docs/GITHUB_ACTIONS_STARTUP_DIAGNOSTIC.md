# GitHub Actions Startup Diagnostic

This document records the current hosted-CI blocker for the `v0.1.23`
governance candidate.

## Diagnostic Scope

- Repository: `jotaele44/mucaro-ai`
- Branch: `main`
- Local release gate: `CERTIFY_RELEASE_OK`
- Exact-SHA fresh-clone gate: `FRESH_CLONE_CERT_OK`
- GitHub governance gate: `CERTIFICATION_FAIL`
- Blocking gate: `github_latest_ci`

Use `git rev-parse HEAD` before each release attempt; documentation commits move
the live HEAD, so the exact release candidate SHA must be re-bound at the time
of certification.

The named `CI` workflow has repeatedly completed with `startup_failure` before
GitHub created jobs or logs. Diagnostic examples:

```text
workflow=CI
run=33972601223
event=workflow_dispatch
head_sha=8baef57c3188990c29bf7cb83ad3003aa1828d18
status=completed
conclusion=startup_failure
jobs=0
```

```text
workflow=CI
run=33972734825
event=workflow_dispatch
head_sha=5d5711a26e668d468523fb47774cdcdbf8b3a052
status=completed
conclusion=startup_failure
jobs=0
```

The manual-only `CI Startup Probe` workflow also failed before jobs or logs.
Diagnostic examples:

```text
workflow=CI Startup Probe
run=33972500645
event=workflow_dispatch
head_sha=b70c5a859a879e651304091b57505b74c49f6953
status=completed
conclusion=startup_failure
jobs=0
```

```text
workflow=CI Startup Probe
run=33972734765
event=workflow_dispatch
head_sha=5d5711a26e668d468523fb47774cdcdbf8b3a052
status=completed
conclusion=startup_failure
jobs=0
```

## Excluded Causes

The startup probe contains only an `echo` step and does not install
dependencies, check out the repository, run Python, run Node, or execute Mucaro
release scripts. Because that probe also fails before jobs exist, the active
blocker is not explained by:

- Python package installation
- Node package installation
- `npm audit`
- frontend build
- retrieval evaluation
- desktop launcher certification
- Mucaro test failures
- checkout/setup action usage

## Verified Repository Settings

Current API-visible repository settings:

```text
actions_enabled=true
allowed_actions=all
sha_pinning_required=false
default_workflow_permissions=read
repo_private=true
repo_disabled=false
repo_archived=false
token_scopes=gist,read:org,repo,workflow
```

The current token cannot inspect account Actions billing because that endpoint
requires the `user` scope.

## Next Checks

Run these checks from a trusted operator terminal:

```bash
gh auth refresh -h github.com -s user
gh api users/jotaele44/settings/billing/actions
```

Then inspect GitHub repository settings:

```text
Settings -> Actions -> General
Settings -> Billing and plans -> Actions minutes / spending limit
Settings -> Actions -> Runners
```

Look specifically for exhausted included minutes, a disabled spending limit for
private repositories, suspended Actions usage, missing GitHub-hosted runner
availability, or an account-level policy that prevents hosted runners from
starting.

## Release Rule

Do not tag the `v0.1.23` governed baseline while `github_latest_ci` fails for
the current HEAD. Local certification and exact-SHA fresh-clone certification
are valid evidence, but they do not replace hosted GitHub governance evidence.

After hosted startup is repaired:

```bash
gh workflow run CI --repo jotaele44/mucaro-ai --ref main
PYTHONPATH=.:backend python3 scripts/mucaro.py certify status --include-github --format text
python3 scripts/certify_release.py
python3 scripts/certify_fresh_clone.py --ref "$(git rev-parse HEAD)"
```

The governed baseline is tag-ready only when the named `CI` workflow succeeds
for the exact current HEAD and the certification status no longer reports a
failed GitHub gate.
