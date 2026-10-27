# 権限拒否への対応

本書は、ツールの呼び出しが権限設定またはauto mode classifierによって拒否された場合の手順を定める。
auto modeのカスタムルールを追加・編集する手順は、`agent-toolkit:writing-standards`の`references/auto-mode.md`に従う。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../writing-standards/references/auto-mode.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/references/auto-mode.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../writing-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/claude-hooks-output.md#「PermissionRequest」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/claude-hooks-output.md#「PermissionRequest」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/references/root-cause-analysis.md#「QとC・Dの区別」` | 判断 | `section:agent-toolkit:*/skills/bugfix/references/root-cause-analysis.md#「QとC・Dの区別」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../writing-standards/references/auto-mode.md#「カスタムルール追加のワークフロー」` | 判断 | `section:agent-toolkit:*/skills/writing-standards/references/auto-mode.md#「カスタムルール追加のワークフロー」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/auto-mode.md#「カスタムルール記述の注意点」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/auto-mode.md#「カスタムルール記述の注意点」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../wi-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `approval-scope.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/approval-scope.md` |

## 拒否の確認手順

拒否が発生した場合は、別の手段で迂回を試みる前に、有効な設定ファイルを読み込んで許可・拒否のルールを確認する。確認対象は`/etc/claude-code/managed-settings.json`、`~/.claude/settings.json`、およびリポジトリ直下の`.claude/settings.json`と`.claude/settings.local.json`である。拒否メッセージの本文自体が同一結果の再試行や関連操作を禁止している場合は、設定ファイルの読み取りも拒否される可能性があるため、読み取りを省略して後述の「偽陽性と判断できる拒否への対応」へ進む。

拒否された操作を、有効な`autoMode.allow`の各規則と、対象操作、適用条件、および信頼境界の包含関係に照らし合わせて確認する。該当するallow規則が存在する場合、拒否本文に記載された分類名との文面上の完全一致にかかわらず、その拒否は偽陽性である可能性が高い。なお、`hard_deny`に該当する操作は偽陽性対応フローの対象外とする。

権限の評価は、permissionsルール（deny → ask → allowの順で最初に一致したものが適用）、作業ディレクトリ内編集などの自動承認、auto mode classifierの順序で実行される。PreToolUseフックの`permissionDecision: "allow"`よりも前にpermissionsの評価が確定するため、拒否ルールに該当する操作は拒否ルール自体の見直しによって解決する。permissions設定によって表示される確認ダイアログの扱いは本書の対象外であり、`agent-basics:writing-standards/references/claude-hooks-output.md`の「PermissionRequest」に従う。

アクセス制御、認証、公開範囲といったセキュリティ設定を変更する操作が拒否された場合は、変更候補の作成、現在の実効値と候補の比較、候補の適用のうち、どの段階が拒否されたのかを特定する（1つのツール呼び出しに複数の操作が含まれていた場合は、候補の作成が拒否されたものとみなす）。候補の作成が拒否された場合は、別の手段で同様の候補を勝手に生成せず、比較が未実施であることを明記した上で、拒否された操作の承認をユーザーへ確認する（委譲先の場合は委譲元へ報告する）。セキュリティ設定の変更は、必ず承認を得てから実施する。比較手段が機能しない場合は、実効値が維持されると断定せず、比較できた項目と未確認の範囲を分けて報告する。

## 既知の誤拒否パターンと対応

次の表は、dotfilesの管理設定`claude_settings_json_managed.json`を導入した環境において、拒否が発生した場合の対応方針をまとめたものである。観測日付や対象バージョンの記録を持たないため、適用する前に実行環境で拒否メッセージ本文および分類名（`claude auto-mode defaults`の出力区分）を取得し、該当するか確認する。

| 拒否される操作 | 分類名 | 対応 |
| --- | --- | --- |
| 自身が作成したHEADへの`git commit --amend` | Git Destructive | 許可ルール`Personal Repository Operations`の対象に含まれる。拒否が解消しない場合は後述の偽陽性フローへ進む |
| MR/PRのマージ（`glab mr merge`・`gh pr merge`等） | Merge Without Review | 同じルールの対象に含まれる。リモートホストによる制限は設けていない |
| リリースワークフローの起動 | `Production Deploy`が有力候補（拒否本文では未取得） | 同じルールが起動および結果確認を対象に含んでいる |
| コンテナーまたはサービスの再作成 | Interfere With Workloads等 | 許可ルール`Reversible Change Approval`が、ユーザー本人の環境で変更前の構成を復元可能な再作成を対象に含んでいる |
| 承認条件の緩和・規範改訂・設定原本変更を含むコミット | Self Modification | 許可ルール`Agent Workspace Writes`の対象に含まれる |
| `atk agents-exit-session`による本人確認済みPIDの停止 | Interfere With Workloads | 許可ルール`Session And Delegation Control`の対象に含まれる |
| 委譲先の状態・終了・通知・結果の観測と受領（`atk agents wait`・`list`・`show`等） | Self Modification | 許可ルール`Session And Delegation Control`が、委譲内容にかかわらず受領済み化および結果・通知ファイルの削除を含めて対象としている |
| 自ら新規に作成したtmuxセッションでの対話CLIの起動し直し、キー入力の送信、画面の取得 | Tmux Self Drive | 許可ルール`Session And Delegation Control`の対象に含まれる。拒否が解消しない場合は後述の偽陽性フローへ進む |
| UWIの回答でエージェントに実行を任せたシステムへの永続的な変更（sudoによるパッケージ導入など） | Unauthorized Persistence | UWIの回答内容は分類器が参照する会話ログ上のユーザー発言には現れない。実行前に個別確認せずそのまま実行し、拒否された場合は設定ファイルの読み取りを省略して後述の偽陽性フローの`AskUserQuestion`へ進む |
| 常時読み込む規範ファイル（`~/.claude/rules/`配下など）の編集 | Self Modification | 許可ルール`Agent Workspace Writes`の対象に含まれる |
| エージェントの誤操作で変わった状態（リポジトリ共通のGit設定など）を変更前の値へ戻す操作 | Modify Shared Resources等 | 許可ルール`Agent Mistake Recovery`の対象に含まれる。拒否が解消しない場合は後述の偽陽性フローへ進む |
| 規範の配布元となるユーザー本人のリポジトリへの読み取り | Self Modification | 同じルールが値を変更しない参照を対象に含んでいる |

- 許可ルールは、リモートホストや由来、所有者による限定を設けず、操作の種別と除外条件によって対象を決定する。
- 必須レビューや必須チェックを回避するオプション（`--admin`、`--force`など）や、リポジトリまたは組織のチェック設定自体を変更する操作は許可ルールの対象外であり、CI Bypass領域として引き続き制限される。
- リリースワークフロー起動時の拒否本文には分類名が表示されないため、`Production Deploy`は初期設定の`soft_deny`との対応関係に基づく有力な候補として扱う。

## 偽陽性と判断できる拒否への対応

auto mode classifierによる拒否は、対象操作の実行そのものを妨げる技術的な遮断であり、UWIへの記録だけでは解消できない。そのため、自律モードと協調モードのいずれであっても、本フローに従って`AskUserQuestion`を発行する。判定器が拒否するかどうかを事前に正確に予測することは難しいため、確認は実際に拒否を観測した後に実施する。偽陽性と判断できる拒否を最初に観測した時点で、同一の操作を再試行せず本フローへ進む。`clears`が成立するのは、ユーザーが拒否を偽陽性であると明示して回答した場合に限られ、単なる作業継続への同意はこれに含まれない。

この手順は、メインエージェントとサブエージェントの双方が発行した操作に適用される。委譲先のエージェントは自身で`AskUserQuestion`を発行せず、拒否メッセージの本文、分類名、拒否された操作と対象を、`agent-toolkit/share/rules-subagent.md`「確認事項の即時通知」に従って委譲元へ報告する。代替手段で作業を進めた場合や、下位の委譲先から拒否を受け取った場合も同様に委譲元へ引き継ぐ。委譲元はその拒否を自身が発行した操作と同様に扱い、設定ファイルやallow規則を確認する。承認が得られた後は、委譲先の状態にかかわらずメイン側で該当操作を直接実行する。

対象となる操作は、`git commit`、`git commit --amend`、`Write`／`Edit`／`MultiEdit`によるファイル編集、外部サービスの設定変更コマンドなど、広く一般的な操作を含む。本フローを適用する条件は、「ユーザーまたは処理中のAWIによってその操作が承認済みであることが確認でき、かつ拒否理由がその承認を反映していない場合」、あるいは「拒否の確認手順において対応するallow規則が存在する場合」のいずれかを満たすことである。なお、`hard_deny`領域の操作は対象外とする。

1. 拒否理由をメッセージ本文で確認し、取得可能な場合は分類名と本文を記録する。`claude auto-mode defaults`の実行自体が拒否された場合は、メッセージ本文を根拠として処理を進める。
2. `AskUserQuestion`を用いて次の選択肢を提示する。確認ダイアログの説明は要点にとどめ、詳細な情報は同一ターン内のテキスト出力に記載する（努力目標。この種の拒否はほとんどが偽陽性であり、確認を簡潔に保つため）。
   - 偽陽性として実行を承認する
   - 該当操作を撤回する
   - サブエージェント側でレビューを完了してから判断する
3. ユーザーから承認を得た場合は、メイン側で該当操作を直接実行する。撤回となった場合は該当操作を取りやめ、委譲された操作であれば撤回の旨と再開条件をサブエージェントへ伝える。レビュー完了を待つ場合は、サブエージェント側でレビューを実施し、完了報告を受領した後に手順1へ戻る。
4. `AskUserQuestion`が未回答のままタイムアウトを迎えた場合は対象操作を保留とし、回答待ちのUWIを記録して代替手段のみで作業を継続する。
5. 未回答の後にUWIへ回答が保存された場合であっても、classifierが認識する承認は同一セッションの対話履歴内で成立する必要がある。そのため、対話内で同じ操作に対する再確認を発行し、回答を受領した後に手順1へ戻る。
6. 手順3または手順5で承認を得て操作を実行したメインエージェントは、同様の承認要求の繰り返しを減らすための許可ルールの追加を検討する。対処の要否や省略の可否は、`agent-toolkit:bugfix`の`references/root-cause-analysis.md`「QとC・Dの区別」に基づいて判定する（委譲先から報告された拒否であってもメイン側が担当し、承認済み操作の実行を優先して完了させる）。
   - 文面作成: 個別の識別子を操作種別へと一般化する。有効な規則との比較には2資料を参照する。まず`agent-toolkit:writing-standards`の`references/auto-mode.md`「カスタムルール追加のワークフロー」を参照する。また`agent-basics:writing-standards/references/auto-mode.md`「カスタムルール記述の注意点」も参照する。既存の規則で完全に対象を網羅できる場合は追記を省略する。
   - 環境固有のルール（マシン構成、ローカル設定、接続先などを前提とする規則）: 配布元ソースの有無にかかわらず、`~/.claude/settings.json`の`autoMode.allow`へ直接追記する。管理ラベルや非推奨文字列と衝突しない独自の文面とする。
   - 汎用的なルール: 文面、根拠、および反映先（`share/claude_settings_json_managed.json`）を記載したdotfiles向けのAWIを、`agent-toolkit:wi-standards`に従って登録する。dotfilesの作業中であっても管理原本をその場で直接編集することは避ける。
   - 今回限りの対応: 明確な一時的条件が存在し、同じ種別の誤拒否が再発しない根拠を示せる場合に限り、ルールの追加を省略できる。過去に発生例がないことや分類に迷うことだけを理由に省略してはならず、前述の判定基準に従って処置を確定する。
   - 追加確認の要否: 作成した文面がすでに承認された操作種別、拒否区分、副作用区分の範囲内であれば、直接追記やAWI登録は追加のユーザー確認なしで実行する（`references/approval-scope.md`の成立済み承認の適用）。許容範囲が広がる場合に限り、文面案、追記先、および拡張される範囲を事前承認としてユーザーへ確認する。
   - 完了報告: 直接追記した場合は、追加したルールの本文、追記先、および設定の取り消し方法を報告する。AWIを登録した場合は、設定配布の通常反映を経て有効になることを説明し、現在の実効設定への即時反映とは区別して伝える。追加を省略した場合は、その一時的な条件と再発しない根拠を報告する。
