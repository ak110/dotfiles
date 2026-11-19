# 確認判断の固定課題による前後比較

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 固定課題の比較を準備する前 | `agent-toolkit:refine-prompt/references/multi-turn-trials.md`、`agent-toolkit:refine-prompt/references/multi-turn-trials-comparisons.md` |

## 評価の手順

対象は、確認要否を定める次の条文を変える変更である。

- `agent-toolkit/skills/atk-mode/playbooks/confirmation.md`「確認要否の判定」
- `agent-toolkit/skills/atk-mode/references/omission-check.md`「点検類型」の(j)
- `agent-toolkit/skills/atk-mode/references/choice-construction.md`「根拠の確定」

これらの変更では、確認を増やす向きと減らす向きの双方で、固定課題の変更前後の結果を比べてから採用する。
必要な確認と技術判断の両方を保ち、過去の是正の反転を検出するためである。
モデルの費用がかかるため手動で実行し、CIや定期工程には組み込まない。

1. 使用するworktree版の`atk info`でplugin rootを確かめ、`atk run-script multi-turn-trials -- --list`で課題を取得する。
   `protection-removal-agent`、`protection-removal-user`と`purpose-boundary`を使う。
   前2課題は保護の由来による確認判断、後者は目的の確認後の`technical-autonomy`などを比較する。
2. `agent-toolkit:refine-prompt`の固定課題の手順で、変更前後の文書、同じ公開素材・初期状態・モデル・effort・開示条件・終了条件・採点基準をそろえ、新規文脈で試す。
   公開素材と対象文書の複製だけを評価対象へ渡し、非公開要件、以前の返却と採点結果は仲介担当に保つ。
3. 機能の要件と対話中の判断を別に採点し、質問の要否と時機は全対話と引用から判定する。
   課題ごとの`private/comparisons.jsonl`へ全試行の条件・文書の版・実際のモデルと設定・結果・判断根拠・取得できた指標を記録する。
   合成保存例は実モデルの比較記録と区別する。
4. 反転や悪化があれば理由を確かめ、必要な本文を是正して再比較し、採否を決める。
   対話全文・提出物・採点出力はmanaged-tempに保持し、比較要約から受領時に再取得できるようにする。

結果は試した固定入力での観測として扱い、課題に含まれない話題や、条文変更を伴わない資料の未読・適用誤りまで検出したとは扱わない。
