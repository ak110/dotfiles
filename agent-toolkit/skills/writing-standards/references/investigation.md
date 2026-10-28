# 成果物へ書く事実の調査

品質基準の正式な提供元は`agent-basics:writing-standards`の`references/investigation.md`である。呼び出し元の取得条件と注目箇所を引き継ぎ、本書と正式な資料を全文読む。節指定がある場合は、指定された見出しを持つ資料に注目する。両方に同名の見出しがあれば両方に注目する。

以下はagent-toolkit固有の運用条件である。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/investigation.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/references/pending-decisions.md#「未確定判断の保留と暫定判断」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/references/pending-decisions.md#「未確定判断の保留と暫定判断」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/SKILL.md#「認可を要する操作」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「認可を要する操作」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/references/root-cause-analysis.md#「QとC・Dの区別」`、`agent-basics:bugfix/references/root-cause-remediation.md#「類似見直し」` | 判断 | `section:agent-toolkit:*/skills/bugfix/references/root-cause-analysis.md#「QとC・Dの区別」`、`section:agent-basics:*/skills/bugfix/references/root-cause-remediation.md#「類似見直し」` |

## 裏付け手段の回復と前提の相違

- 依頼が参照する外部資料を代替手段でも取得できない場合は、`agent-toolkit:user-confirmation-and-report`の`references/pending-decisions.md`「未確定判断の保留と暫定判断」に従う。

- 既存の記述が前提としているホスト機能や実行環境の挙動と異なる結果を観測した場合は、公式の一次資料に明示されているか、実機で再現できるかを確認し、既存の条文が失効しているかを判定する。失効を確定できた場合は、調査したエージェントが`agent-toolkit:bugfix`を起動し、`agent-toolkit/skills/bugfix/SKILL.md`「問題を見つけたときの対処」に従ってその場で是正するか改訂を提案する。ユーザー確認するのは、確定に必要な観測がユーザーの環境や資料でしか得られない場合と、是正内容が`agent-toolkit:user-confirmation-and-report`の`SKILL.md`「認可を要する操作」に該当する場合に限る。その際は、観測した事象、対象条文の記載箇所、再検証手段を添えて報告する。失効を確定できない場合は条文を維持し、相違内容と再検証手段を`agent-toolkit/rules/01-agent.md`「完遂と先送り」の手順に沿ってWIとして残す。

## 実行結果の検証

- 応答内容自体を必要とせず、実機確認や対照実験、識別子の観測のみを目的としてエージェントCLI（`claude -p`や`codex exec`など）や`agents_server`の委譲を起動する場合がある。このとき、検証対象がモデルやeffort、設定、ツールによって左右されない限り、軽量工程向けのモデル別名とeffort `low`を指定する。モデルはバージョン付きの古いIDに固定しない。`agents_server`では`model_type`に`claude:haiku/low`か`codex:luna/low`を渡す。検証対象が`delegate`や`task`の起動そのものでない限り、軽量起動である`explore`または`shell`の`mode`で起動する。CLIを直接起動する場合はセッションを保存せず、設定・ツール・MCPを読み込まず、最小限のシステムプロンプトで起動する。Claude Codeでは`--model haiku --effort low`に加えて`--no-session-persistence --setting-sources "" --tools ""`を指定する。さらに`--strict-mcp-config --disable-slash-commands --system-prompt <文>`も指定する。プロンプトは`--`の後ろに配置する（`--tools ""`が後続の位置引数を値として消費してしまうのを防ぐため）。なお`--bare`はOAuth認証を読み込まない。Codexでは`-c model_reasoning_effort=low --ignore-user-config --ignore-rules`を指定し、記録が不要な場合は`--ephemeral`も加える。`codex exec`はChatGPT認証において系列名`luna`を拒否するため、直接起動するときは`luna`系列の最新の完全なIDを指定する。起動回数は判定に必要な最小限に抑える。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/investigation.md：実行結果の検証：2026年10月18日（2）」にある。

## エージェント向け文書の技術的条文

- 実機や現物の観測結果を根拠として、エージェント向け文書の技術的な条文（外部ライブラリ、実行環境、外部コマンドの挙動など）を新規に作成または改訂する場合は、観測した事象を条文に明記し、その観測記録を条文を保持するリポジトリの`docs/development/audit-records.md`に残す。
  記録には、再現条件としての観測日、観測時のバージョン、および再検証手段の3項目のみを記載し、見出しは`<ファイルパス>：<節名>：<日付>`とする。条文本文には観測環境やモデルのバージョンは記載せず、条文の末尾に記録の所在を示す文を1文置く。ただし、適用範囲を定める対象バージョン（参照資料の冒頭に掲げる対象バージョンなど）は条文本文に記載する。
  常時規範（`AGENTS.md`や`CLAUDE.md`など）では、全エージェントの読み込み量を抑えるため、記録の見出しのみを索引として記載する。条文を別の場所へ移動した場合は、見出しのファイルパスと節名も移動先の位置へ修正する。
  再検証の結果が条文の記述と異なっていた場合は、再現条件をそろえた上で再検証し、そろえられなかった条件の差異を記録する。条文の失効は、条件をそろえて実施した再検証の結果に基づいて判定する。
  エンドユーザー向け文書、README、設定ファイル、コードコメント、ならびにユーザーの指示に由来する規範、運用手順、確認時の選択肢の説明は、この記録義務の対象外とする。既存の記述は現行のまま維持する（努力目標）。ただし、既存の条文の誤りが品質（Q）に悪影響を及ぼす場合は、この限定にかかわらず是正要否と対応範囲を判定する。判定は`agent-toolkit:bugfix`の`references/root-cause-analysis.md`「QとC・Dの区別」と、`agent-basics:bugfix/references/root-cause-remediation.md`「類似見直し」に従う。
