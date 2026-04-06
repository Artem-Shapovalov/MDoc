#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"

if [[ ! -d "${VENV_DIR}" ]]; then
  python3 -m venv "${VENV_DIR}"
fi

source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip >/dev/null
python -m pip install -r "${ROOT_DIR}/requirements.txt" pyinstaller >/dev/null

pyinstaller   --noconfirm   --clean   --onefile   --name mdoc   "${ROOT_DIR}/mdoc.py"
