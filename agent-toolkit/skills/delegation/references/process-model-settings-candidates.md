# 工程別モデル設定：候補列と設定の確認・変更

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 可用性の障害による候補切替を扱う前 | `fallback-launch.md` |

## 候補列と設定の確認・変更

- 候補列は`<claude|codex|agy>:<model>[/<effort>]`または`opencode:<provider>/<model>[@<effort>]`の形式をASCIIカンマで区切って記述し、先頭から順に試行する。
  effortを省略した場合は`medium`が適用される。
  OpenCodeのmodel指定に含まれる`/`は維持し、末尾の`@`以降をvariantとして扱う。
  なお、`orchestrate_model`にはOpenCodeを指定できない
- 可用性の問題による障害が発生した場合は、次の候補へ進む。
  OpenCodeのCLIが存在しない場合や、接続・認証に失敗した場合も可用性の障害として処理する。
  Claudeの利用上限に伴う待機手順は、`fallback-launch.md`「代替起動」に従う
- 初期設定として、executeはClaude Opus/low、Codex sol/lowの順、orchestrateはClaude Opus/medium、Codex sol/mediumの順とする。
  reviewはCodex sol/medium、Claude Sonnet/mediumの順、choresはCodex sol/low、Claude Sonnet/lowの順とする。
  review_fastはCodex luna/medium、Claude Haiku/mediumの順、chores_fastはCodex luna/low、Claude Haiku/lowの順とする。
  writeはAntigravity Gemini Flash/low、Claude Opus/lowの順とする
- 初期設定やプリセットの候補を変える前に、変更後の候補でその工程の役割文書を持つ担当（`review`なら実行レビュー担当）を実際の作業で1回以上起動し、最初の応答が返却形式どおりの完了になることを確かめる。
  候補の適否はモデルの挙動で決まり、候補列の保存と実行時解決の配線を確かめる既存の試験では検出できないためである。
  確かめた候補、工程、作業と結果は、候補を変える理由とともにプロジェクトの設計記録か変更のcommit本文へ残す
- `atk config apply-preset <名前>`を実行すると、writeの設定を維持したまま他の6つのキーを一括で更新できる。
  用意されている5種類のプリセットは次の通りである。
  `codex-balanced`は初期設定、`codex-near-primary`はexecuteの先頭をCodexに変更、`codex-primary`はexecuteとorchestrateの先頭をCodexに変更する。
  `claude-balanced`はchoresとchores_fastの先頭もClaudeに変更し、`claude-primary`は全6工程の先頭をClaudeへ変更する。
  OpenCodeを使用する場合は各キーに直接指定する
- Codexの`astra`、`sol`、`terra`、`luna`という指定は、起動時にApp Serverの`model/list`を参照して同一系列の最新の完全IDへ解決する。
  指定されたeffortが利用できない場合、該当系列が存在しない場合、あるいは一覧取得に失敗した場合は、理由を提示して処理を停止する。
  完全IDは固定指定として扱い、Claudeやagyの候補を受理できるかどうかは各engineの処理に委ねる
- 環境変数`AGENT_TOOLKIT_CONFIG_<キー名の大文字>`に空でない値が設定されている間は、保存された設定値よりも環境変数を優先し、その値を設定ファイルへ保存することはない。
  `set`コマンドは設定ファイルの保存先を更新し、参考一覧に含まれないモデルやeffortであっても警告を表示した上で受理する。
  `show`や`get`コマンドは参考一覧外の値もそのまま表示する
