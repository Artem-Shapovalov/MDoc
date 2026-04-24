#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-/opt/MDoc}"
BIN_DIR="${BIN_DIR:-/usr/local/bin}"

"${ROOT_DIR}/build.sh"

SUDO=()
if [[ -w "$(dirname "${PREFIX}")" && -w "${BIN_DIR}" ]]; then
  SUDO=()
elif command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
  SUDO=(sudo)
else
  cat <<EOF
Built bundle: ${ROOT_DIR}/dist/MDoc

Install requires write access to:
  ${PREFIX}
  ${BIN_DIR}

Run these commands from an interactive terminal:
  sudo rm -rf "${PREFIX}"
  sudo mkdir -p "$(dirname "${PREFIX}")" "${BIN_DIR}"
  sudo cp -R "${ROOT_DIR}/dist/MDoc" "${PREFIX}"
  sudo ln -sf "${PREFIX}/MDoc" "${BIN_DIR}/mdoc"
EOF
  exit 0
fi

"${SUDO[@]}" rm -rf "${PREFIX}"
"${SUDO[@]}" mkdir -p "$(dirname "${PREFIX}")" "${BIN_DIR}"
"${SUDO[@]}" cp -R "${ROOT_DIR}/dist/MDoc" "${PREFIX}"
"${SUDO[@]}" ln -sf "${PREFIX}/MDoc" "${BIN_DIR}/mdoc"

echo "Installed MDoc into ${PREFIX}"
echo "Launcher symlink: ${BIN_DIR}/mdoc"
