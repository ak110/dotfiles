# 障害事例集

本文書はWI運用（2026年6月〜）で観測された障害や欠陥のうち、
再発防止の判断材料になる事例を区分別に集約する。
事例を加える場合は、次の表の定義で所属する区分を決め、事例の書式と並び順に従ってその区分へ置く。
各事例は発生時期・事象・直接原因・確定した対策を記す。
各事例の対策は確定当時の内容であり、反映先も当時の位置を指す。
後続の改善で対策の内容または反映先が変わった場合は、現行のルール・スキル側を正とする。
運用方針や意向は[concepts.md](concepts.md)、機構の設計意図は[design.md](design.md)の索引から主題別の本文を参照する。

## 区分

| 区分 | 定義 | 所在ファイル |
| --- | --- | --- |
| [データ破壊と喪失](incidents-data-security.md#データ破壊と喪失) | 成果物、作業ツリー、キューのデータが失われたか破損した事例 | `incidents-data-security.md` |
| [無許可の公開操作と委譲範囲の逸脱](incidents-data-security.md#無許可の公開操作と委譲範囲の逸脱) | 認可の無い外部操作と委譲範囲を超えた操作 | `incidents-data-security.md` |
| [セキュリティ設定の弱体化](incidents-data-security.md#セキュリティ設定の弱体化) | sandbox、権限、秘匿値の保護を弱めた事例 | `incidents-data-security.md` |
| [誤った完了報告と虚偽報告](incidents-validation.md#誤った完了報告と虚偽報告) | 未達や未観測を完了として報告した事例 | `incidents-validation.md` |
| [WI投入とAWI起草の欠陥](incidents-validation.md#wi投入とawi起草の欠陥) | WIの投入、本文の起草、由来の判定の誤り | `incidents-validation.md` |
| [実行レビューと証拠検査の欠陥](incidents-validation.md#実行レビューと証拠検査の欠陥) | 実行レビュー、完成条件証拠、証拠検査の誤り | `incidents-validation.md` |
| [テスト、CIと検索出力の欠陥](incidents-validation.md#テストciと検索出力の欠陥) | テストの隔離、CI、検索の出力量の誤り | `incidents-validation.md` |
| [誤判定と検証不足](incidents-validation.md#誤判定と検証不足) | 根拠の確認を欠いた判定（前記の3区分に当たらないもの） | `incidents-validation.md` |
| [規範の消失と陳腐化](incidents-validation.md#規範の消失と陳腐化) | 規範の削除・縮小・移設で条文や参照が失われた事例 | `incidents-validation.md` |
| [レビューの発散と目的の変質](incidents-workflows.md#レビューの発散と目的の変質) | レビューの往復が発散したか、目的が変わった事例 | `incidents-workflows.md` |
| [停滞と空転](incidents-workflows.md#停滞と空転) | 待機、応答停止、反復で工程が進まなかった事例 | `incidents-workflows.md` |
| [公開とリリース工程の未完了](incidents-workflows.md#公開とリリース工程の未完了) | push、公開、終端工程の取りこぼし | `incidents-workflows.md` |
| [並行実行の競合](incidents-workflows.md#並行実行の競合) | 複数の主体やレーンの書込と割当の競合 | `incidents-workflows.md` |
| [要求と判断の脱落](incidents-workflows.md#要求と判断の脱落) | ユーザーが示した要件、指定、介入が工程から脱落した事例 | `incidents-workflows.md` |
| [フックとセッション状態の不全](incidents-runtime.md#フックとセッション状態の不全) | hookとセッション状態ファイルの不全 | `incidents-runtime.md` |
| [agents_serverと委譲基盤の不全](incidents-runtime.md#agents_serverと委譲基盤の不全) | agents_serverとMCPプロセス、委譲先の起動と待機の不全 | `incidents-runtime.md` |
| [配布と更新に伴う不整合](incidents-runtime.md#配布と更新に伴う不整合) | 配布物の改名・廃止の残存、設定変更の未反映、更新処理の失敗 | `incidents-runtime.md` |

## 事例の書式と並び順

各事例は次の形で書く。`背景原因`、`影響`、`後継方針`、`再発`、`調査の経過`の行は該当する場合だけ置き、補足の観点（混入要因、根本原因、棄却した仮説など）は`背景原因`の行の中へ括弧付きの小見出しで書く。

```text
- YYYY年M月D日: 事象
  直接原因: …
  背景原因: …
  調査の経過: …
  影響: …
  対策: …
  後継方針（YYYY年M月D日）: …
  再発（YYYY年M月D日）: …
```

日付は`YYYY年M月D日`、日が不明なら`YYYY年M月`、期間は`YYYY年M月D日〜D日`、複数日は`YYYY年M月D日とD日`とする。
区分の中では日付の昇順に並べ、日の無い事例はその月の末尾に置く。
同じ機構の再発は、先の事例へ`再発（YYYY年M月D日）:`の行を加えて1つの事例にまとめる。
