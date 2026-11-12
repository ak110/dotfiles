# 設計コンセプト集

本文書はAWI運用（2026年6月〜）でユーザーが決めた意向・運用方針の背景と経緯を集約する。
実行時に従う規範は現行のルールファイル（`agent-toolkit/share/rules/common/`配下）と各スキルが定め、本文書は規範本文を代替しない。
記述が現行規範と矛盾する場合は現行規範を正とし、本文書側を更新する。
各節の冒頭には、その節の条文を実行時に適用する規範文書の所在を明記する。
条文が実行規範として参照されていない場合は、適用する工程の文書へ移すか、経緯記録であることを本文で示す。
本文書にだけ存在する判断規則を残さない。
機構の実装構造の設計意図（知識境界・却下した代替案）は[design.md](design.md)の索引から主題別の本文を選んで読む。
障害の経緯は[incidents.md](incidents.md)が扱う。
断りのない項目はユーザーの発話・回答に由来する意向・決定であり、
障害や動作確認の結果に由来する項目は、観測事実に基づくことを文中で明示する。
各項目は方針・判断の内容を規範述語で直接書き、「決まった」「確定した」のような決定イベントの報告として書かない。
経緯を述べる場合は行為者（ユーザーなど）を明示し、時期・契機・出典は括弧書きで添える。
コーディングエージェント向け文書を編集する前に本文書とincidents.mdを読み、
過去の決定と矛盾する変更（再発）を止めることを目的とする。

## [完遂と縮退の禁止](concepts-completion.md#完遂と縮退の禁止)

## [判断の優先順位（QCD）](concepts-priorities.md#判断の優先順位qcd)

## [計画と実行の運用](concepts-planning.md#計画と実行の運用)

## [所要時間とコスト](concepts-duration.md#所要時間とコスト)

## [付帯作業の単位](concepts-incidental-work.md#付帯作業の単位)

## [過剰設計の抑制](concepts-overengineering.md#過剰設計の抑制)

## [概念設計と最小実装の優先順位](concepts-design-testing.md#概念設計と最小実装の優先順位)

## [テストの本質性](concepts-design-testing.md#テストの本質性)

## [原因分析と再発防止の深さ](concepts-root-causes.md#原因分析と再発防止の深さ)

## [規範文書の書き方](concepts-document-writing.md#規範文書の書き方)

## [計画レビュー廃止前の運用（2026年9月13日まで）](concepts-planning-history.md#計画レビュー廃止前の運用2026年9月13日まで)

## [複数環境での利用](concepts-workflows.md#複数環境での利用)

## [developとmasterのリリース運用](concepts-workflows.md#developとmasterのリリース運用)

## [WIの状態と遷移](concepts-wi-states.md#wiの状態と遷移)

## [`atk`の出力と操作](concepts-workflows.md#atkの出力と操作)

## [AWI・UWIの本文と投入](concepts-wi-drafting.md#awiuwiの本文と投入)

## [確認と承認](concepts-wi-approval.md#確認と承認)

## [process-wiとレーン](concepts-workflows-lanes.md#process-wiとレーン)

## [WI選定と処理](concepts-wi-processing.md#wi選定と処理)

## [工程と検証の名前](concepts-workflows-lanes.md#工程と検証の名前)

## [多数の単純作業の分担](concepts-wi-processing.md#多数の単純作業の分担)

## [計画ファイルの体裁の扱い](concepts-workflows-lanes.md#計画ファイルの体裁の扱い)

## [フックのホスト間共通化](concepts-hooks.md#フックのホスト間共通化)

## [Claude CodeとCodexの規範配置](concepts-host-rules.md#claude-codeとcodexの規範配置)

## [委譲全般と利用上限](concepts-connections.md#委譲全般と利用上限)

## [待機と観測](concepts-waiting.md#待機と観測)

## [レビューの運用](concepts-reviews.md#レビューの運用)

## [`review-session`](concepts-session-review.md#review-session)

## [Codexの文脈長設定](concepts-codex-context.md#codexの文脈長設定)

## [確認・合意の運用](concepts-confirmation-decisions.md#確認合意の運用)

## [依存とサプライチェーン](concepts-dependencies-temp.md#依存とサプライチェーン)

## [managed-temp](concepts-dependencies-temp.md#managed-temp)

## [秘匿値とworktree](concepts-secrets.md#秘匿値とworktree)

## [auto modeと許可ルール](concepts-auto-mode.md#auto-modeと許可ルール)

## [実行環境と表示](concepts-environment.md#実行環境と表示)

## [配布するブラウザー資産](concepts-environment.md#配布するブラウザー資産)
