<atk-auto source="dotfiles" kind="rules" path=".chezmoi-source/dot_claude/rules/myprojects-common.md">
# myprojects-common.md: ホスト共通の個人プロジェクト規範

実行ホストとエージェントの種別によらず、同一作者の個人プロジェクト全体へ適用する規範を置く。
ホストごとに異なるプロジェクト一覧と同期方針は、Claude Codeへ配布する`myprojects.md`が扱う。

## AWI処理の開始時の横断同期

個人プロジェクトの`agent-toolkit:process-wi`で選定を担当するメインは、選定開始より前に`operate-ak110-projects`スキルを起動する。
判定済みの条件を受けて依存更新だけを実施するレーンは、この契機では起動しない。
同期調査と依存更新の要否の判定結果は、そのセッションの後続の工程へ渡す。
起動名はClaude Code・Codexとも`operate-ak110-projects`とする。
Claude Codeでは`Skill`ツールから起動する。

## WI作成時の追従判定

個人プロジェクトの性質や作業手順（開発・検証できる環境、セットアップ手順、リリース運用など）を変える要求のWIを作成するときは、反映先を確定する前に`operate-ak110-projects`スキルを起動する。
同スキル「判定手順」に従い、そのプロジェクトを記述するdotfiles側の規範への追従と反映先を確定する。

## 個人プロジェクトのリリース開始手順

個人プロジェクトの協調モードの変更では、公開範囲を判定する前に`operate-ak110-projects`を起動し、「リリース運用」の恒常的な認可と明示指定の優先を適用する。

個人プロジェクトでpatch、minorまたはmajorのリリースを求められたときは、具体的な公開コマンドを選ぶ前に
`operate-ak110-projects`スキルを起動し、同スキルの「リリース運用」に従う。
個人プロジェクトの一連の作業（`agent-toolkit:process-wi`の公開工程を含む）がpushとCI成功まで終わったときも同スキルを起動し、「リリース運用」に従ってリリース要否を判定する。
agent-toolkit自身のversion bump、個人プロジェクト外、Dockerイメージの再構築およびworkflow内部の処理は対象外とする。

## pytilpackに無い汎用処理の改善要望

`agent-basics:writing-standards`の`references/pytilpack.md`「汎用処理を自作する前の確認」で、必要な汎用処理がpytilpackに無かった場合に適用する。
作業対象が個人プロジェクトの一覧に無いプロジェクト（業務のプロジェクトなど）でも適用する。
ユーザーへ確認せずに、pytilpackのリポジトリを対象とするAWIを`agent-toolkit:wi-standards`の通常の投入手順で投入し、投入したAWIを作業完了報告で伝える。
その場の作業は、同資料のとおりプロジェクト内へ実装して続ける。
AWIには汎用化した要求（必要な機能、想定する入出力、利用場面の一般的な説明）だけを書き、作業中のプロジェクトに固有の名称、コード、データは除く。
pytilpackは公開リポジトリであり、要求の内容が実装とcommitとして公開されるためである。
pytilpackのキューへ投入できない環境では、作業完了報告だけを行う。
</atk-auto>
