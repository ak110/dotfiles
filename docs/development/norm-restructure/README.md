# 規範の再構築の判定台帳

本ディレクトリは、エージェント向け規範と手順の全体を整理・再構築したとき（2026年10月）に、基準commitの全条文を1行ずつ判定した台帳と、守る障害と方針の逆引き表を置く。
規範から条文を削除、縮小、移設または統合した理由と移設先を、後続のセッションと実行レビューが確かめるための記録である。実行時に従う規範ではない。

## 基準commitと対象集合

基準commitは`9107e2a8cedd64e231890ac739f31048a97266c2`である。
対象集合は基準commitで次の9領域にあるGit追跡のMarkdown（`.md`と`.md.tmpl`）とする。

- `agent-toolkit/rules/`
- `agent-toolkit/share/`
- `agent-toolkit/skills/`
- `.claude/skills/`
- `AGENTS.md`
- `.chezmoi-source/dot_claude/rules/`
- `.chezmoi-source/dot_claude/skills/`
- `.chezmoi-source/dot_claude/docs/`
- `.chezmoi-source/dot_gemini/`

## 条文の切り出し方

条文は見出しを除くMarkdownのブロック単位とし、`scripts/norm_restructure_clauses.py`が次の規則で切り出す。

- ファイル先頭のfrontmatterは1件とする
- 段落は空行で区切られた連続行を1件とする
- 箇条は最上位の項目（行頭の`-`、`*`、`+`か番号）を1件とし、字下げした子の項目と続きの行、空行の後に字下げして続く行を含める
- 表は本体の1行を1件とし、見出し行と区切り行は数えない
- フェンス付きコードブロックは1件とし、内側の行は見出しや空行に見えても同じブロックに含める
- 見出しは条文に数えず、条文キーの見出しの階層に使う

条文キーは`<リポジトリ相対パス>#<見出しの階層>#<節内の連番>`とし、見出しの階層はH1からの見出しを` > `で連結する。見出しより前の条文は階層が空になる。

切り出しを再実行して件数を確かめるには、リポジトリのルートで次を実行する。

```sh
uv run --frozen python scripts/norm_restructure_clauses.py 9107e2a8cedd64e231890ac739f31048a97266c2 --count
```

出力の件数は、本ディレクトリの台帳TSVの見出し行を除く行数の合計と一致する。

```sh
for f in docs/development/norm-restructure/*.tsv; do [ "$f" = docs/development/norm-restructure/reverse-index.tsv ] || tail -n +2 "$f"; done | wc -l
```

## 台帳ファイル

条文を持つファイルごとにTSVを置く。ファイル名は条文のパスの`/`を`__`へ置き換え、先頭の`.`を`dot-`へ置き換えて`.tsv`を付けた名前とする（例: `agent-toolkit__rules__01-agent.md.tsv`、`dot-claude__skills__merge-pr__SKILL.md.tsv`）。1行目は列名の見出し行であり、セル内のタブと改行は空白へ置き換えてある。基準commitの後に撤去したファイル（`agent-toolkit/skills/realign-with-user/SKILL.md`など）の台帳も残し、各行の移設先を記録する。

列は次のとおりである。

| 列 | 内容 |
| --- | --- |
| 条文キー | 前節の条文キー |
| 先頭抜粋 | 条文の先頭40字 |
| 判定 | 維持、書換、移設、統合、経緯へ移動、削除のいずれか |
| 根拠 | 維持以外は`agent-toolkit/skills/writing-standards/references/agent-documents-additions.md`「文書記述量の管理」の8つの根拠（SSOT違反、自明導出、公式ドキュメント代替可能、上位指針への統合、手段固定、個別事象特化、条件の入れ子、拘束力の過剰）か、再構築の設計の項目（矛盾の解消、参照の向き、読込表、名前の導入、続行不能の表し方など）。維持は維持する理由 |
| 削除した場合のQCD | Quality、Cost、Deliveryのそれぞれについて悪化、変化なし、改善と1文の理由 |
| 移設先・統合先 | 移設、統合、経緯へ移動の行で、変更後のパスと節、および元の条文の価値、適用範囲、条件、例外が全て含まれるか |
| 保護の根拠 | その条文が対策となっている障害（`incidents-*.md`の区分と日付）、方針（`concepts-*.md`の節）、硬い制約、またはなし |
| 由来 | 人間由来、エージェント由来、未確定と、その根拠 |
| 事前承認 | 要か不要と理由。要の場合は承認の所在（承認待ちのAWIと事前承認型UWIのファイル名） |
| 連動対象 | 追随させたテスト、生成物、通知文、`docs/development/`の参照、`audit-records.md`の見出し |

由来列の初期値は`scripts/norm_restructure_clauses.py`の`--origin`が生成する。条文の最長行の中央付近から選んだ部分文字列で、パスを限定しない`git log -S`により最古の導入commitを求め、`Co-Authored-By`か`Claude-Session`のtrailerがあればエージェント由来、無ければ未確定とする。未確定の行のうち削除・縮小する行は、`.claude/skills/agent-toolkit-edit/SKILL.md`「規範を削除・縮小するときの消失確認」の手順で対応するキュー項目を調べ、人間由来かエージェント由来かを確定する。

由来が人間由来か未確定で、移設・統合の包含も失効も確かめられない削除・縮小は、本台帳の作成工程の差分から外し、承認待ちのAWI（`hold`）と事前承認型UWIへ渡す。その行の事前承認の列に両方のファイル名を書く。

## 逆引き表

`reverse-index.tsv`は`docs/development/incidents-*.md`の全事例と`docs/development/concepts-*.md`の全項目について、それを防ぐ条文か方針を適用する条文のキーと、変更後の位置を対応付ける。列は`記録`（区分と日付、または節と項目）、`防ぐ条文キー`、`変更後の位置`（パスと節）とする。
