# session-review-advisorの起動と受領

session-reviewのメインは、証拠収集を開始する工程で本書を全文読み、`session-review-advisor`の起動、出力の検収及び構造検収へ適用する。本書はメインが所有する採否・原因確定の手順を定義しない。

## 起動

`session-review-advisor`の起動前に`agent-toolkit:delegation`をSkill機能で起動する。
起動時に必ず読み取り専用の`session-review-advisor`を1つ起動し、transcriptの絶対パスを渡す。
advisorはtranscript内で観測した問題と証拠位置だけを問題一覧として返す。
問題の原因、対策及び改善提案の要否はメインが確定する。

- Stopフックのreasonまたは手動コマンドの`additionalContext`から受け取った`transcript_path`の絶対パス

問題一覧と証拠位置の出力契約は`session-review-advisor`定義の出力を正本とし、起動文へ複製しない。

## ユーザー入力イベントの構造検収

advisor報告の`status`が`evidence_insufficient`の場合は、既存の証拠不足報告経路を維持し、証拠の再抽出、構造検収及び「提案無し」の確定へ進まない。
`status`が`completed`の場合だけ、受領済み`transcript_path`を既存の証拠抽出器へ`--index`付きで一度だけ渡し、読み取り専用で証拠索引を照会する。
メインは索引照会結果を`checked_user_events`の構造検収だけに用いる。
問題一覧が参照するdistinctな完全`query`文字列は、同じ引数列と順序で各1回だけ再実行する。
異なる`--detail`引数列を1回の照会へまとめない。
`query=default`を持つ問題は判断材料に用いない。
各`locator`が`event_index`だけを持ち、advisorが実行したものと同じ完全引数列の照会結果内に対象イベントが存在することを確認する。
locatorの形式が異なる、又は対象イベントが存在しない証拠を持つ問題は判断材料に用いない。
Claude Codeでは次のコマンドを使う。

```sh
uv run --no-project --script ${CLAUDE_PLUGIN_ROOT}/scripts/_session_review_evidence.py <transcript_path> --index
```

Codexでは、起動方針で確定した現行plugin rootの絶対パスへ次の`<plugin root>`を置き換える。

```sh
uv run --no-project --script "<plugin root>/scripts/_session_review_evidence.py" <transcript_path> --index
```

両ホストとも既存の抽出器とJSONL出力を再利用する。抽出器は手動起動と自動起動の全候補（Claudeの開始markerと
Codexのstopに結び付く開始markerを含む）から、最新の適用可能な起動境界を共通に選ぶ。`finalize`・`stats`・`warning`は同じ選択結果を使い、
境界後を保持するか除外するかは各利用経路の既存契約に従う。
メインは索引照会結果の全`kind=user`イベントから`(sequence, line)`列を作成し、advisorの`checked_user_events`の値・件数・順序が一致することを機械的に確認する。
advisorが初回報告又は外部訂正後の返却で`evidence_insufficient`を返した場合、メインは既存の証拠不足報告経路へ進み、追加の訂正要求、利用者介入の分類、原因・処置の確定及び「提案無し」の確定へ進まない。

初回照合で値・件数・順序が一致しない場合、メインは索引照会結果にだけ存在する不足`(sequence, line)`を索引順で、`checked_user_events`にだけ存在する余剰`(sequence, line)`を報告順で列挙する。メインは不足ID列と余剰ID列だけを同一advisor sessionへ返し、advisor定義の継続入力契約に従って初回報告を含む累積出力全体の訂正を1回だけ求める。利用者入力本文、問題分類又は対策は返さない。

メインは訂正済み`completed`の累積出力へ、この節が定める問題一覧の証拠位置検収と`checked_user_events`の値・件数・順序の照合を再実行する。訂正後も値・件数・順序が一致しない場合は`evidence_insufficient`として既存の証拠不足報告経路へ進み、利用者介入の分類、原因・処置の確定及び「提案無し」の確定へ進まない。

集合差だけでなく順序不一致も初回不一致として扱う。不足・余剰がともに空でも順序が異なる場合は、空の2列を同一advisor sessionへ返し、累積出力の訂正を1回だけ求める。訂正後の再検収で再び順序が異なれば`evidence_insufficient`へ終端する。
