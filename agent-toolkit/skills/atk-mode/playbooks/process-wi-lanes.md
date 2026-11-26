# レーンの実行

目的: メインが独立したWIを並行するレーンで処理させて統合を検収し、ユーザーへ複数の成果を短い時間で欠かさず届ける。
選定結果からレーンを作って担当を起動し、計画と統合の準備を受け取り、統合を許可して検収し、失敗から再開してレーンを完了させる手順書である。
複数レーンで処理するメイン（`/atk-mode process-wi`の複数レーン、all-reposで`lane_limit`が2以上の対象）が使い、入力は`pick-wi-check`が保存した選定結果である。
レーン担当側の実行は`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`にある。
統合の入力・manifest・最終化の形式は`agent-toolkit:atk-mode/references/lane-integration-format.md`にある。
終了条件は、全てのレーンの統合結果の検収、AWIの終了か保留への遷移、計画ファイルの保存を終え、資源を終了時の回収へ渡した時点である。

## レーンと資源

1. 目標から始める判断は`agent-toolkit:atk-mode/playbooks/process-wi-selection.md`「明示された目標からの実行」に従う。
2. 選定結果の`着手前に必要なレーン`が全て統合済みのレーンを、統合先の最新HEADからworktreeを作成し、段階をまたいで並行に起動する。
   この欄が無いか空のレーンにも、着手に必要な先行成果はない。
   初回・選定への追加・先行成果の統合後は、最新の選定を `atk run-script pick-wi-check` で確認する。
   要約の「着手条件を満たすレーン」を既存の `start` の成功結果と照合し、担当がいないレーンだけを起動する。
   worktreeの準備は起動成功とみなさず、履歴表示は現在の起動指示に使わない。
   先行工程を正当な理由で次の巡回へ回す場合は、`agent-toolkit:atk-mode/playbooks/process-wi-all-repos-results.md`「結果と終了」の手順10で後続の先行条件を再判定する。
   先行成果が必要な後段は統合完了まで待つ。
   成果不要の読み取り専用準備は`agent-toolkit:atk-mode/playbooks/process-wi-lane-resources.md`「後段レーンの先行調査」で前倒しする。
   `先行レーン`は統合許可の順序に使い、着手を止めない。
3. レーンの専用worktree・branch・所有登録の作成・再利用・回収と、`レーン開始時刻`の記録は`agent-toolkit:atk-mode/playbooks/process-wi-lane-resources.md`に従う。
4. 同じレーンのAWIは選定結果の依存順を守り、依存先から処理する。
   計画が必要なレーンは原則1つの計画にまとめ、単一の計画では行数の警告を避けられない初回の起草に限り`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`「計画の起草」の手順4で分ける。
   依存先の失敗や保留、上流投入の完了待ちで必要な成果が得られなければ後続を依存待ちにし、成果を得てから再開する。
   ユーザーの明示の指示で加わった依存先が既存のレーンにある場合は、`agent-toolkit:atk-mode/playbooks/process-wi-additions.md`「処理中に確定した必須是正レーン」で受け入れられるかを確かめ、同じレーンで受け入れられない項目は待つ。
5. 稼働中が1レーンになっても、未着手のAWIは同じレーンで続ける（移管には計画の分割と書き込み所有権の移譲が必要であり、`agent-toolkit:atk-mode/references/plan-format.md`の計画凍結に反するため）。
   単独稼働の短縮は選定時の段階設計で図る。
6. レーン担当を起動する時は、`agent-toolkit:atk-mode/roles/exec.parent.md`が定める入力を渡し、担当の境界も確かめられる既存の`選定結果の出力先ファイル`を渡す。
   - 境界や先行条件を更新した場合は、関係する担当へ既存の追送の経路で更新した選定結果の所在と差分を渡す（既存の追送で足り、専用の同期状態や常時のポーリングは不要である）。
   - `所有登録`、`統合先worktree`、`統合先branch`、`レーン開始時刻`は初回の起動の入力で渡し、起動の直後に追送しない（`レーン開始時刻`の記録は`agent-toolkit:atk-mode/playbooks/process-wi-lane-resources.md`手順6）。
   - 初回の起動時の各AWIの再開位置は選定結果から読ませ、担当全体の`再開位置`は中断した同じ担当を再開する場合だけ渡す。
   - 各WIの`担当モデル.実装担当`がユーザー由来なら、同じレーンの`start`へ`model_type`として渡す。
     指定のある場合は`model_override=user_instruction`と、WIのファイル名・ユーザー由来である根拠を入れた`model_override_evidence`も`start`へ渡す。
     指定値と実行値の比較と記録は`agent-toolkit:atk-mode/references/model-settings.md`「工程別モデル設定」に従う。
     異なる指定が混ざる場合はWI名と設定値を明示し、どの指定も独断で捨てずに起動する。
   - 初回の`書込担当一覧`は合意済みのセッションIDと担当範囲から確定する。
     担当が自分のIDを加えるので、起動の結果の`session_id`は自分の記録の一覧へ加えるだけで追送しない。
     担当の交代・再開・追送の時は一覧を更新して渡す。
     一覧と対象worktreeの対応は引き継ぎ記録へ保ち、統合するレーン担当が同じ認可の集合を使えるようにする。
7. 準備した専用worktreeと処理対象WIを各1名のレーン担当へ渡し、レーン数によらず`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`を適用させる。
   工程の境界も同じ契約で続け、メインは`agent-toolkit:atk-mode/playbooks/lane-receipt.md`「統合の許可」に従って統合許可を送る。

## 各段階の全初期通常レーン起動後の会話圧縮

1. レーンの起動に伴って会話圧縮を一律に手動で予約せず、その時点で並行して始められる独立の工程を先に進めて通常の待機へつなぐ。
   圧縮は`agent-toolkit:atk-mode/playbooks/host-claude-code.md`「長時間待機前の会話圧縮」の条件付きの自動予約とホスト標準の圧縮に任せ、既存の予約があればそれを使う。
2. 圧縮後の復元では、注入された要約を起点に、要求と認可を原本から確定し、必要なら次の記録と照会から情報を得る。
   - 選定YAMLから各レーンの段階、統合状態、後段の先行統合条件を読み、担当の`session_id`と稼働状態は起動結果と`atk agents list --include-terminated`を対応付ける。
   - 資源の登録から専用worktreeとbranchの所有主体と回収の対象を、自分の引き継ぎ記録から各レーンの`レーン開始時刻`を得る（所有登録の`lane_started_at`は最初の統合の後に同じ値を持つ）。
   - 担当の起動入力が示す引き継ぎ記録と保存出力から、各レーンの計画の集合と保存状態、次に受け取る工程の境界を確かめる。
3. 統括が同じ会話IDで本人再開した時も、引き継ぎ記録と既存の照会で手順2の状態を復元し、最初の `atk agents wait` で同じ待機対象の通知と未受領の結果を回収する。
4. 通知を受けたら、確かめた段階と担当の`session_id`で`agent-toolkit:atk-mode/roles/exec.parent.md`の該当する受領の工程へ戻る。

## 実装とレビュー

実行レビューを起動するのはレーン担当自身であり、メインは`agent-toolkit:atk-mode/playbooks/lane-receipt.md`の受領と検収を担う。

1. 計画準備の返却の扱いと、待機が要る場合の`実装開始`か`実装なし`の返却は`agent-toolkit:atk-mode/playbooks/lane-receipt.md`「計画準備の受領」に従う。
   実行レビューとユーザビリティレビューの起動、指摘の修正、収束の判定は、レーン担当が`agent-toolkit:atk-mode/playbooks/exec-review-loop.md`で行う。
   メインは`agent-toolkit:atk-mode/playbooks/lane-receipt.md`の受領を担う。
2. 実行レビューの前に、取得版、入力・環境の条件、要求別の観測箇所を`agent-toolkit:atk-mode/playbooks/verification.md`の検証記録へ対応付けさせる（保存済みの結果の適用の可否と追加の観測の範囲は実行レビュー担当が判断する）。
3. 選定結果の担当モデル、起動側が解決したモデルの候補、終了後の実行ログから観測した実行のidentityを別々の値として比べ、不一致は値と出所を分け、観測できなければ未観測として後続へ渡す。
4. レーン担当から確認事項や事後承認の対象を受けたら、その時点で根拠と既存の認可を確かめる。
   発行の要否は`agent-toolkit:atk-mode/playbooks/confirmation.md`「確認要否の判定」と`agent-toolkit:atk-mode/playbooks/wi-drafting.md`で決める。
   - `足りないユーザー固有の値:`と`対案を選ぶ理由:`を確かめ、欄が欠けるか技術と品質・費用・時間だけで判断できる事項は中継せず、担当へ返すか自分で決める。
     既存の要求から決まらず、ユーザーの選好・費用・範囲・公開契約に依る事前承認だけを受け取り次第発行する。
   - 事後承認型のUWIは、実行レビュー（並行する画面レビューを含む）の収束後かつ統合の前に、最終的な実装と一致する本文で発行する。
     実行レビューしないレーン（`実装なし`と`マージなし`）では、終了の返却を受けた時点で発行する。
   - `atk wi add`の成功と作成したファイル名を記録し、回答を待たずに後続を続ける。
     受け取った全事項の判定と発行を確かめてから統合へ進み、確認が不要な報告は統合と並行して扱う。

## 採否の確定と終了区分

1. 人間由来の要求を全面か部分的に不採用とする場合のレーン担当の判定と通知は`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`「計画の起草」に従う。
   メインは除外範囲、原文との差、技術的な理由、代替案を受け取り、部分採用の除外範囲も含めてユーザーへ確認する。
   確認待ちの間も、回答を待たずに進められる他のレーンの作業を続ける。
   計画の確定後の工程では要求の由来を判定し直さず、計画で確定した採否をそのまま使う。
2. 回答が得られない場合は、`agent-toolkit:atk-mode/playbooks/wi-drafting.md`に従ってUWIを登録する。
   続けて`agent-toolkit:atk-mode/references/states-and-dependencies.md`「承認型UWIと元項目」に従い、元AWIの`depends_on`へUWIを加えて`inbox`へ戻す。
   - UWIの本文には、元AWIのファイル名、対象、必要な回答、回答で分かれる結果を書く。
   - 回答後の再開工程は、計画の有無によらず`atk wi edit <元AWIのファイル名> --append --body-file <絶対パス> --target-repo=<対象リポジトリ>`で元AWIの本文へ追記する。
   - 採否は回答を受けてから決める。
   - 完成条件の観測を求めるUWIは、`agent-toolkit:atk-mode/references/draft-semantic-review.md`に従って原稿と元WIの必要な結果を比べ、元WIの現行の完成条件と再開記録の同じ観測結果へ対応付ける。
     UWIの保存は観測の完了ではなく、回答の受領時にも元の原本と既存の証拠を比べて各完成条件の結果を確かめてから採否と再開を決める。
   - 同じ実行の中で回答が得られたら、実装開始前ならレーン担当が計画へ、実装着手後ならレビュー指摘管理表へ反映し、同じレーンで採否を確定する。
3. 各AWIを次の終了区分へ割り当てる。
   - 採用した要求を実装して完成条件を満たした項目と、既存の実装で要求と完成条件が満たされていることを現物で確かめた項目: `adopt`
   - 必要な採否の手順を経て不採用と確定した項目: `reject`
   - 回答を保存した事前承認型のUWIと、その回答を反映して採否を確定した元AWI: どちらも`adopt`
   - ユーザーの回答待ちの項目: 採否を確定せず、`agent-toolkit:atk-mode/references/states-and-dependencies.md`「状態と依存」の承認型UWIと元項目の規定に従ってUWIへの依存を保存し、`inbox`へ戻す
   - 外部環境の状態変化や上流の変更を待つ未実装の項目: `agent-toolkit:atk-mode/playbooks/process-wi-observation.md`「延期adoptと反映後の観測」の境界に従ってUWIか依存・冷却を設定し、`inbox`へ戻す
   - 実装と統合を終え、反映後の完成条件の観測だけが残る項目: 延期`adopt`とし、観測のみの再開で確かめた結果で次のように扱う（再開記録の追記、状態の変更、計画への記録は同節に従う）。
     - 直接観測で完成条件を満たしたと確かめた場合: `adopt`
     - 直接観測で完成条件を満たせなかったと確かめた場合: `adopt`せず、観測結果を使って同じ巡回で計画から再開し、既存の実装の未達部分を直す。
       担当側で満たせない外部の前提で再開できない場合だけ、試した代替手段と結果を記録し、`再開区分: 再実装へ戻す`を追記して終了区分を`終了しない`とする。
     - 観測自体ができなかった場合: 採否を確定せず、レーン担当が返した観測結果（理由、足りない前提、前提を妨げる状態の生成元・起動元・起動の契機、未確定の範囲）から、残る完成条件ごとに前提を整えられる主体を判定する。
       エージェント自身や常駐のプロセスが作った状態は、OSの実行ユーザーが同じでも人間が起動した状態と区別し、生成元が未確定の状態や人間が通常操作する入力・環境であることだけを根拠にユーザーの操作が必要と判定しない。
       自己操作の手段が成り立つかは同節の自己観測の判定で確かめる。
       - 自分で前提を整えて次回観測できる項目（プロセスの起動、反映待ちなど）: 実装を繰り返さずに観測へ回し、確認の要否・解決の見込み・統括への通知・UWIの保存と依存か冷却による差し戻しは同節に従う。
       - ユーザーの操作か追加の認可が無ければ前提を整えられない項目（実行ログでユーザーが起動したと確かめたセッションの停止など）: 自己操作が成り立たないと確かめたものに限り、完成条件を変えずにUWIで前提の充足を求める。
         元AWIは`agent-toolkit:atk-mode/references/states-and-dependencies.md`「承認型UWIと元項目」に従って`depends_on`へUWIを加えて`inbox`へ戻す。
         操作の主体と選択肢は`agent-toolkit:atk-mode/references/uwi-format.md`「ユーザーへ操作を依頼する場合」で振り分ける。
         UWIには、必要な操作か認可、観測結果、足りない前提、自己操作が成り立たない根拠を書く（同じ観測を無批判に次回へ回すと、前提が整わないまま同じ処理を繰り返すため）。

   複数の要求を持つAWIは、採用した要求を全て満たし、未解決の保留が残らない場合だけファイル全体を`adopt`する。
   固有の指示が公開工程の後の`adopt`を求める項目と、版数の更新を完成条件に持つ項目は延期の対象とする。

## 統合とAWI終了

1. 統合前の確認は`agent-toolkit:atk-mode/references/review-record-acceptance.md`「統合時の完成条件判定」に従う。
   レーン担当が完遂できる未達は、`agent-toolkit:atk-mode/playbooks/delegation-receiving.md`「終了と報告内容の確認」に従って同じ担当へ戻す。
   レーン担当へ任せる統合の指示と、メイン自身がCLIを実行する場合の検収は`agent-toolkit:atk-mode/roles/exec.parent.md`と`agent-toolkit:atk-mode/playbooks/lane-receipt.md`に従う。
   内部の群の焦点の検証は`agent-toolkit:atk-mode/playbooks/implementation.md`「実装」に従う。
   内部の群の独立文脈レビューは`agent-toolkit:atk-mode/references/exec-review-launch-groups.md`に従う。
   レーン担当は群の保存が終わった順にレビューを始め、全群の完了を待つ一斉の初回レビューは避ける。
   共有実行枠（`agent-toolkit:atk-mode/playbooks/verification.md`「統合実行ツール経由の起動」）へ群ごとに早く投入し、群別の対象の和集合と元WIの全条件の対応を保つ。
   受理済みの同じ本文・環境・契約の結果は再利用し、全群の取り込み後に群間の契約、共有の生成物、組合せの差分と未達条件を検収してから統合入力を確定する。
2. Git統合、全計画の保存、WIの採否の確定、選定の統合状態の更新は、同じ所有登録から`atk lane integrate`が一括で行う。
   引数とmanifestの作り方は`agent-toolkit:atk-mode/references/lane-integration-format.md`「公開統合操作」に従い、メイン自身が呼ぶ場合も独立文脈のレビューと完成条件の判定を通った同じ入力を使う。
   同じ統合先へのレーンの間で直列になるのは統合先HEADの一致の確認とfast-forwardの短い更新だけであり、先行レーンに列挙したレーンの`統合完了`を待ってから統合を許可する。
   先行レーンにないレーンの最終化の完了を待つ必要はない（`agent-toolkit:atk-mode/playbooks/lane-receipt.md`「統合の許可」）。
3. 終了コード0、`phase: finalized`、統合結果のOID、計画ファイルの保存、終了対象のAWIの採否の確定を確かめる。
   `git-integrated`は最終化の途中の状態であり、統合完了とはみなさない。
   `終了しない`項目と延期の対象は採否を確定しない。
   失敗した場合は、同じmanifestとレビュー対象のHEADで、同じ公開操作から未完了の工程を再開する。
4. 統合前の稼働の判定では、所有セッション、合意済みの`書込担当一覧`、統合を呼ぶ担当の委譲元の連鎖を共通の認可の集合とする。
   合意済みの担当は`--writer-session`で指定する（`agent-toolkit:atk-mode/references/lane-integration-format.md`「公開統合操作」）。
   完了済みのレーンと所属WIの記録は、追加の作業に備えて保つ。
5. 資源は`agent-toolkit:atk-mode/playbooks/process-wi-lane-resources.md`「レーンworktreeの扱い」の時点で`atk lane delete`で回収し、ターンごとの`SessionEnd`では回収しない。
   未統合、dirty、稼働中、状態不明、途中で失敗した対象は、理由と残る操作を回収対象の外へ保存し、再実行では残りから再開する。
   セッションのmanaged-tempと証拠のファイルは専用worktreeの中に置かない（回収の対象から外すため）。
   統合済みの判定を確かめてからbranchを`-D`で削除するのは、通常の`-d`がupstreamを基準に判定し、push前のローカル統合を未統合として削除を拒否するためである。
   監査記録は`docs/development/audit-records-wi.md`にある。
   該当の節は「agent-toolkit/skills/atk-mode/playbooks/process-wi-lanes.md「統合とAWI終了」：所有資源の回収：2026年9月3日」である。

### 未回収レーンの再回収

1. 回収結果に未解放と示された委譲セッションは、終了していることと、`atk agents wait`で結果本文を回収済みであることを先に確かめる。
   公開ツールの`stop`は終了結果も破棄し、結果を保つ引数が無いため、結果の回収を必ず先に行う（回収処理が所有者側へ要求する自動解放は未回収の結果を保つ）。
   今後再開しないものは、公開MCPの`agents_server`の`stop(session_id="<回収結果にある委譲sessionのID>")`を呼び、実行中のセッションは終了を待って結果を回収する。
2. 停止に成功して保持の理由が解消したら、対象リポジトリをcwdとして`atk lane delete --session-id <レーン資源を作成した所有sessionのID>`を再実行する。
   `--session-id`は元の回収操作と同じ所有セッションのIDであり（`atk lane delete --list`の所有登録で確かめられる）、`stop`に渡す委譲セッションのIDとは別である。
3. 停止に失敗した場合や外部プロセスの使用が続く場合は、メインが共通の判断で用途と現在の担当を調べ、不要と確定したプロセスは個別に止めて同じdeleteへ戻し、使用中の対象は引き継ぎか終了待ちへ進む。
   保持する場合は、資源と回収結果に加え、保護の対象か残るユーザー判断と再開の条件を報告する。

## 中断レーンの再開

1. `processing`のまま残る項目は、計画に含まれる項目では選定結果の`再開位置`の計画ファイルを使う。
   - 複数の計画がある場合は、引き継ぎ記録に蓄えた全ての計画、対象AWIの集合、進捗、保存状態を突き合わせてから渡す。
   - 計画に含まれない項目は、引き継ぎ記録のAWIの集合、レビュー指摘管理表、`git merge-base <専用branch> <ベースbranch>`と`git log <その結果>..<専用branch>`のcommit履歴から再開する工程を決め、`再開位置: 計画なし`として渡す。
   - 計画ファイルか引き継ぎ記録の実在、AWIの集合の一致、専用worktreeの元の所有主体の終了を事前に確かめる。
   - 反映後の観測だけが残る項目は、AWI本文へ追記された再開記録に基づき、選定結果の観測のみの再開位置をそのまま渡し、レーン担当は`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`「再開」に従って観測だけを行う。
   - 再開するレーンへ回答済みのUWIや、依存の解消・`unhold`で`inbox`へ戻った元の項目を渡す場合は、渡す前に`atk wi show --pull`で状態を照会する。
     `inbox`の項目は`atk wi start-processing`で`processing`へ移す（`agent-toolkit:atk-mode/references/states-and-dependencies.md`「状態と依存」）。
2. 残った専用worktreeとbranchを安全に再利用できる場合は、同じ絶対パス、branch、最新のHEAD、再開位置を新しいレーン担当へ渡す。
   安全に再利用できなければ、既存の成果を損なわないよう最新のHEADから新しい専用worktreeを作り、移した内容を`atk run-script plan-progress -- <引き継ぎ記録> --handoff`で引き継ぎ記録へ記録する。
   未commitの差分は所有と実態を確かめ、権限の範囲で成果を損なわずに戻すか移す作業は自分で行う。
   他者やユーザーの差分の破棄と、所有を確定できない成果を失うおそれのある処置は、`agent-toolkit:atk-mode/playbooks/confirmation.md`「確認要否の判定」に従って事前に認可を求める。

## mergeの競合

レーン担当が、`agent-toolkit:atk-mode/references/lane-integration-format.md`「マージありの統合」でmergeが競合で止まった時の手順である。

1. 専用worktreeの両親のOID、競合したパス、両側の要求と解消の方針を確かめる。
   所有する要求の範囲で解消できれば同じレーン担当が続け、追加の判断か認可が必要な単位だけを委譲元へ返す（対象worktreeとmerge進行中の状態と必要な判断を伝え、未保存の成果は保つ）。
2. 両側の要求を保って競合を解消し、解消したパスだけをステージングして通常のmerge commitを作り、既存の実装のOIDはそのまま保つ。
   - 解消で生じた変更と影響するAWIを、`agent-toolkit:atk-mode/references/commit-wi-records.md`「WI実装commitの対応」に従ってmerge commitへ対応付ける。
   - 競合箇所、解消の方針、変更内容、影響範囲を`atk review-table add`でレビュー指摘管理表へ1行加え、同じ行に`atk review-table respond`で解消内容を書く。
     行の書き方は`agent-toolkit:atk-mode/references/review-record-table.md`「レビュー指摘管理表の共通操作」に従う。
   - 影響する利用側を検証し、解消差分の再レビューは`agent-toolkit:atk-mode/references/exec-review-head-update.md`で要否を確かめる。
3. クリーンな現在のHEADを`--reviewed-head`へ渡し、受理済みのレビューとcommitの対応を反映したmanifestで、`atk lane integrate`へ`--restart-review`を付けて再開する。
   統合先がさらに進んだ場合も同じ節に従って追加でmergeし、その影響だけを検収する。

メインがmerge競合の判断を受け取った場合は、レーン担当が確定できる解消を一律に止めず、必要な判断と解消差分の独立文脈レビューを区別して、次のように扱う。

1. 同じレーン担当のthreadへ競合の解消を指示する（別の修正担当を起こさずに同じ担当で解く）。
   同じthreadを続けられない場合は、`agent-toolkit:atk-mode/playbooks/delegation-recovery.md`「継続と新規起動」に従い、新しいレーン担当へ`再開位置`として`~/.claude/plans`配下の計画ファイルの絶対パスを渡す。
2. レーン担当は本節の手順1〜3を同じthreadで終える。
3. 新しいHEADで受理した証拠とmanifestを保ち、`agent-toolkit:atk-mode/playbooks/lane-receipt.md`の統合前の検収に従って再開させる。
   既存の実装OIDの書換えや旧新の対応表は求めない。

## 統合後の変更範囲の検証の失敗

レーン担当が、mergeの後の変更範囲の検証が失敗した時の手順である（是正の着手前に`agent-basics:bugfix`を起動する）。

1. レビュー済みのHEADでは再現せず、統合先との組み合わせでだけ起きた失敗: 失敗した検証が確かめる契約の送信側・受信側・実装・テストを整理し、専用worktreeに是正commitを作る。
   是正で変えた定義・文言・構成・実行前提の変更前の値を固定文字列で探し、それを消費する実装・検査・テストを変更範囲の検証の対象へ戻す（`agent-basics:check-execution/references/verification-scope.md`「範囲の決め方」）。
   是正前のHEADで得た成功は是正の検証に使わず、是正後のHEADで変更範囲の検証を再実行して、成功と、処理を妨げる警告が無いことを確かめる。
   commitと検証の証拠（警告の判定の根拠を含む）を「続行できない理由」としてメインへ返し、再指示を受けたら`agent-toolkit:atk-mode/references/lane-integration-format.md`「マージありの統合」へ戻る。
2. 是正対象の失敗がレーンの変更と同じ原因を持つ場合: 是正の範囲と完了の判定は`agent-basics:bugfix`の問い5（類似見直し）と問い7（確認）に従い、認可範囲外の対象を含めば次の手順で伝える。
3. レビュー済みHEADで再現する失敗: コマンドと出力を保持し、`agent-basics:bugfix` で調査・認可内の是正後に手順1と同じ方法で検証する。
   認可外・未回復分の影響・試行・再開条件だけを `続行できない理由:` に書く。

メインが是正commitを受け取った場合は、失敗した検証が対象とする契約（送信側・受信側・実装・テスト）の差分と、再検証の終了コードを確かめる。
警告はレーン担当が`agent-toolkit:atk-mode/references/lane-integration-format.md`「検証結果の警告の判定」で行った判定と根拠を受け入れ、処理の成立に影響しない既存の警告だけを理由に再度の受理確認を求めない。
確かめたHEADを明記して同じthreadへ統合の再開を指示し、契約外の差分や検証の不足があれば不足箇所を伝えて修正を求める。

## レーンの変更範囲の検証の失敗

レーン担当が、引き継ぎ記録で確定した変更範囲の検証コマンド（旧形式の計画では`## 検証`の`変更範囲の検証`行）の失敗に当たった時の手順である。
`agent-basics:bugfix`を起動し、比較の検証で失敗の原因の所在を特定してから、認可された範囲で直す。

1. 失敗を既存の不具合、変更範囲外の要因、環境差に帰する前に、起動コマンドの形式、入力、並列度、実行環境をそろえて、ベースブランチと専用worktreeの両方で結果を比べ、それぞれの結果と残る条件の差を検証結果に書く（危険な副作用がある場合は安全な同等の条件と残る差を書く）。
2. 原因と影響範囲を特定し、認可された範囲で直して再検証する。
3. 認可範囲外の既存不良が検証を妨げる場合は、観測した失敗、直接の原因、直す対象と方針をメインへ通知し、指示を待つ間もその修正が不要な他の実装を続ける。
   認可を得た修正は計画に基づく実装と別のcommitに分け、commitメッセージの本文に計画外の既存不良の修正である旨と理由を書き、引き継ぎ記録へ認可の出所、修正対象、一意な短縮OIDを記録する。
4. 計画に基づく実装と認可された修正の両方を終えてから、変更範囲の検証を1回実行する。

## レーン完了

1. 全てのレーンについて、統合結果の検収、AWIの終了か保留への遷移、計画ファイルの保存を終え、資源を終了時の回収へ渡す。
   追加した処理対象WIは、元の選定結果へ反映した分だけを含める。
2. `統合完了`を検収して引き渡したレーンへは、メインの新しい指示なく変更を足さない。
   追加と再統合はメインの指示で行う（検収した成果を後から黙って変えると、検収と公開の前提が崩れるため）。
3. 最終の `統合完了` を検収し、その担当を再開しないと決めたら、担当の `session_id` を指定してagents_serverの `stop` で破棄する。
   計画作成や統合準備などの中間返却では破棄しない。
4. 公開工程へは、`agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`冒頭の開始条件が成り立ってから進む。

返す報告に含める項目: レーンごとの統合結果（`phase: finalized`と統合後のHEAD）、採否と延期、保存した計画、回収と保持の結果、残工程と再開先。
