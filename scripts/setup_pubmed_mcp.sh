#!/usr/bin/env bash
# One-time: clone, install, and build cyanheads/pubmed-mcp-server (Bun-based upstream).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${TARGET_DIR:-$ROOT/pubmed-mcp-server}"
REPO_URL="https://github.com/cyanheads/pubmed-mcp-server.git"

echo "Target directory: $TARGET"

if [ ! -d "$TARGET" ]; then
  echo "Cloning $REPO_URL ..."
  git clone "$REPO_URL" "$TARGET"
else
  echo "Repository already present at $TARGET (skip clone)."
fi

cd "$TARGET"

# Upstream uses Bun (packageManager, build scripts). Prefer Bun for install + build.
if command -v bun >/dev/null 2>&1; then
  echo "Using Bun: $(bun --version)"
  bun install
  echo "Building (bun run rebuild)..."
  bun run rebuild
  echo ""
  echo "Done. Start the MCP server (from $TARGET):"
  echo "  STDIO:   MCP_TRANSPORT_TYPE=stdio bun run start:stdio"
  echo "  HTTP:    MCP_TRANSPORT_TYPE=http MCP_HTTP_PORT=3010 bun run start:http"
  echo "    → HTTP base: http://localhost:3010/mcp"
  echo ""
  echo "NCBI (recommended, set in your shell or .env):"
  echo "  export NCBI_ADMIN_EMAIL=you@example.com"
  echo "  export NCBI_API_KEY=...   # optional, higher rate limits"
  exit 0
fi

# npm cannot run 'bun run scripts/build.ts' without Bun; offer npx one-liner instead.
if command -v npm >/dev/null 2>&1; then
  echo "Bun is not installed. This project builds with Bun; npm alone cannot run upstream's build."
  echo "Install Bun: https://bun.sh/"
  echo ""
  echo "Workaround: run the published server without a local build:"
  echo "  npx -y @cyanheads/pubmed-mcp-server@latest"
  echo "  (set MCP_TRANSPORT_TYPE=stdio in the environment, or use streamable HTTP per upstream README.)"
  exit 1
fi

echo "Neither bun nor npm found. Install Bun from https://bun.sh/ and re-run this script."
exit 1
