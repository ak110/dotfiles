---
mode: delegate
model_type: execute
required_inputs:
- 対象の不良
- 引き継ぎ記録先
optional_inputs: []
writes_worktree: false
---

# 既存不良の調査担当タスク

<!-- parent-read-source: agent-toolkit:/skills/bugfix/references/investigation-delegation.md#既存不良の調査担当の起動と受領 -->

受領した既存不良の原因分析、類似見直しおよび対策・横展開処置・再発防止策の確定までを行い、確定した内容を返す。
修正の実施、対象リポジトリとキューへの書込み、AWIの投入と、委譲元の元の作業の続行は担当せず、いずれも委譲元が担う。
最初に`agent-toolkit:bugfix`を起動する。

完了報告と成果物は日本語で書く。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/bugfix/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/managed-temp/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/managed-temp/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/bugfix/references/root-cause-analysis.md#「QとC・Dの区別」「類似見直し」「処置の階層」「再発防止策の必須性」` | 判断 | `section:agent-toolkit:*/skills/bugfix/references/root-cause-analysis.md#「QとC・Dの区別」「類似見直し」「処置の階層」「再発防止策の必須性」` |

## 入力

- `対象の不良`: 事象、期待する契約、実際の結果、症状を観測した所在を含む発生条件、ログと資料の絶対パス、対象commit、再現手順
- `引き継ぎ記録先`: 到達点を記録するファイルの絶対パスと新規・継続の別。記録する内容は`${CLAUDE_PLUGIN_ROOT}/share/rules-subagent.md`「多段工程の引き継ぎ記録」が定める

## 調査

`agent-toolkit:bugfix`に従い、初動から対策・横展開処置・再発防止策の確定まで行う。対象リポジトリとキューは読み取りだけで扱い、一時ファイルはmanaged-temp（`agent-toolkit:managed-temp`）へ置く。対策は委譲元がそのまま修正に使える粒度（変更する対象、内容、修正後に確かめる観測）で確定する。

認識の違いで要件または結果が変わる未確定事項は、回答を待たずに進められる調査を済ませてから続行できない理由として返す。

## 出力

調査を終えてから、次の形式だけを返す。

```text
状態: completed
拡張原因分析: 要 | 不要
直接的原因: <観測で確定した直接的原因と根拠>
根本原因: <確定した根本原因（破られた不変条件か判断規則）と根拠>
類似見直し: <QとC・Dの区別と重大度の結果・根拠、探索範囲・限界・許容する残存リスク、根本原因から導いた母集団、各対象の該当・非該当と根拠、探索方法、履歴を遡った場合の期間と見直し対象外とした範囲、独立走査を適用した場合の範囲・観点、担当識別子、起点問題の摘出の有無、未摘出の原因と見直し経過>
対策: <変更する対象、変更の内容および修正後に確かめる観測>
横展開処置: <類似見直しで該当とした各対象への処置>
再発防止策: <処置と、その処置が作用する根本原因の単位>
```

各欄の値は`agent-toolkit:bugfix`の`references/root-cause-analysis.md`の「QとC・Dの区別」「類似見直し」「処置の階層」「再発防止策の必須性」で判定し、`拡張原因分析`の要否によらず全ての欄を同じ基準で書く。
類似見直しが0件の場合と、採用する再発防止策なしとした場合は、同書が求める記録をその欄へ書く。

続行不能時は`agent-toolkit/share/rules-subagent.md`「返却形式の受け渡し」の形式で返す。
想定外事象の追加行は`agent-toolkit/share/rules-subagent.md`に従う。
