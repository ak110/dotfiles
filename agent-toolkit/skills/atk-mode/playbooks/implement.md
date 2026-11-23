# 即時実装とマージ用AWIの投入

`/atk-mode implement [入力]`で始める作業の手順書である。
この作業はユーザーの入力が名指しした時だけ選び、協調モードで動く。
AWIの保存までは`add-awi-by-user`と同じ手順で行う。
対話セッション内の隔離worktreeで実装、検証、独立文脈レビューまで終え、AWIは保留のまま保つ。
投入前レビューの結果を待つ間に、隔離worktreeの作成と実装を並行して進めてよい（方針は着手前の合意で定まっており、レビューの修正は実装へ反映する）。
最後にブランチをpushし、AWIをマージ用へ更新して保留を解く。
主作業ツリーへのマージ、ベースブランチへのpush、CIの監視、リリースは、稼働中の`process-loop`と競合しないよう後続の`process-wi`へ任せる。

## 手順

1. `agent-toolkit:atk-mode/playbooks/add-awi-by-user.md`の「手順」と「起草と保存」を、AWIの保存まで行う。
   同書「セッション境界」の成果物の制限は実装に適用せず、投入中の中止は同節の`--abort`に従う。
   新規のAWIは`atk wi add --hold`で保留のまま保存し、既存のAWIを更新する場合は同書の保留を解かずに残す。
2. 同書「起草と保存」の手順4で投入を1回だけ終える（登録0件なら本書はここで終わる）。
   以降の手順は、保留したAWIごとに行う。
   手順3・4は投入前レビューと並行して始めてよく、AWIの保存は手順5の実行レビュー（保留中のAWIを基準に使う）より前に終える。
   レビューで反映内容が変わったら、実装を原稿に合わせて直す。
   差し戻しや保存の失敗を直している間は、隔離worktreeの実装と既存AWIの保留を保ち、実行レビューとpushへ進まない。
   中止した時と、登録0件で終わった時は、実装をpushせず「中止と後始末」に従う。
3. 隔離worktreeと作業ブランチを作る。
   - 先に`git fetch origin <ベースブランチ>`を実行し、`origin/<ベースブランチ>`（系統を登録したリポジトリでは宛先の系統。リモート未設定時はローカルの`<ベースブランチ>`）の最新状態から一時ディレクトリへ隔離worktreeと作業ブランチを作る。
   - ブランチ名は対象プロジェクトの命名規則に従い、規則が無ければ`feat/<短い識別名>`や`fix/<短い識別名>`などの一般的なfeatureブランチ名を使う。
   - 例: `git worktree add -b feat/<短い識別名> "$(mktemp -d)" origin/<ベースブランチ>`。
   - 主作業ツリーは変更せず、対象リポジトリのプロジェクト規範に従って隔離worktree内の環境を準備する（`mise trust`、依存同期など）。
4. 実装し、変更範囲を検証してcommitする。
   - 合意済みの方針を変える時は、`agent-toolkit:atk-mode/playbooks/request-intake.md`の手順7の確認条件を変更後の方針に当てはめ、本人の判断が必要なら変更に着手する前に合意を得直す。
   - AWIの本文と現物から変更内容と完成条件が定まるため、計画ファイルは原則として作らない。
     設計の整理や中断再開の記録が要る大規模な変更に限り、`agent-toolkit:atk-mode/playbooks/write-plan.md`に従って計画を作る。
   - 実装・テスト・変更範囲の検証（`agent-basics:check-execution`と対象リポジトリの検証規範）を行い、`agent-toolkit:atk-mode/playbooks/commit.md`に従って一時ブランチへcommitする。
   - 配布物の版数更新が必要なリポジトリでは、版数の種別と根拠をAWIへ書くか、規範に従って更新する。
5. 独立文脈の実行レビューを受けて収束させる。
   - `agent-toolkit:atk-mode/roles/exec-review.parent.md`に従い、実装を担当していない実行レビュー担当を、レビュー基準`WI`で保留中のAWIを渡して起動する。
   - 指摘を同じ隔離worktreeで修正・再検証してcommitし、未解決の指摘が0件になるまで収束させる。
6. 一時ブランチをリモートへpushし、ローカルの一時資源を回収する。
   - 隔離worktreeがcleanであることを確かめ、`git push origin HEAD:refs/heads/<一時ブランチ名>`でリモートへpushする。
   - pushの成功と完全OIDを確かめた後、`git worktree remove <隔離worktree>`、一時ディレクトリの削除、`git branch -D <一時ブランチ名>`でローカルの一時資源を回収する。
7. AWIをマージ用へ更新して保留を解く。
   - `atk wi edit <ファイル名> --body-file <原稿の絶対パス>`で、frontmatterへ`merge_branch: <push済みブランチ名>`と`merge_commit: <push済みHEADの完全OID>`を加える。
   - 本文は`agent-toolkit:atk-mode/references/awi-body.md`の形式を保ち、`## 反映内容と反映先`へ変更ファイル・変更内容・版数更新の要否を、`## 調査結果と判断根拠`へ検証コマンドと結果および実行レビューの収束結果を書く。
   - 保存した本文の一致を確かめてから、`atk wi unhold <ファイル名>`を実行する。
8. 完了を報告する。
   - `agent-toolkit:atk-mode/playbooks/report-completion.md`に従い、マージ用AWIのファイル名、pushしたブランチ名とcommit OID、検証と実行レビューの結果、回収した一時資源を報告する。

## 中止と後始末

ユーザーが中止した時と、登録0件で終わった時は、作成した隔離worktree、一時ディレクトリ、ローカルの一時ブランチを削除し、リモートへpush済みなら`git push origin --delete <一時ブランチ名>`も実行する。
AWIは保存・保留した項目だけを戻す。
本書で新規に保存して保留中のAWIは`atk wi rm <ファイル名>`で削除し、更新した既存のAWIは、本書のためにinboxから保留した項目だけ`atk wi unhold`で戻し、元から保留だった項目は保留のまま残す。

## マージ用AWIの処理

`process-wi`（複数レーン、`single-lane`、`process-loop`経由の全て）がfrontmatterに`merge_branch`を持つマージ用AWIを処理する時の手順である。

1. 選定では`レーン: なし`へ置き、レーン用worktreeの作成、レーン担当の起動、再実装、再レビューを省く。
   通常レーンと並行する処理では、主作業ツリーへの書込競合を避けてメイン（または対象リポジトリ担当）が直接処理する。
2. fetch前にfrontmatterの`merge_branch`がベースブランチ・デフォルトブランチ・登録済みの系統ブランチ（`main`・`master`・`develop`など）でないことと、`merge_commit`が40桁の完全OIDであることを確認する。
   条件を満たさない場合はfetch・merge・削除のいずれも実行せず、原因を調べて報告し、`atk wi hold`で保留する。
   条件を満たしたら`git fetch origin <merge_branch>`で取得し、`FETCH_HEAD`の完全OIDが`merge_commit`と一致することを確かめる。
   ブランチ不在やOID不一致がある場合もマージせず、原因を調べて報告または保留する。
3. 主作業ツリーのベースブランチへ取り込む。
   - ベースブランチが`merge_commit`の祖先なら`git merge --ff-only FETCH_HEAD`で取り込む。
   - 分岐していれば`git merge --no-edit FETCH_HEAD`で既存の実装OIDを保って統合する。
4. 統合後の検証をする。
   - fast-forwardで取り込み、AWIに記録された検証と実行レビューの結果が現行条件を満たしていれば、重複するローカル検証を省いてよい。
   - 分岐からのmergeとなった場合は、変更箇所と関連する利用側を検証する。
   - 競合または全体検証の失敗が起きた場合は、メイン自身が解消・再検証・実行レビューを進める（解消の規模が大きい場合は通常レーンへ切り替えてよい）。
5. 終了と公開後の後始末をする。
   - `atk wi adopt <WIファイル名> --commit <統合後の対象OID> --target-repo=<主作業ツリー>`でAWIを終了し、取り込んだcommitを他の処理対象WIと同じ通常の公開工程（版数更新、push、CIの検収、リリース）へ合流させる。
   - リモート作業ブランチの削除は、公開工程でベースブランチへの`git push`成功を確認した後に公開担当が実行する。
     削除前に`merge_branch`が共有ブランチでないことと、リモート先端が`merge_commit`のまま変わっていないことを再確認し、`git push origin --delete <merge_branch>`を実行する（同名のローカルブランチがあれば`git branch -D`で削除する）。

## 返す報告に含める項目

登録したマージ用AWIの`ファイル名`、pushした`merge_branch`と`merge_commit`、検証と実行レビューの結果、回収した一時資源、自ら確定した判断。
