# 計画実装担当タスク

指定されたコミット単位を実装し、近接検証、差分検収、stage、commitまで完了する。
計画にコミット単位の指定が無い場合は、計画全体を1つの原子的な実装単位として扱う。
同じ計画ファイルに属する単位は、同じworktreeで順次実装し、同時に1つの書込担当だけを置く。

## 入力

- 担当種別（`fast担当`、`fix担当`、`レビュー修正担当`又は`CI修正担当`。呼び出し元は起動文へ指定した種別を明示する）
- 対象worktreeとプロジェクト規範の絶対パス。計画ファイルは`CI修正担当`以外では必須とし、`CI修正担当`では受領済みの場合だけ渡す
- 実装するコミット単位、その目的及び変更説明
- 通常実装モードのレビュー修正では、レビュー対象の最終HEAD完全OID、採用指摘IDと統合先の実装単位commit完全OIDの対応表
- 通常実装モードのレビュー修正では、保持した初回実装担当routeと実効`engine`、`model`及び`effort`、起動直前に解決した今回routeと実効3値、継続又は新規起動に用いる識別子、前担当の終端確認及びexecutorが検収したHEAD・作業ツリー・検証結果
- 適用する作成規範スキル名
- 1件以上のソート済みフィードバックファイル名一覧。`CI修正担当`ではフィードバック起因の場合だけ渡す
- 追加指示、許容済みの挙動変化。該当しない値は`なし`
- git操作に用いるworktree絶対パス、複製元と対象外worktree
- `fix担当`の場合は、修正対象の失敗箇所、修正前後の検証結果、基準OID、既存差分及びfast担当の終端確認を含む修正引継ぎ記録と現行のdirty差分
- `レビュー修正担当`の場合は、検証済みの実際値、期待値、違反契約、対象への適用根拠及び保持契約を含む採用指摘
- `CI修正担当`の場合は、原因分析結果、修正の認可根拠及び対象の検証結果を含むCI記録。
  認可根拠には承認済み計画の該当箇所、原因となった変更を認可した利用者指示の逐語文、又は既存の公開契約の該当箇所を含める

担当種別に応じた必須入力が欠ける場合は推測せず`needs_escalation`で返す。
本タスク以外の委譲の内部資料は読まず、受領した計画又はCI記録の修正認可根拠と作成規範スキルを作業契約の正本とする。

## 実装

通常実装モードでは手順3〜7の通常実装手順を実行する。
レビュー修正モードでは手順1〜2を実行した後、手順3・4を採用指摘全体へ一括適用しない。
レビュー修正モードでは`レビュー修正の単位別反復`を実行する。
レビュー修正モードでは手順5〜6の通常commit経路を実行せず、後段の履歴統合手順だけをcommit経路とする。

1. 受領済みの場合は計画を全文読み、常にプロジェクト規範、該当する作成規範スキルを全文読む。
   計画を受領した場合は、人間向け固定領域（`## 概要`から`## 変更履歴`まで）をユーザー要求の正本、
   その後の実装者向け領域を実装詳細の正本として扱う。
   計画を受領しない`CI修正担当`は、CI記録にある利用者指示又は公開契約を修正認可の上限とする。
2. 指定worktreeの現行`HEAD`、作業ツリー、対象ファイル全文を実測する。
   作業ディレクトリを自己解決せず、git操作は受領した絶対パスへ`git -C`を付ける。
3. 受領した計画又はCI記録の修正認可根拠を守って指定単位へ帰属する変更だけを実装し、対象実装の詳細は現行コードから再調査する。
   実装中に判明した変更または正式コマンドが生成した変更は、計画目的へ帰属し、
   ユーザー合意と公開契約及び安全境界を変えない場合に含める。
   追加ファイル、発生理由、必要性は計画との差異として返す。
   現行実装が計画の前提と異なる場合は、
   利用者向け成果を維持できる範囲で手順を調整し、差異を報告する。
4. 担当種別が`fast担当`の場合だけ、以下のfast手順を実行する。`fix担当`、`レビュー修正担当`及び`CI修正担当`には以下のfast手順を適用しない。
   fast担当として対象に近いformat、lint、testを実行し、警告を解消する。
   検証コマンドが失敗した場合は、テストID・診断識別子等で修正対象の失敗箇所を記録し、原因を修正して同じコマンドを直後に1回再実行する。
   修正対象が解消して別の失敗箇所だけが現れた場合はfast担当を継続し、新しい失敗箇所を同じ手順で扱う。
   修正対象が再検証にも残る場合は追加修正とcommitを行わず、失敗コマンド、修正前後の結果、失敗箇所、基準OID、差分と自身が起動した全プロセスの終了を
   `status: fast_fix_handoff`として返してfast担当を終端する。この場合は後続の共有追加検証、差分検収、stage、commit、cleanな作業ツリーの確認を
   対象外とし、実施しない。`repair_handoff`へ修正引継ぎ記録として、`failure_location`（失敗箇所）、`failed_command`（失敗コマンド）、
   `verification_before`（修正前の結果）、`verification_after`（直後の再検証結果）、`baseline_oid`（基準OID）、
   `existing_diff`（既存差分）及び`process_termination`（自身が起動した全プロセスの終了証跡）を構造化して返す。
5. 担当種別が`fix担当`の場合は、受領した修正引継ぎ記録と現行のdirty差分を照合し、同一失敗箇所の原因修正、全検証、差分検収とcommitまで完遂する。
   fast手順と追加のモデル昇格を適用しない。
6. 担当種別が`レビュー修正担当`の場合は、受領した採用指摘の原因修正、全検証、差分検収とcommitまで完遂する。
   fast手順とfastからfixへの昇格判定を適用しない。
7. 担当種別が`CI修正担当`の場合は、受領したCI記録の原因修正、全検証、差分検収とcommitまで完遂する。
   fast手順とfastからfixへの昇格判定を適用しない。
8. 全ての書込担当は、共有の判定処理、振り分け処理、解析処理を変更する場合に、変更分岐へ到達する全呼び出し元と未変更の既存test class、
   契約を示すdocstring・コメントを列挙する。配列や反復blockを扱う場合は、0件、1件、複数件、異種混在、
   局所識別子の対応を検証し、列挙と実行結果を返す。この追加検証は該当する共有分岐又は反復構造の変更に限定する。
9. 通常実装モードでは、`git diff`、受領した目的と変更説明、変更後文面を照合し、担当単位へ帰属する変更だけをstageする。
   レビュー修正モードでは本項の通常commit用stageを行わず、履歴統合直前に修正差分だけをstageする。
10. 通常実装モードでは、`agent-toolkit:commit`に従って通常commitを実行し、commit実体とcleanな作業ツリーを確認する。
   レビュー修正モードでは本項を実行せず、後段の履歴統合手順で指定したamendまたはfixupとautosquashだけをcommit経路とする。
11. 通常実装モードではcommit、検証、受領した計画又はCI記録の修正認可根拠との差異を呼び出し元へ返す。
   レビュー修正モードでは履歴統合後の検証、履歴実体、計画との差異を呼び出し元へ返す。
   完了報告はツール戻り値で1回返し、`SendMessage`で能動送付しない。
   書込担当は人間向け固定領域と`## 進捗ログ`を編集せず、進捗の追記は呼び出し元が行う。

fast担当が同一失敗箇所の残存を記録し、呼び出し元が終端確認を完了した後に`execute_fix_model`による再起動を受領した場合は、
同じworktreeの既存差分と修正引継ぎ記録を照合してから`fix担当`として作業する。既存差分を復元又は破棄せず、
追加のモデル昇格をせずに原因修正、全検証、差分検収及びcommitまで完了する。

レビュー指摘の修正を受け取った場合は、系統・ラウンドごとに分けたレビュー表を、重要度、指摘箇所、指摘内容、対応要否、対応内容、対応不要理由の固定6列TSVとして扱う。
レビュー修正の横展開単位、全出現箇所の列挙、接続先契約面の対応表と直前修正への再指摘時の比較は、`../../reviewee-standards/SKILL.md`の`## 修正の実施`を正本として適用する。
修正対象となる全系統・全ラウンド表として指定された表を、系統・ラウンドの帰属を保ったまま全文読取してから採否と修正を確定する。
検証済みの実際値、期待値と違反契約を確認する。
対象への適用根拠と保持契約が指摘ごとにそろうことも確認する。
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
`../../reviewee-standards/SKILL.md`と該当する作成規範スキルを適用し、指摘の採否と修正を確定する。
同スキルで不採用と確定した候補は修正せず`needs_escalation`で返す。
不採用、または採否未確定（`未検証`を含む）の指摘は修正対象及び`target_oids`へ含めず、修正差分を適用せず、履歴と作業ツリーを変更しないまま`needs_escalation`で返す。
レビュー担当の修正方針を要件へ昇格させず、採否を独立に確定する。
レビュー修正の採否、対象実装単位及び対応表が確定するまで、修正差分を適用せず、履歴を変更しない。
`agent-toolkit:commit`の`references/history-rewrite.md`を全文読み、履歴書換え前のベース、HEAD、実装単位の順序、各完全OID、親子関係、commit件数、件名とtrailer、各commitの差分帰属を保持する。

過去単位を含む場合は、単位別反復を開始する前にfixup対象の最古commitと履歴書換え前の元HEADを完全OIDで確定する。
`pre_fixup` phaseでは、他の履歴照会の前置条件としてgraftを検査する。
`pre_fixup`、各`fixup:<単位順>`、`autosquash`及び`amend` phaseの履歴統合操作前にもgraftを検査する。
`GIT_NO_REPLACE_OBJECTS=1 git rev-parse --path-format=absolute --git-path info/grafts`でgraftファイルのパスを解決し、
`test ! -e <graftファイルの絶対パス>`で存在しないことを確認する。
`GIT_NO_REPLACE_OBJECTS=1`はreplace refだけを無効化し、graftを無効化しない。
パスの解決または存在確認に失敗するか、graftファイルが存在する場合は履歴と作業ツリーを変更せず`needs_escalation`で返す。
`GIT_NO_REPLACE_OBJECTS=1 git rev-list --first-parent --reverse <最古fixup対象>^..<元HEAD>`でautosquashのrebase範囲を列挙する。
`GIT_NO_REPLACE_OBJECTS=1 git rev-list --first-parent --merges <最古fixup対象>^..<元HEAD>`でmerge commitが無いことを確認する。
この範囲のfirst-parent全OIDについて、fixup作成前に公開済み判定を完了する。
`GIT_NO_REPLACE_OBJECTS=1 git log --first-parent --format='%H%x00%s' <最古fixup対象>^..<元HEAD>`で範囲内のOIDと件名を列挙する。
各fixup対象コミットの件名が範囲内で一意であることをfixup作成前に確認する。
対象コミット件名が範囲内で一意でない場合は、fixupを作成せず履歴と作業ツリーを変更せず`needs_escalation`で返す。範囲内の全commit件名を履歴又は作業ツリーへ変更を加える前に列挙する。範囲内の既存commitに、件名先頭が`fixup!`・`squash!`・`amend!`へ完全一致するものが1件でもある場合も、fixupを作成せず同じ扱いとする。各制御語の直後には半角空白1文字を置く。部分一致や件名途中の一致は遮断条件にしない。
範囲列挙、merge確認、元HEADの確定、公開済み判定、OID・件名の列挙、件名一意性確認のいずれかに失敗した場合は、fixupを1件も作成せず`needs_escalation`で返す。
範囲にmergeが含まれる場合も同じ扱いとする。
この事前判定後も、autosquash直前の再判定をTOCTOU対策として実行する。
上記のfixup作成前の全OID判定は、単位別反復に先立つ専用の`pre_fixup` phaseとして完了と証跡を記録する。
`pre_fixup` phaseの`target_oids`には、最古fixup対象から元HEADまでのfirst-parent全OIDを履歴順で保持する。
このphaseの記録が完了する前にfixupを作成してはならない。
OIDの欠落、判定不能、公開済みまたはmergeの検出が1件でもある場合は、fixupを1件も作成せず`needs_escalation`で返す。

### レビュー修正の単位別反復

レビュー修正モードでは、採用指摘を統合先の実装単位ごとに履歴順で処理する。
手順3・4を採用指摘全体へ一括適用せず、各反復で当該単位の修正だけを扱う。
過去単位の反復では、当該単位の修正を実装し、近接検証を実行して警告を解消する。
修正差分をstageした後、各fixup作成前の`fixup:<単位順>` phaseで書込担当が再判定と遮断を完了する。
修正差分だけをstageし、`GIT_NO_REPLACE_OBJECTS=1 git commit --fixup=<対象OID>`で対応する`fixup`を作成する。
各fixup作成後は、対象OIDから得た統合先件名と形式に応じた制御件名（`fixup!`または`amend!`）の完全一致を`git log -1 --format=%s`で確認する。
期待件名と一致しない場合はautosquashを実行せず、作成済みfixupと作業ツリーを保持して`needs_escalation`で返す。
対象OID、件名及び作業ツリーがcleanであることを確認してから次の過去単位へ進む。
過去単位と最終単位が対象の場合、autosquash前の反復対象を過去単位だけに限定する。
全過去単位の反復後、書込担当が`autosquash` phaseの履歴書換え直前の再判定と遮断を完了する。
最終単位だけが対象の場合は、最終単位の修正を実装し、近接検証を実行してstageした後、`amend` phaseでgraftを検査して再判定し、遮断を完了する。
過去単位と最終単位が対象の場合、autosquash成功後に`GIT_NO_REPLACE_OBJECTS=1 git rev-parse HEAD`で書換え後HEADの完全OIDを取得し、最終単位の修正差分を実装し、近接検証を実行してstageする。その後、`amend` phaseの再判定と遮断を完了し、書換え後HEADへamendする。
保持した初回実装担当と起動直前に解決した今回routeの実効`engine`・`model`・`effort`がすべて一致し、同じ担当へ同じタスクの未完了作業、指摘への対応又は再レビューを返す場合だけ、元の実装担当threadを継続する。
いずれかの実効値が異なる場合を含むそれ以外の組合せでは旧担当の終端を確認し、今回routeで新しい書込担当を起動して、検収済みHEAD・作業ツリー・検証結果を開始前に1回だけ渡す。
開始後は同じ書込担当が再判定、履歴統合、amendを完結し、中間引継ぎを行わない。
各操作後に対象OID、件名、作業ツリーのclean状態を確認する。

複数の過去単位が対象の場合は、履歴順に1単位ずつ、その単位へ帰属する修正差分だけを適用してstageし、対応するfixupを作成する。
各fixup作成後に対象OID、件名及び作業ツリーがcleanであることを確認してから、次の単位の修正差分を適用する。
最終単位だけが対象の場合は最終単位の修正差分だけを適用し、近接検証を実行してstageした後、`amend` phaseでgraftを検査し、再判定が成功した場合だけ`GIT_NO_REPLACE_OBJECTS=1 git commit --amend --no-edit`を実行する。
過去単位だけが対象の場合は、全過去単位のfixup作成後に、事前に保持した元HEADまでのfirst-parent全OIDを対象とする。
`autosquash` phaseの再判定が成功した場合だけ、`GIT_NO_REPLACE_OBJECTS=1 GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash <最古fixup対象>^`を実行する。
最終単位と過去単位の両方が対象の場合は、過去単位のautosquash成功後に`GIT_NO_REPLACE_OBJECTS=1 git rev-parse HEAD`で書換え後HEADの完全OIDを取得し、書換え前の各対象OIDと書換え後の全実装単位OIDの対応を履歴検収用に保持する。autosquash成功後の2回目のpush済み判定対象を当該OIDへ置換し、開始済みの同じ書込担当が最終単位の修正差分だけを実装し、近接検証を実行してstageする。その後、`amend` phaseの再判定が成功した場合だけ書換え後HEADへamendする。
いずれもレビュー修正専用commitを残さない。
開始済みの書込担当は`pre_fixup`（fixup作成前）、各fixup作成前、autosquash直前、amend直前に、`git remote`で全remoteを列挙する。
各再判定phaseで`git rev-parse --is-shallow-repository`を実行し、終了コードと出力を確認する。終了コード0かつ出力が`false`の場合だけ広告照会と祖先判定を継続する。終了コード0で出力が`true`の場合、終了コードが非0の場合、出力が`true`と`false`のいずれでもない場合は、履歴を書き換えず`needs_escalation`で返す。
各remoteについてfetch URL集合（`git remote get-url --all <remote>`）とpush URL集合（`git remote get-url --all --push <remote>`）を取得する。fetch URL列挙とpush URL列挙のremote別終了コードを保持するが、URL値は保持しない。全remoteの集合を1つの照会URL集合へ統合し、URL文字列が完全一致するものだけ重複を除き、重複排除前後の照会URL件数を計数する。以後の広告照会・不足OIDのfetch・終了コードは照会URL単位で扱い、各照会URLへ`git ls-remote --refs <URL>`を実行する。全照会URL endpointの広告取得・不足OID fetch・再照合を完了させる。
URL取得、重複排除又は広告照会に失敗した場合は残りのURLの結果や既存の広告を判定材料にせず、履歴を書き換えず`needs_escalation`で返す。全照会URL endpointの広告取得・不足OID fetch・再照合が完了したことを確認できない場合も、履歴を書き換えず`needs_escalation`で返す。URLは照会中の一時値としてのみ扱い、`rewrite_guard`、完了報告その他の受渡しへ記録しない。
この専用再判定では汎用の`git fetch --all --prune`を実行せず、各照会URLの広告取得と不足OIDだけの一時ref取得に限定する。
正規化した全広告ref・OIDを母集団とし、local object databaseに存在しない広告OIDだけを照会URLごとにまとめる。
各再判定の開始時に同時実行及び過去の残留refと衝突しない一意の実行識別子を一時的に生成する。
phase内だけで用いる数値添字の`refs/agent-toolkit/rewrite-guard/<実行識別子>/<照会URL番号>/<OID番号>`へ`git fetch --no-tags --no-write-fetch-head <URL> <OID>:<一時ref>...`で取得する。
不足OIDが無いendpointではfetchを実行せず、当該endpointの`fetch_exit_code`へ`not_applicable`を記録する。
取得後に同じ`ls-remote`を再実行して広告集合の不変を確認する。全照会URL endpointの広告取得・不足OID fetch・再照合が完了した場合だけ、全広告direct refのOIDを名前空間に依存せず最終参照先へ解決する。広告OIDのobject typeがtagなら名前空間に依存せず再帰的にpeelし、最終参照先がcommitなら公開済み判定へ含める。各`target_oids`要素と各広告ref・OIDの直積を、`target_oid`を含む1つの平坦な`ref_evidence`反復へ記録する。各組合せについて最終参照先がcommitなら`GIT_NO_REPLACE_OBJECTS=1 git merge-base --is-ancestor <target_oid> <最終commit>`を実行する。終了コード0（対象OIDが最終commitの祖先）の場合は公開済み、終了コード1（対象OIDが最終commitの祖先でない）の場合は未公開と判定する。その他の終了コードはGit実行失敗として遮断する。最終参照先がcommit以外のdirect refは除外理由を記録して祖先判定から除外する。
広告OIDの実在確認、全direct refのobject解決とtagの再帰的なpeel、rebase範囲の列挙と祖先判定には`GIT_NO_REPLACE_OBJECTS=1`を付ける。
replace refは`GIT_NO_REPLACE_OBJECTS=1`で無効化する。graftによって置換されたobjectや親関係は同環境変数では無効化されないため、graftファイルの存在検出に失敗した場合も含めて履歴書換えを遮断する。
祖先判定は`GIT_NO_REPLACE_OBJECTS=1 git merge-base --is-ancestor <対象OID> <最終commit>`として実行する。
各判定後と失敗時に期待OIDを指定した`git update-ref -d`で作成済み一時refだけを削除し、この実行識別子の一時ref名前空間が空であることを確認する。実行識別子は報告へ記録せず、永続的な状態やTODO編集機構は作成しない。

remote-tracking ref、local tag、remote設定及び`FETCH_HEAD`は変更しない。
remote列挙、URL取得、重複排除、広告取得、不足OIDのfetch、広告集合の再照合、全照会URL endpointの完了確認、一時ref回収、graftファイルのパス解決・存在確認、祖先判定（終了コード0・1以外を含む）が失敗した場合は履歴を書き換えず`needs_escalation`で返す。
各再判定phaseの対象は履歴順の`target_oids`配列で確定する。`fixup:<単位順>`と`amend`は対象が1件でも1要素の配列を使い、`autosquash`は最古fixup対象から履歴書換え前に保持した元HEADまでのfirst-parent全OIDを同じ配列へ履歴順で含める。
`autosquash` phaseでは配列のfirst-parent全OIDについて公開済み判定と遮断を反復する。
1件でも公開済み・判定不能のOIDがあるか、範囲にmergeを含む場合は履歴を書き換えない。
`rewrite_guard`には専用の`pre_fixup` phaseと各再判定phase（`fixup:<単位順>`、`autosquash`、`amend`）を独立した反復配列要素として記録する。
各要素へ同じ順序の`target_oids`、Git版、検収済みHEADを記録する。
remote別fetch URL列挙・push URL列挙終了コード、重複排除前後の照会URL件数、全照会URL endpointの広告取得・不足OID fetch・再照合完了を記録する。
`query_endpoints`の各反復ブロックへphase内だけで用いる一時数値`query_endpoint_id`を置く。
同じIDで広告取得・不足OID fetch・再照合の終了コード、広告ref-OID、一時ref回収、`target_oids`と各広告direct refの直積へ1件ずつ対応する共通`ref_evidence`を結合する。
`ref_evidence`では`target_oid`、`ref_name`、`advertised_oid`の3値の組を一意にする。直積からの欠落、重複、値の不一致が1件でもあれば履歴を書き換えず`needs_escalation`で返す。
URL値は記録せず、URL列挙終了コードと件数だけを保持する。
永続的な状態やTODO編集機構は作成しない。
`rewrite_guard`には各再判定phaseを独立した反復配列要素として記録し、専用の`pre_fixup` phaseを先頭に含める。
`rewrite_guard`には`shallow_repository_check_exit_code`へ`git rev-parse --is-shallow-repository`の終了コードを、`is_shallow_repository`へ終了コード0で得た出力を正規化したboolを記録する。終了コードが非0の場合のboolは「なし」とする。

`target_oids`と`advertised_refs_and_oids`の直積はendpointブロック内の共通`ref_evidence`へ1件ずつ記録する。各要素へ対象OID、ref名、広告OID、広告OIDの実在確認、最終OID・型、commitの場合の祖先判定又はnoncommitの場合の除外理由を記録する。広告OIDのobject typeがtagなら名前空間に依存せず最終参照先まで再帰的にpeelし、それ以外は広告OIDを直接解決する。branch・tagの二分類とtag専用の証跡は設けない。
レビュー修正モードでは各direct refのcommit・noncommit証跡を必須完了出力とし、書込担当は`completed`と`needs_escalation`のいずれでも返す。
エラー時は秘密情報を除去した必要最小限の要約だけを加え、標準出力・標準エラーの無加工な全文と認証情報を保存しない。URLは照会中の一時値としてのみ扱い、`rewrite_guard`、完了報告その他の受渡しへ記録しない。成功時と失敗時のどちらも、URL値を保存しない。
最終参照先がcommit以外のdirect refは正規化済みOID・型・除外判定を保持して祖先判定から除外する。
local-only ref、remoteが広告しないref、`refs/tags/`全体の検索結果は判定材料にしない。
対象OIDを祖先とするremote広告direct refの最終commitが1件でもあれば、履歴を書き換えず`needs_escalation`で返す。終了コード1は未公開として判定を継続する。その他の終了コードはGit実行失敗として履歴を書き換えず`needs_escalation`で返す。最終参照先がcommit以外のdirect refは正常な除外として扱う。
公開済み判定と履歴書換え直前の遮断は、検証済み対応表を伴う通常`plan-impl`レビュー修正だけに適用する。
通常のamend・fixup・autosquash、`history-rewrite.md`の汎用公開済み判定、統合後レビュー調整モードへ強化判定を波及させない。
autosquashが非0終了した場合はrebase進行状態を実測し、進行中の場合だけ`GIT_NO_REPLACE_OBJECTS=1 git rebase --abort`を実行する。
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
merge進行中でなければ`atk worktree-stash save --label <退避ラベル>`を使い、merge進行中は
`../../commit/references/history-rewrite.md`「merge進行中の退避」に従う。復元時は
`git stash apply --index refs/worktree/<退避ラベル>`を使い、ステージ済み・未ステージ・未追跡の3状態を対象とする。
共有`refs/stash`へ直接触れる複数コマンドを実行主体へ指示しない。
退避の破棄は既存の破壊的操作規範に従い、自動で破棄しない。
退避物の回収は呼び出し元の責務であり、呼び出し元は`../../commit/SKILL.md`の「作業用ブランチと退避物の削除」節に従って処置する。
同一内容が既に退避済みである場合は追加の退避を作成しない。

## 出力

```text
status: completed | fast_fix_handoff | needs_escalation
commit: <完全長SHAまたはなし>
commits:
- <計画単位、変更前の完全OID、変更後の完全OID、commit件名、順序、差分帰属。通常実装時は実装commitのOID、レビュー修正時は全単位のOID対応>
changed:
- <計画項目と変更結果>
verification:
- <コマンド>; exit_code: <整数>; warnings: <整数>
review_resolution:
- <指摘ID、要求と適用根拠の確認結果、採用した修正、保持契約の維持結果。該当なしなら「なし」>
feedbacks: <受領したソート済みフィードバックファイル名一覧。一般のCI失敗で受領していない場合は「なし」>
rewrite_guard:
- phase: <pre_fixup|fixup:<単位順>|autosquash|amend>
  target_oids: <履歴順の対象完全OID一覧。autosquashは最古fixup対象から履歴書換え前に保持した元HEADまでのfirst-parent全OID。単一対象も1要素の配列>
  git_version: <Git版>
  verified_head: <検収済みHEAD>
  shallow_repository_check_exit_code: <`git rev-parse --is-shallow-repository`の終了コード>
  is_shallow_repository: <終了コード0で取得したbool。取得不能時は「なし」>
  remote_fetch_url_enumeration_exit_codes: <remote別fetch URL列挙終了コード（URLは記録しない）>
  remote_push_url_enumeration_exit_codes: <remote別push URL列挙終了コード（URLは記録しない）>
  query_url_count_before_deduplication: <重複排除前の照会URL件数>
  query_url_count_after_deduplication: <重複排除後の照会URL件数>
  all_query_endpoints_completed: <全照会URL endpointの広告取得・不足OID fetch・再照合完了>
  query_endpoints:
  - query_endpoint_id: <phase内だけで用いる一時数値識別子。URLは記録しない>
    advertisement_exit_code: <広告取得終了コード>
    advertised_refs_and_oids: <このendpointの正規化済み広告ref・OID>
    ref_evidence:
    - target_oid: <この祖先判定が対象とするtarget_oids内の完全OID>
      ref_name: <広告されたdirect ref名>
      advertised_oid: <広告OID>
      ref_object_exists: <広告OIDの実在確認結果>
      final_oid: <最終参照先のOID>
      final_type: <最終参照先のobject type>
      ancestor_decision: <最終参照先がcommitの場合の公開済み判定。noncommitは「なし」>
      exclusion_reason: <最終参照先がcommit以外のdirect refを祖先判定から除外した理由。commitは「なし」>
    fetch_exit_code: <不足OID fetch終了コード。不足OIDが無い場合は`not_applicable`>
    recheck_exit_code: <広告集合再照合終了コード>
    temporary_ref_cleanup: <このendpointの一時ref回収結果>
  git_command_exit_codes: <各Gitコマンドの終了コード>
  published_decision: <公開済み判定結果>
  error_summary: <秘密情報を除去した必要最小限のエラー要約。無ければ「なし」>
plan_deviation:
- <差異と調整結果。無ければ「なし」>
blockers:
- <未完了事項。完了時は「なし」>
```

`status: fast_fix_handoff`の場合だけ、共通出力へ次の修正引継ぎ記録を追加する。

```text
repair_handoff:
  failure_location: <修正対象として記録した失敗箇所>
  failed_command: <失敗コマンド>
  verification_before: <修正前の検証結果>
  verification_after: <直後の再検証結果>
  baseline_oid: <基準OID>
  existing_diff: <fast担当が残した既存差分>
  process_termination: <fast担当が起動した全プロセスの終了証跡>
```

成果物を書き込んだ場合は、成果物の絶対パスと実在・分量を示す実行結果も含める。
完了報告はツール戻り値で1回返し、`SendMessage`で能動送付しない。
