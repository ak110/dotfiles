# AWIの単一レーン処理

メインエージェントがAWIを取得し、計画、実装、実行レビュー、採否、公開、報告までを1つの流れで自分で行う手順書である。
`/atk-mode process-wi single-lane`の手動起動と、all-reposで`lane_limit`が1以下の対象を処理するメイン（統括かリポジトリ担当）が使う。
キューと対象worktreeを読み書きできるファイル操作、Gitと`atk`のCLI、独立したレビュー担当の起動と結果の受領がそろった環境で行う。
これらが無い環境では、提示されたWIの実施内容と計画の原稿を`agent-basics:writing-standards`に従って整え、未実施の実装・レビュー・公開を明示して、実際の処理はClaude CodeかCodexに任せる。
終了条件は、全対象の採否と公開を終え、本書「実行順」手順9の保存状態の確認を通して報告か返却を終えた時点である。
工程ごとの責務と受け渡しは`agent-toolkit:atk-mode/references/workflow-phases.md`、WIの共通の契約は`agent-toolkit:atk-mode/playbooks/wi-drafting.md`にある。

## process-wiの規定の読み替え

all-reposから受け取った候補を処理する場合は、`agent-toolkit:atk-mode/playbooks/process-wi-all-repos.md`「取得済み候補の受領」の確認結果を引き継ぎ、受け取った候補・readiness・対象別の選定の対応を保つ。
候補が1件でも同節の着手可否と選定を適用する。

複数レーン向けの規定は本処理の間も適用するが、レーン担当・公開担当・専用worktreeを前提とする契約は直接適用せず、同等の工程をメイン自身が担う。
直接適用しない文書は、レーン担当と公開担当向けの資料（本書が明示的に参照する共通資料は適用する）と、次の役割の文書である。

- レーン担当の起動と実行: `agent-toolkit:/share/exec.parent.md`、`agent-toolkit:/share/exec.subagent.md`。
- 公開担当の起動と実行: `agent-toolkit:atk-mode/roles/session-termination.parent.md`、`agent-toolkit:atk-mode/roles/session-termination.subagent.md`。

- `agent-toolkit:write-plan`を起動した場合は、ユーザー確認の要否と手段を`agent-toolkit:atk-mode/playbooks/confirmation.md`で選び、計画の起草後は本書の実行順へ戻って主作業ツリーで実装する。
- 計画のstemは`dd-HHmm_process-wi-single-lane`とし、実行レビューのレビューイーはメインとする。
- Codex環境では、`agent-toolkit:write-plan/references/codex-runtime.md`が専用処理に定めるUWIの記録と暫定判断の規則を本処理にも適用する。
- 版数は、対象リポジトリの版数管理の規範のうち単一worktree向けの一般原則に従い、実装の段階でメインが更新する。

## 処理の振り分け

計画が要る項目を「計画対象」、要らない項目を「直接実装対象」として、本書「実行順」手順1の対応表へ区分を書く。
区分を書く前に`agent-toolkit:write-plan`の`SKILL.md`「計画ファイルの作成要否」を取得し、WIごとに同節の4つの事項（何をどう変えるか、外部から観測できる仕様、完成を判定する条件、バグ対応では確定した原因分析）を本文と現物に照らして判定する。
計画を省略する項目は、区分の欄に省略の条件を満たす変更内容を短く併記する。
例えば「直接実装、4事項は本文で確定: `X`の初期値を`a`から`b`へ変える」と書く。
定義を取得しないまま付けた「直接実装」の区分は、手順3の計画の省略と手順4の直接実装の根拠に使わない（過去に、定義を読まずに付けた区分のまま計画を省いた誤りがあったため）。
保存済みの対応表をもとに、対象worktreeの全ての計画対象を1つの計画ファイルへまとめ、計画段階のレビューは省いて実装後の実行レビューで確かめる。
専用のworktreeは作らず、対応表のworktreeで実装する。

## 不変条件

- メインが作業ツリーの書込を所有し、一部の書込を委譲先へ分担させる場合は`agent-toolkit:delegation/references/mandatory-rules.md`「作業ツリーの保護」に従う。
- 実行レビューは、実装を担当していない独立した担当が行う。
- 人間由来の変更で不採用とする範囲がある場合は、ユーザーの確認を得てから終える。
- 処理対象のWIは手順1で固定し、途中の追加はユーザーの明示の追加と、`agent-toolkit:atk-mode/playbooks/process-wi-immediate-fix.md`「即時対応」で今回追加と判定した正式改善AWIに限る。
- 同じ対象worktreeに属する全ての計画対象と直接実装対象の和を1件の実行レビューにまとめ、異なる対象worktreeの項目を同じ計画や実行レビューに混ぜない。

## 見つけた既存不良の扱い

追加の操作を選ぶ前に、発話と発生条件を`agent-toolkit:atk-mode/playbooks/process-wi-immediate-fix.md`「即時対応」の分類に当てはめ、ユーザーの是正要求、今回混入した不良、同じ実行への明示の追加を区別して手順を選ぶ。
直接修正の担当・基点・時機は同書「レーンの変更範囲へ加える修正の認可」に従い、統合後の独立した修正は統合先の担当が現行のHEADで行う。

1. 実行レビューから返された差分外の既存不良は、`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`「継続する工程」の受領の契約で専任のWI投入担当へ渡す。
   対処の時機は「即時対応」で判定し、今回追加とした正式改善AWIは既存の対応表へ加え、同じworktreeで凍結済みの計画を保って追加の計画・実装・独立文脈レビューへ進む。
   保存済みのAWI名、対応表、追加の計画、是正の結果を同じ引き継ぎ記録へ対応付け、実行全体の終了前に処置を検収する。
   元のレビューの収束やAWIの保存だけを是正の完了とせず、共通の先送り条件に当たる場合は事実・残作業・再開条件を保つ。
2. 公開工程（手順8）の開始前に見つけた不良は、観測した事象、進行への影響、直接修正する範囲を確かめる。
   必要な修正の後、観測結果、差分、検証結果、未完了の正式対応を`agent-toolkit:atk-mode/roles/add-wi.parent.md`の要求として渡し、`source: process-wi`のAWIとして保存する。
3. 今回の作業で混入させた不良と、初期選定した要求に属する類似箇所は、元の要求の範囲で是正する（独立した改善の追加は同書の対処時機の判定を経る）。
4. 公開工程の開始後に見つけた不良も、同書「対象外の不良と是正要求」に従い、公開を妨げる不良の修正と次のセッションの停止・遮断へ即時に対応する。
   独立した不良は同書「即時対応」で正式対応の時機を判定して委譲し、追加統合後の再公開は`agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`「公開状態の検収と再起動」に従う。

## 実行順

計画、実装、実行レビューの進行と、起動と返却後の検収、`検証結果`の記録は`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`の契約に従う。
本書は対象の取得、worktreeの対応付け、公開を定める。

1. 取得と対応表:
   - 処理可能なWI（processable）の一覧と各WIの本文を取得する。
     初回の一覧に`--snapshot-output <開始時集合JSONの絶対パス>`を付け、all-reposから受け取った場合は既存の`snapshot_file`を使う。
     30分ごとの点検と公開前の新着の確認は`agent-toolkit:atk-mode/playbooks/process-wi-immediate-fix.md`「開始後に追加されたWIの確認」に従い、同じ開始時の集合と判定済みの記録を保つ。
   - `atk wi show --all --type=uwi --answered=yes --status=processable --target-repo=<対象リポジトリの絶対パス> --target-branch=auto`で回答済みのUWIの本文も取得する。
     frontmatterに`run_skill_report: true`を持つ報告用UWIだけを除く（次回の同じ起動単位の`atk run-skill`が扱う）。
     `source: run-skill`の承認用UWIも対象にし、扱いは`agent-toolkit:atk-mode/playbooks/process-wi-selection.md`「処理対象の決定」に従う。
     回答済みのUWIは実装前に`agent-toolkit:atk-mode/references/states-and-dependencies.md`「状態と依存」に従って終えるか処理対象へ加える。
   - 依存が未解決の項目は、依存元のWIがprocessableで同じ`target_repo`を対象とする場合だけ今回の実行に含めて報告する。
     依存元の状態変更や集合からの除外が要る場合は、`agent-toolkit:atk-mode/playbooks/confirmation.md`「確認要否の判定」で確認の要否を決める。
   - 対象worktreeごとに公開対象とベースブランチを明示し、`agent-toolkit:atk-mode/playbooks/process-wi-selection.md`「処理対象の決定」の開始時のCI点検をする。
     実装に着手する前に実際のCIの状況を照会し、失敗ログを取得して残工程への影響を判定し、既存の失敗は本書「見つけた既存不良の扱い」で扱う。
   - 本書「処理の振り分け」に従って計画の要否を判定してから、各WIの次の値を対応表にまとめる。
     ファイル名、保存された`target_repo`、Git操作に使うworktreeの絶対パス、そのworktreeの処理開始時のHEADの短縮OID、開始前の状態、区分（直接実装では省略の条件を満たす変更内容を併記）、完了済みの工程と残工程である。
     処理を始める前に全件を既存の引き継ぎ記録へ保存し、後続の工程の比較の基準にする。
   - 同書「集合確定直後の処理開始」に従い、`atk wi show`で保存された`target_repo`と対応worktreeのoriginの一致を確かめる。
     不一致は同書「処理対象の決定」の移転の判定と編集の規則に従い、移転の根拠が得られなければ両方の識別子と足りない根拠を報告し、移転が確定したら処理開始前に保存値と本文中の操作先をそろえる。
   - 初回の`atk wi start-processing <ファイル名>... --target-repo=<対応worktreeの絶対パス>`には、対応表のうち開始前に`inbox`だった全件をworktreeごとに渡し、処理開始済みの項目は重ねて遷移させない。
     中断した実行を再開する場合と、回答済みUWIや依存の解消・`unhold`で`inbox`へ戻った元の項目を加える場合も、作業の再開前に`atk wi show`で対応表の全件の状態を照会し、`inbox`の項目を同じ操作で`processing`へ移す。
     途中で失敗した場合は、CLIの診断と残った状態を同じ対応表に記録し、未完了の工程から再開する。
   - ユーザーの明示の追加と、本書「見つけた既存不良の扱い」で今回追加とした正式改善AWIは対応表へ反映して比較の基準を更新し、集合の固定後の追加は`agent-toolkit:atk-mode/playbooks/process-wi-additions.md`「処理対象WIの追加」の基準で扱う。
2. `atk wi`の`adopt`や`reject`に`--commit`を渡す場合は`--target-repo`に対象worktreeの絶対パスを、それ以外の操作では保存された`target_repo`を使う。
   Gitの起点の比較、実装、検証、commit、レビューには対応表のworktreeと開始時のHEADを使い、別のworktreeや複製元のHEADで代用しない。
3. 計画対象があれば、対象worktreeごとに`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`「計画の起草」で計画を起草する。
   計画メタ情報の対象リポジトリと計画構造の自動チェックの`--work-dir`にはそのworktreeを指定し、対応表の全ての計画対象を`--expected-wi <WIファイル名>`で繰り返し指定して、実装前に過不足なく一致することを確かめる。
   全件が直接実装対象なら計画ファイルを作らず、対応表とレビュー対象の集合の和で網羅を検収する。
4. 対応表のworktreeで、計画対象は計画の`## 反映内容と反映先`と`## 完成条件`に従い、直接実装対象はWIの要求と完成条件に従って実装する。
   - 8節形式の計画では、`agent-toolkit:write-plan/references/legacy-plan-file-standards.md`「8節形式」の対応表で節を読み替える。
   - 完成条件の具体化、検証単位の選定、検証結果の保存、独立文脈レビューへの受け渡しは`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`「継続する工程」に従う。
   - Git管理外のファイルを変える場合は、最初の変更の前に変更前の複製を作り、対応を計画の`## 進捗ログ`か直接実装の引き継ぎ記録に残す。
   - commit履歴の整理で取得時の版が変わった記録の再利用は、`agent-toolkit:/share/exec-review-recording-grounds.md`「根拠の書き方」に従う。
   - 互いの成果に依存しないworktreeの間では作業を並行して進めてよい。
   - commitは`agent-toolkit:commit`で作り、WI実装commitごとに、計画（直接実装では引き継ぎ記録）と同じstemの対応記録ファイルへ記録する。
     記録の手段は`agent-toolkit:commit/references/commit-wi-records.md`「WI実装commitの対応」に従う。
     レビュー指摘の修正、CI修正、commit履歴の変更の後も同じ手順に従い、手順8の前に最新の対応を取得して`adopt`へ渡す。
5. 保存済みの対応表から、対象worktreeに属する全ての計画対象と直接実装対象の和を導き、1件の実行レビューを起動する。
   全ての対応表の所在と区分は同じ引き継ぎ記録から辿れるように保つ。
   計画対象があれば計画ファイルの絶対パスを、直接実装対象があればWIの記録を委譲プロンプトへ渡し、計画対象が無ければそのworktreeの開始時のHEADを渡す。
   `cwd`、`開始時のHEAD`、レビュー指摘管理表、レビュー基準には同じworktreeの値だけを使う。
6. worktreeごとに`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`「継続する工程」に従って指摘を収束させる。
   修正はメイン自身が行い、同じ開始時のHEADとレビュー指摘管理表を使い続ける。
7. 各計画について`atk run-script plan-progress --`で完了の判定を`## 進捗ログ`へ記録し、計画構造の自動チェックの成功を確かめてから計画バンドルを保存する。
8. 採否と公開:
   - 各WIを`adopt`か`reject`し、`adopt`では`agent-toolkit:atk-mode/references/states-and-dependencies.md`「状態と依存」のcommitの対応付けを使う。
     採否は公開前に確定し、CIの待機の後へ延ばさない。
   - worktreeごとに`agent-toolkit:commit`を起動し、`agent-toolkit:commit/references/publish.md`に従って公開する。
   - 複数のリポジトリが対象なら成果物間の依存順を守り、互いに独立したリポジトリのpushを先に全て終えてからCIの監視を並行して始め、全ての実行識別子の終了を待って結果を個別に回収する。
     待機中の操作は`agent-toolkit:delegation/references/waiting-and-monitoring-independent-work.md`「待機中の先行実施」で決め、CIの成功を前提とする公開後の操作はその対象の成功後に行う。
   - 開発マシンで動く常時稼働サーバーへの反映は、`agent-toolkit:report-completion/references/server-reflection.md`の条件を満たす場合にCIの待機と並行して始める。
     反映したHEADの一意な短縮OID、反映コマンドの終了ステータス、稼働確認の結果を完了報告へ渡す。
   - Claude Codeでpush後のCIの監視を待つ前に、`agent-toolkit:delegation/references/waiting-and-monitoring.md`を読む。
     CIの待機のために会話圧縮を一律に手動で予約せず、`agent-toolkit:delegation/references/waiting-claude-code.md`「長時間待機前の会話圧縮」の条件付きの自動予約とホスト標準の圧縮に任せる。
     起動済みの`wait_ci.py`のバックグラウンドタスクを保ち、前景を占有せずにターンを終え、完了通知を受けた後に同じ入力と保存出力からCIの結果を回収する。
   - 中断や会話圧縮の後は、注入された要約を起点に計画、WI、資源の登録、CIの監視出力、担当別の引き継ぎ記録から状態を復元し、要約に書かれた作業順などで選定の集合を置き換えない。
   - 公開後に追加の修正を統合して再公開する場合は、`agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`「公開状態の検収と再起動」に従う。
9. 報告か委譲元への返却の前に、`agent-toolkit:atk-mode/playbooks/process-wi-observation.md`「報告前に確かめる保存状態」を適用する。
   初回・直接処置・追加・延期の全対象について、現在のキューと保存済みの採否・再開条件の一致を確かめ、未達は未達として報告する。
   一時的に作ったレビュー指摘管理表は、正式な保存か回収の契約に従って処理する。
   - 明示的に委譲されたリポジトリ担当は、役割の契約に従って成果・検証・公開・反映の結果と残工程を委譲元へ返し、報告用UWI・送達・祖先ホストの終了は担当しない。
   - 自分が報告を所有するメインは、成果・検証・公開の結果を`agent-toolkit:report-completion`の作業完了報告へ1回にまとめて送り、公開結果の受領だけで終えずに同スキルの終了処理まで行う。

返す報告に含める項目: 対応表（区分と省略理由を含む）、計画と実行レビューの結果、採否と公開・CIの結果、反映の結果、残工程と再開条件、保存状態の確認結果。
