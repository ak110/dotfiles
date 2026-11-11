# 委譲の要否判定と委譲単位の確定：会話を引き継ぐ委譲

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 委譲する部分を選び、起動手段とモデルを確定する前 | `runtime-routing.md` |
| 委譲する部分を選び、起動手段とモデルを確定する前 | `process-model-settings.md` |
| 定型処理の実行先をモデル選定前に比較する時 | `agent-basics:writing-standards/references/design-heuristics-decisions.md` |

## 会話を引き継ぐ委譲

`runtime-routing.md`「実行手段」が定める`agents_server`標準の例外として、次の条件をすべて満たす場合に、ホスト標準のfork機能を選択する。

- 委譲先が、親エージェントが受け取ったユーザー指示、読み込んだ資料、調査結果の大半を必要としており、新規起動すると同一の背景情報を再度プロンプトへ書き直すことになる
- 親エージェントと同一のモデルおよびreasoning effortの設定で作業が成立する
- 自律的に切り出せる5工程の能力で処理し、上位モデルが担う判断は元の担当が所有する
- 工程別モデル設定のキーが割り当てられている工程ではなく、また`share/`配下の専用の`<役割名>.subagent.md`を渡す委譲でもない
- 独立文脈レビュー（成果物の作成者と文脈を共有しない委譲先が先入観を持たずに行う客観的なレビュー。実行レビューなど）のように、独立した思考文脈を必要とする評価作業ではない

forkを利用する際の入力は作業対象、役割分担、完了条件、および報告様式に限定し、すでに会話内で共有されている規範や背景情報の説明は省略する。
ホスト別の具体的な起動コマンドや指定方法は`references/runtime-routing.md`「実行手段」が定める。
関連する監査記録は、`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/routing.md：会話を引き継ぐ委譲：2026年9月27日」に記載されている。
