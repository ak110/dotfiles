# 追従台帳：Markdownと文書

## Markdownを1文ごとに改行する

- 項目ID: markdown-one-sentence-per-line
- 実施日: 2026年11月
- 内容: 既存Markdownを1文ごとに改行し、複数文を1行に書いた箇所を検査する
- 理由: 文単位で差分を読み、行数から文書の大きさを把握できるようにする
- 当てはまる条件: 日本語のMarkdown文書を持つ
- 適用方法: 適用方法は公開版では未整備であり、現在はdotfilesの`scripts/check_markdown_structure.py`と`pyproject.toml`のカスタムコマンド登録を参照し、既存文書の内容を保って改行する
- 参照先: `github.com/ak110/dotfiles`の`scripts/check_markdown_structure.py`と`pyproject.toml`
- 適用済みの見分け方: 地の文と箇条書きが1文1行で、表・コード・逐語引用を除く複数文の行をCIの検査が拒否する

## 全角丸括弧の入れ子を解消する

- 項目ID: markdown-no-nested-fullwidth-parentheses
- 実施日: 2026年11月
- 内容: 全角丸括弧の入れ子を直し、行をまたぐ入れ子も検査する
- 理由: 補足の範囲を読み取りやすくし、同じ表記の崩れを防ぐ
- 当てはまる条件: 日本語のMarkdown文書を持つ
- 適用方法: 適用方法は公開版では未整備であり、現在はdotfilesの`scripts/check_markdown_structure.py`と`pyproject.toml`のカスタムコマンド登録を参照し、名称を保って外側の括弧を読点や別文へ直す
- 参照先: `github.com/ak110/dotfiles`の`scripts/check_markdown_structure.py`と`pyproject.toml`
- 適用済みの見分け方: コードや引用の外に全角丸括弧の入れ子が無く、表・見出し・行をまたぐ入れ子を検査が報告する

## 自然な日本語を保つtextlint設定へ改める

- 項目ID: textlint-natural-japanese
- 実施日: 2026年10月
- 内容: 読点の上限と冗長表現の辞書を見直す
- 理由: 自然な列挙や「操作を実行する」を、検査のために不自然な文へ書き換える往復を減らす
- 当てはまる条件: textlintの設定で`preset-ja-technical-writing`を使う
- 適用方法: 推奨ガイドの`.textlintrc.yaml`節に沿って該当する閾値と辞書だけを変更し、読点の境界と通常の文を検査する
- 参照先: `github.com/ak110/pyfltr`の`docs/guide/recommended.md`「.textlintrc.yaml」と、`github.com/ak110/lc`の`.textlintrc.yaml`
- 適用済みの見分け方: `max-ten.max`が`5`、`ja-no-redundant-expression.dictOptions.dict6.disabled`が`true`で、他の有用な検査は維持されている

## VitePressのビルド後に全リンクを検査する

- 項目ID: vitepress-built-site-link-check
- 実施日: 2026年10月
- 内容: 生成HTMLのページ・見出し・外部リンクを公開前に検査し、見出しIDをNFCへそろえる
- 理由: frontmatterとナビゲーションのリンクや、日本語見出しの正規化の差をソースの検査だけでは検出できない
- 当てはまる条件: VitePressで文書サイトを生成する
- 適用方法: ビルド後・公開前にlycheeの見出し検査を置き、公開baseと検査rootを対応付け、slugifyの結果をNFCへ正規化し、壊したページと見出しを拒否することを確かめる
- 参照先: `github.com/ak110/lc`の`.github/workflows/docs.yaml`と、`github.com/ak110/gv`の`docs/.vitepress/config.ts`
- 適用済みの見分け方: 全HTMLを`--include-fragments`付きで検査し、失敗時は公開されず、濁点を含む見出しのIDとリンクが一致する

## Markdownの内部アンカーを検査する

- 項目ID: markdown-internal-anchor-check
- 実施日: 2026年10月
- 内容: 同じファイル内と別Markdownへの見出し付きリンクが実在する見出しへ解決することを検査する
- 理由: 見出しの記号・英大小文字・重複によるIDの違いで、索引から節へ移動できないことを防ぐ
- 当てはまる条件: リポジトリ内のMarkdownへ見出し付きリンクを書く
- 適用方法: 対象の描画先と同じ見出しIDの生成規則でリンクを照合し、コード内を除外し、不一致のファイル・行・リンク先を検査で報告する
- 参照先: `github.com/ak110/dotfiles`の`markdown_link_anchor_invariant_test.py`
- 適用済みの見分け方: リポジトリ内の全Markdownリンクを検査し、見出しの削除・改名・記号を含む誤ったアンカーを検出する

## 文書へ写した実装の一覧を検査する

- 項目ID: documentation-implementation-list-consistency
- 実施日: 2026年10月
- 内容: 設定キー・選択肢・ツールなど、実装が要素を決める文書の一覧を実装とそろえ、整合テストを加える
- 理由: 機能追加に一覧が追随せず、利用できる機能や既定値を誤って伝えることを防ぐ
- 当てはまる条件: エンドユーザー向け文書に、実装が決める集合を閉じた一覧として手書きしている
- 適用方法: 文書の一覧を列挙し、定義元から取得した集合と比較するテストを置き、新しい一覧の追加手順にも同じ検証を含める
- 参照先: `github.com/ak110/pyfltr`の`tests/integration/documentation_test.py`と`.claude/skills/ssot/SKILL.md`
- 適用済みの見分け方: 一覧ごとに実装の定義と比較するテストがあり、要素を文書か実装の片方だけで変えると失敗する

## 文書を読み手と責務ごとに再編する

- 項目ID: documentation-responsibility-structure
- 実施日: 2026年10月
- 内容: エンドユーザー向け・開発者向け・エージェント向けの文書を、目的と問いごとの構成へ整理する
- 理由: 同じ説明の重複、内部仕様の混在、実装と合わない説明や参照を減らす
- 当てはまる条件: 読み手の違う情報が混在する文書や、同じ説明を繰り返す文書がある
- 適用方法: 各文書の読み手と主要操作を定め、操作案内・保守理由・作業指示の配置を分け、重複を参照へ変え、移動先・索引・アンカー・更新手順をそろえる
- 参照先: `github.com/ak110/pytilpack`の`docs/guide/index.md`・`docs/development/architecture.md`・`AGENTS.md`と、`github.com/ak110/dotfiles`の`docs/guide/`
- 適用済みの見分け方: 見出しが読み手の問いへ対応し、主要操作はガイド内で分かり、内部手順と保守理由は対応する資料へ分かれ、参照が解決する
