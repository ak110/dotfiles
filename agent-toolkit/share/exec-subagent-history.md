# レーン実行タスク：レビュー修正の履歴統合

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 不具合修正の再現と修正後の確認を行う前 | `agent-basics:bugfix/references/root-cause-analysis-evidence.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-analysis-evidence.md` | |
| 観測で実装の前提が崩れ、次の確認や方針を選ぶ前 | `agent-basics:writing-standards/references/investigation-hypotheses.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation-hypotheses.md` | |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `workflow-phases.md` | 判断 | `read:agent-toolkit:*/share/workflow-phases.md` | |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:/share/workflow-phases.md` | 判断 | `read:agent-toolkit:*/share/workflow-phases.md` | |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review-responses-completion.md` | 判断 | `read:agent-toolkit:*/share/exec-review-responses-completion.md` | |
| レーン担当として計画または実装を始める前 | `${CLAUDE_PLUGIN_ROOT}/share/single-lane-execution.md` | 判断 | `read:agent-toolkit:*/share/single-lane-execution.md` | |
| 計画の起草前、計画なしで実装へ着手する直前、レビュー修正担当とCI修正担当が修正へ着手する直前 | `agent-toolkit:delegation`の`references/routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/routing.md` | |
| CI修正担当として原因を調べる前 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` | |
| 計画を起草するか既存計画から再開する直前 | `agent-toolkit:process-wi`の[references/lane-planning.md](../skills/process-wi/references/lane-planning.md) | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md`、`read:agent-toolkit:*/skills/process-wi/references/lane-planning.md` | |
| 初回実装（`CI修正担当`では修正）に着手する直前と、変更範囲の検証（履歴統合後の再検証を含む）を実行した後 | `agent-basics:check-execution`の`references/diagnostics.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/diagnostics.md` | |
| 手動観測を保存する前 | `verification-evidence-observation.md` | 判断 | `read:agent-toolkit:*/share/verification-evidence-observation.md` | |
| `検証結果`を書く前 | `verification-evidence-records.md` | 判断 | `read:agent-toolkit:*/share/verification-evidence-records.md` | |
| 変更範囲の検証が失敗したとき | `agent-toolkit:process-wi`の[references/lane-verification-failure.md](../skills/process-wi/references/lane-verification-failure.md) | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md`、`read:agent-toolkit:*/skills/process-wi/references/lane-verification-failure.md` | |
| `実装なし`の確定後かレビュー収束後に統合入力を確定するとき、またはメインから統合指示を受領したとき | `${CLAUDE_PLUGIN_ROOT}/share/lane-integration.subagent.md` | 判断 | `read:agent-toolkit:*/share/lane-integration.subagent.md` | |
| ユーザーが直接記入した質問への回答下書きを作成する直前 | `agent-toolkit:wi-standards`の`SKILL.md`（語の基準） | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` | |
| `レビュー修正担当`か`CI修正担当`として修正へ着手する前と、レビュー修正の履歴統合に着手する前 | `exec-review-fix.md` | 判断 | `read:agent-toolkit:*/share/exec-review-fix.md` | |

## レビュー修正の履歴統合

**5.** 修正後の履歴・応答と返却形式を確定し、手順6へ進む。

履歴統合の手順（修正の適用方法、書き換え前後の確認、記録、再開時の確認）は、`exec-review-fix.md`「レビュー修正の履歴統合」に従う。
履歴書換えが失敗した場合も、同書が接続する`history-rewrite.md`「失敗時の扱い」で状態と成果帰属を確認し、認可内の復旧・再試行は同じ担当が続ける。
追加の判断が必要な範囲だけメインへ返す。

`history-rewrite.md`が定義する各フェーズの対象は以下のとおりとする。

| phase | 対象 |
| --- | --- |
| `pre_fixup` | そのラウンドにおける最古のfixup対象から元のHEADまでのfirst-parentの全OID |
| `fixup` | 指摘を対応付けた元コミットの完全OID（過去の作業単位を含む場合は元のHEADも対象） |
| `autosquash` | そのラウンドにおける最古のfixup対象から元のHEADまでのfirst-parentの全OID |
| `amend` | 書き換え後のHEADの完全OID |

すべての指摘に対する採否、根拠、応答の記録、および返却前の確認が完了した段階で`対応完了`を返す。
採用した指摘が1件もなかった場合も同一の形式で返却し、修正コミットや履歴統合は行わずに履歴と作業ツリーの状態をそのまま保つ。
修正を実施した場合は、履歴統合まで完了させたうえで返却する。
`exec-review-responses-completion.md`「完了報告」にある再レビュー省略条件を満たす場合に限り、`対応完了（再レビュー不要）`を返すことができる。
同節に定める優先条件、修正後HEADのフェーズ、詳細内容、実装のみに基づく省略条件の判定根拠を、既存の管理表の応答欄と検証結果に記録する。
いずれの場合も、`実装完了`と同一書式の`検証結果`と`画面差分`の行を続けて出力する。

### 検証結果の記録

**6.** 後続が判定できるよう、`verification-evidence-observation.md`、`verification-evidence-records.md`に従って`検証結果`と手動観測全量を記録する。

### 返却前の確認

**7.** 返却の不足を防ぐため、`実装完了`と「レビュー修正の履歴統合」の返却前に次を確認し、`exec.subagent.md`「出力」へ進む。

- `git -C <対象worktreeの絶対パス> status --porcelain=v1`を実行した際、自身の担当範囲のパスが出力されないこと。
  もし表示された場合はコミットしてから再確認する（未コミットの変更は統合されない）。
  担当範囲外のパスのみが残っている場合はそのまま返却してよい。
- `検証結果`に記載されている手動観測の各行に、実在する保存先の絶対パスが明記されていること。
  パスが欠けている行がある場合は、適切に保存したうえで記載し直す。
- 返却する`検証結果`と同一の内容が、進捗ログ（計画を作成している場合は`atk run-script plan-progress --`）にすでに記録されていること。

`画面差分`は担当した差分に含まれる画面資産の有無から判定し（定義は`workflow-phases.md`「用語」を参照）、`あり`の場合は変更した画面資産のリポジトリ相対パスを列挙する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| レーン実行タスクの基準を適用する前 | `exec.subagent.md` | 判断 | `read:agent-toolkit:*/share/exec.subagent.md` |
