# 固定課題による多ターン試行：素材と準備

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 素材と準備

**1.** 比較する課題と開示条件を選ぶため、`atk run-script multi-turn-trials -- --list`で課題名、種別`kind`と条件を取得する。
固定素材は`${CLAUDE_PLUGIN_ROOT}/skills/refine-prompt/fixtures/multi-turn/`に配置されている。

| 課題 | 開示条件 | 成果物で確かめる結果 | 原型の節 |
| --- | --- | --- | --- |
| `attachment-paths` | `questions-only` | 添付の参照・関連付け・削除のパスと従来API | C.2.1 A1 |
| `weekday-queries` | `questions-only` | Sunday=1の維持と別の公開ISO問い合わせ | C.3.1 F1 |
| `batch-conditions` | `questions-only` | 異なる条件の複数要素、順序、空入力と単体API | C.3.2 F2 |
| `display-spaces` | `no-followup`、`observations-only` | 空白を含む表示の回復と正常表示の維持 | C.1 |
| `verification-status` | `no-dialogue` | 未実行と実行済みの期待失敗の分類 | 合成素材 |
| `skill-selection` | `no-dialogue` | 依頼と説明による初期選択と、本文を読んだ後の追加選択 | 合成素材 |
| `test-selection` | `no-dialogue` | 隠れた依存を含む変更から実行すべきテストの選択 | 合成素材 |
| `goal-discovery` | `no-dialogue` | 目標から注文集計CLIを完成させ、既存APIを保つ | 独自素材 |
| `strategy-update` | `no-dialogue` | 単位の異なる明細と担当間の矛盾を整合させ、既存結果を保つ | 独自素材 |
| `purpose-boundary` | `questions-only` | 目的の確認後に、確定した改善領域を要求へ反映する | 独自素材 |

上表の`attachment-paths`、`weekday-queries`、`batch-conditions`、`display-spaces`の原型は、[SWE-Journey v1 Appendix C](https://arxiv.org/html/2610.11559v1)で報告された失敗パターンを、扱いやすい小規模な課題として具体化したものである。
`private/trial.json`には、出典情報、非公開の要件、開示条件、対話の評価基準、終了条件、および参考となる成功例・失敗例が記録されている。
なお、表示課題に含まれる第三者テスターの存在や回答制約は原型論文には含まれておらず、本試行の要件に合わせて独自に追加した条件である。
`kind`が`dialogue`の課題では、以下のユーザー役との仲介手順と対話JSONを使う。
`kind`が`single`の課題では、評価対象だけを起動して提出までの終了を観測し、`multi-turn-trials-grading.md`「単独作業の採点」の実施情報を保存する。
単独課題`verification-status`は、未実行と実行済みの期待失敗の分類を扱う保存形式の検証用合成素材であり、実モデルの比較実績とは別の素材である。
単独課題`skill-selection`と`test-selection`の保存例も採点契約を確かめる合成素材であり、実モデルの改善実績や選択精度の記録とは別の素材である。
両課題で採点するのは提出した識別子による選択判断であり、本文を実際に読んだことや依存を探索したことの証拠からは除く。
実モデルで比較する場合は、session記録から開いた本文、実行した検索と読取の順序を確かめ、取得できた入力・出力トークン、所要時間、費用を併記し、取得できない値は「未取得」とする。
`test-selection`の手動比較では、最初に与えた情報だけで選ぶ方式と、検索・読取の結果を次の判断へ反映する方式を区別して記録し、結果を実行するテストの縮小へ反映しない。

**2.** 試行間の素材を分離するため、managed-temp直下に版・課題ごとの作業ディレクトリを用意し、次のコマンドで公開素材を準備する。
コマンド例における`<公開素材の新規絶対パス>`には、まだ存在しない新規パスを指定する。

```sh
atk run-script multi-turn-trials -- prepare --case attachment-paths --output <公開素材の新規絶対パス>
```

準備コマンドを実行すると、課題の`public/`配下の公開素材（`request.md`、`task.py`と課題の素材ファイル）だけが指定先に複製される。
評価対象エージェントの読み書き権限はこの公開素材と評価対象文書の複製のみに限定し、課題専用のコードのみを変更させる。
変更の適用対象は課題専用コードに限定する。
`private/`ディレクトリ、採点用テスト、正解データ、ユーザー役の入力内容およびその配置場所は、仲介担当が評価対象から隠蔽して管理する。
同一OS上のディレクトリ分離はセキュリティ上の完全なサンドボックスではないため、意図しないアクセスが行われていないかを仲介担当が確認する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 固定課題による多ターン試行：保存対話と採点の基準を適用する前 | `multi-turn-trials-grading.md` | 判断 | `read:agent-toolkit:*/skills/refine-prompt/references/multi-turn-trials-grading.md` |
