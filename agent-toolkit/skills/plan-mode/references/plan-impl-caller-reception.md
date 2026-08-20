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
各管理対象領域には`git worktree add --detach <absolute-path> <common-base>`で計画ファイル専用worktreeを作成する。
計画が呼び出し元によるレーンのworktreeの作成も明示する場合は、同じ方法でレーンのworktreeを作成する。
作成直後に用途、絶対パス、管理対象領域の絶対パス、HEADの完全OID、作成主体、回収可否を`## 進捗ログ`へ記録する。
以降、この6項目をworktreeの記録属性と呼ぶ。
起動文は受信者への命令を先頭に置き、次だけを渡す。

- モード指定`通常の実装モード`
- 計画ファイル、プロジェクト規範、該当する作成規範スキルの絶対パス。1計画ファイルの実装は1つの書込担当へ順次割り当てること
- worktreeの完全な一覧。各worktreeへ前掲の記録属性を付し、借用時の管理対象領域は`なし`とする
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
4. 二系統のレビューの対象commit、読み取り専用状態、指摘と対応結果
5. 計画の完了条件と`## 進捗ログ`
6. 共通base、統合順、全単位commit、各worktreeの前掲の記録属性と状態
7. 通常実装モードのレビュー修正以外、統合後レビュー調整モードでは、`rewrite_guard`が`not_applicable`でありphase証跡を要求していないことを確認する
8. 通常の実装モードでレビュー修正を受領した場合は、レビュー対象の最終HEAD完全OIDと指摘ID・統合先commit完全OIDの対応表を確認する。
   単位ごとの変更前OIDと変更後OID、commit数と順序、commitメッセージを確認する。
   各commitの差分帰属、最終HEAD、レビュー修正専用commitが残っていないことも確認する。
   過去単位を含む場合は、fixup作成前に保持した元HEADとfixup対象の最古commitを完全OIDで確定したことを確認する。
   `GIT_NO_REPLACE_OBJECTS=1 git rev-list --first-parent --reverse <最古fixup対象>^..<元HEAD>`でautosquashのrebase範囲を列挙したことを確認する。
   `GIT_NO_REPLACE_OBJECTS=1 git rev-list --first-parent --merges <最古fixup対象>^..<元HEAD>`でmerge commitを事前確認する。
   範囲列挙、merge確認、元HEADの確定のいずれかに失敗するか範囲にmergeを含む場合は、fixupを作成せず`needs_escalation`で返したことを確認する。
   書込担当が各fixup作成前、autosquash直前とamend直前の各phaseで`git remote`により全remoteを列挙したことを確認する。
   各remoteについてfetch URL集合（`git remote get-url --all <remote>`）とpush URL集合（`git remote get-url --all --push <remote>`）を取得したことを確認する。remote別のfetch URL列挙・push URL列挙終了コード、重複排除前後の照会URL件数を確認する。全remoteの集合を1つの照会URL集合へ統合し、URL文字列が完全一致するものだけ重複を除く。各照会URLへ`git ls-remote --heads --tags --refs <URL>`を実行して全照会URL endpointの再判定と遮断を完結させたことを確認する。全照会URL endpointの広告取得・不足OID fetch・再照合完了が記録されていない場合は、履歴を書き換えず`needs_escalation`で返したことを確認する。
   URL取得、重複排除または広告照会の失敗時に残りの結果を判定材料とせず、履歴を書き換えず`needs_escalation`で返したことも確認する。
   汎用の`git fetch --all --prune`を実行せず、各照会URLの広告取得と不足OIDだけの一時ref取得に限定したことを確認する。
   不足OIDだけを`git fetch --no-tags --no-write-fetch-head <URL> <OID>:<一時ref>...`で照会URLごとの数値添字の一時refへ取得し、広告集合を再照合してから一時refを回収したことを確認する。
   URL取得、重複排除、広告取得、不足OIDのfetch、広告集合の再照合、一時ref回収のいずれかに失敗した場合は、履歴を書き換えず`needs_escalation`で返したことも確認する。
   広告OIDの実在確認、branchまたはtagのobject解決とtagの再帰的なpeel、rebase範囲の列挙及び祖先判定に`GIT_NO_REPLACE_OBJECTS=1`を付けたことを確認する。
   replace refやgraftの影響を除外したことも確認する。
   祖先判定を`GIT_NO_REPLACE_OBJECTS=1 git merge-base --is-ancestor <対象OID> <branchTip>`として実行したことを確認する。
   phaseごとに反復された`rewrite_guard`の履歴順`target_oids`、Git版及び検収済みHEADを履歴実体と照合する。`shallow_repository_check_exit_code`と`is_shallow_repository`を照合し、終了コード0でboolが`false`の場合だけ後続判定を許可し、boolが`true`又は終了コードが非0の場合は`needs_escalation`で返したことを確認する。`remote_fetch_url_enumeration_exit_codes`と`remote_push_url_enumeration_exit_codes`を照合する。`query_url_count_before_deduplication`、`query_url_count_after_deduplication`と`all_query_endpoints_completed`も照合する。remote別のURL列挙終了コード、重複排除前後の照会URL件数、全照会URL endpointの広告取得・不足OID fetchと再照合完了、URL単位の各終了コードも照合する。URL値は受け取らず、URL列挙終了コードと件数だけを照合する。`fixup:<単位順>`と`amend`は単一対象でも1要素の配列とし、`autosquash`は最古fixup対象から履歴書換え前に保持した元HEADまでのfirst-parent全OIDを順序どおり含める。autosquashでは`target_oids`のfirst-parent全OIDを公開済み判定して遮断し、1件でも公開済み・判定不能または範囲にmergeを含む場合は履歴を書き換えていないことを確認する。正規化済み広告ref・OID、一時ref回収結果、各Gitコマンドの終了コード、判定結果と秘密情報を除去した必要最小限のエラー要約も照合する。非commit tagの`noncommit_tag_peeled_object_exists`にpeeled objectの実在確認結果が記録されていることを照合する。`noncommit_tag_final_oid_and_type`に最終OIDとobject typeが記録されていることを照合する。`noncommit_tag_exclusion_reason`に祖先判定からの除外理由が記録されていることを照合する。
   remote広告branch・tagの広告集合だけを対象とする。各phaseで`git rev-parse --is-shallow-repository`を実行し、終了コード0かつ出力が`false`の場合だけ広告集合と祖先判定を継続したことを確認する。出力が`true`、終了コードが非0、出力が`true`と`false`のいずれでもない場合は、履歴を書き換えず`needs_escalation`で返したことを確認する。対象OIDの子孫であるremote広告branch tip（`GIT_NO_REPLACE_OBJECTS=1 git merge-base --is-ancestor <対象OID> <branchTip>`の終了コード0）又は同じ判定で対象OIDの子孫であるremote広告tagだけを公開済みと判定する。終了コード1は未公開として扱い、その他の終了コードはGit実行失敗として履歴を書き換えず`needs_escalation`で返したことを確認する。local-only tag、remoteが広告しないtagと最終参照先がcommit以外のtagを誤って公開済みと判定していないことを確認する。
   remote-tracking ref、local tag、remote設定と`FETCH_HEAD`が変更されていないことを確認する。
   Gitコマンドの出力、stderr全文と認証情報が無加工で受渡しされず、URLが照会中の一時値としてのみ扱われ、`rewrite_guard`、完了報告その他の受渡しへ記録されていないことを確認する。
   executorが書込担当の完了後にphaseごとの`rewrite_guard`反復証跡を検収し、履歴書換え前の中間受渡しを設けていないことを確認する。
   executorがレビュー検収後に内部確定して書込担当へ渡した実行系、継続・新規起動に用いる識別子及び前担当の終端確認結果を照合する。実行系に応じた担当の引継ぎは開始前に一度だけ行われ、Codex経路は元の実装担当threadを継続し、Claude経路は旧担当の終端確認後に検収済み状態を渡して新しい書込担当を起動したことを確認する。開始後は同じ書込担当が再判定からamendまでを所有したことを確認する。
   autosquashとamendの両方を実行した場合は、autosquash成功後に`GIT_NO_REPLACE_OBJECTS=1 git rev-parse HEAD`で書換え後HEADの完全OIDを取得し、autosquash成功後の2回目のpush済み判定対象を当該OIDへ置換したことを確認する。
   fixupは`GIT_NO_REPLACE_OBJECTS=1 git commit --fixup=<対象OID>`で実行したことを確認する。
   rebaseは`GIT_NO_REPLACE_OBJECTS=1 GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash <base>`で実行したことを確認する。
   amendは`GIT_NO_REPLACE_OBJECTS=1 git commit --amend`で実行したことを確認する。
   書換え前の各対象OIDと書換え後の全実装単位OIDの対応を履歴検収用に保持したことも確認する。
   開始前の担当引継ぎ以外に、履歴書換え途中の担当引継ぎを行っていないことも確認する。
   複数の過去単位では、各fixup作成後のclean確認で次の過去単位へ進み、全過去単位のfixup作成後に1回だけautosquashを実行したことを確認する。
   過去単位と最終単位が混在する場合は、autosquash前の反復対象を過去単位だけに限定し、autosquash成功後に最終単位の修正を実装し、近接検証を実行してstageした後、amend直前の再判定後にだけamendしたことを確認する

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

実装委譲はcommitと二系統のレビューまでとする。呼び出し元が`agent-toolkit:commit`の
`references/push-and-ci.md`を読み、pushとCI通過確認を所有する。
CI失敗時は`agent-toolkit:bugfix`で原因を確定する。原因分析によりコード・テスト・設定の修正commitが必要と確定した場合だけ、
通常モードの`plan-impl-executor`へ元計画を再投入せず、呼び出し元が`runtime-routing.md`の`execute_model`を起動直前に解決して
単一の書込担当へ委譲する。入力は対象worktree、原因分析結果、修正の認可根拠となる承認済み計画、適用規範、許容する変更、
対象外worktree、push禁止とする。担当は修正、検証、commitを完了し、呼び出し元は二系統レビュー、再push、CI確認へ戻る。
外部基盤障害など修正commitを要しない失敗では修正担当を起動せず、既存の原因別経路を維持する。
pushとCI成功を実測し、ソート済みフィードバックファイル名一覧の順で既存の`atk mq adopt`を1件ずつ実行する。
各採用処理の保存結果を照合する。
全条件の成立後だけ、進捗ログの記録値と`git worktree list --porcelain`を照合する。
`作成主体=caller`かつ`回収可否=可`で、管理対象領域を記録したworktreeだけを`git worktree remove <exact-path>`で除去し、
続いて`atk managed-temp cleanup --path <exact-parent>`で対応する管理対象領域を回収する。
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
