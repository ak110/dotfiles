#!/usr/bin/env bash
set -euo pipefail
if [ "$OWNS" = false ]; then
    exit 0
fi
if [ "$OWNS" != true ] || [ "$OWNER_RESULT" != success ]; then
    echo '::error::検査領域の判定が完了していません。common-ci-ownerのログを確認し、修正後にRe-run failed jobsで再実行してください。'
    exit 1
fi
case "$SELECTED" in
true | false) ;;
*)
    echo '::error::検査領域の出力が不正です。common-ci-ownerのログを確認し、修正後にRe-run failed jobsで再実行してください。'
    exit 1
    ;;
esac
printf '%s: 実行=%s、%s\n' "$CHECK_NAME" "$SELECTED" "$SCOPE_REASON"
echo "selected=$SELECTED" >>"$GITHUB_OUTPUT"
