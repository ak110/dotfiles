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

## 自動コードレビュー監査担当の起動と受領

```text
起動対象: copilot-review-audit.subagent.md
```

`agent-toolkit:process-wi`のメインエージェントが自動コードレビュー監査を委譲する際は、本書に従って監査担当を起動し、結果を受け取る。
監査の具体的な手順は、`${CLAUDE_PLUGIN_ROOT}/share/copilot-review-audit.subagent.md`に定められている（所在参照）。返却項目は本書「監査担当の返却契約」に従う。

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

通常・代替取得とも、Dependabotの判定対象がある場合はdefault_branchと確認SHAを受け取り、取得・比較・markが同じSHAに基づくことを確かめる。
判定対象が0件なら版情報は不要で、判定欄の「なし」と取得状態を受領する。
機能無効・権限不足の場合は、その状態と理由を受領する。
却下はAPI終了0、応答number一致、state=dismissedを確認できた番号だけ処置済みとする。
反映待ち・要修正の確認版付きmarkと、却下失敗・未判定の未markを区別する。
未markアラートは単一・all-reposの既存確認間隔で再取得し、古い番号だけの無進捗記録で抑止しない。
同版の判定済み除外はreview-auditが担い、GitHub APIの`default_branch`が指すブランチの更新後は同番号も再監査する。
WI部分の無進捗抑止は維持し、未観測Noneを取得成功0件と解釈しない。

実行結果は引数なしの`atk agents wait`で受け取り、本書「監査担当の返却契約」に規定された固定形式を満たしていることを確認する。修正を要する指摘事項やDependabotアラートについては、同一セッション内で修正するか、AWIに記録して後続へ回すかを確定する。機能の無効化や権限不足などによってDependabotアラートを取得できなかった状態は、完了報告へ引き継ぐ。

<!-- return-contract:copilot-review-audit:start -->
<!-- 生成元: share/copilot-review-audit.subagent.md「出力」。手動編集しない。 -->
### 監査担当の返却契約

監査が問題なく完了したと判定できた場合は、次の形式のみを返却する。

```text
状態: completed
指摘の分類: <全Pull RequestのCopilot由来のreview本文と、未解決threadを持つPull RequestのCopilot由来のinline commentごとの所在、分類および処置>
要修正の指摘: <要修正と分類した指摘の所在と対処案。無い場合は「なし」>
GitHubへの書き込み: <返信、threadの解決およびコメント投稿の結果と、非0で終了した書き込み。無い場合は「なし」>
記録したreview本文: <`atk review-audit mark`で記録したreview本文のdatabaseId。無い場合は「なし」>
取得対象外のPull Request: <inline commentの取得対象へ入らなかったPull Request番号。無い場合は「なし」>
代替取得で除いた判定済み: <GraphQLで代替取得した場合に、判定済みとして除いたdatabaseIdの一覧と件数。代替取得しなかった場合は「なし」>
Dependabotアラートの判定: <番号、default_branch、確認SHA、manifest/package、解決済み版・修正版、判定区分、処置。API終了コードと応答number/state、未処置の理由と次回再取得を含む。無い場合は「なし」>
要修正のDependabotアラート: <番号、マニフェスト、パッケージ、修正版と対処案。無い場合は「なし」>
記録したDependabotアラート: <版付きmarkが成功した番号と確認SHA。却下失敗・未判定は含めない。無い場合は「なし」>
Dependabotアラートの取得状態: <取得できた場合は「取得済み」。機能無効または権限不足で取得できなかった場合はその状態と理由>
```

続行不能と追加報告行は、共通契約`../../../share/rules-subagent.md`「返却形式の受け渡し」に従う。取得不能の理由には、失敗したコマンド、終了コードと取得できなかった範囲を記す。
<!-- return-contract:copilot-review-audit:end -->
