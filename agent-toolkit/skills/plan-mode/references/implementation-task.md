# 計画実装担当タスク

指定されたコミット単位を実装し、近接検証、差分検収、stage、commitまで完了する。
計画にコミット単位の指定が無い場合は、計画全体を1つの原子的な実装単位として扱う。
同じ計画ファイルに属する単位は、指定された1つの書込担当が同じworktreeで順次実装する。

## 入力

- 計画ファイル、対象worktree、プロジェクト規範の絶対パス
- 実装するコミット単位、その目的及び変更説明
- 通常実装モードのレビュー修正では、レビュー対象の最終HEAD完全OID、採用指摘IDと統合先の実装単位commit完全OIDの対応表
- 通常実装モードのレビュー修正では、実行系、継続又は新規起動に用いる識別子、前担当の終端確認及びexecutorが検収したHEAD・作業ツリー・検証結果
- 適用する作成規範スキル名
- 1件以上のソート済みフィードバックファイル名一覧
- 追加指示、許容済みの挙動変化。該当しない値は`なし`
- git操作に用いるworktree絶対パス、複製元と対象外worktree

入力が欠ける場合は推測せず`needs_escalation`で返す。
本タスク以外の委譲の内部資料は読まず、計画と作成規範スキルを作業契約の正本とする。

## 実装

通常実装モードでは手順3〜7の通常実装手順を実行する。
レビュー修正モードでは手順1〜2を実行した後、手順3・4を採用指摘全体へ一括適用しない。
レビュー修正モードでは`レビュー修正の単位別反復`を実行する。
レビュー修正モードでは手順5〜6の通常commit経路を実行せず、後段の履歴統合手順だけをcommit経路とする。

1. 計画、プロジェクト規範、該当する作成規範スキルを全文読む。
   人間向け固定領域（`## 概要`から`## 変更履歴`まで）をユーザー要求の正本、
   その後の実装者向け領域を実装詳細の正本として扱う
2. 指定worktreeの現行`HEAD`、作業ツリー、対象ファイル全文を実測する。
   作業ディレクトリを自己解決せず、git操作は受領した絶対パスへ`git -C`を付ける
3. 計画契約を守って指定単位へ帰属する変更だけを実装し、対象実装の詳細は現行コードから再調査する。
   実装中に判明した変更または正式コマンドが生成した変更は、計画目的へ帰属し、
   ユーザー合意と公開契約及び安全境界を変えない場合に含める。
   追加ファイル、発生理由、必要性は計画との差異として返す。
   現行実装が計画の前提と異なる場合は、
   利用者向け成果を維持できる範囲で手順を調整し、差異を報告する
4. 対象に近いformat、lint、testを実行し、警告を解消する
   共有の判定処理、振り分け処理、解析処理を変更する場合は、変更分岐へ到達する全呼び出し元と未変更の既存test class、
   契約を示すdocstring・コメントを列挙する。配列や反復blockを扱う場合は、0件、1件、複数件、異種混在、
   局所識別子の対応を検証し、列挙と実行結果を返す。この追加検証は該当する共有分岐又は反復構造の変更に限定する
5. 通常実装モードでは、`git diff`、計画記載の目的と変更説明、変更後文面を照合し、担当単位へ帰属する変更だけをstageする。
   レビュー修正モードでは本項の通常commit用stageを行わず、履歴統合直前に修正差分だけをstageする
6. 通常実装モードでは、`agent-toolkit:commit`に従って通常commitを実行し、commit実体とcleanな作業ツリーを確認する。
   レビュー修正モードでは本項を実行せず、後段の履歴統合手順で指定したamendまたはfixupとautosquashだけをcommit経路とする
7. 通常実装モードではcommit、検証、計画との差異を呼び出し元へ返す。
   レビュー修正モードでは履歴統合後の検証、履歴実体、計画との差異を呼び出し元へ返す。
   完了報告はツール戻り値で1回返し、`SendMessage`で能動送付しない。
   書込担当は人間向け固定領域と`## 進捗ログ`を編集せず、進捗の追記は呼び出し元が行う
通常実装モードのレビュー修正では、書込担当の完了報告に、各再判定phase（`fixup:<単位順>`、`autosquash`、`amend`）の`rewrite_guard`を反復した配列を必ず含める。
executorは書込担当の完了後にphaseごとの`rewrite_guard`を検収し、executorの完了報告にも同じ反復証跡を必ず含める。
`rewrite_guard`のphaseは通常実装モードのレビュー修正だけに記録し、レビュー修正以外の通常実装モードでは`rewrite_guard: not_applicable`とする。

レビュー指摘の修正を受け取った場合は、履歴書換えを開始する前に、指摘が根拠とする原文と対象への適用条件を確認し、指摘の成立性と修正方法を別々に確定する。
各指摘の事実と違反契約を自身でも実測し、通常運用の再現経路と入力主体へ照合して問題を再現する。
検証済みの実際値、期待値、違反契約、対象への適用根拠、保持契約が指摘ごとにそろうことを確認する。
根拠または適用条件が不足する指摘は再取得するか`未検証`へ分離する。
いずれかが欠ける場合は推測して修正せず`needs_escalation`で返す。
採否確定前に指摘の成立性と修正方法を独立に判定し、採用済みと確定した指摘だけを修正対象とし、対応する実装単位commitを履歴統合の対象へ含める。
通常実装モードでは、レビュー対象の最終HEAD完全OIDと、採用指摘IDと統合先の実装単位commit完全OIDの対応表を受け取る。
現行HEADと受領したレビュー対象の最終HEAD完全OID、各OIDの履歴上の一致を確認する。
対応表又は最終HEAD完全OIDが不足する場合、OIDが履歴と一致しない場合、対象commitがpush済みである場合、複数単位へ不可分にまたがる場合、各中間commitの公開契約を維持できない場合は、新規commitへフォールバックせず`needs_escalation`で返す。
`agent-toolkit:reviewee-standards`と該当する作成規範スキルを適用し、指摘の採否と修正を確定する。
同スキルで不採用と確定した候補は修正せず`needs_escalation`で返す。
不採用、または採否未確定（`未検証`を含む）の指摘は修正対象及び`target_oids`へ含めず、修正差分を適用せず、履歴と作業ツリーを変更しないまま`needs_escalation`で返す。
レビュー担当の修正方針を要件へ昇格させず、採否を独立に確定する。
レビュー修正の採否、対象実装単位及び対応表が確定するまで、修正差分を適用せず、履歴を変更しない。
`agent-toolkit:commit`の`references/history-rewrite.md`を全文読み、履歴書換え前のベース、HEAD、実装単位の順序、各完全OID、親子関係、commit件数、件名とtrailer、各commitの差分帰属を保持する。

### レビュー修正の単位別反復

レビュー修正モードでは、採用指摘を統合先の実装単位ごとに履歴順で処理する。
手順3・4を採用指摘全体へ一括適用せず、各反復で当該単位の修正だけを扱う。
過去単位の反復では、当該単位の修正を実装し、近接検証を実行して警告を解消する。
修正差分をstageした後、各fixup作成前の`fixup:<単位順>` phaseで書込担当が再判定と遮断を完了する。
修正差分だけをstageし、対応する`fixup`を作成する。
対象OID、件名及び作業ツリーがcleanであることを確認してから次の過去単位へ進む。
過去単位と最終単位が対象の場合、autosquash前の反復対象を過去単位だけに限定する。
全過去単位の反復後、書込担当が`autosquash` phaseの履歴書換え直前の再判定と遮断を完了する。
最終単位だけが対象の場合は、最終単位の修正を実装し、近接検証を実行してstageした後、`amend` phaseの再判定と遮断を完了する。
過去単位と最終単位が対象の場合、autosquash成功後に`git rev-parse HEAD`で書換え後HEADの完全OIDを取得し、最終単位の修正差分を実装し、近接検証を実行してstageする。その後、`amend` phaseの再判定と遮断を完了し、書換え後HEADへamendする。
実行系は開始前に確定し、Codex経路では元の実装担当threadを継続し、Claude経路では旧担当の終端を確認して検収済み状態を渡した新しい書込担当を起動する。開始後は同じ書込担当が再判定、履歴統合、amendを完結する。
Codex経路では同一の書込担当を継続し、中間引継ぎを行わない。
各操作後に対象OID、件名、作業ツリーのclean状態を確認する。

複数の過去単位が対象の場合は、履歴順に1単位ずつ、その単位へ帰属する修正差分だけを適用してstageし、対応するfixupを作成する。
各fixup作成後に対象OID、件名及び作業ツリーがcleanであることを確認してから、次の単位の修正差分を適用する。
最終単位だけが対象の場合は最終単位の修正差分だけを適用し、近接検証を実行してstageした後、`amend` phaseの再判定が成功した場合だけ`git commit --amend`を実行する。
過去単位だけが対象の場合は、全過去単位のfixup作成後に`autosquash` phaseの再判定が成功した場合だけ、`GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash <base>`を実行する。
最終単位と過去単位の両方が対象の場合は、過去単位のautosquash成功後に`git rev-parse HEAD`で書換え後HEADの完全OIDを取得し、書換え前の各対象OIDと書換え後の全実装単位OIDの対応を履歴検収用に保持する。autosquash成功後の2回目のpush済み判定対象を当該OIDへ置換し、開始済みの同じ書込担当が最終単位の修正差分だけを実装し、近接検証を実行してstageする。その後、`amend` phaseの再判定が成功した場合だけ書換え後HEADへamendする。
いずれもレビュー修正専用commitを残さない。
開始済みの書込担当はfixup作成前、autosquash直前、amend直前に、`git remote`で全remoteを列挙する。
各再判定phaseで`git rev-parse --is-shallow-repository`を実行し、終了コードと出力を確認する。終了コード0かつ出力が`false`の場合だけ広告照会と祖先判定を継続する。終了コード0で出力が`true`の場合、終了コードが非0の場合、出力が`true`と`false`のいずれでもない場合は、履歴を書き換えず`needs_escalation`で返す。
各remoteについてfetch URL集合（`git remote get-url --all <remote>`）とpush URL集合（`git remote get-url --all --push <remote>`）を取得する。fetch URL列挙とpush URL列挙のremote別終了コードを保持するが、URL値は保持しない。全remoteの集合を1つの照会URL集合へ統合し、URL文字列が完全一致するものだけ重複を除き、重複排除前後の照会URL件数を計数する。以後の広告照会・不足OIDのfetch・終了コードは照会URL単位で扱い、各照会URLへ`git ls-remote --heads --tags --refs <URL>`を実行する。全照会URL endpointの広告取得・不足OID fetch・再照合を完了させる。
URL取得、重複排除又は広告照会に失敗した場合は残りのURLの結果や既存の広告を判定材料にせず、履歴を書き換えず`needs_escalation`で返す。全照会URL endpointの広告取得・不足OID fetch・再照合が完了したことを確認できない場合も、履歴を書き換えず`needs_escalation`で返す。URLは照会中の一時値としてのみ扱い、`rewrite_guard`、完了報告その他の受渡しへ記録しない。
この専用再判定では汎用の`git fetch --all --prune`を実行せず、各照会URLの広告取得と不足OIDだけの一時ref取得に限定する。
正規化した全広告ref・OIDを母集団とし、local object databaseに存在しない広告OIDだけを照会URLごとにまとめる。衝突しない数値添字の`refs/agent-toolkit/rewrite-guard/<実行識別子>/<照会URL番号>/<OID番号>`へ`git fetch --no-tags --no-write-fetch-head <URL> <OID>:<一時ref>...`で取得する。
取得後に同じ`ls-remote`を再実行して広告集合の不変を確認する。全照会URL endpointの広告取得・不足OID fetch・再照合が完了した場合だけ、広告branchのOIDをcommitとして解決し、広告tagのOIDを再帰的に参照先へ解決する。広告branch tipには`git merge-base --is-ancestor <対象OID> <branchTip>`を実行する。終了コード0（対象OIDがbranch tipの祖先、すなわちbranch tipが対象OIDの子孫）の場合は公開済み、終了コード1（対象OIDがbranch tipの祖先でない）の場合は未公開と判定する。その他の終了コードはGit実行失敗として遮断する。広告tagは最終参照先がcommitの場合だけ同じ祖先判定する。終了コード0を公開済み、1を未公開、その他をGit実行失敗として扱う。commit以外は除外する。
各判定後と失敗時に期待OIDを指定した`git update-ref -d`で作成済み一時refだけを削除し、実行識別子のref名前空間が空であることを確認する。

remote-tracking ref、local tag、remote設定及び`FETCH_HEAD`は変更しない。
remote列挙、URL取得、重複排除、広告取得、不足OIDのfetch、広告集合の再照合、全照会URL endpointの完了確認、一時ref回収又は祖先判定（終了コード0・1以外を含む）が失敗した場合は履歴を書き換えず`needs_escalation`で返す。
各再判定phaseの対象は履歴順の`target_oids`配列で確定する。`fixup:<単位順>`と`amend`は対象が1件でも1要素の配列を使い、`autosquash`は全過去単位のOIDを同じ配列へ履歴順で含める。`autosquash` phaseでは配列の各OIDについて公開済み判定と遮断を反復し、1件でも公開済みなら履歴を書き換えない。
`rewrite_guard`には各再判定phase（`fixup:<単位順>`、`autosquash`、`amend`）を独立した反復配列要素として記録する。各要素へ同じ順序の`target_oids`、Git版、検収済みHEADを記録する。remote別fetch URL列挙・push URL列挙終了コード、重複排除前後の照会URL件数、全照会URL endpointの広告取得・不足OID fetch・再照合完了と、照会URL単位の各終了コードも記録する。URL値は記録せず、URL列挙終了コードと件数だけを保持する。正規化済み広告ref・OID、一時ref回収結果、各Gitコマンドの終了コードと公開済み判定結果だけを保持する。
`rewrite_guard`には`shallow_repository_check_exit_code`へ`git rev-parse --is-shallow-repository`の終了コードを、`is_shallow_repository`へ終了コード0で得た出力を正規化したboolを記録する。終了コードが非0の場合のboolは「なし」とする。
非commit tagについては、peeled objectの実在確認結果を`noncommit_tag_peeled_object_exists`へ正規化して記録する。最終OIDとobject typeは`noncommit_tag_final_oid_and_type`へ正規化して記録する。祖先判定から除外した理由は`noncommit_tag_exclusion_reason`へ正規化して記録する。レビュー修正モードでは必須完了出力とし、書込担当は`completed`と`needs_escalation`のいずれでも返す。
エラー時は秘密情報を除去した必要最小限の要約だけを加え、標準出力・標準エラーの無加工な全文と認証情報を保存しない。URLは照会中の一時値としてのみ扱い、`rewrite_guard`、完了報告その他の受渡しへ記録しない。成功時と失敗時のどちらも、URL値を保存しない。
最終参照先がcommit以外のtagは正規化済みOID、型及び除外判定を保持して祖先判定から除外する。
local-only tag、remoteが広告しないtag、`refs/tags/`全体の検索結果は判定材料にしない。
対象OIDの子孫であるremote広告branch tip（`git merge-base --is-ancestor <対象OID> <branchTip>`の終了コード0）が1件以上あれば、履歴を書き換えず`needs_escalation`で返す。終了コード1は未公開として判定を継続し、その他の終了コードはGit実行失敗として履歴を書き換えず`needs_escalation`で返す。同じ判定で対象OIDの子孫であるremote広告tagの最終commitが1件以上ある場合も、履歴を書き換えず`needs_escalation`で返す。
公開済み判定と履歴書換え直前の遮断は、検証済み対応表を伴う通常`plan-impl`レビュー修正だけに適用する。
通常のamend・fixup・autosquash、`history-rewrite.md`の汎用公開済み判定、統合後レビュー調整モードへ強化判定を波及させない。
autosquashが非0終了した場合はrebase進行状態を実測し、進行中の場合だけ`git rebase --abort`を実行する。
未開始の場合はabortせず、autosquash終了コード、HEAD、fixup commit、stage、作業ツリー、rebase進行状態と秘密情報を除去した必要最小限のエラー要約を保持して`needs_escalation`で返す。
amendが非0終了した場合も追加の履歴操作をせず、失敗時点のHEAD、stage、作業ツリーと診断を保持して`needs_escalation`で返す。
履歴統合後は各実装単位の変更前後OID、件名、順序、件数、親子関係、差分帰属を実測する。
近接検証とclean状態も実測する。
履歴統合後は、通常commitを再実行せず、各実装単位の近接検証を再実行して履歴実体とclean状態を確認する。
ユーザー合意と衝突する指摘は修正せず`needs_escalation`で返す。
通常実装時の新規commit手順と統合後レビュー用の`merge-task.md`には本項の履歴統合を適用しない。

対象worktree以外を編集しない。担当外差分を復元しない。`git push`、タグ作成、リモートrefも変更しない。
書込担当は履歴書換え直前の再判定と遮断を完結させ、executorまたは呼び出し元へ履歴書換え前の証跡を渡して許可を受領する中間工程を設けない。
委譲元から着手前の未コミット差分の採否判定を明示的に委ねられ、不採用と判定して破棄する場合は、
破棄前に差分を退避し、退避識別子を完了報告へ含める。
merge進行中でなければ`git stash push --include-untracked`相当を使い、merge進行中は
`agent-toolkit:commit`の`references/history-rewrite.md`「merge進行中の退避」に従う。
退避の破棄は既存の破壊的操作規範に従い、自動で破棄しない。
退避物の回収は呼び出し元の責務であり、呼び出し元は`agent-toolkit:commit`の「作業用ブランチと退避物の削除」節に従って処置する。
同一内容が既に退避済みである場合は追加の退避を作成しない。

## 出力

```text
status: completed | needs_escalation
commits:
- <計画単位、変更前の完全OID、変更後の完全OID、commit件名、順序、差分帰属。通常実装時は実装commitのOID、レビュー修正時は全単位のOID対応>
changed:
- <計画項目と変更結果>
verification:
- <コマンド>; exit_code: <整数>; warnings: <整数>
review_resolution:
- <指摘ID、原文と適用根拠の確認結果、採用した修正、保持契約の維持結果。該当なしなら「なし」>
feedbacks: <受領したソート済みフィードバックファイル名一覧。0件は返さない>
rewrite_guard:
- phase: <fixup:<単位順>|autosquash|amend>
  target_oids: <履歴順の対象完全OID一覧。単一対象も1要素の配列>
  git_version: <Git版>
  verified_head: <検収済みHEAD>
  shallow_repository_check_exit_code: <`git rev-parse --is-shallow-repository`の終了コード>
  is_shallow_repository: <終了コード0で取得したbool。取得不能時は「なし」>
  remote_fetch_url_enumeration_exit_codes: <remote別fetch URL列挙終了コード（URLは記録しない）>
  remote_push_url_enumeration_exit_codes: <remote別push URL列挙終了コード（URLは記録しない）>
  query_url_count_before_deduplication: <重複排除前の照会URL件数>
  query_url_count_after_deduplication: <重複排除後の照会URL件数>
  all_query_endpoints_completed: <全照会URL endpointの広告取得・不足OID fetch・再照合完了>
  query_url_command_exit_codes: <重複排除した照会URL単位（URLは記録しない）の広告取得・不足OID fetch・再照合終了コード>
  advertised_refs_and_oids: <正規化済み広告ref・OID>
  noncommit_tag_peeled_object_exists: <非commit tagのpeeled object実在確認結果>
  noncommit_tag_final_oid_and_type: <非commit tagの最終OIDとobject type>
  noncommit_tag_exclusion_reason: <非commit tagを祖先判定から除外した理由>
  temporary_ref_cleanup: <一時ref回収結果>
  git_command_exit_codes: <各Gitコマンドの終了コード>
  published_decision: <公開済み判定結果>
  error_summary: <秘密情報を除去した必要最小限のエラー要約。無ければ「なし」>
plan_deviation:
- <差異と調整結果。無ければ「なし」>
blockers:
- <未完了事項。完了時は「なし」>
```

成果物を書き込んだ場合は、成果物の絶対パスと実在・分量を示す実行結果も含める。
完了報告はツール戻り値で1回返し、`SendMessage`で能動送付しない。
