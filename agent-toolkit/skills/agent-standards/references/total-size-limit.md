# 文書サイズ上限の算出

対象範囲: `agent-toolkit/rules/`・`agent-toolkit/skills/`（`scripts/`配下の`.py`実装コードを含む）・
`agent-toolkit/agents/`・`agent-toolkit/references/`配下のファイル合計。

- 現在値算出コマンド:

  ```sh
  find agent-toolkit/rules agent-toolkit/skills agent-toolkit/agents agent-toolkit/references \
    -type f \( -name '*.md' -o -name '*.py' \) -not -path '*/references/*/scripts/*_test.py' \
    | xargs wc -l | tail -1
  ```

- 上限値: 実装完了時の実測値へ20%の増加余地を加えた値とする。
  再構築後の実測値は事前に確定できないため、実装完了時に上記コマンドで実測してから確定し
  `check_total_size.py`へ設定する。固定値を先に決めると、実装完了時点で既に上限へ達した状態から
  運用が始まる恐れがある
- 検査: `agent-toolkit/scripts/check_total_size.py`が実装完了時に上限超過を検出しexit 1で失敗する
  （prek hookへ登録。個別ファイル単位の判定は行わない）
