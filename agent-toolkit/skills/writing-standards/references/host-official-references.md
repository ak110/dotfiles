# ホスト機能の公式リファレンス

本書はスキルの新規作成、hookの実装、auto modeのカスタムルールの編集およびホスト機能の可否・入出力契約の判定で、Claude Codeの仕様を確かめる一次資料と参照の手順を示す。子Claudeへ環境値を渡す場面では、settingsと起動環境の優先関係を示す。

## 公式リファレンス（Claude Code）

スキル新規作成・hook実装、ホスト機能の可否・入出力契約の判定では公式マーケットプレイス（`anthropics/claude-plugins-official`）の
`skill-creator:skill-creator`・`plugin-dev`各スキルを参照する。
仕様は一次資料を優先して確認する。各スキルの`references/`に記載のない仕様と、記載と認識が相違する事項は公式ドキュメントで確認する。
参照先は`https://code.claude.com/docs/ja/`配下（`memory.md`・`skills.md`・`sub-agents.md`・
`hooks.md`・`plugins.md`・`plugins-reference.md`など）とする。
列挙にないページを参照する場合は、ドキュメントインデックス（`https://code.claude.com/docs/llms.txt`）を
取得して対象ページのURLを特定する。インデックスが列挙するURLは英語版
（`https://code.claude.com/docs/en/<page>.md`）のため、日本語版を読む場合は言語部分を`ja`へ置換して用いる。
`WebFetch`はページの特定と概要の把握に使用してよい。

## 子Claudeへ環境値を渡す

通常のClaude Codeでは、settingsの`env`がシェルの起動環境やAgent SDKの`options.env`の同名変数を上書きする。シェルへの前置代入・exportや`options.env`だけで変更を確定せず、CLIの`--settings`へJSONファイルまたはJSON文字列を渡すか、SDKの`settings`を使う。
settingsの優先順はmanaged、CLI、local、project、userである。managedの値はCLIから上書きできない。適用されるsettingsの`env`のキー名を事前に確認し、秘匿値を表示しない。値は変数単位で置換されるため、`NO_PROXY`などへ追加するときは、既存値と追加値を結合した完成値を渡す。

Desktopとself-hosted runnerでは、競合時に起動環境の値がsettingsの値より優先される。特殊な適用条件を持つ変数は個別の公式資料を確認する。仕様の根拠と最新の条件は[環境変数](https://code.claude.com/docs/en/env-vars)、[settingsの優先順](https://code.claude.com/docs/en/settings)、[settings-referenceのenv](https://code.claude.com/docs/en/settings-reference#env)を参照する。
