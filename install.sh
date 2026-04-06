#!/usr/bin/env bash
set -euo pipefail

./build.sh
sudo cp dist/mdoc /usr/bin/
