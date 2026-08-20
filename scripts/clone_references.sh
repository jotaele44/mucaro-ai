#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/_references"
cd "$ROOT/_references"
repos=(
  "https://github.com/anthropics/claude-cookbooks.git"
  "https://github.com/anthropics/anthropic-retrieval-demo.git"
  "https://github.com/anthropics/claude-quickstarts.git"
  "https://github.com/anthropics/anthropic-sdk-python.git"
  "https://github.com/run-llama/llama_index.git"
  "https://github.com/chroma-core/chroma.git"
  "https://github.com/BerriAI/litellm.git"
  "https://github.com/microsoft/graphrag.git"
  "https://github.com/modelcontextprotocol/servers.git"
)
for repo in "${repos[@]}"; do
  name="$(basename "$repo" .git)"
  if [[ -d "$name/.git" ]]; then
    echo "REFERENCE_EXISTS $name"
  else
    git clone --depth 1 "$repo"
  fi
done
echo "REFERENCE_CLONE_OK"
