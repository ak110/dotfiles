---
name: completion-report
user-invocable: false
description: >
  作業を完了したメインエージェントが、作業完了報告から常時稼働サーバーへの反映、managed-tempの回収、
  公開、振り返り、終了処理までを一続きに行うときに起動する。
  単発の調査依頼への回答、単純な事実回答、確認質問、途中報告、待機・失敗報告などでは起動しない。
  適用するプロジェクト規範によって起動を省く場合は、本スキルを起動せずにreferences/termination-evidence.mdを直接読む。
---

# 作業完了報告とセッションの完了処理

作業の結果は、不都合な結果も含めて正直に報告する。この原則は、作業完了報告、振り返り結果報告、AWI投入結果報告のいずれにも適用する。

反映、公開、UWI保存、セッション終了の各操作は、対象を操作するCLIや必要な参照資料が整っている環境で実行する。操作機能がない環境では、得られた成果と確認済みの証拠に基づいて結果を説明し、未実施の反映・公開・保存を区別して明記する。残る操作は、それらの機能が利用できるClaude CodeやCodexに引き継ぐ。ここで作成する報告の下書きには[文章の品質基準](references/quality-standards.md)を適用する。

本スキルは、実装、計画、AWI投入などの成果物を確定した作業をメインエージェントが完了したときに適用する。元の作業が適用対象であれば、中断理由、担当の交代、後続担当による新規成果物の保存の有無にかかわらず、未完了の完了処理を引き継ぐ。適用の要否は元の作業内容に基づいて判断する。最初から成果物の確定を伴わない単発の調査回答や、サブエージェントが委譲元へ返す完了報告は対象から外れる。`atk wi process-loop all-repos`のリポジトリ担当は委譲元が常駐プログラムのため、メインエージェントとして本スキルの対象に含める（`agent-toolkit:process-wi`の`references/all-repos-orchestration.md`「担当の役割」）。
適用するプロジェクト規範に基づいて起動を省略するかどうかは、`agent-toolkit/rules/01-agent.md`「方針が衝突する場合の優先順位」に沿ってメインエージェントが判断する。
本スキルの起動を省略する場合は、`references/termination-evidence.md`を直接読み、省略した旨を記録したうえで通常の報告をする。

各報告は、入力内容が確定した時点で内容を確認し、それぞれ独立した節として届ける。報告の順序は次のとおりとする。

- 振り返りを起動する前に`## 作業完了報告`を届ける。
- 振り返りでAWIを1件以上投入する場合は、`agent-toolkit:session-review`の`SKILL.md`「工程」におけるWI投入担当の起動規定に従い、投入前に`## 振り返り結果の予告`を届ける。投入後に届ける`## AWI投入結果報告`を最終報告とする。
- 投入件数が0件、未実施、または失敗した場合は、振り返り完了後に届ける`## 振り返り結果報告`を最終報告とする。

報告の送達方法は`agent-toolkit:user-confirmation-and-report`「手段の選択」に従う。Claude Code環境では、途中報告・最終報告のいずれも`mcp__agent-toolkit__send_to_user`を使用する。最終報告の末尾にのみ「以上で、このセッションの作業は全て終わりました」と記載する。回答や委譲先の結果を待つために作業を中断した報告は最終報告に当たらないため、この固定文は含めず、再開条件を記載する（`references/report-formats.md`「振り返り結果報告のフォーマット」）。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `references/quality-standards.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/references/quality-standards.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../process-wi/references/all-repos-orchestration.md#「担当の役割」` | 判断 | `section:agent-toolkit:*/skills/process-wi/references/all-repos-orchestration.md#「担当の役割」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../session-review/SKILL.md#「工程」` | 判断 | `section:agent-toolkit:*/skills/session-review/SKILL.md#「工程」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/SKILL.md#「手段の選択」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「手段の選択」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../session-review/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/session-review/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../managed-temp/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/managed-temp/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/SKILL.md#「原文の保持と取り込み」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「原文の保持と取り込み」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../single-lane-process/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/single-lane-process/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/SKILL.md#「割り込み・訂正・中止」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「割り込み・訂正・中止」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../wi-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../wi-standards/SKILL.md#「投入と取得」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/SKILL.md#「投入と取得」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../add-awi-by-user/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/add-awi-by-user/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../bugfix/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/SKILL.md` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 通常起動時と、作業完了報告の到達水準を判定する前 | 起動のwhole_contract_pathまたはその正式な解決結果が示す現行の作業契約 | 起動時 | `skill:completion-report` | `input:作業契約` |
| 作業完了報告の本文を書く前 | `references/report-formats.md`の「検証結果と実行記録」「作業完了報告のフォーマット」 | 判断 | `section:agent-toolkit:*/skills/completion-report/references/report-formats.md#「検証結果と実行記録」「作業完了報告のフォーマット」` | |
| 振り返り結果報告かその予告を書く前 | `references/report-formats.md`の「振り返り結果報告のフォーマット」 | 判断 | `section:agent-toolkit:*/skills/completion-report/references/report-formats.md#「振り返り結果報告のフォーマット」` | |
| AWI投入結果報告を書く前 | `references/report-formats.md`の「AWI投入結果報告のフォーマット」 | 判断 | `section:agent-toolkit:*/skills/completion-report/references/report-formats.md#「AWI投入結果報告のフォーマット」` | |
| 報告用UWIを書く前 | `references/report-formats.md`の「報告用UWIのフォーマット」 | 判断 | `section:agent-toolkit:*/skills/completion-report/references/report-formats.md#「報告用UWIのフォーマット」` | |
| 手順4で、協調モードの確認済みの公開範囲がcommitより先へ及ぶ場合 | `agent-toolkit:commit`の`references/publish.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/publish.md` | |
| Stop hookが本スキルの起動不足を示して遮断した時と、ユーザーの中止・置換、回答や委譲先の待機、技術的不成立の判断を記録する時 | `references/termination-evidence.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/references/termination-evidence.md` | |

## 完了の到達水準

読込表で受け取った現行の作業契約、および`share/work-contract.md`の工程境界における要求と成果の確認手順に従い、各要求と残る完成条件を「証拠」「明示的な取り下げや置換」「ユーザー対応が必要な未完了事項」に対応付ける。未処理の追加入力があれば統合し、残っている元の工程を完了させてから全体を判定する。

「完了」とは、対象全体および既知の問題への対応が済み、ユーザーによる操作が不可欠な事項だけが残っている状態を指す。代表箇所や一部のみの変更にとどまる場合は、途中経過として報告する。
環境上の制約などで十分に確認できない場合は、実施可能な範囲で検証し、その結果とともに、実機確認などを実施できなかった範囲と理由を報告する。
確認できていない範囲があることだけを理由として、個別のUWIやユーザーへの操作依頼を追加することは控える。
既知の不具合や未達成の明示要件は、未達事項として区別して記載する。

## 工程

各番号の手順は必要な入力が揃い次第進める。前の手順の出力に依存しない作業は、`agent-toolkit:session-review`の実行中も含めて並行して進めてよい。
本スキルの起動と本書の通読は工程の開始を意味し、手順5の作業完了報告から手順9の終了処理までを同一の応答内で一続きに進める。中断から再開した場合は、元の要求、記録済みの未完了工程、および工程ごとの終了根拠を確認し、最初の未完了工程から作業を続行する。主要な成果物の保存や検収の成功は、該当工程の終了根拠として扱う。登録済みAWIの検収後に残る振り返りや最終報告、既存コミットの検収後に残る公開後検収、レビュー対応の保存後に残る全指摘の検収なども、同様の判断で引き継ぐ。

1. 常時稼働サーバーへの反映: 対象プロジェクトが開発機上で常時稼働するサーバーを持ち、そのプロジェクト規範やタスクランナー定義で反映手順と稼働確認手順の両方が定められている場合に実施する。起動元の工程が先行して反映した結果（反映済みHEADの一意な短縮OID、反映コマンドの終了ステータス、稼働確認結果）があり、報告時点の主作業ツリーで短縮OIDを解決した結果がHEADと一致し、両方の処理が成功していれば再反映を省略する。それ以外の場合は現行のHEADを反映し、稼働を確認する。報告には再反映の有無と稼働確認の結果を含める。反映しない場合はその理由と未反映の状態を報告し、ユーザーの対応が必要な残作業については`agent-toolkit:user-confirmation-and-report`「手段の選択」に沿って個別UWIの登録要否を判定する。自明な反映手順の案内は報告に添える。
   起動元の工程が反映を先行して開始できるのは、反映処理が対象リポジトリへの書き込みを行わず、公開向けの入力を生成せず、公開操作や成果物・排他リソースと競合しない場合に限る。
2. メインエージェント自身が作成した作業用領域は、`agent-toolkit:managed-temp`に定められた寿命に従って回収する。元資料から再生成できる抽出・比較用の一時複製や検証環境は、すべての消費工程と外部プロセスの終了後に`rm -rf -- <作成した絶対パス>`で回収する（worktreeは事前に`git worktree remove`で削除する）。セッションのmanaged-temp本体や、後続ターンへの入力・証拠・引き継ぎ記録は、7日間保持する領域に残す。
   process-wiの登録済みレーンリソースはこの時点では回収せず、追加作業や振り返り後の工程に備えて保持する。全工程と外部プロセスの終了後に、通常終了であれば`atk agents-exit-session`が、process-loopであれば子プロセスの終了後処理が、`atk lane delete`と同等の条件で回収する。未統合、未コミット差分（dirty）、稼働中、状態不明、または部分的に失敗した対象は、成果、理由、および残る操作を記録して保持し、次回開始時の`atk lane delete --list`で再判定する。
3. 当該セッションでユーザーが確定した判断事項を`agent-toolkit/rules/01-agent.md`「協調と自律」の保存対象と照らし合わせ、未保存の判断の保存またはAWIの投入を完了する。保存する前に、`agent-toolkit:user-confirmation-and-report`の`SKILL.md`「原文の保持と取り込み」に記載されている、程度の限定や確度を保持する規定、および確度を伴う発話の保存に関する規定を適用する。
4. 協調モードで作成したコミットがあり、確認済みの公開範囲がコミットよりも先まで及ぶ場合は、`agent-toolkit:commit`の`references/publish.md`に従ってその範囲の公開を完了する。`agent-toolkit:process-wi`および`agent-toolkit:single-lane-process`は、それぞれの工程内で公開する。
5. 実施した公開範囲とリリース判定の結果を含む作業完了報告を、`references/report-formats.md`「作業完了報告のフォーマット」に従って1回届け、同一の応答内で手順6に進む。
6. `agent-toolkit:session-review`を起動する。起動を省略できるのは次の3つの場合に限られ、その理由は`references/report-formats.md`に定義されている所定の値を用いて手順8の報告に記載する。個別の指摘やユーザー発話への対応が完了していること、追加の調査コストや所要時間、実装変更の有無などは、起動を省略する根拠にならない。
   - 適用するプロジェクト規範に振り返りの省略が明記されている場合。
   - 既存の振り返りの成果がセッション記録の最後までカバーしている場合（後述の再起動省略の条件）。
   - 確認への回答や委譲先の結果を待つために作業を中断した場合（再開後は残りの手順から続行する）。

   本スキルの起動後にユーザーから発話（割り込み）があった場合、その内容は振り返りの入力として扱う。準備処理の前に届いた発話は初回の準備に含め、準備処理の後に届いた発話は同スキル工程6の再実行条件に該当する。割り込みへの対応は`agent-toolkit:user-confirmation-and-report`「割り込み・訂正・中止」に従い、同一ターン内で本手順の起動または再実行へ戻る。

   同一セッションで振り返りをすでに実施している場合でも、前回の準備時刻（`atk run-script session-review-prepare`が出力するJSONの`prepared_at`）より後に本体作業を継続した場合や、準備結果に含まれていない実際の改善点が届いた場合は、再度起動する。準備後のユーザー発話や改善点の扱いは同スキルの工程6が定め、前回投入したAWIは既存項目として重複判定される。前回の振り返り自体の起草、投入、待機、検収や、取り込み済み行の転記・再掲は、前回実行の継続分として扱う。実施済みの振り返りについて再起動を省略できるのは、追加の作業、ユーザー発話、未取り込みの改善点のいずれも存在しない場合に限られ、再起動の要否はこの追加分の有無に基づいて判断する。
7. 手順6によって作業ツリーやコミット履歴が変更され、起動元の工程がその公開を定めている場合は、公開を完了する。起動元の工程が報告前の確認を定めている場合は、その確認も完了する。
8. 振り返りの最終報告を作成する。対策のAWIを1件以上投入した場合は`references/report-formats.md`「AWI投入結果報告のフォーマット」、0件、未実施、または失敗の場合は同資料「振り返り結果報告のフォーマット」に従う。

   自律モードでは、作業完了報告と振り返り結果を元に、ユーザーが結果を把握して必要な是正を指示できるようにするための事後承認型UWIを1件作成する。これを「報告用UWI」と呼び、本手順をその定義元とする。本文は`references/report-formats.md`「報告用UWIのフォーマット」、投入は`agent-toolkit:wi-standards`の事後承認型の書式に従う。保存結果は`atk wi add`および`atk wi edit`の成功メッセージと警告を確認して検収する（`agent-toolkit:wi-standards`の`SKILL.md`「投入と取得」）。保存の検収をもってユーザーへの伝達完了とみなし、画面上の可視発話の有無とは個別に判定する。投入に失敗した場合は、既存のWI投入失敗時の対応手順に従う。

   `atk run-skill`による実行時は、報告用UWIの投入時に`--source run-skill --scope <実行したスキル名>`を指定し、本文先頭のfrontmatterに`run_skill_report: true`を設定する。`atk wi add`と`atk wi edit`は、起動元の実行識別子を`run_skill_run_id`に記録する。同一実行の再開や報告更新を行う場合でも、この区分を維持する。報告本文の作成と十分性の判断は本スキルが担い、起動元は保存されたUWIとGitの反映結果から、本文のコミットおよびupstream設定時のプッシュが完了していることを確認する。保存未達はタイムアウトと区別された非ゼロ終了となるため、保存CLIの警告を解消し、同一の会話内で報告の保存を完了させる。

   続いて最終報告を1回届ける（自律モードでは報告用UWIの保存を検収してから届ける）。`send_to_user`が成功した時点で本文の送達は完了したものとし、要約や受領確認のメッセージを追加することなく、同一の応答内で手順9に進む。ツールが利用できない環境では、最終報告の内容自体をターンの最終出力とする。
9. 起動元の工程が報告完了後に実行する処理（`agent-toolkit:process-wi`の`atk agents-exit-session`など）を定めている場合は、最終報告と同一の応答内でその処理を実行する。別の応答へ持ち越すと、処理が実行されないまま`atk wi process-loop`が停止してしまう。新たな改善点がない場合、報告後に実行する処理はこの指定工程のみとし、通常の作業ターンへは次の指示を受けてから戻る。
   送達後に実際の新たな改善点が生じた場合は、`agent-toolkit:session-review`の工程6に戻って内容を取り込み・分析し、手順8を再実行して報告用UWIと結果を更新したうえで、更新後の最終報告を届けてから本手順へ進む。
   すべての工程を完了して通常のターンのみを終了する場合、`send_to_user`で最終報告を送達済みであれば`…`のみを出力する。`atk agents-exit-session`でセッションを停止した分岐では、出力もそこで終了する。送達の失敗、実害防止のためのStop hookによる遮断、あるいは新しいユーザー指示があった場合は、それぞれの復旧・続行手順に従う。セッションを終了する前に、元の要求と実際の送達・対応結果を突き合わせて未達事項や残工程を確認し、不足している工程があれば継続して実施する。

最終送達後の終了指示の突き合わせおよび再検証手順は、`docs/development/audit-records.md`の「completion-report：最終報告送達後のターン終端：2026年10月9日」を参照する。

Claude Codeの終了時に本スキルの起動漏れを警告された場合は、適用可能な省略条件を`references/termination-evidence.md`で確認する。
起動が必要な場合は手順1から、すでに起動済みの場合は残っている手順から作業を続ける。
ユーザーによる作業の中止や置換、回答や委譲結果の待機、技術的な続行不能に関する記録手順も同資料で定めている。

完了判定は本節に定めた各手順が満たされたかどうかで行い、完了を自己申告する状態フラグは作成しない（セッション途中でフラグが失われると誤警告が繰り返され、正当な工程が遮断される原因となるためである）。
