---
name: process-wi
argument-hint: "[single-lane|all-repos] [追加指示]"
description: >
  対象リポジトリのAWIを計画、実装、レビュー、公開まで処理するときに起動する。
  single-lane引数で主作業ツリーの単一処理、all-repos引数で登録全体の処理を行う。
  常駐process-loopの統括担当もall-reposで起動する。
---

# AWIの処理

引数の最初の語を確認し、共通工程を始める前に次の表から1つの処理資料を全文読む。
先頭語が完全一致する場合だけモードとして消費し、残りを追加指示として保持する。
先頭の空白と語の区切りには空白・タブ・改行を使える。
一致しない場合と引数なしは通常のレーン処理とし、引数全体を追加指示として扱う。
選んだ資料の工程に従い、他のモードの処理資料はそのモードへ切り替える時だけ読む。

## 開始後に追加されたWIの確認

選定を担当するメインは、実行開始時刻とタイムゾーンを既存の作業記録へ残す。
all-reposでは統括の巡回開始時刻を各リポジトリの担当へ引き継ぐ。
30分点検ごとと公開工程の開始前に、`references/immediate-fix.md`「開始後に追加されたWIの確認」に従って新着WIを判定する。
対象はall-reposの統括、各リポジトリの選定メイン、single-laneのメインとする。

## 即時対応

不良・是正要求・改善の対処時機は、`references/immediate-fix.md`「即時対応」で判定する。
現時点で起きている不良は工程を止めなくても今回の正式対応へ含め、先送りは共通規範「完遂と先送り」の条件内で行う。
潜在不良・効率改善の最小修正と正式対応の分離、今回追加の比較、担当・基点・認可、公開後の再公開も同資料に従う。

## 検証環境

処理担当は検証の対象を選ぶ前に、`repositories`の対象リポジトリの`platforms`を確認する。
実行中のOSが含まれなければ、ローカルではそのOSで可能なコンパイルなどの検査だけを行い、テスト合否は登録されたOSのCIで判定する。
含まれるOSでは従来どおりローカルで検証する。
OSの指定がない登録はLinuxとWindowsの両方を対象とし、必要な検証を省く理由にしない。

## 系統を登録したリポジトリ

`atk config repos list`で`branches`を持つリポジトリは、継続して作業が入るoriginのブランチ（系統）ごとに処理する。
処理中の系統は、常駐から受け取った対象では集合JSONの`target_branch`、手動起動では起動した作業ツリーが追跡するブランチである。
規範の「ベースブランチ」は処理中の系統のブランチを指し、候補の取得、レーンの作成と統合、push、CIの確認、公開状態の検収はそのブランチを対象にする。
系統ごとの作業ツリーではローカルブランチ名がリモートのブランチ名と異なる。
そのためpushは`git push --dry-run --porcelain origin HEAD:refs/heads/<系統>`で到達先を確定し、同じrefspecで実行する。
公開状態の4項目のうちブランチ名の項目は、`# branch.upstream`が`origin/<系統>`であることで確かめる。
処理中に投入するUWIとAWIは、`atk wi add --target-branch <系統>`で処理中の系統を宛先にする。
両方の系統に必要な修正のAWIは、`agent-toolkit:wi-standards`の`references/wi-standards-operations.md`「投入と取得」に従って取り込み元の系統を宛先にする。
GitHub CopilotのレビューとDependabotアラートの監査はリポジトリ単位の作業のため、`default_branch`の系統（系統を省略したWIの宛先）の処理だけが行う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 30分点検を行う時と公開工程を始める前 | `agent-toolkit:process-wi/references/immediate-fix.md` |
| ユーザーが時機の指示として「即時対応」や同義の表現を使った時と、即時対応の対象・担当・基点・時機を決める前 | `references/immediate-fix.md` |

## 読込表

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 最初の語がsingle-laneと完全一致する時 | `references/single-lane.md` |
| 最初の語がall-reposと完全一致する時 | `references/all-repos.md` |
| 上記の語に一致しない時、または引数なしの時 | `references/lanes.md` |
