#!/usr/bin/env bash
# 公式installerでuvの導入先の記録を現在の実体に合わせる。
set -euo pipefail
uv_path=$1
unset UV_UNMANAGED_INSTALL
export UV_INSTALL_DIR="${uv_path%/*}" UV_NO_MODIFY_PATH=1
curl --fail --silent --show-error --location --max-time 120 https://astral.sh/uv/install.sh | sh || exit 1
