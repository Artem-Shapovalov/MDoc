#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PLANTUML_VERSION="${PLANTUML_VERSION:-1.2025.2}"

if [[ ! -d "${VENV_DIR}" ]]; then
  python3 -m venv "${VENV_DIR}"
fi

source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip >/dev/null
python -m pip install -r "${ROOT_DIR}/requirements.txt" pyinstaller >/dev/null

mkdir -p "${ROOT_DIR}/third_party/plantuml"
curl -fsSL -o "${ROOT_DIR}/third_party/plantuml/plantuml.jar"   "https://github.com/plantuml/plantuml/releases/download/v${PLANTUML_VERSION}/plantuml-${PLANTUML_VERSION}.jar"


pyinstaller --noconfirm --clean "${ROOT_DIR}/mdoc.spec"
python "${ROOT_DIR}/scripts/ci/prune_bundle.py"
GRAPHVIZ_DOT="$(command -v dot)" JAVA_BIN="$(command -v java)" python "${ROOT_DIR}/scripts/ci/postbuild_bundle.py"

echo "Built bundle: ${ROOT_DIR}/dist/MDoc"
