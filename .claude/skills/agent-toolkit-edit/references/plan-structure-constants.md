# 計画ファイルの構造定数の改訂と追随先

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 構造定数の追随

- 計画ファイルの見出し、固定H3、表の行名は、`agent-toolkit/agent_toolkit/_plan/structure/constants.py`の構造定数で定義されている。
  これらを改訂する場合は構造定数を変更し、同一名称を使用している以下の文書も同一の変更単位で揃える。
  - `agent-toolkit/skills/write-plan/references/plan-file-standards.md`
  - `agent-toolkit/share/`配下の`<役割名>.subagent.md`
  - `docs/development/`配下の該当設計資料および`concepts.md`
  ※ユーザーの操作や画面の観測結果が変わる場合のみ、対応するガイドを更新する。
    改訂前の名称は読み取り互換用の構造定数として残し、新規作成や改訂のバリデーション処理でのみエラーとする。
- 構造定数を持たず、`plan-file-standards.md`のみで必須とされている見出しについては、同ファイルの記述に従う。
  稼働中の計画が自動チェックで不合格になるのを防ぐためにこの形式を採用した場合は、その理由を計画ファイルへ記録する
