# 自動コードレビュー監査

自動コードレビュー監査とは、選定と並行してGitHub CopilotのレビューとDependabotアラートを取得し、必要な処置を確定する工程を指す。監査担当は対象リポジトリの成果物を読み取りだけで扱う。

選定の開始前に、対象がGitHub上にあれば`atk review-audit pending --repo <OWNER>/<REPO>`を実行する。終了コード0で`counts.reviews`、`counts.threads`、`counts.dependabot`がいずれも0なら監査担当を起動せず、3件数を監査省略の根拠として完了報告へ渡す。それ以外（1件以上、非0終了、JSONや件数を解釈できない場合）は本書「自動コードレビュー監査担当の起動と受領」に従って監査担当を起動し、選定と並行させる。`dependabot.status`が`disabled`か`unauthorized`なら、その状態を完了報告へ渡す。監査の処置と公開工程の開始の関係は`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/finish-session.md`冒頭が定め、新しいレビューの到着は次の実行の監査で扱う。

`atk wi process-loop`はキューが空でも未判定のDependabotアラートがあればAWIを起票せずにprocess-wiを起動する。このときの候補0件の選定結果は正常として保存し、レーンを起動せずに監査の処置を確定して、`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/finish-session.md`「公開対象が無い場合の短絡」を経て終端する。要修正を同じセッションで直す場合は`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/immediate-fix.md`「即時対応の手順」に従う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/copilot-review-audit.subagent.md` | 判断 | `read:agent-toolkit:*/share/copilot-review-audit.subagent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../managed-temp/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/managed-temp/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/references/base-contract.md#「引き継ぎ記録先」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/base-contract.md#「引き継ぎ記録先」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/copilot-review-audit.subagent.md#「出力」` | 判断 | `section:agent-toolkit:*/share/copilot-review-audit.subagent.md#「出力」` |

## 自動コードレビュー監査担当の起動と受領

```text
起動対象: copilot-review-audit.subagent.md
```

`agent-toolkit:process-wi`のメインが自動コードレビュー監査を委譲するときに、本書に従って監査担当を起動し、結果を受領する。
監査の手順と返却項目は`${CLAUDE_PLUGIN_ROOT}/share/copilot-review-audit.subagent.md`が定める。

### 起動

`agents_server`の`start`を次の引数で呼び、監査担当を1件起動する。

| 引数 | 値 |
| --- | --- |
| `cwd` | 対象リポジトリの絶対パス |
| `subagent_md_path` | `copilot-review-audit` |
| `extra_params` | 次の名前付き入力だけ |

- `pending取得結果`: `atk review-audit pending`の標準出力のJSONを持つファイルの絶対パス。`atk`が`保存先:`を示した場合はその絶対パス、直接表示された場合はそのJSONをmanaged-temp（`agent-toolkit:managed-temp`）のファイルへ書いたパス。コマンドが非0で終わった場合と、JSONまたは件数を解釈できない場合は`なし`
- `引き継ぎ記録先`: 省略と継続の扱いは`agent-toolkit:delegation`の`references/base-contract.md`「引き継ぎ記録先」に従う

### 受領

結果は引数なしの`atk agents wait`で受け取り、`${CLAUDE_PLUGIN_ROOT}/share/copilot-review-audit.subagent.md`「出力」の固定形式を満たすことを確かめる。要修正の指摘と要修正のDependabotアラートは、同じセッションで是正するかAWIへ記録するかを確定する。Dependabotアラートを取得できなかった状態（機能無効または権限不足）は完了報告へ渡す。
