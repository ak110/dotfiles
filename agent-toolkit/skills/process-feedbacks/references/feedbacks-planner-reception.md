# feedbacks-plannerの起動と受領

Claude Codeホストで通常型feedbackを処理する場合に、メインが調査から計画レビューまでを委譲するsender契約を定める。
メインはqueue操作と検収を担当し、planner固有の実行手順を起動文へ複製しない。

## 起動

active一覧を取得した時点のreadyな通常型feedbackを、対象リポジトリごとの1 waveとして
`agent-toolkit:feedbacks-planner`へ1回だけ渡す。
blocked項目、未回答TBD、スナップショット後に追加された項目は含めない。
plannerは採用項目を1つの統合計画へまとめ、項目ごとの原文、採否、対象、完了条件、実装単位を識別可能に保つ。

メインは`atk config show`で`private_notes`の絶対パスを解決し、
`atk managed-temp create --prefix feedbacks-planner-source`を単独で実行する。
新規`inbox`項目ではreadiness確定後かつ`atk mq start-processing`前の
`private_notes/inbox/<filename>`をUTF-8で直接読む。
既存`processing`項目の再開では、再開時点の`private_notes/processing/<filename>`をUTF-8で直接読む。
対象パスが存在しない場合又は想定した状態のディレクトリにない場合は原文正本を作成しない。
active一覧と保存内容を再取得し、readiness判定から再開する。
`atk mq show`は対象確認だけに使い、表示用見出しや複数件の区切りを本文へ含めない。

メインは管理対象一時領域直下の`feedback-source.json`へ単一のJSON objectを書く。
feedback filenameをproperty名、frontmatterを含む保存本文の論理文字列をvalueとし、標準JSON serializerを使う。
標準JSON parserで直後に読み戻し、property集合とfilename集合の一致を確認する。
各valueと保存本文の完全一致も確認する。
完全一致には本文内の表示用見出しと同じ文字列、コードフェンス行、末尾改行の有無を含む。
期限切れ`cooldown_until`や非正規化YAML frontmatterも遷移前の保存表現のままvalueへ含める。
JSONのescapeは保存表現とし、parserが返す論理文字列を逐語比較に用いる。
メインは完全一致を確認した各filenameの論理文字列を、作成時か直近の再開時に検収した比較基準として原文正本の回収まで保持する。
遷移中の内容差により原文正本を再作成した場合は、再作成後の読戻しで完全一致を確認した各valueへ比較基準を更新する。
原文正本のwriterはメインだけとし、planner、調査担当、author、reviewerは読み取り専用とする。

新規`inbox`項目では読戻し検収後に`atk mq start-processing`を実行し、対象filenameと
`processing`配置を照合する。
続いて遷移commitの親snapshotにある各`inbox/<filename>`と原文正本のvalueを完全一致で比較する。
状態競合では原文正本を回収してactive一覧と保存本文の取得からやり直す。
遷移中の内容差では親snapshotの保存本文から同じJSONを再作成し、読戻し検収が終わるまでplannerを起動しない。
遷移commit又は親snapshotを一意に確認できない場合もplannerを起動しない。
既存`processing`項目の別セッション再開では履歴を探索せず、`start-processing`を再実行しない。
遷移後に`atk mq edit`などで本文を変更した場合は、再開時点の変更後の保存実体を正本とする。

起動文には次の絶対パスと値だけを渡す。

- 検収済み`feedback-source.json`の絶対パスとfilename昇順の対象一覧
- 直接受領した人間由来の利用者指示がある場合は、出所と引用範囲を付けた逐語文
- 常駐自動起動で人間由来の利用者指示がない場合は、非該当であることと起動事実
- 対象worktreeとプロジェクト規範
- `process-feedbacks/references/`配下の`explore-template.md`、`decision-format.md`、`review-checklists.md`
- `plan-mode/references/plan-review-task.md`
- `agent-toolkit:plan-mode`などのauthor skillと、バグ対応時は`agent-toolkit:bugfix`
- 既存ファイルと衝突しない乱数サフィックス付きで、委譲元が確定した計画ファイルの絶対パス

これらはplannerがauthorへ元の提示素材、確定した採否と合意、対象、規範、author用taskを欠落なく渡せる形で指定する。
同一waveの調査、起草、レビュー及び同一セッション内の再試行では、同じ検収済み原文正本を保持する。
TBD候補は、技術調査と明文化済み方針で確定できず、かつ採用済み本文が要求しない選択肢に限定する。
採用済み本文が明示する変更自体を確認事項又は実装前提にしない。
採用項目内で既存の許可条件と明文化済み方針により確定できる利用者判断事項は、
plannerのauthorが既存の許可条件と明文化済み方針に基づく推奨案を暫定判断として確定する。
成果物は未回答事項による実装・検証の条件分岐を残さない単一経路の計画とする。

plannerへqueue変異、push、フィードバック投入、worktree作成と回収の権限を渡さない。

## 受領

完了報告を次の実体へ照合する。

- 採否記録と`decision-format.md`
- 採用時の計画ファイルの実在、分量、機械検査結果、レビュー収束状態
- 計画の提示素材と合意表が渡したfeedback本文、確定した採否、利用者合意に対応していること
- 計画に未回答事項による実装・検証の条件分岐が残っていないこと
- planner起動前後のGit状態と`write_status`
- TBD候補と利用者判断事項
- 原文正本の絶対パス、対象filename及び各項目の採用・却下・保留又は失敗の結果

条件分岐が残る場合は、計画本文を編集せず同じplanner系統へ差し戻す。
計画全文をplannerの完了報告へ要求しない。
採用時は各filenameへ`atk mq convert-to-plan <filename> --plan-file=<計画絶対パス> --target-repo=<repo>`を実行し、
保存結果の`plan_file`を同じ実在する計画パスへ照合する。
終端工程の一覧、対象及び認可根拠となる本文の逐語引用を照合し、本文にない操作は差し戻す。
実装変更がない終端工程専用項目は計画なしであることを検収し、終端待機集合へ登録する。
受理した`user_decisions`の各項目は、`agent-toolkit:add-feedback`のTBD投入経路で記録する。
回答を受領した場合は`agent-toolkit/rules/01-agent.md`「協調と自律」のTBD受領規定に従い、回答だけを記録する。
暫定判断の内容、根拠、回答後に必要な追随作業、検証はTBD本文へ残し、
将来の専用処理経路又は利用者が参照する情報とする。
これらの情報を自動追随・自動再開・自動実行の契機としない。
ユーザー判断の保留時はTBD候補を`agent-toolkit:add-feedback`へ渡す。
`hold-with-tbd-inject.md`の`保留と再開`に従い、既存の有効依存とTBD filenameを登録してから
通常の`atk mq return-to-inbox`でinboxへ戻し、active一覧で`blocked`を確認する。
filenameで表せない外部条件待ちは、観測方法、現在値、解除条件、再開工程を本文へ記録し、
`atk mq return-to-inbox <filename> --cooldown-days=3`で戻す。別feedback待ちは`depends_on`を使う。

採用、却下、利用者判断待ち、外部条件待ちが混在するwaveでも、全対象の結果が確定し、
保存結果と全下流主体の終了を照合した後に原文正本を1回だけ回収する。
回収直前に管理対象一時領域の絶対パスとmarkerを読み取り専用で再照合し、標準JSON parserで原文正本を再度読み戻す。
property集合と対象filename集合の一致に加え、各valueとメインが作成時か直近の再開時から保持する比較基準の論理文字列が完全一致することを確認する。
この比較は、原文正本が検収後に改変されていない自己同一性の確認であり、`convert-to-plan`や`reject`後のqueueの現在内容との比較ではない。
`atk managed-temp cleanup --path <検収済み絶対パス>`を単独で実行し、終了コード0とパスの不在を確認する。
同一waveを再試行する場合や未確定項目がある場合は回収しない。
中断後も保持済みの絶対パスと比較基準で再開できる場合は、回収直前と同じ構造検証と各valueの完全一致を再検収して同じ正本を使う。
異常終了で正確なパスを保持できない場合は、共有領域の一覧から対象を推測して回収しない。

planner失敗又は`needs_escalation`を同一waveで再試行しない場合は、feedback本文、frontmatter、queue状態を変更しない。
全対象が`processing`に残ることと全下流主体の終了を照合し、原文正本を回収してから失敗として返す。
次のセッションは現在の`processing`保存実体から新しい原文正本を作成し、未完了のplanner工程から再開する。
