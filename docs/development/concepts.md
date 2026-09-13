# 設計コンセプト集

本文書は、AWI運用（2026年6月〜）で利用者が決めた意向・運用方針の背景と経緯を集約する。
実行規範の正本は現行のルールファイル（`agent-toolkit/rules/`配下）と各スキルであり、本文書は規範本文を代替しない。
記述が現行規範と矛盾する場合は現行規範を正とし、本文書側を更新する。
各節の冒頭には、当該節の条文が実行時に適用される正本の所在を明記する。
正本を持たない条文は、当該条文を適用する工程の文書へ移すか、経緯記録であることを本文で示す。
本文書にだけ存在する判断規則を残さない。
機構の実装構造の設計意図（知識境界・却下した代替案）は[design.md](design.md)、事故の経緯は[incidents.md](incidents.md)が扱う。
断りのない項目は利用者の発話・回答に由来する意向・決定であり、
観測事実（事故・実測）に由来する項目は文中でその旨を示す。
各項目は方針・判断の内容を規範述語で直接書き、「決まった」「確定した」のような決定イベントの報告として書かない。
経緯を述べる場合は行為者（利用者など）を明示し、時期・契機・出典は括弧書きで添える。
コーディングエージェント向け文書を編集する前に本文書とincidents.mdを読み、
過去の決定と矛盾する変更（再発）を止めることを目的とする。

## [完遂と縮退の禁止](concepts-principles.md#完遂と縮退の禁止)

## [品質とコスト効率の優先順位](concepts-principles.md#品質とコスト効率の優先順位)

## [過剰設計の抑制](concepts-principles.md#過剰設計の抑制)

## [概念設計と最小実装の優先順位](concepts-principles.md#概念設計と最小実装の優先順位)

## [テストの本質性](concepts-principles.md#テストの本質性)

## [原因分析と再発防止の深さ](concepts-principles.md#原因分析と再発防止の深さ)

## [規範文書の書き方](concepts-principles.md#規範文書の書き方)

## [複数環境での利用](concepts-workflows.md#複数環境での利用)

## [developとmasterのリリース運用](concepts-workflows.md#developとmasterのリリース運用)

## [WIキューの運用](concepts-workflows.md#WIキューの運用)

## [計画ファイルの体裁の扱い](concepts-workflows.md#計画ファイルの体裁の扱い)

## [フックのホスト間共通化](concepts-runtime.md#フックのホスト間共通化)

## [Claude CodeとCodexの規範配置](concepts-runtime.md#Claude CodeとCodexの規範配置)

## [委譲の運用](concepts-runtime.md#委譲の運用)

## [確認・合意の運用](concepts-governance.md#確認・合意の運用)

## [セキュリティと環境](concepts-governance.md#セキュリティと環境)
