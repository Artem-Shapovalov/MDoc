#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-/opt/MDoc}"
BIN_DIR="${BIN_DIR:-/usr/local/bin}"

"${ROOT_DIR}/build.sh"
sudo rm -rf "${PREFIX}"
sudo mkdir -p "$(dirname "${PREFIX}")" "${BIN_DIR}"
sudo cp -R "${ROOT_DIR}/dist/MDoc" "${PREFIX}"
sudo ln -sf "${PREFIX}/MDoc" "${BIN_DIR}/mdoc"

echo "Installed MDoc into ${PREFIX}"
echo "Launcher symlink: ${BIN_DIR}/mdoc"
