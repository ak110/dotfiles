# Bash記述スタイル

Bashのスクリプト（`sh`、`bash`、`sh.tmpl`）を書く時とレビューする時の、採る書き方と、`exec`と削除で誤りやすい点を示す。
原則9（`agent-basics:writing-standards/references/quality-principles-subtraction.md`）の適用である。
削除の規定は原則4（`agent-basics:writing-standards/references/quality-principles-constraints.md`）の適用である。
shellcheckが導入されている環境では、その診断に従う。

## 基本

- ASCIIの設定キー・拡張子など機械向けの大小文字変換・比較にだけ`LC_ALL=C`を使う。
  `${var^^}`・`${var,,}`、`declare -u`・`declare -l`、`nocasematch`を使う`[[ ]]`・`case`のほか、`tr`の文字クラス、`awk`の`toupper`・`tolower`、`sed`の大小文字変換、`grep -i`もロケールの影響を受ける。
  外部コマンドは`LC_ALL=C tr ...`、組み込みは関数の`local LC_ALL=C`かサブシェルで範囲を限る。
  スクリプト全体には適用せず、自然言語の表示にはユーザーの設定を使う。
- シバンは`#!/usr/bin/env bash`にし、先頭で`set -euo pipefail`を宣言し、自身の場所は`BASH_SOURCE`から解決する。
- `pipefail`が有効なパイプでは後段の`grep -q`・`grep -m`・`head`が残りを読まずに終了すると、前段が`SIGPIPE`で止まり、正常な一致でもパイプ全体が非0になることがある。
  後段が全入力を消費する形にするか、生成と検索を分けて生成の終了状態を先に確認する。
  一律の成功化や`pipefail`の解除で前段の実際の失敗を隠さない。
- 変数の参照とコマンド置換は二重引用符で囲む（shellcheckの`SC2086`、`SC2046`）。
- 作った一時ファイルは`trap`で終了時に回収する。
- `exec`が成功するとプロセスが置き換わるため、`exec`の起動の失敗の処理と、コマンドの終了コードの処理を分けて書く。

## 外部入力と削除

- 外部入力による分岐は`case`で固定の候補へ振り分け、外部入力を`eval`や`bash -c`へ渡すことを避ける。
- `rm -rf`より範囲の狭い手段（空ディレクトリは`rmdir`、Git管理下は`git rm -r`）を先に選ぶ。
- 一時ディレクトリの後始末で`rm -rf`を使う時はパスの変数が空でないことを確かめ、`trap`と組み合わせる（shellcheckの`SC2115`）。
