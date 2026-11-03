#!/usr/bin/env bash
# Builds the demo as one binary for this machine: dist/modelplane-demo-<os>-<arch> and its .sha256.
# Needs uv. The release workflow runs this on each platform.
set -euo pipefail
cd "$(dirname "$0")"

os=$(uname -s | tr '[:upper:]' '[:lower:]')
case "$(uname -m)" in
  x86_64|amd64) arch=amd64 ;;
  arm64|aarch64) arch=arm64 ;;
  *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac
name="modelplane-demo-${os}-${arch}"

uvx --from 'pyinstaller>=6' --with 'rich>=13' pyinstaller --onefile --clean --noconfirm --log-level WARN \
  --name "$name" --distpath dist --workpath build/work --specpath build modelplane_demo.py

(cd dist && shasum -a 256 "$name" > "$name.sha256")

# Smoke test: the whole demo plays, with no terminal and no network.
MPDEMO_PACE=0 "dist/$name" --auto < /dev/null | grep "Back on two clouds" > /dev/null
echo "dist/$name  $(du -h "dist/$name" | cut -f1)  ok"
