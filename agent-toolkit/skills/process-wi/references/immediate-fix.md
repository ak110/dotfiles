# 即時対応の手順と対象外の扱い

本書は`agent-toolkit:process-wi`のメインが、不良またはユーザーの是正要求に同スキルの`SKILL.md`「即時対応」の判定基準を当てはめた後の手順を定める。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md#「即時対応」` | 判断 | `section:agent-toolkit:*/skills/process-wi/SKILL.md#「即時対応」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec-review.parent.md#「返却に含まれる既存不良の受領」` | 判断 | `section:agent-toolkit:*/share/exec-review.parent.md#「返却に含まれる既存不良の受領」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/add-wi.parent.md` | 判断 | `read:agent-toolkit:*/share/add-wi.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../wi-standards/SKILL.md#冒頭` | 判断 | `section:agent-toolkit:*/skills/wi-standards/SKILL.md#冒頭` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../session-review/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/session-review/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `selection-procedure.md#「処理対象WIの追加」` | 判断 | `section:agent-toolkit:*/skills/process-wi/references/selection-procedure.md#「処理対象WIの追加」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `run-lanes.md#「処理中に確定した必須是正レーン」` | 判断 | `section:agent-toolkit:*/skills/process-wi/references/run-lanes.md#「処理中に確定した必須是正レーン」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/references/root-cause-analysis.md#「類似見直し」` | 判断 | `section:agent-toolkit:*/skills/bugfix/references/root-cause-analysis.md#「類似見直し」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec-review.parent.md#%E3%80%8C%60review_contract%60%E3%81%AE%E7%94%9F%E6%88%90%E3%80%8D` | 判断 | `section:agent-toolkit:*/share/exec-review.parent.md#%E3%80%8C%60review_contract%60%E3%81%AE%E7%94%9F%E6%88%90%E3%80%8D` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../wi-standards/SKILL.md#「由来と承認」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/SKILL.md#「由来と承認」` |

## 即時対応の手順

対象は公開工程（実行順7）の開始前に、メインが見つけた不良、委譲先から`想定外事象:`行で返された不良、処理中に届いたユーザーの是正要求である。実行レビューが返した差分外の既存不良は対象から除き、`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`「返却に含まれる既存不良の受領」で専念型のWI投入担当へ渡す。同節の例外とする残りの工程を停止・遮断する不良だけを本手順で扱う。判定基準に当たるものは、同じセッションで修正、類似見直し、再発防止策まで行う。元の作業と文脈を分けるのは調査だけとし、修正はレーンが行う（`agent-toolkit/rules/01-agent.md`「完遂と先送り」の、独立した既存不良をその場で是正する一般則に対する本スキルの例外）。

1. メインは`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`に従い、`agent-toolkit:wi-standards`冒頭のAWIの粒度で分けた要求ごとに、調査、AWIの起草と投入を委譲する。`agent-toolkit:session-review`が同じ事象へ投入したAWIがあれば重ねて投入せずそれを使う。
2. 投入または再利用したAWIを`selection-procedure.md`「処理対象WIの追加」で選定し、`run-lanes.md`「処理中に確定した必須是正レーン」の基準で割り当てる。分類と割当の意味判断を取得済み追加候補とともに`pick-wi-check`の生成付きmergeへ渡し、元の選定原本への確認・保存を1回で行う。処理開始状態と意味を検収した後、保持した同じ資源・担当threadへ渡す（合流先を未統合へ戻し、上限と初回履歴は保持する）。担当は現在工程を終えてから追加を扱い、初回と同じ計画・実行レビュー・直列統合・保存とWI終端へ進む。是正で見つかった不良も上限なく同じ手順で扱い、以降は修正後の規範に従う。
3. 委譲先は担当外の既存不良を自ら修正・投入せず、問題、原因、修正の対象と方向をメインへエスカレーションする。実行レビューの返却とレーン経由のその報告は、メインが`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`「返却に含まれる既存不良の受領」に従って対象ごとの処置へ対応付ける。メインは処置を確定した時点で自身の引き継ぎ記録へ対応を残し、実行レビュー収束後の工程へ進む前に対象全件の処置を確かめる。

類似見直しは`agent-toolkit:bugfix`の`references/root-cause-analysis.md`「類似見直し」の共通手順（根本原因から範囲と観点を定め、母集団の潜在不良の是正と予防の採否、処置の完了まで）で行う。即時対応へ加える対象と担当の認可は前掲の選定・割当で確定する。

## レーンの変更範囲へ加える修正の認可

レーン担当が自レーンの変更範囲の検証を妨げる既存不良を報告し、修正の対象（同じ原因の類似箇所を含む）と方向が報告と現物から技術的に定まる場合は、手順1〜2に代えて、その修正を同じレーンの変更範囲へ加える認可を同じthreadへ追送する。原因と対象が定まっていれば調査を分ける意味が無く、AWIを経ると投入担当とレーン担当が同じ検証を二重に行うためである。

- 認可には修正の対象、方向、出所（レーン担当の報告とメインの判定）を含める。この対象と方向が加える修正の書込の認可の境界であり、エージェント向け文書も別に名指しせず同じ境界で扱う。凍結済みの計画は更新しない
- 実行レビュー担当の起動時に、その認可を`review_contract`の条項として渡す（`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`「`review_contract`の生成」）
- 対象か方向が定まらない場合と、方向がユーザーの選好で決まる場合は手順1〜2に従う
- `agent-toolkit:bugfix`の初動判定で拡張原因分析を要すると判定した場合は、原因分析と再発防止策だけを手順1〜2で扱い、レーン内の修正はそのAWIの対象から外して修正のcommitと報告を投入担当へ渡す

## 対象外の不良と是正要求

即時対応の対象外とした不良と是正要求、および公開工程の開始後に見つけた公開を妨げない不良と新しい作業要求は、レーンへ割り当てず`add-wi.parent.md`の委譲で`source: process-wi`のAWIとして投入し、次のセッションへ回す。ユーザーの要求は`agent-toolkit:wi-standards`「由来と承認」に従って発話を逐語で転記する。
メインは即時対応の対象外とした既存不良についても、投入担当の結果を受領した時点で投入済みWIと対象を自身の引き継ぎ記録へ対応付け、報告元へその処置を返す。
公開工程の開始後でも、公開を妨げる不良（CI失敗など）は終端担当の手順で直し、公開を妨げるか同じ実行での実施をユーザーが明示した是正要求と作業要求はその実行の中で扱う。本セッションの作業で導入した不良への是正要求は、時機によらず`agent-toolkit/rules/01-agent.md`「完遂と先送り」に従って同じセッションで扱う。本セッションで扱った問題と根本原因を同じくする不良は対象外の不良に当たらず、`../SKILL.md`「即時対応」のとおり元の問題の類似見直しとして扱う。
