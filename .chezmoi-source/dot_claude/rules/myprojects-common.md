<atk-auto source="dotfiles" kind="rules" path=".chezmoi-source/dot_claude/rules/myprojects-common.md">
# myprojects-common.md: ホスト共通の個人プロジェクト規範

実行ホストとエージェントの種別によらず、同一作者の個人プロジェクト全体へ適用する規範を置く。
ホストごとに異なるプロジェクト一覧と同期方針は、Claude Codeへ配布する`myprojects.md`が扱う。

## AWI処理の開始時の横断同期

`process-wi` の選定を担当するメインは、個人プロジェクトの選定前に `operate-ak110-projects` で横断同期を調べ、依存更新の要否を決める。
追加の判定も含め、この結果を後続の工程へ渡す。
同じ会話の次の選定では、対象のHEADとロックファイルが変わらなければ結果を再利用する。
依存更新だけを引き受けたレーンは、受領した判定で作業し、この契機でスキルを起動し直さない。

## WI作成時の追従判定

個人プロジェクトの性質や作業手順（開発・検証できる環境、セットアップ手順、リリース運用など）を変える要求のWIを作成する時は、反映先を確定する前に`operate-ak110-projects`を起動する。
同スキル「判定手順」に従い、そのプロジェクトを記述するdotfiles側の規範への追従と反映先を確定する。

## 公開後の案内を受け取る利用環境

使い方が変わる変更の案内では、私用のstheno（Windows）とeuryale（Linux）、仕事のWindowsとLinuxの計4環境について、実施済み・要操作・不要を示す。
接続できる私用ホストでの操作は、ホスト別の認可の範囲に限る。

## 個人プロジェクトのリリース開始手順

次の時は`operate-ak110-projects`を起動し、同スキルの「リリース運用」に従う。

- 協調モードの変更で、公開範囲を判定する前
- patch、minorまたはmajorのリリースを求められ、具体的な公開コマンドを選ぶ前
- 一連の作業（`agent-toolkit:atk-mode`の作業`process-wi`の公開工程を含む）がpushとCI成功まで終わった時（リリースの要否を判定する）

agent-toolkit自身のversion bump、個人プロジェクトの外、Dockerイメージの再構築およびworkflowの内部の処理は対象外とする。

## pytilpackに無い汎用処理の改善要望

`agent-basics:writing-standards/references/pytilpack.md`「汎用処理を自作する前の確認」で、必要な汎用処理がpytilpackに無かった場合に適用し、作業対象が個人プロジェクトの一覧に無いプロジェクト（業務のプロジェクトなど）でも適用する。
ユーザーへ確認せずに、pytilpackのリポジトリを対象とするAWIを`agent-toolkit:atk-mode/playbooks/wi-drafting.md`の手順で投入し、投入したAWIを作業完了報告で伝える。
その場の作業は、同資料のとおりプロジェクト内へ実装して続ける。
AWIには汎用化した要求（必要な機能、想定する入出力、利用場面の一般的な説明）だけを書き、作業中のプロジェクトに固有の名称、コード、データは除く。
pytilpackは公開リポジトリであり、要求の内容が実装とcommitとして公開されるためである。
pytilpackのキューへ投入できない環境では、作業完了報告だけを行う。
</atk-auto>
