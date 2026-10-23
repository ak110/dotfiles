#!/usr/bin/env bash
# uvの自己更新を行い、導入先の記録が異なる場合は公式installerで現行の導入先へ戻す。
set -euo pipefail
uv_path=$1
if update_output=$("$uv_path" self update 2>&1); then
    [[ -z "$update_output" ]] || printf '%s\n' "$update_output"
    exit 0
fi
if [[ "$update_output" != *'but the standalone installer was used to install uv to'* ]]; then
    [[ -z "$update_output" ]] || printf '%s\n' "$update_output" >&2
    exit 1
fi
# 既存のユーザー用実体へ再導入し、receiptと実体を公式installer自身でそろえる。
printf '%s\n' 'uvの導入先の記録を現在の実体へそろえるため、公式installerで再導入する。'
unset UV_UNMANAGED_INSTALL
export UV_INSTALL_DIR="${uv_path%/*}" UV_NO_MODIFY_PATH=1
if curl --fail --silent --show-error --location --max-time 120 https://astral.sh/uv/install.sh | sh; then
    exit 0
fi
[[ -z "$update_output" ]] || printf '%s\n' "$update_output" >&2
exit 1
