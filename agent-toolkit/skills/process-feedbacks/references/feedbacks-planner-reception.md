# feedbacks-plannerの起動と受領

Claude Codeホストで通常型のフィードバックを処理する場合に、メインが調査から計画レビューまでを委譲する委譲元契約を定める。
メインはキュー操作と検収を担当し、`feedbacks-planner`固有の実行手順を起動文へ複製しない。

## 起動

active一覧を取得した時点のreadyな通常型のフィードバックを、対象リポジトリごとの1バッチとして
初回の`agent-toolkit:feedbacks-planner`へ1回だけ渡す。
blocked項目、未回答TBD、一覧取得後に追加された項目は含めない。
`feedbacks-planner`はバッチ全項目について、原文正本ID、投入元、人間由来の指示又は方針の優先度、調査根拠、欠陥原因、
採否及び項目固有の採否理由を記録し、採用又は部分採用の採用範囲だけを1つの統合計画の実施内容へ反映する。
不採用、保留、対象外及び別リポジトリへの移管を含む全項目の記録を概要の採否一覧へ渡し、項目ごとの原文、対象、完了条件、実装単位を識別可能に保つ。

新規`inbox`項目では着手可否の確定後に`atk mq start-processing`を実行し、対象ファイル名と
`processing`配置を照合する。
状態競合で拒否された場合はactive一覧と保存本文を再取得し、着手可否の判定から再開する。
既存`processing`項目の別セッション再開では履歴を探索せず、`start-processing`を再実行しない。
既存`processing`項目を未完了の`feedbacks-planner`工程の再開起点にしない。

`feedbacks-planner`が`status: awaiting_confirmation`を返した場合、メインはこれを失敗として処理しない。
確認回答、保存済みTBDのいずれかを受領する。受領後は停止済みの識別子へ継続せず、同じ`feedbacks-planner`系列（同じバッチと計画）の
新しい識別子を起動する。確認待ち後の再開起動には、元のバッチ全項目の調査結果全文、原文frontmatterの`source`原値（欠落は値なし）、
`user_decisions`の原文、出所と引用範囲付きの逐語回答又は保存TBD、同じ計画ファイルの絶対パスを渡す。
調査結果、原文source及び`user_decisions`は再取得、再調査、要約をしない。回答又はTBDを対応する採否記録へ統合する。

初回起動には再開コンテキストを含めない。
確認待ち後の再開起動には、`confirmation_context`の`original_investigations`、`raw_sources`、`user_decisions`、`answer_or_tbd`、`plan_path`を全て渡す。
各値は元のバッチ全項目の調査結果全文、原文frontmatterの`source`原値（欠落は値なし）、`user_decisions`の原文、
出所と引用範囲付きの逐語回答又は保存TBD、初回起動と同じ計画ファイルの絶対パスに対応させる。

初回起動文には次の絶対パスと値だけを渡す。

- ファイル名昇順の対象一覧と対象リポジトリ
- 直接受領した人間由来の利用者指示がある場合は、出所と引用範囲を付けた逐語文
- 常駐自動起動で人間由来の利用者指示がない場合は、非該当であることと起動事実
- 対象worktreeとプロジェクト規範
- バグ対応時はその旨
- 既存ファイルと衝突しない乱数サフィックス付きで、委譲元が確定した計画ファイルの絶対パス

確認待ち後の再開起動文には、初回起動の入力に加えて、上記の確認待ち再開コンテキストを必須入力として渡す。

agent-toolkitプラグイン内のタスク文書と規範スキルの絶対パスの解決、および作成規範スキルの選定は`feedbacks-planner`が自身で確定するため渡さない。
これらは`feedbacks-planner`が起草担当へ対象ファイル名、対象リポジトリ、確定した採否と合意、対象、規範、
起草担当用のタスク文書を欠落なく渡せる形で指定する。
フィードバック本文を起動文へ複製しない。
調査担当、起草担当及びレビュー担当は各ファイル名について
`atk mq show <filename> --target-repo=<repo> --skip-pull`を1回実行して保存本文を取得する。
表示用見出し、YAML frontmatter、CLI付加の末尾改行を除いたフィードバック本文を逐語照合の対象とする。
専用の原文ファイルを作成しないため、原文ファイル固有の作成後失敗と再作成を扱わず、保持も回収もしない。
各下流主体の終了確認は委譲の一般契約として維持する。

通常の将来判断TBD候補は、技術調査と明文化済み方針で確定できず、かつ採用済み本文が要求しない選択肢に限定する。
採用済み本文が明示する変更自体を確認事項又は実装前提にしない。
目的及びフィードバック本文が指定する外部可視要素を維持したコーディングエージェント向け規範文書の文言、列挙及び節配置は、
`feedbacks-planner`の起草担当が技術判断として確定する。
これらの差異は`user_decisions`へ含めない。
差異と根拠は`decision-format.md`の採否理由又は反映内容と計画へ記録する。
採否確定工程では`source: session-review`だけをエージェント由来と判定し、それ以外のsource、source欠落及び不明の不採用候補を
原文との差異と技術的理由付きの不採用確認用`user_decisions`へ返す。`user_decisions`は通常の将来判断TBDと区別し、
不採用候補の採否確定だけに用いる。部分採用はこの確認へ機械的に含めず、差異、採用範囲、除外範囲、採否理由を採否記録へ残す。
メインは不採用確認用`user_decisions`ごとに`AskUserQuestion`を発行し、直接回答を受領した場合は出所と引用範囲付きの逐語文を渡して
同じ`feedbacks-planner`系列の新しい識別子を起動し、当該項目の採否を確定する。
回答が無い場合は同じ質問内容を不採用確認用TBDとして`agent-toolkit:add-feedback`へ渡し、初回だけ保存・依存設定・inbox差し戻し・
`blocked`確認が完了するまで元項目をrejectしない。保留確認後は保留結果を渡して同じ系列の新しい識別子を起動する。
保存済みの不採用確認用TBDを受領した再開では、`atk mq show <TBD filename> --target-repo=<repo-path> --skip-pull`で既存TBDの保存内容を照合する。
`atk mq list --status=active --target-repo=<repo-path>`で元項目の`blocked`状態を照合する。
この再開では既存TBDと`blocked`状態の照合だけを行い、`agent-toolkit:add-feedback`によるTBD再投入、
`atk mq set-dependencies`による再依存と`atk mq return-to-inbox`による再inboxを実行しない。
既存TBD又は`blocked`状態を照合できない場合は新しい識別子を起動せず失敗として返す。
保留項目を含む全項目の採否一覧と採用範囲だけで計画起草を続行する。
採用項目内で既存の許可条件と明文化済み方針により確定できる利用者判断事項は、
`feedbacks-planner`の起草担当が既存の許可条件と明文化済み方針に基づく推奨案を暫定判断として確定する。
成果物は未回答事項による実装・検証の条件分岐を残さない単一経路の計画とする。

`feedbacks-planner`へキュー変更、push、フィードバック投入、worktree作成と回収の権限を渡さない。

## 受領

完了報告の`status`を最初に確認する。`awaiting_confirmation`は確認待ちであり、`needs_escalation`の失敗経路より先に
上記の新規起動経路へ渡す。
`awaiting_confirmation`では`confirmation_context`の元の調査結果全文、raw source、`user_decisions`原文、回答又はTBD、
同じ計画ファイルの絶対パスを照合する。計画ファイルの起草とレビューは新規起動後に検収する。
完了報告を次の実体へ照合する。

- 採否記録と`decision-format.md`
- バッチ全項目の採否記録が起草担当へ渡され、概要の採否一覧と入力件数が一致すること
- 採用時の計画ファイルの実在、分量、機械検査結果、レビュー収束状態
- 計画の提示素材と合意表が対象のフィードバック本文、確定した採否、利用者合意に対応していること
- 計画に未回答事項による実装・検証の条件分岐が残っていないこと
- `feedbacks-planner`の起動前後のGit状態と`write_status`
- TBD候補と利用者判断事項
- 対象ファイル名及び各項目の採用・却下・保留・技術的失敗の結果

`source: session-review`と確認できない項目の不採用は、原文との差異と技術的理由を示す`AskUserQuestion`の回答後だけ確定する。
回答又はTBDの保存・依存設定を確認できない場合は`atk mq reject`を実行せず、元項目をactiveのまま保持して失敗を返す。
同じrejectメモを複数項目へ用いる場合も、各項目で同じ理由が成立する根拠を採否記録へ対応付ける。

条件分岐が残る場合は、計画本文を編集せず同じ`feedbacks-planner`系統へ差し戻す。
計画全文を`feedbacks-planner`の完了報告へ要求しない。
終端工程の一覧、対象及び認可根拠となる本文の逐語引用を照合し、本文にない操作は差し戻す。
実装変更がない終端工程専用項目は計画なしであることを検収し、終端待機集合へ登録する。
受理した不採用確認用`user_decisions`の各項目は、`agent-toolkit/rules/01-agent.md`「協調と自律」節の確認境界に
該当することを照合してから確認する。
確認境界に該当しない技術判断が含まれる場合は、計画本文を編集せず同じ`feedbacks-planner`系統へ差し戻す。
不採用確認用`user_decisions`への直接回答は、出所と引用範囲を付けた逐語文を渡して同じ系列の新しい`feedbacks-planner`識別子を起動し、
更新された採否記録を再検収する。回答なしで保存した不採用確認用TBDは、既存TBDの保存内容と元項目の`blocked`状態を照合した保留結果を渡して同じ系列の新しい識別子を起動し、
全項目の採否一覧と採用範囲だけで計画起草を続行する。
通常の将来判断TBDを受領した場合だけ、`agent-toolkit/rules/01-agent.md`「協調と自律」のTBD受領規定に従い、回答だけを記録する。
通常の将来判断TBDでは、暫定判断の内容、根拠、回答後に必要な追随作業、検証をTBD本文へ残し、将来の専用処理経路又は
利用者が参照する情報とする。これらの情報を自動追随・自動再開・自動実行の契機としない。

`awaiting_confirmation`を処理した後、`feedbacks-planner`の失敗又は解消不能な`needs_escalation`では、対象の元のファイル名ごとに失敗TBDを`agent-toolkit:add-feedback`で1件保存する。
失敗TBDには失敗した事象、期待値、実際値、発生条件を含める。
直接的原因、再開に必要な情報、元のファイル名も含める。
失敗TBDの保存コマンドの完了表示にエラーが無いことを確認する。
警告が出た場合は`atk mq show <失敗TBD filename> --target-repo=<repo>`で保存内容に欠落が無いことを確認する。
`source: session-review`と確認できる項目は、確認後に`atk mq reject <filename> --note=<失敗TBD filename>`で元のフィードバックを終端する。それ以外の項目は、`hold-with-tbd-inject.md`の「技術的失敗」に従い、失敗TBDを依存へ追加して`blocked`まで確認する。元のフィードバックをrejectせず、失敗TBDの回答後は不採用確認を再開せず、次の`process-feedbacks`セッションで新しい`feedbacks-planner`を起動して通常経路で元のフィードバックを再開する。
失敗TBDを保存できない場合と欠落を修復できない場合はrejectを実行せず、元のフィードバックをactiveのまま保持して失敗として返す。
`source: session-review`と確認できる項目でrejectだけが失敗した場合は、一意な失敗TBDとactiveな元のフィードバックを確認できるときだけrejectを1回再実行する。
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
`source: session-review`と確認できる項目は、確認後に`atk mq reject <filename> --note=<失敗TBD filename>`を実行する。それ以外の項目は、`hold-with-tbd-inject.md`の「技術的失敗」に従ってTBD依存を設定し、`blocked`を確認して保留する。不採用確認を経ずに元項目をrejectしない。
`source: session-review`と確認できる項目でrejectだけが失敗した場合は、一意な失敗TBDとactiveな元のフィードバックを確認できるときだけrejectを1回再実行する。
再取得失敗、想定外状態、失敗TBDの保存失敗、reject再失敗では、当該項目への追加操作だけを止める。
全ての分岐で保持済みの`feedbacks-planner`結果により後続項目をファイル名昇順で各1回処理する。
結果反映エラーが先頭、中間、末尾のいずれで発生しても、全ファイル名を各1回処理する。
全ファイル名の走査後に警告・エラーが1件でもあればバッチを失敗として返す。
Git操作、3分類及び元項目の`feedbacks-planner`再開は行わない。

採用結果では`atk mq convert-to-plan <filename> --plan-file=<計画絶対パス> --target-repo=<repo>`を実行し、
保存結果の`plan_file`を同じ実在する計画パスへ照合する。
別リポジトリ項目は終端結果として扱わない。投入前処理で入力メッセージの予約frontmatterキー`target_repo`だけを
移管先の値へ一時的に置き換える。元項目のfrontmatterと本文を含むメッセージ全体、指定済みsource及び正しい`target_repo`を
`agent-toolkit:add-feedback`へ渡して登録・照合する。通常の`atk mq add`はfrontmatterの`target_repo`をCLI値で置き換えず、
frontmatterの値を優先する。
`alert_keys`などの非予約frontmatterは元項目の値を保持する。
source欄がない場合はsourceを指定しない。
sourceを指定した場合は移管先のsource、本文、`target_repo`、非予約frontmatter全体を照合する。
照合には`atk mq show <移管先ファイル名> --target-repo=<target_repo> --skip-pull`を使う。
sourceを指定しない場合は本文、`target_repo`、非予約frontmatter全体を同じshow経路で照合する。
登録・照合の結果から移管先ファイル名と本文を照合できた場合だけ、
元項目を移管先リポジトリとファイル名付きの項目固有メモでrejectする。登録又は照合に失敗した場合は元項目を保持する。
ユーザー判断の保留時はTBD候補を`agent-toolkit:add-feedback`へ渡す。
`hold-with-tbd-inject.md`の`保留と再開`に従い、既存の有効依存とTBDのファイル名を登録してから
通常の`atk mq return-to-inbox`でinboxへ戻し、active一覧で`blocked`を確認する。
ファイル名で表せない外部条件待ちは、観測方法、現在値、解除条件、再開工程を本文へ記録し、
`atk mq return-to-inbox <filename> --cooldown-days=3`で戻す。別のフィードバック待ちは`depends_on`を使う。
