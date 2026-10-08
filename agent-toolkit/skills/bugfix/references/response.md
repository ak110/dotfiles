# 問題を見つけたときの対処

対処の候補を選ぶ前に`agent-toolkit/rules/01-agent.md`「判断指針」の3段判定を適用し、許容されない対象、操作、副作用を候補から除く。
対処案の構造比較には`agent-toolkit:writing-standards`の`references/design-heuristics.md`「設計案の予備選別」を適用する。

作業中に発見した問題の区別、原因分析と処置は、`agent-toolkit:bugfix`の`SKILL.md`「初動と拡張原因分析の判定」とその参照先に従う。

- 既存不良も原則として同じセッションで類似見直し・再発防止策まで完遂する。先送りと委譲先が見つけた担当外不良は`agent-toolkit/rules/01-agent.md`「完遂と先送り」、実行中のスキルに固有の扱いはその手順（`agent-toolkit:process-wi`など）に従う。拡張原因分析・類似見直し・再発防止策のいずれかを要する調査は`${CLAUDE_PLUGIN_ROOT}/share/defect-investigation.parent.md`へ委譲し、修正は不良を見つけたエージェントが返却された対策に従って行う。拡張原因分析が不要で1箇所で完結する軽微な不良はその場で直す
- 続行を妨げない不自然さ・非効率・反復（使い勝手を含む）は即時是正から外す。メインは`agent-toolkit/share/rules-main.md`「作業中の改善点の報告」、委譲先は`agent-toolkit/share/rules-subagent.md`「返却と終端」の`気付いた改善点:`で扱う
- 完成条件外のエンドユーザー向けの不足は、`agent-toolkit:user-confirmation-and-report`の`references/judgment.md`「認可を要する操作」で判定する。文書を機能の制約へ合わせた場合も欠如した機能を判定し、新しい挙動の追加は既存不良として自ら是正・先送りせず、同書「確認を要する事項」へ送る
- 終了コードが0でも出力に警告が含まれる場合は、警告の意味を確かめる。警告は処理の一部が未適用または未完了であることを示す場合があり、成否は終了コードと、未適用・未完了を示す警告の不在の両方で判定する。未適用・未完了を示す警告には対処し、処理と無関係と判断した警告（依存の非推奨通知など）はその根拠を報告へ残す
- ユーザーが示した要件を変える変更と、`agent-toolkit:user-confirmation-and-report`の`references/judgment.md`「認可を要する操作」に当たる対処はユーザー確認し、合意を得てから着手する。要件を満たす手段の選択は`agent-toolkit:user-confirmation-and-report`「確認要否の判定」で決める。内部実装に閉じる対処は自律的に完遂する。調査だけの依頼の扱いは`agent-toolkit/rules/01-agent.md`「完遂と先送り」に従う
- フック・ツール・実行環境の通知で、担当外の対象、実行できない処置、実在しない前提を観測した場合は、通知機構の欠陥として扱う。通知への応答・終端と機構の欠陥への処置を分け、両方行う。「対象外なら何もせず終える」も応答だけを定める。欠陥への処置は同一セッション内の是正、`agent-toolkit:wi-standards`に従うAWI投入の提案、同じ欠陥を扱う未終端WIの参照のいずれかとする
