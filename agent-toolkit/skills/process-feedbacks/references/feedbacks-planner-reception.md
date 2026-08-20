# feedbacks-plannerの起動と受領

Claude Codeホストで通常型のフィードバックを処理する場合に、メインが調査から計画レビューまでを委譲する委譲元契約を定める。
メインはキュー操作と検収を担当し、`feedbacks-planner`固有の実行手順を起動文へ複製しない。

## 起動

active一覧を取得した時点のreadyな通常型のフィードバックを、対象リポジトリごとの1バッチとして
`agent-toolkit:feedbacks-planner`へ1回だけ渡す。
blocked項目、未回答TBD、一覧取得後に追加された項目は含めない。
`feedbacks-planner`は採用項目を1つの統合計画へまとめ、項目ごとの原文、採否、対象、完了条件、実装単位を識別可能に保つ。

新規`inbox`項目では着手可否の確定後に、同じ対象リポジトリのreadyなファイル名を昇順でまとめ、
`atk mq start-processing <filename>... --target-repo=<repo>`を1回実行し、対象集合と`processing`配置を照合する。
全対象の存在、inbox配置、frontmatter及び`target_repo`一致は移動前に検証する。1件でも不適合なら集合全体を拒否し、どの対象も移動しない。
状態競合で拒否された場合はactive一覧と保存本文を再取得し、着手可否の判定から再開する。
既存`processing`項目の別セッション再開では履歴を探索せず、`start-processing`を再実行しない。
既存`processing`項目を未完了の`feedbacks-planner`工程の再開起点にしない。

移動開始後にI/O、commit又はpushが失敗した場合は、次のコマンドで指定集合のprocessing配置と保存本文を確認する。
`atk mq list --status=active --target-repo=<repo>`と
`atk mq show <filename>... --target-repo=<repo> --skip-pull`を実行する。
管理リポジトリでは`git status --porcelain`及び
`git show --name-status --format=%H%n%s HEAD`を実行し、未コミット差分と遷移commitを照合する。
remote設定時は遷移commitの完全OIDを取得し、`git fetch`後に
`git merge-base --is-ancestor <transition-commit-oid> @{u}`でupstream包含を確認する。
全対象がprocessingへ移動し、管理リポジトリがcleanで、集合の移動だけを含む遷移commitがあり、
remote設定時にupstream包含を確認できた場合だけ完了とする。
commit前失敗で指定集合の移動だけが未コミット差分として残り、集合外差分とrebase中間状態がない場合に限り、
`atk mq commit`を1回実行して全条件を再検査する。push失敗後にremoteが遷移commitを含む場合は追加操作なしで完了とする。
remoteが遷移commitを含まないcleanなローカルcommit、集合の状態混在、集合外差分、遷移commitの対応付け不能、
rebase中間状態、復旧失敗又はupstream包含の確認不能では、項目別コマンドを再実行せず未完了で停止する。

起動文には次の絶対パスと値だけを渡す。

- ファイル名昇順の対象一覧と対象リポジトリ
- 直接受領した人間由来の利用者指示がある場合は、出所と引用範囲を付けた逐語文
- 常駐自動起動で人間由来の利用者指示がない場合は、非該当であることと起動事実
- 対象worktreeとプロジェクト規範
- バグ対応時はその旨
- 既存ファイルと衝突しない乱数サフィックス付きで、委譲元が確定した計画ファイルの絶対パス

agent-toolkitプラグイン内のタスク文書と規範スキルの絶対パスの解決、および作成規範スキルの選定は`feedbacks-planner`が自身で確定するため渡さない。
これらは`feedbacks-planner`が起草担当へ対象ファイル名、対象リポジトリ、確定した採否と合意、対象、規範、
起草担当用のタスク文書を欠落なく渡せる形で指定する。
フィードバック本文を起動文へ複製しない。
各調査担当は担当ファイル名1件について`atk mq show <filename> --target-repo=<repo> --skip-pull`を1回実行する。
起草担当及びレビュー担当は同じ対象リポジトリのファイル名をまとめ、
`atk mq show <filename>... --target-repo=<repo> --skip-pull`を対象リポジトリごとに1回実行して保存本文を取得する。
表示用見出しから本文を項目へ対応付け、警告・エラー後の当該項目だけの再取得は単数形を維持する。
表示用見出し、YAML frontmatter、CLI付加の末尾改行を除いたフィードバック本文を逐語照合の対象とする。
専用の原文ファイルを作成しないため、原文ファイル固有の作成後失敗と再作成を扱わず、保持も回収もしない。
各下流主体の終了確認は委譲の一般契約として維持する。

TBD候補は、技術調査と明文化済み方針で確定できず、かつ採用済み本文が要求しない選択肢に限定する。
採用済み本文が明示する変更自体を確認事項又は実装前提にしない。
目的及びフィードバック本文が指定する外部可視要素を維持したコーディングエージェント向け規範文書の文言、列挙及び節配置は、
`feedbacks-planner`の起草担当が技術判断として確定する。
これらの差異は`user_decisions`へ含めない。
差異と根拠は`decision-format.md`の理由又は反映内容と計画へ記録する。
採用項目内で既存の許可条件と明文化済み方針により確定できる利用者判断事項は、
`feedbacks-planner`の起草担当が既存の許可条件と明文化済み方針に基づく推奨案を暫定判断として確定する。
成果物は未回答事項による実装・検証の条件分岐を残さない単一経路の計画とする。

`feedbacks-planner`へキュー変更、push、フィードバック投入、worktree作成と回収の権限を渡さない。

## 受領

完了報告を次の実体へ照合する。

- 採否記録と`decision-format.md`
- 採用時の計画ファイルの実在、分量、機械検査結果、レビュー収束状態
- 計画の提示素材と合意表が対象のフィードバック本文、確定した採否、利用者合意に対応していること
- 計画に未回答事項による実装・検証の条件分岐が残っていないこと
- `feedbacks-planner`の起動前後のGit状態と`write_status`
- TBD候補と利用者判断事項
- 対象ファイル名及び各項目の採用・却下・保留・技術的失敗の結果

条件分岐が残る場合は、計画本文を編集せず同じ`feedbacks-planner`系統へ差し戻す。
計画全文を`feedbacks-planner`の完了報告へ要求しない。
終端工程の一覧、対象及び認可根拠となる本文の逐語引用を照合し、本文にない操作は差し戻す。
実装変更がない終端工程専用項目は計画なしであることを検収し、終端待機集合へ登録する。
受理した`user_decisions`の各項目は、`agent-toolkit/rules/01-agent.md`「協調と自律」節の確認境界に
該当することを照合してから`agent-toolkit:add-feedback`のTBD投入経路で記録する。
確認境界に該当しない技術判断が含まれる場合は、計画本文を編集せず同じ`feedbacks-planner`系統へ差し戻す。
回答を受領した場合は`agent-toolkit/rules/01-agent.md`「協調と自律」のTBD受領規定に従い、回答だけを記録する。
暫定判断の内容、根拠、回答後に必要な追随作業、検証はTBD本文へ残し、
将来の専用処理経路又は利用者が参照する情報とする。
これらの情報を自動追随・自動再開・自動実行の契機としない。

`feedbacks-planner`の失敗又は解消不能な`needs_escalation`では、対象の元のファイル名ごとに失敗TBDを`agent-toolkit:add-feedback`で1件保存する。
失敗TBDには失敗した事象、期待値、実際値、発生条件を含める。
直接的原因、再開に必要な情報、元のファイル名も含める。
失敗TBDの保存コマンドの完了表示にエラーが無いことを確認する。
警告が出た場合は`atk mq show <失敗TBD filename> --target-repo=<repo>`で保存内容に欠落が無いことを確認する。
確認後に`atk mq reject <filename> --note=<失敗TBD filename>`で元のフィードバックを終端する。
失敗TBDを保存できない場合と欠落を修復できない場合はrejectを実行せず、元のフィードバックをactiveのまま保持して失敗として返す。
rejectだけが失敗した場合は、一意な失敗TBDとactiveな元のフィードバックを確認できるときだけrejectを1回再実行する。
それ以外では新しいTBDを作成せず、Git操作も`feedbacks-planner`の再開も行わず失敗として返す。

計画レビューの収束不能判定により分離した単位の`needs_escalation`は、失敗TBDと`atk mq reject`の対象としない。
当該単位はTBDを記録し、現行の有効依存を保持したままTBDのファイル名を明示依存へ追加して
`atk mq return-to-inbox`でinboxへ戻し、active一覧で`blocked`であることを確認する。

`feedbacks-planner`の完了後は項目別結果をファイル名昇順で各1回反映する。
各結果反映コマンドが警告・エラーを返した場合は、同じ結果を再実行せず、
`atk mq show <filename> --target-repo=<repo>`で当該項目だけを1回再取得する。
意図した保存後状態を確認できた場合は同じ結果を再実行せず次のファイル名へ進む。
元項目がactiveな場合は、元のファイル名と失敗内容を持つ失敗TBDを既存の投入経路で1件保存し、
保存コマンドの完了表示にエラーが無いことを確認する。
警告が出た場合は`atk mq show <失敗TBD filename> --target-repo=<repo>`で保存内容に欠落が無いことを確認する。
確認後に`atk mq reject <filename> --note=<失敗TBD filename>`を実行する。
rejectだけが失敗した場合は、一意な失敗TBDとactiveな元のフィードバックを確認できるときだけrejectを1回再実行する。
再取得失敗、想定外状態、失敗TBDの保存失敗、reject再失敗では、当該項目への追加操作だけを止める。
全ての分岐で保持済みの`feedbacks-planner`結果により後続項目をファイル名昇順で各1回処理する。
結果反映エラーが先頭、中間、末尾のいずれで発生しても、全ファイル名を各1回処理する。
全ファイル名の走査後に警告・エラーが1件でもあればバッチを失敗として返す。
Git操作、3分類及び元項目の`feedbacks-planner`再開は行わない。

採用結果では`atk mq convert-to-plan <filename> --plan-file=<計画絶対パス> --target-repo=<repo>`を実行し、
保存結果の`plan_file`を同じ実在する計画パスへ照合する。
ユーザー判断の保留時はTBD候補を`agent-toolkit:add-feedback`へ渡す。
`hold-with-tbd-inject.md`の`保留と再開`に従い、既存の有効依存とTBDのファイル名を登録してから
通常の`atk mq return-to-inbox`でinboxへ戻し、active一覧で`blocked`を確認する。
ファイル名で表せない外部条件待ちは、観測方法、現在値、解除条件、再開工程を本文へ記録し、
`atk mq return-to-inbox <filename> --cooldown-days=3`で戻す。別のフィードバック待ちは`depends_on`を使う。
