#!/usr/bin/env bash
# 呼出し元の前景で端末のINTと期限のTERMを受け、今回の段だけに転送する。
set -mu
stopping=''
stop_stage() {
    stopping=$1
    # %+ はこのシェルのジョブ。既に終了したジョブの数値PGIDを再利用しない。
    kill -s "$stopping" %+ 2>/dev/null || :
}
trap 'stop_stage INT' INT
trap 'stop_stage TERM' TERM

# timeoutは専用群全体へ転送し、起点が応答しなければ10秒後に強制終了する。
# 起点だけが先に終了した時は、その親のbashが同じ群に残った孫も終了させる。
# shellcheck disable=SC2016 # 引数は段の群に入ってから展開する。
timeout --kill-after=10 0 bash -c '
    trap "kill -KILL 0" INT TERM
    "$@"; exit $?
' bash "$@" <&0 &
stage=$!
[[ -z "$stopping" ]] || stop_stage "$stopping"
code=0
wait "$stage" || code=$?
if [[ -n "$stopping" ]]; then
    trap '' INT TERM
    wait "$stage" 2>/dev/null || :
    [[ "$stopping" != INT ]] || exit 130
    exit 124
fi
exit "$code"
