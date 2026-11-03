#!/bin/sh
# Installs modelplane-demo from the latest GitHub release:
#
#   curl -fsSL https://raw.githubusercontent.com/dennis-upbound/modelplane-booth-demo/main/install.sh | sh
#
# BIN_DIR picks where it goes ($HOME/.local/bin by default). MPDEMO_VERSION=v0.1.0 picks a release.
set -eu

repo="${MPDEMO_REPO:-dennis-upbound/modelplane-booth-demo}"
version="${MPDEMO_VERSION:-latest}"
bin_dir="${BIN_DIR:-$HOME/.local/bin}"

os=$(uname -s | tr '[:upper:]' '[:lower:]')
case "$(uname -m)" in
  x86_64|amd64) arch=amd64 ;;
  arm64|aarch64) arch=arm64 ;;
  *) arch=$(uname -m) ;;
esac
name="modelplane-demo-${os}-${arch}"

if [ "$version" = latest ]; then
  base="https://github.com/${repo}/releases/latest/download"
else
  base="https://github.com/${repo}/releases/download/${version}"
fi
base="${MPDEMO_BASE_URL:-$base}"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

if ! curl -fsSL "${base}/${name}" -o "${tmp}/${name}"; then
  echo "No build for ${os}-${arch}. With uv installed, run it from source instead:" >&2
  echo "  uv run https://raw.githubusercontent.com/${repo}/main/modelplane_demo.py" >&2
  exit 1
fi
curl -fsSL "${base}/${name}.sha256" -o "${tmp}/${name}.sha256"
if command -v sha256sum >/dev/null 2>&1; then
  (cd "$tmp" && sha256sum -c "${name}.sha256" >/dev/null)
else
  (cd "$tmp" && shasum -a 256 -c "${name}.sha256" >/dev/null)
fi

mkdir -p "$bin_dir"
install -m 0755 "${tmp}/${name}" "${bin_dir}/modelplane-demo"
echo "Installed ${bin_dir}/modelplane-demo. Run: modelplane-demo"
case ":${PATH}:" in
  *":${bin_dir}:"*) ;;
  *) echo "${bin_dir} isn't on your PATH. Add it, or run ${bin_dir}/modelplane-demo" ;;
esac
