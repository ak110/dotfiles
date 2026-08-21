# 計画実装担当の起動と受領

呼び出し元が`plan-impl-executor`を起動し、完了実体を検収してpushとCIを引き取る。

## 起動

対象worktreeが上流追随済みでcleanであることと、同じworktreeに書込担当がいないことを確認する。
計画から単位、共通のベースコミット、統合順を読み、`plan-impl-executor`へ渡すworktreeの完全な一覧を作成する。
通常経路では受領済みの現在worktreeをレーンのworktreeとして借用し、`作成主体=既存`かつ`回収可否=不可`で記録する。
借用時は`管理対象領域=なし`とする。

| worktreeの種別 | 作成主体 | 回収可否 | 管理対象領域 |
| --- | --- | --- | --- |
| 借用（受領済みの現在worktree） | `既存` | `不可` | `なし` |
| 呼び出し元が管理対象領域内へ作成（並列単位・計画が明示したレーン） | `caller` | `可` | 絶対パス必須 |

上記2組合せ以外は`plan-impl-executor`へ渡さない。

先行成果へ依存しない複数の計画ファイルを並列実装する場合は、ファイル重複にかかわらず、
呼び出し元が計画ファイルごとに`atk managed-temp create --prefix <unit>`で管理対象領域を作成する。
変更ファイルの重複を理由に先行レーンの完了を待たず、各計画ファイルを専用worktreeへ割り当てる。
各管理対象領域には`git worktree add --detach <absolute-path> <common-base>`で計画ファイル専用worktreeを作成する。
計画が呼び出し元によるレーンのworktreeの作成も明示する場合は、同じ方法でレーンのworktreeを作成する。
作成直後に用途、絶対パス、管理対象領域の絶対パス、HEADの完全OID、作成主体、回収可否を`## 進捗ログ`へ記録する。
以降、この6項目をworktreeの記録属性と呼ぶ。

通常の実装モードでは、呼び出し元が各レーンの起動前に`atk managed-temp create --prefix <レビュー用途>`を単独で実行し、標準出力の絶対パスを実装レビュー用managed temp領域として保持する。
借用worktreeを使う場合も実装レビュー用managed temp領域を作成する。
作成した実装レビュー用managed temp領域の用途と絶対パスを、対応する計画の`## 進捗ログ`へ記録する。
起動文は受信者への命令を先頭に置き、次だけを渡す。

- モード指定`通常の実装モード`
- 計画ファイル、プロジェクト規範、該当する作成規範スキルの絶対パス。同じ計画ファイルの実装単位は同じworktreeへ順次割り当て、同時に1つの書込担当だけを置くこと
- worktreeの完全な一覧。各worktreeへ前掲の記録属性を付し、借用時の管理対象領域は`なし`とする
- 通常の実装レビュー用managed temp領域の絶対パス
- 1件以上のソート済みフィードバックファイル名一覧
- 追加指示と許容済みの挙動変化。該当しない場合は`なし`
- 複製元と対象外worktree、commit・統合可、worktreeの作成・回収不可、push不可などの権限

計画が対象リポジトリ外への操作を列挙している場合は、列挙された対象だけを認可範囲として渡し、
記載の無い対象への操作は許可しない。
agent定義とタスク文書が持つ手順、書式、完了条件を起動文へ複製しない。

## 受領

`plan-impl-executor`の要約を受領したら、本文より先に次を実測する。

1. 計画が明示した単位に対応するcommitと変更ファイル。単位の指定が無い場合は計画全体に対応する単一commit
2. cleanな作業ツリーと担当外差分の有無
3. 近接検証と最終検証の実行結果
4. 二系統のレビューの対象commit、読み取り専用状態、指摘と対応結果、系統・ラウンド別レビュー表と対応`.lock`の実在
5. 計画の完了条件と`## 進捗ログ`
6. 共通base、統合順、全単位commit、各worktreeの前掲の記録属性と状態
7. 通常実装モードのレビュー修正以外、統合後レビュー調整モードでは、`rewrite_guard`が`not_applicable`でありphase証跡を要求していないことを確認する
8. 通常の実装モードでレビュー修正を受領した場合は、レビュー対象の最終HEAD完全OIDと指摘ID・統合先commit完全OIDの対応表を確認する。
   単位ごとの変更前OIDと変更後OID、commit数と順序、commitメッセージを確認する。
   各commitの差分帰属、最終HEAD、レビュー修正専用commitが残っていないことも確認する。
   過去単位を含む場合は、fixup作成前に保持した元HEADとfixup対象の最古commitを完全OIDで確定したことを確認する。
   専用の`pre_fixup` phaseをfixup作成前に完了し、最古fixup対象から元HEADまでのfirst-parent全OIDを`target_oids`へ履歴順で記録したことを確認する。欠落、判定不能、公開済み又はmergeが1件でもある場合はfixupを作成せず`needs_escalation`で返したことを確認する。
   `pre_fixup` phaseでは、他の履歴照会の前置条件としてgraftを検査する。続けて、`GIT_NO_REPLACE_OBJECTS=1 git rev-parse --path-format=absolute --git-path info/grafts`でgraftファイルのパスを解決し、`test ! -e <graftファイルの絶対パス>`で存在しないことを確認したことを検収する。各`fixup:<単位順>`、`autosquash`及び`amend` phaseの履歴統合操作前にも同じ検査をしたことを検収する。`GIT_NO_REPLACE_OBJECTS=1`はreplace refだけを無効化しgraftを無効化しないため、パスの解決又は存在確認に失敗するかgraftファイルが存在する場合は、履歴と作業ツリーを変更せず`needs_escalation`で返したことを確認する。
   最終単位だけが対象の場合は、amend直前の`amend` phaseでgraft検査を含む再判定が成功した後だけamendしたことを確認する。
   `query_endpoints`はphase内だけで用いる数値`query_endpoint_id`で広告取得・fetch・再照合の終了コード、広告ref-OID、一時ref回収及び広告direct refごとの共通`ref_evidence`を同じ反復ブロックへ結合する。
   `ref_evidence`は`target_oids`と`advertised_refs_and_oids`の全direct refの直積へ1件ずつ対応し、対象OID、ref名、広告OID、広告OIDの実在確認、最終OID・型及びcommitの祖先判定又はnoncommitの除外理由を直接照合できることを確認する。`target_oid`、`ref_name`及び`advertised_oid`の組が一意であり、欠落、重複又は値の不一致があれば受領を遮断する。広告OIDのobject typeがtagなら名前空間に依存せず再帰的にpeelし、branch・tagの二分類とtag専用証跡を残していないことも確認する。
   URL値と永続的な状態・TODO編集機構を受け取っていないことを確認する。
   `GIT_NO_REPLACE_OBJECTS=1 git rev-list --first-parent --reverse <最古fixup対象>^..<元HEAD>`でautosquashのrebase範囲を列挙したことを確認する。
   `GIT_NO_REPLACE_OBJECTS=1 git rev-list --first-parent --merges <最古fixup対象>^..<元HEAD>`でmerge commitを事前確認する。
   範囲内のfirst-parent全OIDの公開済み判定をfixup作成前に完了したことを確認する。
   `GIT_NO_REPLACE_OBJECTS=1 git log --first-parent --format='%H%x00%s' <最古fixup対象>^..<元HEAD>`で範囲内のOIDと件名を列挙したことを確認する。
   各fixup対象コミットの件名が範囲内で一意であることをfixup作成前に確認したことを検収する。
   対象コミット件名が範囲内で一意でない場合は、fixupを作成せず履歴と作業ツリーを変更せず`needs_escalation`で返したことを確認する。範囲内の既存commitに、件名先頭が`fixup!`・`squash!`・`amend!`へ完全一致するものが1件でもある場合も同じ扱いとする。各制御語の直後には半角空白1文字を置く。部分一致や件名途中の一致は遮断条件にしない。
   範囲列挙、merge確認、元HEADの確定、公開済み判定、OID・件名の列挙、件名一意性確認のいずれかに失敗するか範囲にmergeを含む場合は、fixupを作成せず`needs_escalation`で返したことを確認する。
   autosquash直前の再判定をTOCTOU対策として維持したことも確認する。
   書込担当が各fixup作成前、autosquash直前とamend直前の各phaseで`git remote`により全remoteを列挙したことを確認する。
   各remoteについてfetch URL集合（`git remote get-url --all <remote>`）とpush URL集合（`git remote get-url --all --push <remote>`）を取得したことを確認する。remote別のfetch URL列挙・push URL列挙終了コード、重複排除前後の照会URL件数を確認する。全remoteの集合を1つの照会URL集合へ統合し、URL文字列が完全一致するものだけ重複を除く。各照会URLへ`git ls-remote --refs <URL>`を実行して全照会URL endpointの再判定と遮断を完結させたことを確認する。全照会URL endpointの広告取得・不足OID fetch・再照合完了が記録されていない場合は、履歴を書き換えず`needs_escalation`で返したことを確認する。
   URL取得、重複排除または広告照会の失敗時に残りの結果を判定材料とせず、履歴を書き換えず`needs_escalation`で返したことも確認する。
   汎用の`git fetch --all --prune`を実行せず、各照会URLの広告取得と不足OIDだけの一時ref取得に限定したことを確認する。
   不足OIDだけを`git fetch --no-tags --no-write-fetch-head <URL> <OID>:<一時ref>...`で、一意の実行識別子・照会URL番号・OID番号を持ち同時実行及び過去の残留refと衝突しない一時ref名前空間へ取得し、広告集合を再照合してから一時refを回収したことを確認する。不足OIDが無いendpointではfetchを実行せず、`fetch_exit_code`へ`not_applicable`を記録したことを確認する。
   URL取得、重複排除、広告取得、不足OIDのfetch、広告集合の再照合、一時ref回収のいずれかに失敗した場合は、履歴を書き換えず`needs_escalation`で返したことも確認する。
   広告OIDの実在確認、direct refのobject解決、object typeがtagである広告OIDの再帰的なpeel、rebase範囲の列挙及び祖先判定に`GIT_NO_REPLACE_OBJECTS=1`を付けたことを確認する。
   replace refは`GIT_NO_REPLACE_OBJECTS=1`で無効化し、graftは同環境変数で無効化せず存在検出で遮断したことも確認する。
   祖先判定を`GIT_NO_REPLACE_OBJECTS=1 git merge-base --is-ancestor <対象OID> <最終commit>`として実行したことを確認する。
   phaseごとに反復された`rewrite_guard`の履歴順`target_oids`、Git版及び検収済みHEADを履歴実体と照合する。`shallow_repository_check_exit_code`と`is_shallow_repository`を照合し、終了コード0でboolが`false`の場合だけ後続判定を許可し、boolが`true`又は終了コードが非0の場合は`needs_escalation`で返したことを確認する。`remote_fetch_url_enumeration_exit_codes`と`remote_push_url_enumeration_exit_codes`を照合する。`query_url_count_before_deduplication`、`query_url_count_after_deduplication`と`all_query_endpoints_completed`も照合する。remote別のURL列挙終了コード、重複排除前後の照会URL件数、全照会URL endpointの広告取得・不足OID fetchと再照合完了、URL単位の各終了コードも照合する。URL値は受け取らず、URL列挙終了コードと件数だけを照合する。`fixup:<単位順>`と`amend`は単一対象でも1要素の配列とし、`autosquash`は最古fixup対象から履歴書換え前に保持した元HEADまでのfirst-parent全OIDを順序どおり含める。autosquashでは`target_oids`のfirst-parent全OIDを公開済み判定して遮断し、1件でも公開済み・判定不能または範囲にmergeを含む場合は履歴を書き換えていないことを確認する。正規化済み広告ref・OID、一時ref回収結果、各Gitコマンドの終了コード、判定結果と秘密情報を除去した必要最小限のエラー要約も照合する。`ref_evidence`の各要素について`target_oid`、`ref_name`、`advertised_oid`、`ref_object_exists`、`final_oid`、`final_type`、`ancestor_decision`及び`exclusion_reason`を照合する。`target_oids`と`advertised_refs_and_oids`の直積へ各1件だけ対応し、組の欠落、重複又は不一致が無いことを確認する。
   remote広告direct refの広告集合を対象とする。各phaseで`git rev-parse --is-shallow-repository`を実行し、終了コード0かつ出力が`false`の場合だけ広告集合と祖先判定を継続したことを確認する。出力が`true`、出力が`true`と`false`のいずれでもない場合又は終了コードが非0の場合は、履歴を書き換えず`needs_escalation`で返したことを確認する。名前空間に依存せず、最終参照先がcommitで対象OIDの子孫であるremote広告direct ref（`GIT_NO_REPLACE_OBJECTS=1 git merge-base --is-ancestor <対象OID> <最終commit>`の終了コード0）を公開済みと判定する。終了コード1は未公開として扱い、その他の終了コードはGit実行失敗として履歴を書き換えず`needs_escalation`で返したことを確認する。remoteが広告しないrefと最終参照先がcommit以外のdirect refは、除外証跡を残して公開済み判定から除外することを確認する。
   remote-tracking ref、local tag、remote設定と`FETCH_HEAD`が変更されていないことを確認する。
   Gitコマンドの出力、stderr全文と認証情報が無加工で受渡しされず、URLが照会中の一時値としてのみ扱われ、`rewrite_guard`、完了報告その他の受渡しへ記録されていないことを確認する。
   executorが書込担当の完了後にphaseごとの`rewrite_guard`反復証跡を検収し、履歴書換え前の中間受渡しを設けていないことを確認する。
   executorが保持した初回実装担当routeと実効`engine`、`model`及び`effort`、起動直前に解決した今回routeと実効3値、継続・新規起動に用いる識別子及び前担当の終端確認結果を照合する。両方の`engine`がCodexで実効3値がすべて一致する場合だけ元の実装担当threadを継続し、いずれかの実効値が異なる場合を含むそれ以外は旧担当の終端確認後に今回routeで新しい書込担当を起動して、検収済み状態を開始前に1回だけ渡したことを確認する。開始後は同じ書込担当が再判定からamendまでを所有したことを確認する。
   autosquashとamendの両方を実行した場合は、autosquash成功後に`GIT_NO_REPLACE_OBJECTS=1 git rev-parse HEAD`で書換え後HEADの完全OIDを取得し、autosquash成功後の2回目のpush済み判定対象を当該OIDへ置換したことを確認する。
   各fixup作成後に、対象OIDから得た統合先件名と形式に応じた制御件名（`fixup!`または`amend!`）が`git log -1 --format=%s`で完全一致したことを確認する。期待件名と一致しない場合はautosquashを実行せず、作成済みfixupと作業ツリーを保持して`needs_escalation`で返したことを確認する。
   fixupは`GIT_NO_REPLACE_OBJECTS=1 git commit --fixup=<対象OID>`で実行したことを確認する。
   rebaseは`GIT_NO_REPLACE_OBJECTS=1 GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash <base>`で実行したことを確認する。
   amendは`GIT_NO_REPLACE_OBJECTS=1 git commit --amend --no-edit`で実行したことを確認する。
   書換え前の各対象OIDと書換え後の全実装単位OIDの対応を履歴検収用に保持したことも確認する。
   開始前の担当引継ぎ以外に、履歴書換え途中の担当引継ぎを行っていないことも確認する。
   複数の過去単位では、各fixup作成後のclean確認で次の過去単位へ進み、全過去単位のfixup作成後に1回だけautosquashを実行したことを確認する。
   過去単位と最終単位が混在する場合は、autosquash前の反復対象を過去単位だけに限定する。autosquash成功後に`GIT_NO_REPLACE_OBJECTS=1 git rev-parse HEAD`で書換え後HEADの完全OIDを取得し、2回目のpush済み判定対象を当該OIDへ置換した後、最終単位の修正を実装し、近接検証を実行してstageする。amend直前の再判定後にだけ書換え後HEADへamendしたことを確認する

呼び出し元は各commit単位の受領時と最終レビュー時に`## 進捗ログ`の3列表へ行を追記する。
単位ごとのレーンのworktreeについて、用途、正確な絶対パス、状態、完全OID、管理対象領域の絶対パス、借用時は`なし`、作成主体、回収可否も記録する。
`結果・特記事項`にはcommit、検証、計画との差異、阻害要因を必要な範囲で書き、全操作履歴を要求しない。
レビュー修正を受領した場合は、履歴書換え前後のOID対応を進捗ログへ記録し、書込担当へpush権限を移さない。
完了報告の直前に計画の`## 完了条件`を全文再読し、各条件の充足根拠または未達理由を進捗ログの最終行へ記録する。
実装中に判断を変えた場合は`## 変更履歴`へ起点、指摘内容、採否、現在の結論、同期先を追記し、
人間向け固定領域の本文と矛盾しない状態に保つ。

報告本文と実体が異なる場合は実体を優先する。一意に補正できる値は呼び出し元が補正し、
実作業不足、証拠不足、候補が複数残る場合だけ未完了事項へ縮減して`plan-impl-executor`へ返す。
開始SHA、全ラウンドの応答全文、大規模な固定の報告書式は要求しない。

`needs_escalation`では認可範囲外の変更を成果へ混入させない。
深掘り条件が成立する事象は現行規範に従って`agent-toolkit:bugfix`を起動し、原因分析結果を確認経路でフィードバックへ送る。
ユーザー判断事項も同じ確認経路へ送り、独自の手順を本文書へ複製しない。

## pushとCI

実装委譲はcommitと二系統のレビューまでとする。呼び出し元が`../../commit/SKILL.md`の
`references/push-and-ci.md`を読み、pushとCI通過確認を所有する。
CI失敗時は`agent-toolkit:bugfix`で原因を確定する。原因分析によりコード・テスト・設定の修正commitが必要と確定した場合だけ、
通常モードの`plan-impl-executor`へ元計画を再投入せず、呼び出し元が`runtime-routing.md`の`execute_fix_model`を起動直前に解決して
単一の書込担当へ委譲する。起動文へ`skills/plan-mode/references/implementation-task.md`と担当種別`CI修正担当`を明示し、次の共通必須入力を渡す。

- 対象worktreeとプロジェクト規範の絶対パス。計画ファイルは計画起因の場合だけ渡す
- 実装単位、その目的及び変更説明
- 適用する作成規範スキル名と絶対パス
- ソート済みフィードバックファイル名一覧。フィードバック起因の場合だけ渡す
- 追加指示、許容済みの挙動変化。該当しない場合は`なし`
- git操作に用いるworktree絶対パス、複製元及び対象外worktree
- CIの原因分析結果、修正の認可根拠、`execute_fix_model`及びpush禁止。
  認可根拠には承認済み計画の該当箇所、原因となった変更を認可した利用者指示の逐語文、又は既存の公開契約の該当箇所を渡す

起動文へ担当種別を`CI修正担当`として明示する。
CI修正担当にはfast担当の1回修正とfastからfixへの昇格判定を適用しない。
担当はCI記録の原因修正、全検証、差分検収、stage及びcommitを完了し、呼び出し元は二系統レビュー、再push、CI確認へ戻る。
外部基盤障害など修正commitを要しない失敗では修正担当を起動せず、既存の原因別経路を維持する。
pushとCI成功を実測し、ソート済みフィードバックファイル名一覧の順で既存の`atk mq adopt`を1件ずつ実行する。
各採用処理の保存結果を照合する。
全条件の成立後だけ、進捗ログの記録値と`git worktree list --porcelain`を照合する。
`作成主体=caller`かつ`回収可否=可`で、管理対象領域を記録したworktreeだけを`git worktree remove <exact-path>`で除去し、
続いて`atk managed-temp cleanup --path <exact-parent>`で対応する管理対象領域を回収する。
実装レビュー用managed temp領域も、レビュー表と対応`.lock`の検収、push、CI、採用処理の完了後に`atk managed-temp cleanup --path <exact-parent>`で回収する。
借用した現在worktree、複製元、対象外worktreeは記録と検収だけを行い、削除しない。
中断または失敗時は全領域を保持し、対象外worktreeを変更しない。

全工程の完了後、呼び出し元は次の構成で完了報告を利用者へ提示する。
完了報告のメッセージはこの構成だけとし、枠の前後へ地の文を加えない。
`## 振り返り`節は書かない（session-review完了後の最終報告だけが置く）。

```text
<冒頭1文: 実装完了の言い切り>

## 成果

- 計画: `<計画ファイルの絶対パス>`
- commit: `<完全OID>`（単位ごとに列挙）
- push・CI: `<push先とCIの結果>`
- 採用フィードバック: `<ファイル名>`（無い場合は`なし`）
```
