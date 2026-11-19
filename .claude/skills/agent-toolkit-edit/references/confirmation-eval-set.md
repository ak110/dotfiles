# 確認判断の固定課題による前後比較

確認要否を定める条文を変える変更を、固定課題で変更の前後に比べてから採る手順書である。
確認を増やす向きと減らす向きの両方で比べ、必要な確認と技術判断の両方を保ち、過去の是正の反転を検出する。
モデルの費用がかかるため、CIと定期の工程から外して手動で実行する。

## 評価の手順

対象は、次の条文を変える変更である。

- `agent-toolkit/skills/atk-mode/playbooks/confirmation.md`「確認要否の判定」
- `agent-toolkit/skills/atk-mode/references/omission-check.md`「点検類型」の(j)
- `agent-toolkit/skills/atk-mode/references/choice-construction.md`「根拠の確定」

1. 使うworktreeの版の`atk info`でplugin rootを確かめ、`atk run-script multi-turn-trials -- --list`で課題を取る。
   `protection-removal-agent`、`protection-removal-user`、`purpose-boundary`を使う。
   前の2課題は保護の由来による確認の判断を、後の1課題は目的の確認の後の`technical-autonomy`などを比べる。
2. 固定課題の手順は`agent-toolkit:refine-prompt/references/multi-turn-trials.md`と`agent-toolkit:refine-prompt/references/multi-turn-trials-comparisons.md`にある。
   変更前後の文書と、同じ公開素材、初期状態、モデル、effort、開示条件、終了条件、採点基準をそろえ、新しい文脈で試す。
   公開素材と対象文書の複製だけを評価の対象へ渡し、非公開の要件、以前の返却と採点結果は仲介担当が保つ。
3. 機能の要件と対話中の判断を別に採点し、質問の要否と時機は全対話と引用から判定する。
   課題ごとの`private/comparisons.jsonl`へ、全試行の条件、文書の版、実際のモデルと設定、結果、判断根拠、取れた指標を記録する。
   合成の保存例は、実モデルの比較記録と区別する。
4. 反転や悪化があれば理由を確かめ、要る本文を直して比べ直し、採否を決める。
   対話の全文、提出物、採点の出力はmanaged-tempに保ち、比較の要約から受領時に取り直せるようにする。

結果は試した固定入力での観測として扱い、課題に無い話題と、条文を変えない資料の未読・適用の誤りまで検出したとは扱わない。

## 返す報告に含める項目

課題ごとの変更前後の判断と採点、反転と悪化とその理由、採否、`private/comparisons.jsonl`の記録先を返す。
