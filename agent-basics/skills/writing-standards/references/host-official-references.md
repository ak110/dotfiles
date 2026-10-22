# ホスト機能の公式リファレンス

本書はスキルの新規作成、hookの実装、auto modeのカスタムルールの編集およびホスト機能の可否・入出力契約の判定で、Claude Codeの仕様を確かめる一次資料を示す。子Claudeへ環境値を渡す場面では、settingsと起動環境の優先関係を示す。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

<!-- read-table-source: host-official-references-delivery-1.md -->

## 公式リファレンス（Claude Code）

公式マーケットプレイス（`anthropics/claude-plugins-official`）の`skill-creator:skill-creator`・`plugin-dev`各スキルを参照する。その`references/`に無い仕様と、記載が認識と一致しない事項は公式ドキュメントで確かめる。
参照先は`https://code.claude.com/docs/ja/`配下（`memory.md`・`skills.md`・`sub-agents.md`・`hooks.md`・`plugins.md`・`plugins-reference.md`など）とする。
列挙に無いページはドキュメントインデックス（`https://code.claude.com/docs/llms.txt`）から特定する。インデックスのURLは英語版（`https://code.claude.com/docs/en/<page>.md`）であり、日本語版は言語部分を`ja`へ置換する。

## 子Claudeへ環境値を渡す

通常のClaude Codeでは、settingsの`env`がシェルの起動環境やAgent SDKの`options.env`の同名変数を上書きする。前置代入・exportや`options.env`だけで変更を確定せず、CLIの`--settings`（JSONファイルかJSON文字列）かSDKの`settings`で渡す。
settingsの優先順はmanaged、CLI、local、project、userであり、managedの値はCLIの指定より優先される。適用されるsettingsの`env`のキー名を事前に確かめ、秘匿値は表示対象から除く。値は変数単位で置換されるため、`NO_PROXY`などへ追加するときは既存値と結合した完成値を渡す。

settingsは環境変数を取り除く手段を持たない。
空の値はClaude Codeの接続選択では未設定と同じに扱われるが、Bashのコマンド、MCPサーバー、hookへ空の値のまま継承される。
継承させない変数は子を起動する環境から取り除く。
子が自ら読むuser・project・local設定の`env`が同じ変数を定義する場合だけ、CLIのsettingsへ空の値を指定して接続の選択から外す。
この場合には子プロセスにも空の値が残る。
観測と再検証手段は`docs/development/audit-records.md`にある。参照する節は「agent-basics/skills/writing-standards/references/host-official-references.md：子Claudeへ環境値を渡す：2026年10月22日」である。

Desktopとself-hosted runnerでは、競合時に起動環境の値がsettingsより優先される。根拠と最新の条件は[環境変数](https://code.claude.com/docs/en/env-vars)、[settingsの優先順](https://code.claude.com/docs/en/settings)、[settings-referenceのenv](https://code.claude.com/docs/en/settings-reference#env)にある。
