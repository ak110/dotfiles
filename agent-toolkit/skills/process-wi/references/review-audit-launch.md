# 自動コードレビュー監査

自動コードレビュー監査とは、WI選定と並行してGitHub CopilotのレビューおよびDependabotアラートを取得し、必要な対応内容を確定させる工程を指す。監査担当は、対象リポジトリの成果物を読み取り専用で扱う。

WI選定の開始前に、対象リポジトリがGitHub上にあれば`atk review-audit pending --repo <OWNER>/<REPO>`を実行する。終了コード0で`counts.reviews`、`counts.threads`、`counts.dependabot`がいずれも0である場合は、監査担当を起動せず、これら3つの件数を監査省略の根拠として完了報告へ引き継ぐ。それ以外の場合（1件以上存在する、終了コードが非0、あるいはJSONや件数を正常に解釈できない場合）は、本書「自動コードレビュー監査担当の起動と受領」に従って監査担当を起動し、選定処理と並行して実行する。`dependabot.status`が`disabled`または`unauthorized`の場合は、その状態を完了報告へ引き継ぐ。監査で判明した処置と公開工程開始との関係は`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/finish-session.md`冒頭に定められており、新たに到着したレビューは次回実行時の監査で取り扱う。

`atk wi process-loop`は、キューが空であっても未判定のDependabotアラートが存在する場合は、AWIを起票せずに`process-wi`を起動する。この場合に候補が0件となる選定結果は正常な状態として保存し、レーンを起動せずに監査の対応内容を確定させ、`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/finish-session.md`「公開対象が無い場合の短絡」を経由して終了する。修正が必要な項目を同一セッション内で修正する場合は、`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/immediate-fix.md`「即時対応の手順」に従う。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../managed-temp/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/managed-temp/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/references/base-contract.md#「引き継ぎ記録先」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/base-contract.md#「引き継ぎ記録先」` |
| 監査担当の返却を受け取った後に形式を確かめる時 | `../../../share/copilot-review-audit.subagent.md#「出力」` | 判断 | `section:agent-toolkit:*/share/copilot-review-audit.subagent.md#「出力」` |

## 自動コードレビュー監査担当の起動と受領

```text
起動対象: copilot-review-audit.subagent.md
```

`agent-toolkit:process-wi`のメインエージェントが自動コードレビュー監査を委譲する際は、本書に従って監査担当を起動し、結果を受け取る。
監査の具体的な手順と返却項目は、`${CLAUDE_PLUGIN_ROOT}/share/copilot-review-audit.subagent.md`に定められている（所在参照）。

### 起動

`agents_server`の`start`を以下の引数で呼び出し、監査担当を1件起動する。

| 引数 | 値 |
| --- | --- |
| `cwd` | 対象リポジトリの絶対パス |
| `subagent_md_path` | `copilot-review-audit` |
| `extra_params` | 次の名前付き入力だけ |

- `pending取得結果`: `atk review-audit pending`の出力内の`保存先:`に示された、生成元JSONファイルの絶対パス。エージェント環境ではデータ量にかかわらずファイルへ保存し、件数とDependabotの取得状態を併記する。保存先のパスをそのまま渡すことで、モデルによるJSONの再保存処理を省く。コマンドが非0で終了した場合、あるいはJSONや件数を解釈できない場合は`なし`を指定する。
- `引き継ぎ記録先`: 指定の省略や継続時の取り扱いについては、`agent-toolkit:delegation`の`references/base-contract.md`「引き継ぎ記録先」に従う。

### 受領

実行結果は引数なしの`atk agents wait`で受け取り、`${CLAUDE_PLUGIN_ROOT}/share/copilot-review-audit.subagent.md`「出力」に規定された固定形式を満たしていることを確認する。修正を要する指摘事項やDependabotアラートについては、同一セッション内で修正するか、AWIに記録して後続へ回すかを確定する。機能の無効化や権限不足などによってDependabotアラートを取得できなかった状態は、完了報告へ引き継ぐ。
