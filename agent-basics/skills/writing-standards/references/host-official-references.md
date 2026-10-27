# ホスト機能の公式リファレンス

本書は、スキルの新規作成、フックの実装、Auto Mode向けカスタムルールの編集、およびホスト機能の可否や入出力契約の判定において、Claude Codeの正確な仕様を確認するための一次資料を案内する。また、子プロセスのClaude Codeへ環境変数を引き渡す際の、設定（settings）と起動環境との優先関係についても解説する。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

<!-- read-table-source: host-official-references-delivery-1.md -->

## 公式リファレンス（Claude Code）

公式マーケットプレイス（`anthropics/claude-plugins-official`）の`skill-creator:skill-creator`や`plugin-dev`の各スキルを参照する。これらの`references/`に記載がない仕様や、手元の認識と記述内容が一致しない点については、公式ドキュメントで確認する。
参照先のドキュメントは、`https://code.claude.com/docs/ja/`配下の各ページ（`memory.md`、`skills.md`、`sub-agents.md`、`hooks.md`、`plugins.md`、`plugins-reference.md`など）である。
上記に記載のないページは、ドキュメントのインデックス（`https://code.claude.com/docs/llms.txt`）から探す。インデックス内のURLは英語版（`https://code.claude.com/docs/en/<page>.md`）となっているため、日本語版を参照するときは言語パスの`en`を`ja`に置き換えてアクセスする。

## 子Claudeへ環境値を渡す

通常のClaude Code環境では、settingsで定義された`env`の値が、シェルの起動環境やAgent SDKの`options.env`で設定された同名変数を上書きする。そのため、環境変数の前置代入や`export`、あるいは`options.env`の指定だけで安心せず、CLIオプションの`--settings`（JSONファイルパスまたはJSON文字列）やSDKの`settings`引数を通じて確実に値を渡すようにする。
settingsの優先順位は高い順にmanaged、CLI、local、project、userであり、managedの設定値はCLIでの指定よりも優先される。実際に適用されるsettings内の`env`キーを事前に確認し、ログや画面表示には秘匿値を含めないよう注意する。環境変数は変数単位で丸ごと置換されるため、`NO_PROXY`などのリストに追加したい場合は、既存の値と結合した完全な文字列を渡す。

settingsには環境変数を削除する機能が存在しない点に留意する。
空文字（`""`）を設定した場合、Claude Codeの接続先選択などでは未設定と同じ扱いになるが、Bashで実行するコマンドやMCPサーバー、各種フックへは空文字のまま値が引き継がれる。
子プロセスへ変数を引き継ぎたくない場合は、子プロセスを起動する元の環境からその変数をunsetしておく必要がある。
子プロセスが直接読み込むuser、project、local設定の`env`で同一変数が定義されている場合に限り、CLIのsettingsで空文字を指定することで接続先選択から除外できる。
ただしこの場合でも、子プロセス内部には空文字として値が残る。
これらの挙動の観測記録と再検証手順が存在する。詳細は`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/host-official-references.md：子Claudeへ環境値を渡す：2026年10月22日」を参照する。

Desktop版やSelf-hosted runnerの環境では、設定の競合時に起動環境の値がsettingsよりも優先される。その根拠と最新の条件については、公式ドキュメントの[環境変数](https://code.claude.com/docs/en/env-vars)、[settingsの優先順](https://code.claude.com/docs/en/settings)、および[settings-referenceのenv](https://code.claude.com/docs/en/settings-reference#env)を参照する。
