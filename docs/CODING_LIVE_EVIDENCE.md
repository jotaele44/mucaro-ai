# Producing live coding evidence

This is the procedure for the executed evidence that the coding capability still needs
(`docs/CODING_CAPABILITY_STATUS.md`, certification boundary items 1 and 2) plus the live-provider turn for the
agent. It mirrors `.github/workflows/ci.yml` so that a local run and a hosted run produce the same kind of receipt.

Nothing here closes the `coding_programming_expertise` product-audit domain by itself. That needs the frozen coding
regression corpus executed with zero unresolved residue on an exact clean commit, ideally on hosted CI.

## Rules that make a receipt meaningful

- **One commit, from a clean checkout.** A receipt binds the full commit SHA, and a changed SHA makes earlier
  receipts historical (`docs/RELEASE_SCOPE_2026-09-29.md`). Run everything after the last commit you intend to
  claim, from a fresh checkout of exactly that commit, never from a development tree. The container copies the whole
  source mount into a 1024 MB tmpfs (`DEFAULT_WORKSPACE_MB`) under a 2048 MB memory cap, and a development
  tree carries `.venv`, `node_modules`, `data/` and `.env` with it, which can fill the tmpfs, risk an out-of-memory
  kill, and copy secrets into the container. A clean tree of tracked files is a few megabytes.
- **No empty input is evidence.** A missing engine, image, key or server produces a `BLOCKED` receipt that names what
  is missing, and a `BLOCKED` receipt proves nothing else. Do not edit receipts, and do not describe a local run as
  hosted CI evidence.
- **Secrets stay out.** The API key is read from your shell environment only, by the provider layer. It is never an
  argument, never written to a receipt, and redacted from error text. The CLI does not read `.env`.

## 1. A clean checkout of the commit

```bash
SHA="$(git rev-parse HEAD)"                       # the commit you will claim
git worktree add --detach "../mucaro-live-${SHA:0:12}" "$SHA"
cd "../mucaro-live-${SHA:0:12}"
git status --porcelain=v1                         # must print nothing
```

Use a path without commas (the container mount rejects them). Run the scripts with the Python of a development
environment that has the project's dependencies, and check that it imports from the clean tree, not from an editable
install of the development checkout:

```bash
PYTHONPATH="$PWD" /path/to/dev/.venv/bin/python -c 'import backend.app; print(backend.app.__file__)'
```

The printed path must be inside the clean checkout.

## 2. Container receipts (needs Docker or Podman running)

On macOS, Docker Desktop's CLI is not on `PATH` by default:

```bash
export PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH
docker info >/dev/null && echo "engine reachable"
```

If the engine is not reachable, the check rows come out `BLOCKED` (`ERROR_CODING_CONTAINER_ENGINE_UNAVAILABLE`).
Start Docker Desktop and retry; do not treat that as a failure of the code under test. The three container
certifiers summarise anything that is not a full pass as `..._CERT_FAILED`, so read inside the receipt before
drawing a conclusion. In the container and full-profile receipts, `suite.execution_status` and each row's `state`:
`BLOCKED` means the engine or image was unavailable, and only a row in state `FAIL` means a command ran and failed.
In the mutation receipt, `execution.execution_status` of `positive`, `negative` and `rollback`: `BLOCKED` carries a
`blocked` object with the status and detail, while `FAIL` means the probe ran and failed.

```bash
# The isolation boundary on a pinned image
docker pull python:3.11-slim
IMAGE="$(docker image inspect python:3.11-slim --format '{{index .RepoDigests 0}}')"
PYTHONPATH="$PWD" python scripts/certify_coding_container_live.py --image "$IMAGE" --output /tmp/coding-container-live.json

# The dependency-complete image, then the full test/lint/type/build profile and the sandboxed mutation probe
BASE_IMAGE="$(docker image inspect python:3.11-slim --format '{{index .RepoDigests 0}}')"
docker build --build-arg BASE_IMAGE="$BASE_IMAGE" -f docker/coding.Dockerfile -t "mucaro-coding-full:${SHA}" .
IMAGE_ID="$(docker image inspect "mucaro-coding-full:${SHA}" --format '{{.Id}}')"
PYTHONPATH="$PWD" python scripts/certify_coding_full_profile.py --image "$IMAGE_ID" --output /tmp/coding-full-profile.json
PYTHONPATH="$PWD" python scripts/certify_coding_mutation_live.py --image "$IMAGE_ID" --output /tmp/coding-mutation-live.json
```

The pull and the image build use the network. The coding runtime itself never does: it runs with `--pull=never`
and `--network=none`.

`code checks --run` works against the same images, and `--engine PATH` selects a binary that is not on `PATH`. Its exit status is `0` only when the suite passed, `1` when a required check failed and `3` when it was blocked, so it can gate a script.

## 3. Live provider turn

One bounded, read-only turn on a throwaway repository. Nothing from your repositories is sent anywhere.

```bash
# Anthropic: ANTHROPIC_API_KEY must already be set in this shell (type it at your own prompt, never in a file
# or on a command line that is logged); name a model your key can use
MUCARO_AI_LIVE_MODE=1 \
  PYTHONPATH="$PWD" python scripts/certify_coding_agent_live.py --provider anthropic --model <model-id> --output /tmp/coding-agent-live-anthropic.json

# A local model on loopback (for example Ollama with the model pulled)
PYTHONPATH="$PWD" python scripts/certify_coding_agent_live.py --provider local --model llama3.1:8b --output /tmp/coding-agent-live-local.json
```

Exit status is 0 for `CODING_AGENT_LIVE_CERT_OK`, 2 for `..._FAILED` (a model answered and the agent did not
succeed) and 3 for `..._BLOCKED` (a precondition is missing: live mode off, no key, no reachable server, an unknown
model or a rate limit).

## 4. Recording the result

Keep each receipt with the commit SHA it names. If any step needs a code change, make it as a new commit and run
everything again from section 1: a receipt is never patched in place. Remove the temporary checkout when finished:

```bash
cd - && git worktree remove "../mucaro-live-${SHA:0:12}"
```

## What this does not cover

- Hosted governance (CI admission of executable jobs) and the full coding regression corpus (23 frozen requirements
  in `_architecture/coding_regression_manifest.json`) are separate evidence.
- Authenticated remote push and pull-request submission have source and hermetic tests (`mucaro code push`,
  `mucaro code pr-create`), but no live credentialed evidence, by design: that is the last, owner-approved step.
