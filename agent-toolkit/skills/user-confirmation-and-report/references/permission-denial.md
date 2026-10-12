# 権限拒否への対応

ツール呼び出しが権限設定またはauto mode classifierに拒否された場面の手順を定める。
auto modeのカスタムルールを追加・編集する手順は`agent-toolkit:writing-standards`の`references/auto-mode.md`が定める。

## 拒否の確認手順

拒否を観測したら、別の手段で迂回する前に有効な設定ファイルを`Read`して許可・拒否のルールを確認する。対象は`/etc/claude-code/managed-settings.json`、`~/.claude/settings.json`、リポジトリ直下の`.claude/settings.json`と`.claude/settings.local.json`である。拒否本文が同じ結果の追求を禁じている場合は、設定ファイルの読み取りも拒否され得るため、読み取りを省いて「偽陽性と判断できる拒否への対応」へ進む。

拒否された操作を、有効な`autoMode.allow`の各規則と、対象操作、適用条件および信頼境界の包含関係で照らす。対応するallow規則があれば、拒否本文の分類名との意味上の一致によらず偽陽性の可能性が高い。`hard_deny`に属する操作は偽陽性フローの対象から外す。

権限評価はpermissionsルール（deny→ask→allowの順で最初の一致が確定）、作業ディレクトリ内編集等の自動承認、auto mode classifierの順に進む。PreToolUseフックの`permissionDecision: "allow"`より前にpermissions評価が確定する。このため拒否ルールに該当する対象は、許可の追加ではなく拒否ルール自体の見直しで解消する。permissions設定による確認ダイアログは本書の範囲外とし、`agent-toolkit:writing-standards`の`references/claude-hooks-output.md`の「PermissionRequest」に従う。

アクセス制御、認証、公開範囲などのセキュリティ設定をマージする操作が拒否された場合は、変更候補の作成、現行の実効値と候補の比較、候補の適用のどれが拒否されたかを特定する（1つの呼び出しが複数の操作を含んでいた場合は候補の作成が拒否されたものとする）。候補の作成が拒否された場合は、別の手段で同じ候補を生成せず、比較を未実施と明示して、拒否された操作の承認をユーザー確認で求める（委譲先は委譲元へ返す）。セキュリティ設定の変更は承認を得てから行う。比較手段が成立しない場合は、実効値が保たれると言い切らず、比較できた値と未確認の範囲を分けて示す。

## 既知の誤拒否パターンと対応

次の表はdotfilesの管理設定`claude_settings_json_managed.json`を導入した環境で拒否に遭遇した場合の対応である。観測日付と対象版数の記録を持たないため、適用の前に実行中の環境で拒否メッセージ本文と分類名（`claude auto-mode defaults`の出力の区分）を取得して該当を確認する。

| 拒否される操作 | 分類名 | 対応 |
| --- | --- | --- |
| 自身が作成したHEADへの`git commit --amend` | Git Destructive | 許可ルール`Personal Repository Operations`が対象に含む。拒否が残る場合は後掲の偽陽性フローへ進む |
| MR/PRのマージ（`glab mr merge`・`gh pr merge`等） | Merge Without Review | 同じルールが対象に含む。remoteのホストによる限定は置いていない |
| リリースワークフローの起動 | `Production Deploy`が有力候補（拒否本文では未取得） | 同じルールが起動と結果確認を対象に含む |
| コンテナーまたはサービスの再作成 | Interfere With Workloads等 | 許可ルール`Reversible Change Approval`が、ユーザー本人の環境で変更前の構成を復元できる再作成を対象に含む |
| 承認条件の緩和・規範改訂・設定原本変更を含むコミット | Self Modification | 許可ルール`Agent Workspace Writes`が対象に含む |
| `atk agents-exit-session`による本人確認済みPIDの停止 | Interfere With Workloads | 許可ルール`Session And Delegation Control`が対象に含む |
| 委譲先の状態・終端・通知・結果の観測と受領（`atk agents wait`・`list`・`show`等） | Self Modification | 許可ルール`Session And Delegation Control`が、委譲内容にかかわらず受領済み化と結果・通知ファイルの削除を含めて対象にする |
| 自ら新規に作成したtmuxセッションでの対話CLIの起動し直し、キー入力の送信、画面の取得 | Tmux Self Drive | 許可ルール`Session And Delegation Control`が対象に含む。拒否が残る場合は後掲の偽陽性フローへ進む |
| UWIの回答でエージェントに実行を任せたシステムへの永続的な変更（sudoによるパッケージ導入など） | Unauthorized Persistence | UWIの回答は判定器が読む会話上のユーザー発言に現れない。実行前には確認せずに実行し、拒否された場合は設定ファイルの読み取りを省いて後掲の偽陽性フローの`AskUserQuestion`へ進む |
| 常時読み込む規範ファイル（`~/.claude/rules/`配下など）の編集 | Self Modification | 許可ルール`Agent Workspace Writes`が対象に含む |
| エージェントの誤操作で変わった状態（リポジトリ共通のGit設定など）を変更前の値へ戻す操作 | Modify Shared Resources等 | 許可ルール`Agent Mistake Recovery`が対象に含む。拒否が残る場合は後掲の偽陽性フローへ進む |
| 規範の配布元となるユーザー本人のリポジトリへの読み取り | Self Modification | 同じルールが値を変更しない参照を対象に含む |

- 許可ルールはremoteのホスト、由来および所有者による限定を置かず、操作の種別と除外条件で対象を決める
- 必須レビュー・必須チェックの迂回形態（`--admin`・`--force`等）と、リポジトリまたは組織のチェック設定を変える操作は許可の範囲の外にあり、CI Bypass領域のまま残る
- リリースワークフロー起動の拒否本文には分類名が現れないため、`Production Deploy`は初期設定の`soft_deny`との対応から見た有力候補に留まる

## 偽陽性と判断できる拒否への対応

auto mode classifierによる拒否は対象操作の実行自体を妨げる技術的ブロックであり、UWIの記録では解消できない。このため自律モードでも協調モードでも本フローの`AskUserQuestion`を発行する。判定器が拒否するかは予測しにくいため、確認は拒否を観測してから行う。偽陽性と判断できる拒否を最初に観測した時点で、同一操作を再発行せず本フローへ進む。`clears`が成立するのは、ユーザーが拒否を偽陽性と明示した回答に限り、進行への同意は含めない。

メイン・サブエージェントのいずれが発行した操作にも適用する。委譲先は`AskUserQuestion`を自ら発行せず、拒否本文、分類名、拒否された操作と対象を`agent-toolkit/share/rules-subagent.md`「確認事項の即時通知」で委譲元へ返し、代替手段で進めた場合や下位の委譲先から受け取った拒否も同じく上位へ渡す。委譲元はその拒否を自ら発行した操作の拒否と同じく扱い、設定ファイルとallow規則の確認も行い、承認後は委譲先の状態を問わずメイン側が該当操作を直接実行する。

対象操作は`git commit`、`git commit --amend`、`Write`／`Edit`／`MultiEdit`によるファイル編集、外部サービスの設定変更コマンドなどの開放集合とする。適用条件はユーザーまたは処理中のAWIでその操作が承認済みであると実体確認でき拒否理由がその承認を反映していない場合と、「拒否の確認手順」で対応するallow規則がある場合で、どちらか一方で足りる。`hard_deny`領域は対象外とする。

1. 拒否理由をメッセージ本文で確認し、取得できれば分類名と本文を記録する。`claude auto-mode defaults`自体が拒否された場合は本文を根拠として進む
2. `AskUserQuestion`で次の選択肢を示す。説明は要点に絞り、詳細はターン内のテキスト出力側へ置く（努力目標。この拒否はほとんどが偽陽性であり、確認を簡潔に保つ）
   - 偽陽性として実行を承認する
   - 該当操作を撤回する
   - サブエージェント側でレビューを完了してから判断する
3. 承認ならメイン側で該当操作を直接実行する。撤回なら該当操作を破棄し、委譲された操作では撤回と再開条件をサブエージェントへ返す。レビュー完了待ちならサブエージェント側のレビューを起動し、完了報告の受領後に手順1へ戻る
4. `AskUserQuestion`が無応答のまま期限を迎えた場合は対象操作を保留し、回答を待つUWIを記録して代替手段だけを継続する
5. 無応答の後にUWIへ回答が保存されても、classifierが観測する承認はtranscript内の確認で成立するため、transcript内で同じ操作への再確認を発行し、回答の受領後に手順1へ戻る
6. 手順3または5の承認後に実行したメインは、同種の承認の反復を減らす許可規則を検討する。処置の要否と省略の可否は`agent-toolkit:bugfix`の`references/root-cause-analysis.md`「QとC・Dの区別」で判定する（委譲先からの拒否でもメインが担い、承認済み操作の実行を先に行う）
   - 文面: 個別識別子を操作種別へ一般化し、`agent-toolkit:writing-standards`の`references/auto-mode.md`「カスタムルール追加のワークフロー」と「カスタムルール記述の注意点」で有効な規則と比べる。既存規則が対象を全て含めば追記を省く
   - 環境固有（マシン構成、ローカル設定、接続先などに依存する規則）: 配布sourceの有無によらず`~/.claude/settings.json`の`autoMode.allow`へ直接追記する。sourceの管理ラベルや廃止文字列と衝突しない独自文面にする
   - 汎用: 文面、根拠および反映先`share/claude_settings_json_managed.json`を備えたdotfiles向けAWIを`agent-toolkit:wi-standards`に従って投入し、dotfilesを作業中でも管理原本をその場で編集しない
   - 今回限り: 明らかな一時条件があり同じ種別で再発しない根拠を示せる場合だけ省略する。過去に未観測であることや分類に迷うことは省略の根拠にならず、前段の判定に従って処置を確定する
   - 追加確認: 文面が承認済みの操作種別、拒否区分、副作用区分の範囲内なら、直接追記とAWI投入は追加確認なしで行う（`references/approval-scope.md`の成立した承認の適用）。範囲が広がる場合だけ、文面案、追記先、広がる範囲を事前承認として確認する
   - 報告: 直接追記では規則の本文、追記先、取り消し方法を報告する。AWI投入は、配布側の通常の反映後に有効になることを実効設定への反映と区別して伝える。省略では一時条件と再発しない根拠を報告する
