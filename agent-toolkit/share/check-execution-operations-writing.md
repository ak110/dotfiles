# 検証ツールの実行と保存出力の取得：文章チェックの一括実行

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 後続へ渡す検証を起動する前 | `verification-evidence-observation.md` | 判断 | `read:agent-toolkit:*/share/verification-evidence-observation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/notation-checks.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/notation-checks.md` |

## 文章チェックの一括実行

MCPの`run`ツールを利用できない場合に限り、`check-execution-operations-launch.md`「pyfltrの起動形」で解決したCLIを使い、同じ対象と制約を指定して実行する（外部ファイルを指定する場合は`--allow-external-paths`を加える）。
チェックの完了は、出力JSONLの最終行にある`summary`レコードに対し、`agent-basics:writing-standards/references/notation-checks.md`に定められた全対象被覆の条件を適用して判定する。

日本語の地の文や見出しに含まれるダッシュは、プロジェクトのpyfltr設定に`writing-check-dash`として登録し、口語表現の確認やtextlintと同様に`run`の`commands`へ含める。
登録する実行手段には、複数の対象ファイルを受け取れる公開CLIである`atk run-script writing-check-dash -- <ファイル...>`を使用する。
検出範囲の基準は`agent-basics:writing-standards/references/notation-checks.md`が定め、その検出処理の実装は`agent-toolkit/agent_toolkit/_atk/check_dash.py`に配置されている。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/notation-checks.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/notation-checks.md` |

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 検証ツールの実行と保存出力の取得：統合実行ツール経由の起動の基準を適用する前 | `check-execution-operations-launch.md` | 判断 | `read:agent-toolkit:*/share/check-execution-operations-launch.md` |
