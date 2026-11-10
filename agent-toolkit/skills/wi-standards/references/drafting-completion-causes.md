# 起草完了の条件：原因と定義の所有者

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 報告用UWIの確認・保存手順へ切り替える前 | `../../report-completion/references/report-formats-uwi.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/references/report-formats-uwi.md` |
| 複数リポジトリの共通本文を参照するAWIを読む前 | `cross-repository-submission.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/cross-repository-submission.md` |
| 観測事象を契機とする要求の採用を確定する前 | `agent-basics:bugfix/references/root-cause-analysis-evidence.md`、`agent-basics:bugfix/references/root-cause-actions-selection.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-analysis-evidence.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-actions-selection.md` |
| 一連の処理全体の成立を判定する前 | `agent-basics:bugfix/references/root-cause-actions-selection.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-actions-selection.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-severity-impact.md`、`agent-basics:bugfix/references/root-cause-remediation.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity-impact.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-remediation.md` |
| 類似見直しの限定探索と是正範囲を判定する前 | `agent-basics:bugfix/references/root-cause-severity-impact.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity-impact.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-severity-impact.md`、`agent-basics:bugfix/references/root-cause-remediation.md`、`agent-basics:bugfix/references/root-cause-actions-hierarchy.md`、`agent-basics:bugfix/references/root-cause-prevention-requirements.md`、`agent-basics:bugfix/references/root-cause-prevention-verification.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity-impact.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-remediation.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-actions-hierarchy.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-prevention-requirements.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-prevention-verification.md` |
| 原因分析の記録が推定だけに依拠していないことを判定する前 | `agent-basics:bugfix/references/root-cause-analysis-evidence.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-analysis-evidence.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-remediation.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-remediation.md` |

## 原因と定義の所有者

観測された事象を契機とする要求では、起草前に`agent-basics:bugfix`を起動する。
手動登録では投入元が独立別件の原因と変更内容も確定する。
他の工程からの独立別件は、観測事実、支持根拠のある原因仮説、残る問いを区別して引き渡せる。
通常の実装を求めるAWIでは、直接的原因と根本原因を現物に基づいて確定し、対策を1つの案に絞り込む。
類似の見直しを行う母集団と再発防止の要否は、`agent-basics:bugfix/references/root-cause-severity-impact.md`「QとC・Dの区別」に従う。
`agent-basics:bugfix/references/root-cause-remediation.md`「類似見直し」も適用し、分析の深さの区分にかかわらず、その範囲を保持する。
原因分析の対象はAWIとし、UWIは確認事項として扱う。
事象を観測した場所、欠陥を作り出した実装、変更すべき定義、および変更後の検収場所を明確に区別して特定する。
`target_repo`には変更対象の定義を保持しているリポジトリを指定する。
外部ツール、SDK、共有設定の欠陥が消費側で表面化した場合は、その定義を所有する生成側のリポジトリへWIを投入し、消費側で再現するための条件を完成条件に含めておく。
ユーザーが出力結果のみを提示し発生箇所を明示していない場合は、エージェント自身の実行環境（製品のツール実行やサンドボックス環境など）、CI、配備先を含めて候補を探索し、取得可能なログや記録を手がかりに特定を進める。
発生時の操作手順や資料などユーザーしか持ち得ない情報が必要な場合は、共通の判定基準に従ってユーザーへ確認し、推測による発生箇所の特定を前提にしない。
原因の特定に変動する値を用いる場合は、事象発生時点の値との比較結果を`## 原因分析`に記録する。
事象発生後の値しか得られない場合は、原因を未確定として扱う。
ユーザーが選定した値、品質水準、適用範囲に関わる対策では、それを維持する根拠、または変更に対する明示的な合意を同節に記録する。
ファイルや仕組みの削除、改名、意味を変更する場合は、変更前のパスや概念を表す語句を、対象リポジトリ全体（隠しディレクトリを含む）で固定文字列検索する。
設計ドキュメントやエンドユーザー向けガイドを含む直接の影響範囲（`../../write-plan/references/plan-file-standards-requirements.md`「要件・外部仕様」）は、反映先へ含める。
反映対象からの除外は記述ごとに判定し、過去の記録を含むファイルでも、冒頭の案内や現在形の説明など現在の状態を述べる記述は反映対象へ含める。
反映対象に含めない記述の判定根拠は証拠へ残し、処理側の判断を左右する除外事項だけを本文に明記する。

投入先リポジトリの外部にある対象について処置不要と判断する場合は、その根拠として現物のファイルとリビジョン（版）を対応付け、その版において原因となる条件が成立しないことを確認する。
