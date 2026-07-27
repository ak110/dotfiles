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
- 上限超過時の再算出: 超過を検出した場合、まず既存記述の縮減（重複の統合・参照への置換）で上限内へ収める。
  縮減で収まらない場合に限り前項の算出方法で上限値を再算出する。
  再算出する場合は、超過の原因と超過行数に加えて、縮減として実施した内容および削減行数を
  `check_total_size.py`のLIMIT定数のコメントへ記録する。
  縮減を実施せずに再算出する場合は、実施しなかった判断の根拠を同コメントへ記録する。
  記録する数値は`agent-toolkit/rules/01-agent.md`「調査と検証」節に従い実測で裏付ける。
  縮減の検討結果を伴うことを再算出の条件とするのは、超過のたびに上限が上がり検査が形骸化する事態を避けるためである
- 検査: `agent-toolkit/scripts/check_total_size.py`が実装完了時に上限超過を検出しexit 1で失敗する
  （prek hookへ登録。個別ファイル単位の判定は行わない）
