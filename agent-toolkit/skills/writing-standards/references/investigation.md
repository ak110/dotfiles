# 成果物へ書く事実の調査

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/investigation.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。

以下はagent-toolkitが所有する運用条件である。呼出元の条件と読込範囲に従い、ここに残る運用節を補う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/investigation.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/references/pending-decisions.md#「未確定判断の保留と暫定判断」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/references/pending-decisions.md#「未確定判断の保留と暫定判断」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/SKILL.md#「認可を要する操作」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「認可を要する操作」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/references/root-cause-analysis.md#「QとC・Dの区別」「類似見直し」` | 判断 | `section:agent-toolkit:*/skills/bugfix/references/root-cause-analysis.md#「QとC・Dの区別」「類似見直し」` |

## 裏付け手段の回復と前提の相違

- 依頼が参照する外部資料を代替手段でも取得できない場合は`agent-toolkit:user-confirmation-and-report`の`references/pending-decisions.md`「未確定判断の保留と暫定判断」に従う

- 既存の記述が前提とするホスト機能や実行環境の挙動と異なる観測を得たら、公式一次資料の明示記載か実機再現で条文の失効を確定できるか判定する。確定できれば調査したエージェントが`agent-toolkit:bugfix`を起動し、`agent-toolkit/skills/bugfix/SKILL.md`「問題を見つけたときの対処」に従って是正するか改訂を提案する。ユーザー確認するのは、確定に要る観測がユーザーの環境か資料でしか得られない場合と、是正が`agent-toolkit:user-confirmation-and-report`の`SKILL.md`「認可を要する操作」に当たる場合であり、観測事象、条文の位置および再検証手段を添える。確定できない場合は条文を維持し、相違と再検証の手段を`agent-toolkit/rules/01-agent.md`「完遂と先送り」の方法でWIへ残す

## 実行結果の検証

- 応答内容を使わない実機確認・対照・識別子の観測のためにエージェントCLI（`claude -p`、`codex exec`など）や`agents_server`の委譲を起動する場合は、検証対象がモデル・effort・設定・ツールで変わらない限り、軽量工程のモデルの別名とeffort `low`を指定する。モデルは版付きの旧IDへ固定しない。`agents_server`では`model_type`に`claude:haiku/low`か`codex:luna/low`を渡す。検証対象が`delegate`・`task`の起動そのものでない限り、軽量起動の`explore`か`shell`の`mode`で起動する。CLIを直接起動する場合はセッションを保存せず、設定・ツール・MCPを読まず、最小のシステムプロンプトで起動する。Claude Codeでは`--model haiku --effort low`に`--no-session-persistence --setting-sources "" --tools ""`を付ける。さらに`--strict-mcp-config --disable-slash-commands --system-prompt <文>`も付ける。プロンプトは`--`の後ろへ置く（`--tools ""`が後続の位置引数を値として消費する）。`--bare`はOAuth認証を読まない。Codexでは`-c model_reasoning_effort=low --ignore-user-config --ignore-rules`を付け、記録が不要なら`--ephemeral`も付ける。`codex exec`はChatGPT認証で系列名`luna`を拒否するため、直接起動では`luna`系列の最新の完全IDを指定する。起動回数は判定に必要な最小にする。監査記録は`docs/development/audit-records.md`「agent-toolkit/skills/writing-standards/references/investigation.md：実行結果の検証：2026年10月18日（2）」にある

## エージェント向け文書の技術的条文

- 現物の観測を根拠にエージェント向け文書の技術的条文（外部ライブラリ、実行環境、外部コマンドなどの挙動）を新規に書くか改訂する場合は、観測事象を条文へ書き、観測の記録を条文を保持するリポジトリの`docs/development/audit-records.md`へ残す。
  記録には観測した日付（再現条件として）、観測した版数および再検証手段の3項目だけを書き、見出しは`<ファイルパス>：<節名>：<日付>`とする。条文には観測した環境やモデルの版数を書かず、条文の末尾へ記録の所在を示す1文を置く。適用範囲を定める対象の版（参照資料冒頭の対象バージョンなど）は条文へ書く。
  常時規範（`AGENTS.md`・`CLAUDE.md`など）では全エージェントの読込量を抑えるため記録の見出しだけを索引とする。条文を移したら見出しのファイルパスと節名を移動先へ直す。
  再検証が条文と異なる結果を示した場合は再現条件をそろえて再検証し、そろえられない条件差を記録する。失効はそろえた再検証の結果で判定する。
  エンドユーザー向け文書、README、設定ファイル、コードのコメントと、ユーザーの指示に由来する規範、運用手順、確認の選択肢の説明は対象外とする。既存の記述は現行のまま扱う（努力目標）。ただし既存の条文の誤りがQに影響する場合の是正の要否と範囲は、この限定によらず`agent-toolkit:bugfix`の`references/root-cause-analysis.md`「QとC・Dの区別」「類似見直し」に従う
