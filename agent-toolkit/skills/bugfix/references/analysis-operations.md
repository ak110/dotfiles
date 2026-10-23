# 原因分析の運用接続

品質の判定は`agent-basics:bugfix`を適用する。以下はセッション・WI・委譲・ホストへの接続である。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「QとC・Dの区別」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「QとC・Dの区別」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../user-confirmation-and-report/SKILL.md#「確認要否の判定」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「確認要否の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../session-review/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/session-review/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/references/runtime-routing.md#「代替時の組合せの目安」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/runtime-routing.md#「代替時の組合せの目安」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-analysis.md#「再発防止策の必須性」` | 判断 | `section:agent-basics:*/skills/bugfix/references/root-cause-analysis.md#「再発防止策の必須性」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/agent-documents-basics.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-basics.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/references/judgment-details.md#「検収手段の到達可能性」` | 判断 | `section:agent-basics:*/skills/review-standards/references/judgment-details.md#「検収手段の到達可能性」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/design-heuristics.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-heuristics.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/references/runtime-routing.md#「工程別モデル設定」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/runtime-routing.md#「工程別モデル設定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/references/routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/routing.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../delegation/references/runtime-routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/runtime-routing.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../writing-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/claude-hooks-block-warn.md#「遮断・警告フックの成立条件」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/claude-hooks-block-warn.md#「遮断・警告フックの成立条件」` |

## ユーザー介入と確認の判定

ユーザー介入は従来の判断を是正した事象であり、評価を必須とする。探索の深さと再発防止策の要否は`agent-basics:bugfix/references/root-cause-analysis.md`「QとC・Dの区別」で判定する。ユーザー介入とWIの記入欄の是正（`wi-user-response`）を除外できるのは次の4区分だけで、除外区分と根拠を分析結果へ記録する。

- `single-inquiry`（単発の照会）: 進捗・所要時間・状態を尋ねるだけで、是正・停止・方針変更・将来の振る舞い変更を含まない発話。根拠はその発話の本文
- `non-changing-request`（判断を変えない追加要求）: 従来の判断・工程・成果物・要求分類・完了判定のいずれも変えない要求。根拠は変えていないことを示す本文の箇所
  ローカルな終了操作に関係する訂正・続行もこの区分へ含め、問題・再発防止要求への変換と除外理由の報告を省く。終了操作の記録だけを読み直す場合も同じ扱いとする。語の一致だけによる除外は行わず、別の要求や方針変更はその趣旨で判定する
- `unexplained-refusal`（理由の無い拒否・中断）: ツール実行の拒否・中断の定型本文で、直後のユーザー発話が理由や是正を述べていないか、直後の発話が無いもの。根拠は直後の発話の本文またはその不在。直後の発話が是正なら、拒否・中断をその発話と同じ問題としてまとめる
- `credential-report`（資格情報の所在の報告）: 所在・持ち主・種類を問わず、見つけた資格情報と、その報告に関するユーザー発話を対象とする。`agent-toolkit/rules/01-agent.md`「完遂と先送り」の報告だけの扱いで終えたものがこの区分に当たる。根拠は発見の記録位置と所在ごとの報告UWIまたは同じ所在の既存報告で、値の再掲を省く。過剰反応自体の是正を求めた発話はこの区分から外し、その要求を処理する

ユーザー確認（`confirmation-request`）を除外できるのは`necessary-confirmation`（必要なユーザー確認）だけとする。答えをユーザーだけが持つ値（選好、認可、外部の状態、手元に無い資料など）で決め、`agent-toolkit:user-confirmation-and-report`「確認要否の判定」で確認を要すると判定される確認であり、根拠はその値とする。

これ以外のユーザー介入・WIの記入欄の是正・ユーザー確認は欠陥として拡張原因分析の対象にする。「ユーザーの選好」「追加要件」「既存規範で扱える」を根拠とする介入も、同じく対象に含める。これらは介入が生じた理由の説明であり、次のセッションで同じ是正を防ぐ根拠にならない。
実行環境やhookが生成した本文を人間の入力と判定した事象は、介入の除外で処理せず生成本文の判定の誤りとして分析する。
照会に是正・停止・方針変更・将来の振る舞い変更が併記されていれば、その部分を是正として扱う。趣旨を一意に確定できない候補は除外せず、残る解釈候補をユーザーへ確認する。回答を得られずUWIへ送られた場合はそのUWIのファイル名を再発防止策の所在として報告し、解釈候補を前提とする対策は回答後に確定する。

次の事象は前例の有無によらず拡張原因分析の条件に該当するものとして扱う。過去の同種記録は該当の判定に使わず、前回の処置で再発を防げなかった条件を調べる材料に使う。

- 従来の判断・工程・成果物・要求分類・完了判定を変更・撤回・是正したユーザー介入（誤り・選好・追加要件のどれに分類したかを問わず、UWIの`## 回答`とAWIの`## ユーザーコメント`での是正を含む）
- 不適切なユーザー確認: `agent-toolkit/skills/user-confirmation-and-report/SKILL.md`「確認要否の判定」と「判定の細則」に反する不要な確認（`AskUserQuestion`、地の文の問い、UWIを含む）
- 必要なユーザー確認の欠落: 同「確認要否の判定」の判定順3に当たるユーザーだけの値を推定で確定し、誤った成果物、要求された作業の未実施または是正を生んだ事象。技術とQCDで確定する判断の確認省略は、この分類の対象外とする
- ユーザーの手を煩わせた事象: 本来不要な操作・回答・再依頼・是正をユーザーがした事象（必要なユーザー確認への回答を除く）
- 品質に悪影響があった事象: 成果物の欠陥、手戻り、同じ指摘の反復など
- 所要時間目標を超過した事象: `agent-toolkit:session-review`の`SKILL.md`の振り返り工程の目標と、`reference_document`が定める目標の超過
- 同じ成果物かつ同じ責任範囲で発生したレビュー第3ラウンド

## 規範の未適用と目的逸脱

規範の未適用と、規範を適用した結果の目的逸脱は原因を分けて扱う。
事象より前の文脈で規範の判定が`present`だった未適用では、記録から観測した実モデルの段位（上位・中位・軽量）を`agent-toolkit:delegation`の`references/runtime-routing.md`「代替時の組合せの目安」へ当てはめる。上位の段か、それと同等以上と確認できるモデルではモデル性能を原因にせず、条文の除去・緩和・集約・単純化・一般化を追記より先に比べる。中位と軽量では、明瞭な条文と権限・手段・入力が揃った失敗だけ、工程のモデル変更と改善待ちを候補にする。段位を当てはめるのは記録から判定できたモデルに限り、未知・欠落したモデルの工程は推測で補わない。

既存規範が実行時に適用されなかった問題では、次の順に原因を特定する。

1. 条文の特徴的な1行を`atk run-script session-review-evidence --`の`--context-at <事象の記録位置> --phrase=<文字列>`で照会し、判定と、条文が文脈へ入った方法を示す`channel`をAWIの原因へ書く。phraseは事象時点の文面から選ぶ（その後に改訂されていれば`git log -S`で得る）
2. 判定を区分へ対応させる。`absent`は起動契機か参照時点（条文を持つ資料が読まれなかった）、`dropped-by-compaction`は参照時点（圧縮で外れ、再注入の対象外か切り詰めで欠けた）、`present`は判定条件・責務の所在・想起性とする。一致の`channel`が`user`と`assistant`だけなら、会話中の言及として扱う
3. `agent-basics:bugfix/references/root-cause-analysis.md`「再発防止策の必須性」の未適用規範の是正原則で原因を特定し、条文の想起性改善案と、改善後の条文が誤りの機構を想起させる見込みの根拠をAWIへ書く。見込みは`agent-basics:writing-standards/references/agent-documents-basics.md`が挙げる読み手の特性で判定する

目的逸脱には、規範による停止・差し戻し・空転・反復、条文の矛盾や前提の不在、要求を満たさない成果物などがあり、成功した工程の読込・照会・準備・確認の量も目的の操作と比べる。
規範どおりの工程は適用のたびに生じるため、1セッションで1回でも反復する費用として扱い、呼出回数・読込量・所要と適用頻度から、必読範囲の縮小・集約、準備を呼ばれる側へ移す案、探索を不要にする案を比べる。
条文の改訂は守る価値・導入目的・期待する成果へ対応付け、維持する場合は同じ停止が再現する条件と許容理由を記録する。
判断の到達可能性は`agent-toolkit:review-standards`の`references/judgment-details.md`「検収手段の到達可能性」で確かめる。一般の案の比較は`agent-basics:writing-standards/references/design-heuristics.md`による。

## 類似見直しの分担

- 母集団の各対象を該当・非該当へ分ける走査は、`execute`担当が根本原因から母集団と判定観点を確定した後、判定観点と判定基準を`prompt`へ書いて`chores_fast`の`explore`へ委ねてよい。工程の分担は`agent-toolkit:delegation`の`references/runtime-routing.md`「工程別モデル設定」が定める。`chores_fast`が非該当と返した対象は`execute`担当で全件読み直さず、`execute`担当は該当と判定保留の対象と、判定観点を言語化して渡せなかった対象を判定する。現に動いている成果物には致命的な問題が少ないとみなすためである。重大な問題でも、grepなどの機械的抽出とこの分担で効率化し、`execute`担当による全量走査を前提にしない
- 委譲は`agent-toolkit:delegation`の`references/routing.md`と`references/runtime-routing.md`で選び、会話のfork、問題を知る担当の継続、自分自身の走査で代えない。読取専用担当が実行できないコマンドの素材は、取得済みファイルを渡すか実行できる起動形を使う。分担した場合は返却の和で摘出を判定し、走査担当はこの手順を自身の走査へ重ねない

## 潜在欠陥の受渡し

観測事象への因果的寄与を観測で確定していない潜在的な欠陥を同じ計画へ含めるのは、寄与を観測で確認した場合かユーザーが併記に明示合意した場合に限り、その根拠を計画へ記録する。同じ計画へ含めない是正対象は、独立した通常AWIへ投入するか委譲元へ返し、受渡し先と処置の状態を記録する。受渡し先が無い除外は類似見直しの未完了として扱う。分けるのは記録の単位だけで、処置の完了は元の類似見直しの完了条件に残す。

## 自動検出のホスト契約

- hookの採否と遮断・警告の別は`agent-basics:writing-standards/references/claude-hooks-block-warn.md`「遮断・警告フックの成立条件」の費用比較で決める。この比較は手段の選択に使い、重大な問題で遮断・警告の案が成立しなくても`agent-basics:bugfix/references/root-cause-analysis.md`「QとC・Dの区別」に従って根本原因へ作用する別の手段を探す。不可逆性を遮断の根拠にする場合は、観測した事象の事実（パッケージ削除なら`remove`か`purge`かなど）で判定する
