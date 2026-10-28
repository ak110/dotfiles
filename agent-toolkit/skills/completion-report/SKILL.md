---
name: completion-report
user-invocable: false
description: >
  作業を完了したメインエージェントが、作業完了報告から常時稼働サーバーへの反映、managed-tempの回収、
  公開、終了処理までを一続きに行うときに起動する。
  単発の調査依頼への回答、単純な事実回答、確認質問、途中報告、待機・失敗報告などでは起動しない。
  適用するプロジェクト規範によって起動を省く場合は、本スキルを起動せずにreferences/termination-evidence.mdを直接読む。
---

# 作業完了報告とセッションの完了処理

作業の結果は、不都合な結果も含めて正直に報告する。この原則は、作業完了報告、振り返り結果報告、AWI投入結果報告のいずれにも適用する。

反映、公開、UWI保存、セッション終了の各操作は、対象を操作するCLIや必要な参照資料が整っている環境で実行する。操作機能がない環境では、得られた成果と確認済みの証拠に基づいて結果を説明し、未実施の反映・公開・保存を区別して明記する。残る操作は、それらの機能が利用できるClaude CodeやCodexに引き継ぐ。ここで作成する報告の下書きには[文章の品質基準](references/quality-standards.md)を適用する。

本スキルは、実装、計画、AWI投入などの成果物を確定した作業をメインエージェントが完了したときに適用する。元の作業が適用対象であれば、中断理由、担当の交代、後続担当による新規成果物の保存の有無にかかわらず、未完了の完了処理を引き継ぐ。適用の要否は元の作業内容に基づいて判断する。最初から成果物の確定を伴わない単発の調査回答や、サブエージェントが委譲元へ返す完了報告は対象から外れる。`agent-toolkit:process-wi-all-repos`では統括が本スキルで報告を集約し、リポジトリ担当は成果と残工程を統括へ返す。
適用するプロジェクト規範に基づいて起動を省略するかどうかは、`agent-toolkit/rules/01-agent.md`「方針が衝突する場合の優先順位」に沿ってメインエージェントが判断する。
本スキルの起動を省略する場合は、`references/termination-evidence.md`を直接読み、省略した旨を記録したうえで通常の報告をする。

元の成果に対する報告とセッションの作業完了報告を、`## 作業完了報告`の1つの本文へまとめる。
登録結果、レビュー結果、公開担当の返却などを、その本文を作る入力として扱う。必要な報告をすべて届けてから終了する。
振り返りは`agent-toolkit:session-review-batch`が非活動のセッションをまとめて処理する。本スキルでは同期振り返りを工程と送達条件の対象外とする。
手動の`agent-toolkit:session-review`は独立した依頼として実施する。

報告の送達方法は`agent-toolkit:user-confirmation-and-report`「手段の選択」に従う。
Claude Codeでは`mcp__agent-toolkit__send_to_user`、Codexの通常のターン終了では必要なツール処理後の`final`を使い、その本文自体を作業完了報告にする。
最終報告の末尾にのみ「以上で、このセッションの作業は全て終わりました」と記載する。
回答や委譲結果の待機、中止・置換、技術的不成立は通常完了と区別し、残工程と再開条件を報告する。

## 名指し参照の読込方法

本文で名指しした資料と読込表の資料は、条件が成立したらその判断や操作の前に自分で全文読み、指定箇所に注目してから、その基準で判断する。読込表の列の使い方は`agent-basics:writing-standards`の`references/quality-principles.md`「読込表の読み方」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../single-lane-process/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/single-lane-process/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../session-review/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/session-review/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../session-review-batch/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/session-review-batch/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../process-wi-all-repos/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi-all-repos/SKILL.md` |
| 本文の参照基準を適用する前 | `../user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `references/quality-standards.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/references/quality-standards.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/references/communication-methods.md#「手段の選択」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/references/communication-methods.md#「手段の選択」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../managed-temp/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/managed-temp/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/references/user-utterance.md#「原文の保持と取り込み」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/references/user-utterance.md#「原文の保持と取り込み」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/references/user-utterance.md#「割り込み・訂正・中止」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/references/user-utterance.md#「割り込み・訂正・中止」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../wi-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../wi-standards/SKILL.md#「投入と取得」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/SKILL.md#「投入と取得」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../add-awi-by-user/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/add-awi-by-user/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../bugfix/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/SKILL.md` |
| 読込表と本文の名指し参照を読む前 | `agent-basics:writing-standards/references/quality-principles.md`の「読込表の読み方」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/quality-principles.md#「読込表の読み方」` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 通常起動時と、作業完了報告の到達水準を判定する前 | 起動のwhole_contract_pathまたはその正式な解決結果、メインでは自身のmanaged-temp直下にある現行の作業契約 | 起動時 | `skill:completion-report` | `input:作業契約` |
| 作業完了報告の本文を書く前 | `references/report-formats.md`の「検証結果と実行記録」「作業完了報告のフォーマット」 | 判断 | `section:agent-toolkit:*/skills/completion-report/references/report-formats.md#「検証結果と実行記録」「作業完了報告のフォーマット」` | |
| 振り返り結果報告かその予告を書く前 | `references/report-formats.md`の「振り返り結果報告のフォーマット」 | 判断 | `section:agent-toolkit:*/skills/completion-report/references/report-formats.md#「振り返り結果報告のフォーマット」` | |
| AWI投入結果報告を書く前 | `references/report-formats.md`の「AWI投入結果報告のフォーマット」 | 判断 | `section:agent-toolkit:*/skills/completion-report/references/report-formats.md#「AWI投入結果報告のフォーマット」` | |
| 報告用UWIを書く前 | `references/report-formats.md`の「報告用UWIのフォーマット」 | 判断 | `section:agent-toolkit:*/skills/completion-report/references/report-formats.md#「報告用UWIのフォーマット」` | |
| 手順4で、協調モードの確認済みの公開範囲がcommitより先へ及ぶ場合 | `agent-toolkit:commit`の`references/publish.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/publish.md` | |
| Stop hookが本スキルの起動不足を示して遮断した時と、ユーザーの中止・置換、回答や委譲先の待機、技術的不成立の判断を記録する時 | `references/termination-evidence.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/references/termination-evidence.md` | |

メインの作業契約が未作成の場合は、期待パスを示す警告に従い、`share/work-contract.md`の「作成と所有」に基づいて作成し、全文を取得する。

## 完了の到達水準

読込表で受け取った現行の作業契約、および`share/work-contract.md`の工程境界における要求と成果の確認手順に従い、各要求と残る完成条件を「証拠」「明示的な取り下げや置換」「ユーザー対応が必要な未完了事項」に対応付ける。未処理の追加入力があれば統合し、残っている元の工程を完了させてから全体を判定する。

「完了」とは、対象全体および既知の問題への対応が済み、ユーザーによる操作が不可欠な事項だけが残っている状態を指す。代表箇所や一部のみの変更にとどまる場合は、途中経過として報告する。
環境上の制約などで十分に確認できない場合は、実施可能な範囲で検証し、その結果とともに、実機確認などを実施できなかった範囲と理由を報告する。
確認できていない範囲があることだけを理由として、個別のUWIやユーザーへの操作依頼を追加することは控える。
既知の不具合や未達成の明示要件は、未達事項として区別して記載する。

要求された結果と十分な検証がそろったら、必要な作業完了報告と終了処理を完了してセッションを終了する。
完了済みの作業は、新しい要求、結果に影響する変更、新しい失敗、未検証の完成条件を根拠に再開する。
報告の不足は保存済みの証拠で補い、状況確認だけを理由に検証済みの作業を繰り返さない。

## 工程

各番号の手順は必要な入力が揃い次第進め、前の出力に依存しない処理は並行してよい。
起動と通読は工程の開始であり、成果物の保存やレビューの成功はその工程の結果として扱う。
中断から再開するときは、要求ごとに実際の成果・送達結果・未完了工程を確認し、最初の未完了工程から続ける。
局所工程から受け取った結果と残る報告事項を手順5の同じ報告本文へ集約し、元の作業の完了処理として送達する。

1. 常時稼働サーバーへの反映: 対象プロジェクトが開発機上で常時稼働するサーバーを持ち、そのプロジェクト規範やタスクランナー定義で反映手順と稼働確認手順の両方が定められている場合に実施する。起動元の工程が先行して反映した結果（反映済みHEADの一意な短縮OID、反映コマンドの終了ステータス、稼働確認結果）があり、報告時点の主作業ツリーで短縮OIDを解決した結果がHEADと一致し、両方の処理が成功していれば再反映を省略する。それ以外の場合は現行のHEADを反映し、稼働を確認する。報告には再反映の有無と稼働確認の結果を含める。反映しない場合はその理由と未反映の状態を報告し、ユーザーの対応が必要な残作業については`agent-toolkit:user-confirmation-and-report`「手段の選択」に沿って個別UWIの登録要否を判定する。自明な反映手順の案内は報告に添える。
   起動元の工程が反映を先行して開始できるのは、反映処理が対象リポジトリへの書き込みを行わず、公開向けの入力を生成せず、公開操作や成果物・排他リソースと競合しない場合に限る。
2. メインエージェント自身が作成した作業用領域は、`agent-toolkit:managed-temp`に定められた寿命に従って回収する。元資料から再生成できる抽出・比較用の一時複製や検証環境は、すべての消費工程と外部プロセスの終了後に`rm -r -- <作成した絶対パス>`で回収する（worktreeは事前に`git worktree remove`で削除する）。セッションのmanaged-temp本体や、後続ターンへの入力・証拠・引き継ぎ記録は、7日間保持する領域に残す。過去のエージェントが残した資源と誤変更も、`agent-toolkit/rules/02-agent-operations.md`「プロセス終了の安全規定」で調査し、不要な範囲を回収・是正する。
   process-wiの登録済みレーンリソースはこの時点では回収せず、追加作業や公開後の工程に備えて保持する。全工程と外部プロセスの終了後に、通常終了であれば`atk agents-exit-session`が、process-loopであれば子プロセスの終了後処理が、`atk lane delete`と同等の条件で回収する。未回収の場合はメインが共通判断で保持理由を調べ、解消できた対象を同じ回収操作で再判定する。保持が必要な対象は、保護する成果や現在の担当、残るユーザー判断、および再開条件を報告する。
3. 当該セッションでユーザーが確定した判断事項を`agent-toolkit/rules/01-agent.md`「協調と自律」の保存対象と照らし合わせ、未保存の判断の保存またはAWIの投入を完了する。保存する前に、`agent-toolkit:user-confirmation-and-report`の`references/user-utterance.md`「原文の保持と取り込み」に記載されている、程度の限定や確度を保持する規定、および確度を伴う発話の保存に関する規定を適用する。
4. 協調モードで作成したコミットがあり、確認済みの公開範囲がコミットよりも先まで及ぶ場合は、`agent-toolkit:commit`の`references/publish.md`に従ってその範囲の公開を完了する。`agent-toolkit:process-wi`および`agent-toolkit:single-lane-process`は、それぞれの工程内で公開する。
5. 元の要求が求めた結果を各工程の成果から確認し、`references/report-formats.md`「作業完了報告のフォーマット」の本文を確定する。成果、検証、公開・反映・回収の到達点、未達、自ら確定した判断、投入したWIを同じ報告へまとめる。未送達の必要事項があれば補う。追加の送達は必要な未送達事項がある場合に限る。
   改善点の報告は、その場の直接的原因と影響の最低限調査、必要な是正、担当外の処置の引き継ぎまでを終える。確定した不具合の処置は`agent-toolkit/rules/01-agent.md`「完遂と先送り」に従う。非同期の振り返りが残る場合も、元作業の送達と終了を進める。
6. 自律モードでは、確定した作業結果をユーザーが確認し是正を指示できるよう、事後承認型の報告用UWIをセッションにつき1件保存する。これを「報告用UWI」と呼ぶ。本文は`references/report-formats.md`「報告用UWIのフォーマット」、投入は`agent-toolkit:wi-standards`に従い、`atk wi add`と`atk wi edit`の成功メッセージと警告を検収する。原稿作成と保存受付に続けて、実際の保存成功と警告を検収した時点でユーザーへの伝達を完了とする。失敗した場合は既存のWI投入失敗時の対応へ進み、同じUWIの保存を回復する。
   `atk run-skill`による実行では`--source run-skill --scope <実行したスキル名>`とfrontmatterの`run_skill_report: true`を保持する。保存CLIが付与する`run_skill_run_id`を再開・更新でも維持し、起動元が渡した報告先リポジトリを使用する。報告先は起動元の指定から決め、実行cwdとは区別する。起動元は本文、commit、およびupstream設定時のpushを検収する。batchの振り返り結果はその実行の1件の報告へ集約し、対象セッションの作業完了報告とは別に保存する。
7. 確定した本文をホストに合う手段で1回送達して終了する。Claude Codeの`send_to_user`は成功を確認し、起動元が要求する報告後の終了操作を同じ応答で実施する。通常ターンだけを終える場合、送達済みなら`…`のみを出力する。
   Codexで通常の`final`を使う場合は、必要なツール処理と未達事項の確認を先に終え、手順5の本文自体を`final`にする。必要な操作をすべて終えた後に最終応答を届ける。`atk agents-exit-session`などによってホスト本体を停止する場合は、報告用UWIの保存検収と必要な可視報告を先に終え、停止操作を最後のツール呼び出しとする。Codexで停止前に必要な可視報告は`commentary`で送達する。報告の送達は停止操作の前に完了させる。
   回答・委譲結果の待機、中止・置換、技術的不成立は`references/termination-evidence.md`の契約を保持する。送達の失敗、実害防止のStop hookによる遮断、新しいユーザー指示は、それぞれの回復・続行手順へ進む。送達後に実際の新たな問題が分かった場合は、その問題を調査・処置し、既存の報告用UWIと報告内容を更新する。送達済み作業の再検証は、実際に判明した新たな問題に必要な範囲に限る。振り返りは別担当による非同期処理のままとする。

最終送達後の終了指示の突き合わせおよび再検証手順は、`docs/development/audit-records.md`の「completion-report：最終報告送達後のターン終了：2026年10月9日」を参照する。

Claude Codeの終了時に本スキルの起動漏れを警告された場合は、適用可能な省略条件を`references/termination-evidence.md`で確認する。
起動が必要な場合は手順1から、すでに起動済みの場合は残っている手順から作業を続ける。
ユーザーによる作業の中止や置換、回答や委譲結果の待機、技術的な続行不能に関する記録手順も同資料で定めている。

完了判定は本節に定めた各手順が満たされたかどうかで行い、完了を自己申告する状態フラグは作成しない（セッション途中でフラグが失われると誤警告が繰り返され、正当な工程が遮断される原因となるためである）。
