---
name: session-review
description: >
  最上位セッションのメインエージェント（atk wi process-loop all-reposのリポジトリ担当を含む）が、
  ユーザーの手動起動、agent-toolkit:completion-reportからの明示呼び出しまたは
  agent-toolkit:process-wiの公開工程からの明示呼び出しで起動する。
---

# セッション振り返り

同じユーザー介入、レビュー反復、手戻りまたは無駄が再発しないよう、対象セッションの問題を見つけ、原因と対策を確定した通常AWIを投入する。
原因と対策の最終判断と統合は、元の記録を持つメインが同じセッション内で行う。事実の収集は`agent-toolkit:bugfix`の`references/investigation-delegation.md`「事象単位の並列調査委譲」の条件で独立した担当へ委譲でき、メインは取得できる証拠を渡す。
`atk run-script session-review-prepare`は収集できた全担当の発話とツール呼び出しの入力を並べ、結果を成否・量・記録位置で示したセッション全体の流れ（会話の流れ）と、除外区分に当たる結果を機械的に除いた問題候補（`candidates.md`）を書く。候補だけを読むとどの観点にも当たらない問題が分析の入力に現れないため、会話の流れの全範囲から先に問題を探し、`candidates.md`はそこで得られない事象の補完に使う。

全記録の抽出とWI投入には、元の記録・参照資料・キューへ到達するファイル操作とCLIが必要である。これらが無い環境では、提示された会話の範囲で問題候補と確認に要る証拠を整理し、対象全体の振り返りと区別する。全記録の検収と正式な投入は、最上位のメインがそれらの機能を使えるClaude CodeまたはCodexで行う。候補の説明は[文章の品質基準](references/quality-standards.md)で確かめる。

## 読込表

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 工程1の`atk run-script session-review-prepare`が0以外で終了したか、標準出力が1行のJSONでない場合 | `references/analysis-failure.md` |
| 工程2で`atk run-script session-review-prepare`の`reference_document`が値を持つ場合 | `reference_document`が示す対象リポジトリ固有の振り返り参照文書 |
| 工程2で`conversation.md`の全範囲を確認する前（工程2の判定と工程3の選別に使う） | `references/analysis.md` |
| 工程2の全範囲確認の読取を分担する前 | `agent-toolkit:delegation` |
| 工程3か工程4で事実の収集を委譲する前 | `agent-toolkit:bugfix`の`references/investigation-delegation.md` |
| 工程4で問題ごとに`agent-toolkit:bugfix`を起動した時点（`references/analysis.md`「原因と対策」がユーザー介入を拡張原因分析の条件に当たるものとして扱うため） | `agent-toolkit:bugfix`の`references/root-cause-analysis.md` |
| 工程5で要求をWI投入担当へ渡す前 | `${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md` |

## 起動するエージェント

最上位セッションのメインエージェントが自身のセッションを対象として起動する。`atk wi process-loop all-repos`のリポジトリ担当は、委譲元が常駐プログラムのため、自身と配下の担当の記録の最上位として起動する（`agent-toolkit:process-wi`の`references/all-repos-orchestration.md`「担当の役割」）。
エージェントからタスクを受け取ったエージェントは起動判断を委譲元へ返す（1つのエージェントが全記録をまとめて抽出しないと候補が重複または欠落する）。
振り返りの最終報告の前に終わり、その後に続行されなかったセッションは、終わり方（ユーザーの中断、利用上限での打ち切りなど）によらずユーザーがそこで終了させたものとして扱う。そのセッションは後続のセッションの振り返りの対象へ加えず、その記録が振り返られていないことを問題として扱わない。続行したセッション（同じ会話の再開・自動継続と、ユーザーが別のセッションかホストで続きを指示した場合）は、振り返りを元の残工程として引き継ぐ（`agent-toolkit:completion-report`の`SKILL.md`「工程」）。
対策AWIの本文の起草と投入は、`agent-toolkit:wi-standards`「投入と取得」に従ってWI投入担当へ委譲する。

## 工程

1. 次のいずれかの形で1回実行する。出力領域は準備コマンドが対象セッションのmanaged-temp内へ実行ごとに確保する。Claude Codeでは現在のtranscriptの絶対パス（`rules-main.claude-code.md`「自身の会話の記録」の取得元の値）を、Codexでは`CODEX_THREAD_ID`を渡す。`--target-repo`は対象リポジトリを確定できた場合だけ渡す。

   ```text
   atk run-script session-review-prepare -- --transcript <transcriptの絶対パス> --target-repo <対象リポジトリの絶対パス>
   atk run-script session-review-prepare -- --codex-thread-id <thread ID> --target-repo <対象リポジトリの絶対パス>
   ```

   JSONの`work_dir`から確保した出力領域を取得する。生成物は準備終了後も保持し、セッションのmanaged-tempの7日保持へ委ねる。既存の絶対ディレクトリを`--work-dir`で明示した場合の保持と回収は呼出側が担う。

   作業ディレクトリへ会話の流れ（`conversation.md`）、比較材料（`comparison-materials.md`）、問題候補（`candidates.md`）、セッション統計（`stats.md`）の4文書が書かれる。
   - `conversation.md`は発話とツール呼び出しの入力を全文で載せ、ツール結果は本文を載せずに成否（`完了`か`失敗`）・本文量・記録位置の1行（`- ツール結果（<記録位置>）: …`）で示す。結果の本文は記録位置を`--detail`へ渡すと得られる
   - `comparison-materials.md`は委譲入力・規範読込の比較材料を記録別に示す参照資料で、対象・時点・記録位置から詳細照会の全文位置を選ぶのに使う。時系列は会話の流れから読む
   - `stats.md`は律速となったagent threadごとの内訳、メインと全担当のtokens・cache・応答回数の概要と計数定義、記録ごとの圧縮回数と各回の記録位置・時刻を持つ
   - `candidates.md`・`stats.md`の未確認範囲とJSONの`unconfirmed_scope`は、未解決の記録と識別子を得られない呼び出しを区別して示す。候補件数とは別の確認範囲である

   標準出力の1行JSONは各文書の所在（`conversation_path`、`comparison_materials_path`、`candidates_path`、`stats_path`）と`reference_document`（無ければnull）を持つ。
   ほかに`prepared_at`、`utterance_counts`、`candidate_total`・`candidate_counts`、`excluded_counts`、`failure_ledger_skipped`を持つ。
   さらに`mandatory_candidates`、`similar_records`、`elapsed_seconds`、`compaction_count`を持つ。両者の候補を振り返り結果報告でどの行に書くかは`agent-toolkit:completion-report`の`references/report-formats.md`が定める。
   `conversation_ranges`は会話の全行を重複・欠落なく覆う読取単位で、各範囲の`start_line`・`end_line`は両端を含む1始まりの行番号、`utf8_bytes`は改行込みの量、`first_locator`・`last_locator`と`first_timestamp`・`last_timestamp`は範囲内の最初と最後の位置・時刻（取得できなければnull）である。
   `reader_ranges`は`conversation_ranges`の要素を先頭から順に束ねた担当範囲で、1担当が全文を読了できる量に収まる。各範囲は`start_line`・`end_line`・`utf8_bytes`と、束ねた要素の添字`first_range_index`・`last_range_index`を持つ。
   `improvement_lines`と`candidates.md`の取り込んだ改善点は、記録の全文から抽出した実際の行を持つ。
2. `candidates.md`より先に`conversation.md`の全範囲を確認し、観点を限定せず問題と改善機会を列挙する。全範囲を覆う責任と最終判断はメインが持つ。
   - 標識に現れない遠回り・手戻り・反復・ユーザー是正・時間の浪費を、ツール呼び出しと前後の発話から探す（コマンドの試行錯誤、資料の再読・陳腐化、設計の書き直し、規範解釈、委譲指示の不足、定義元への到達、出力の後加工と同目的のコマンド反復など）。
   - 手順書やスキルが求める作業をmanaged-tempへ作成したスクリプトで代替した箇所も探す。委譲先の分は`atk run-script session-review-evidence --`の`--grep`でmanaged-tempのスクリプトのパスを検索する。判定は`references/analysis.md`「一次選別」に従う。
   - 規範自体の適否もQCDから見直し、読込・準備が目的の操作前に積み上がった区間を`references/analysis.md`「規範適用による目的逸脱」で判定する。
   - `気付いた改善点:`で始まる行は全件を問題の列挙へ含める。
   - 成功した操作、正常な返却、訂正されなかった成果も、目的・利用結果・契約・代替手段・総費用と比べる。準備・実行・検収・後始末・値の転記、工程境界や離れた区間の反復も、各操作が守る成果と要求元を確かめ、責務を持つ側への統合、既存機構の利用、操作の除去で同じ品質を保てるか比べる。観点と証拠は`references/analysis.md`「比較して探す観点」を使い、表の外にも同じ比較を適用する。委譲入力、規範読込、非律速担当の費用は準備文書の概要から全文位置を選ぶ。
   - 全範囲の読取単位には`conversation_ranges`の行番号をそのまま使う（Claude CodeのReadでは`offset=start_line`、`limit=end_line-start_line+1`）。単独の発話・呼び出しが上限を超える場合やホストが拒否した範囲だけを分割する。分割は`agent-toolkit/rules/02-agent-operations.md`「ツール・コマンド運用」と`agent-toolkit/share/rules-common.codex.md`「Codex固有の入出力」に従う。同一セッションで確認済みの区間は、記録の識別子・範囲・内容の同一性と問題の有無・根拠を保持できる限り、会話圧縮や再実行の後も再利用する。読むのは残る追加・変更・未確認・根拠不足の区間とする。
   - 読取専用の担当へ分担する場合は、担当1件へ`reader_ranges`の1範囲と、その中の`conversation_ranges`の各要素をそのまま渡す。範囲と要素は合成しない。要素を分けるのは前項の分割の場合だけとする。未読補完の再依頼では、読めなかった`conversation_ranges`の要素だけを渡し、前の担当が返した確認範囲のうちその要素に属する行範囲を確認済み範囲として添える。再依頼を受けた担当は確認済み範囲を除いた行を読む。結果の本文は、担当もメインも、判断に要る位置だけ`--detail`で取得する。対象記録、範囲、前後の境界、観点を限定せず問題を探す目的、返す確認範囲・根拠位置・未確認範囲を渡し、メインは被覆、境界をまたぐ問題、判定の不一致を自ら確かめる。読取は候補に一致した抜粋に限らず範囲全体とする。
3. 通読返却と取得済み証拠へ全候補を対応付ける。同一事象の根拠が足りる部分は再利用し、追加事象・変更・未確認・判定不一致だけを補完する。候補ごとの原記録の独立した再調査は一律に求めない。メインは被覆・境界・不一致と原因・採否を確定し、基準付き走査の非該当全件を同じ目的で再走査しない。追加候補が既存判断を変える証拠を持つ場合だけ確定済み判断へ戻す。`candidates.md`を通読の補助として読み、通読の問題との対応付けと、通読では得られない事象（直近30日の複数セッションで反復した失敗署名、hook通知の発生源と件数、除外件数、その場のコードによる加工（`adhoc-processing`）、委譲先の内部）の追加に使う。候補に対応しない通読の問題と成功した作業の改善機会も独立して扱う。選別と工程4の確定は`references/analysis.md`の基準による。ユーザー自身の秘匿値の露出とその対応を伝えた発話は、同書「一次選別」の除外区分`own-secret-exposure`の条件と例外（過剰反応自体の是正を求めた発話は分析へ送る）で判定する。確定や比較に要る材料が概要から欠ければ、候補の有無によらず`atk run-script session-review-evidence`の`--detail`・`--tool-calls`・`--grep`で照会する。所要時間と全担当の費用には`stats.md`を使う。公開待機中に準備した調査とAWI原稿は、正式な選別結果と公開後の状態に照らして採否を確定し、公開結果へ依存する問題は結果の確定後に調べる。
4. 選別した問題ごとに、メインが自ら`agent-toolkit:bugfix`を起動し、事象、期待する契約、実際の結果、発生条件を入力として適用する（同じセッションで先に起動していても振り返りごとに改めて適用する。WI投入担当の分析はメインの原因分析の代わりにならない）。
   1. 事項が既存の上位規範（`agent-toolkit/rules/01-agent.md`のQCDや「完遂と先送り」など）や対象の目的と常識から導かれるかに答える（`agent-toolkit:bugfix`の`SKILL.md`「原因分析の品質確認」）。条文の追加より除去・緩和・一般化で目的を達成できるかに答える。
   2. 初動、拡張原因分析の判定、原因、類似見直しの結果、対策・横展開処置・再発防止策を証拠と比べ、原因と対策を1案へ確定する。
   3. 見送る問題も原因と比較を確定し、見送りの根拠が元の不成立、原因、比較の結果と対応し、`agent-toolkit:bugfix`の`SKILL.md`「再発防止策の必須性」が定める使えない根拠に当たらないことを`references/analysis.md`の基準で確かめる。
5. 確定した原因と対策を要求として、`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`「起動方法」の単位でWI投入担当へ渡す。
   投入するAWIが1件以上あれば、担当の起動前に`## 振り返り結果の予告`（投入するAWIは`AWI登録予定`の行）を届け、同じ応答で担当を起動する。
   予告の書式は`agent-toolkit:completion-report`の`references/report-formats.md`「振り返り結果報告のフォーマット」に従う。
   送達は`agent-toolkit:user-confirmation-and-report`「手段の選択」の手段（Claude Codeは`mcp__agent-toolkit__send_to_user`）に従う。
   要求の`source`は`session-review`とし、要求間の依存は先行成果の必要性の根拠とともに趣旨へ書く（同書「渡す入力」）。ユーザー発話を逐語引用する要求には、原文を保持する記録ファイル（`conversation.md`や`atk run-script session-review-evidence`の出力など）の絶対パスを出所に含める。検収は同書「受領と検収」に従う。
6. 検収の後、工程1の準備より後に受け取ったユーザー発話と、準備に含まれていない実際の`気付いた改善点:`の行があるかを確かめる。
   - ユーザー発話はキューから配送された発話を含み、自動プロンプトと実行環境の生成本文は含めない。振り返り自身の起動・待機・検収・保存は前回実行の続きとして扱う
   - 改善点はメインの可視発話・送信本文と、正常な委譲返却から届いた行を含む。成功した準備のJSONの`improvement_lines`に前後の空白を除いて同じ行があれば取込み済みとし、同じ行の転記・再掲だけでは再実行しない。文書・コードの引用、ツールで取得した資料本文や生成通知に現れる例は実際の報告と区別する
   - 失敗した準備で取得できなかった追加分を、直前の成功した準備に含まれていたと扱わない。初回・再準備のいずれの失敗も`references/analysis-failure.md`へ進む

   どちらかがあれば工程1から再実行し、その発話と後続の作業、新しい改善点を分析へ含める。既読証拠は工程2の条件で再利用し、前回投入したAWIと保存済みの更新は既存の未終端項目として重複判定にかけ、未保存の担当があれば検収と保存を終えてから再実行する。再実行の工程6でも同じく確かめる。最終報告の送達後に新しい改善点が生じた場合も、この工程へ戻って取込み・分析・結果報告の更新を終えてから終了する。
   どちらも無ければ起動元に応じて進む。`agent-toolkit:completion-report`から起動された場合は、振り返りの結果と投入結果（投入の有無、ファイル名、予告との差分）を同スキルへ渡す。手動起動の場合は同スキルの書式と送達手段で最終報告を届ける。最終報告の種類と末尾の固定文は同スキル冒頭の規定に従う（予告は固定文の対象外）。Stop hookの遮断への対処と、中止・待機の判断の記録は`agent-toolkit:completion-report`の工程の末尾に従う。

   最終報告の送達前（通常のWIの保存を検収した後。投入0件や、最終報告の作成中に初めて超過した場合も同じ）に、本スキルの起動時刻から経過を確かめる。300秒を超え未処置なら、既存の統計・証拠を使って工程3・4の所要時間の分析へ進む。必要な材料が不足する場合だけ、完了したWI投入担当を含む現行記録で統計を再準備する。同一超過の原因・全処置・報告が確定した後は、秒数算出のためだけの再収集と分析反復を省く。判定には振り返り区間の経過を使い、セッション全体の`elapsed_seconds`と区別する。必要な追加AWIは追加分の工程5の予告から既存のWI投入担当の起草・メインの検収・保存へつなぎ、既存AWIによる被覆や再発防止策なしの結論も根拠とともに今回の最終報告へ渡す。今回の律速への処置は、AWI化基準など別の対象への先行回答と分けて確定する（`references/analysis.md`「所要時間の分析と改善」）。同一超過の扱いは`references/analysis.md`「所要時間の分析と改善」に従い、準備後のユーザー発話による再実行の契約は保つ。

振り返り工程の所要時間目標は300秒とする。計測は本スキルの起動から最終報告（投入が1件以上なら`## AWI投入結果報告`、0件なら`## 振り返り結果報告`）の出力までとし、予告の出力は終点の対象外とする。目標は完了条件から外し、超過は工程6で今回の分析・処置・報告へつなぐ。

## 候補の抽出

候補は`atk run-script session-review-evidence`が生成し、`atk run-script session-review-prepare`が1候補1行の要約と記録位置で`candidates.md`へ載せる。各区分の判定条件は`scripts/session_review_evidence.py`と`scripts/session_review_prepare.py`のdocstringが定める。
記録位置には同じ証拠bundleの`record-provenance`から記録の由来（役割文書、起動mode、起動元の記録とその役割文書）を添え、取得できない記録はその旨を示す。候補の実行主体は要約の文言から推さず、この由来で確かめる。

- ユーザー介入、WIの記入欄の是正（`wi-user-response`）、エスカレーションは本文の全文を、hook通知は判定した直前のアシスタント発話を添えて載せる
- ユーザー確認（`confirmation-request`）は提示した選択肢をそのまま選んだ回答（質問、選択肢、回答付き）と、対象セッションが投入したUWI（報告用を除く）とする
- `wi-user-response`の対象は対象セッションが投入したUWIと`atk wi start-processing`・`adopt`・`reject`・`unhold`で処理したWIである（`atk wi show`で読んだだけのWIは除く）。UWIの`## 回答`の選択肢と一致しない記入とAWIの`## ユーザーコメント`の記入を候補にする
- ユーザー介入、ユーザー確認、WIの記入欄の是正は評価が必須であることを示し、深さと再発防止策の要否はbugfixの重大度判断へ渡す。ユーザー介入とWIの記入欄の是正には、private-notesの全WIのうち`## ユーザー指摘の逐語引用`、`## 回答`、`## ユーザーコメント`にその発話の文の大半を含むものを過去の同種記録として添える
- 失敗したコマンドとツールは失敗署名で集約し、直近30日に複数の最上位セッションで観測したものを載せる。単発の失敗は件数表と`single-session-failure`へ計上する
- メインと全委譲先の記録で、インタプリタへその場で書いたコードを渡す実行と保存出力の後加工は、成功を含めて記録ごとに1件の`adhoc-processing`候補へまとめる（失敗署名による選別は適用しない）
- `気付いた改善点:`で始まる行を持つ委譲返却は、正常な完了の形式や本文長によらず`delegate-return`候補へ残す。表示用短縮でも省略区間の標識行は全文で保持する

次の結果は候補から除き、区分ごとの件数だけを数える。

- ユーザー介入ではない入力（`atk wi process-loop`、再開機構、実行環境が生成した定時prompt、委譲先の入力、初期要求）
- 想定外事象を持たず正常な完了だけを示す委譲返却
- 正常な否定結果（出力なしの検索、Gitのグローバルオプションや設定の未定義、単独の`atk`が返す`該当0件:`の行と終了コード1）
- 単独の`atk agents wait`が返す待機継続の終了コード3
- 検証コマンドが欠陥を検出した終了コード1
- auto mode classifierの一時的な判定不能と`atk wi add`・`edit`の本文表記診断の警告
- 応答言語hookの警告と、blockまたはwarn以外の区分のhook出力

検証コマンドの引数誤りやコマンド不在は候補に残す。単独の`atk`と`atk agents wait`の除外は`失敗:`か`警告:`の行を伴う結果と別の終了コード、`&&`やパイプで連結したコマンドを対象外とする（連結全体の終了コードは単独の結果と区別できないため）。先頭の`cd <パス>;`か`cd <パス> &&`の前置1つは外して判定し、`cd:`の診断行を伴う結果は候補に残す。
