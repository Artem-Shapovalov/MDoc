#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PLANTUML_VERSION="${PLANTUML_VERSION:-1.2025.2}"
DEJAVU_VERSION="${DEJAVU_VERSION:-2.37}"

if [[ ! -d "${VENV_DIR}" ]]; then
  python3 -m venv "${VENV_DIR}"
fi

source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip >/dev/null
python -m pip install -r "${ROOT_DIR}/requirements.txt" pyinstaller >/dev/null

mkdir -p "${ROOT_DIR}/third_party/plantuml" "${ROOT_DIR}/third_party/fonts"
curl -fsSL -o "${ROOT_DIR}/third_party/plantuml/plantuml.jar"   "https://github.com/plantuml/plantuml/releases/download/v${PLANTUML_VERSION}/plantuml-${PLANTUML_VERSION}.jar"

TMP_FONT_ARCHIVE="$(mktemp)"
TMP_FONT_DIR="$(mktemp -d)"
trap 'rm -f "${TMP_FONT_ARCHIVE}"; rm -rf "${TMP_FONT_DIR}"' EXIT
curl -fsSL -o "${TMP_FONT_ARCHIVE}"   "https://downloads.sourceforge.net/project/dejavu/dejavu/${DEJAVU_VERSION}/dejavu-fonts-ttf-${DEJAVU_VERSION}.tar.bz2"
tar -xjf "${TMP_FONT_ARCHIVE}" -C "${TMP_FONT_DIR}"
find "${TMP_FONT_DIR}" -maxdepth 2 -type f \( -name 'DejaVuSans*.ttf' -o -name 'DejaVuSansMono*.ttf' \) -exec cp {} "${ROOT_DIR}/third_party/fonts/" \;

pyinstaller --noconfirm --clean "${ROOT_DIR}/mdoc.spec"
python "${ROOT_DIR}/scripts/ci/prune_bundle.py"
GRAPHVIZ_DOT="$(command -v dot)" JAVA_BIN="$(command -v java)" python "${ROOT_DIR}/scripts/ci/postbuild_bundle.py"

echo "Built bundle: ${ROOT_DIR}/dist/MDoc"
