---
name: search
user-invocable: false
description: >
  リポジトリまたはディレクトリのファイル属性と内容を検索する直前に起動する。
---

# 検索手段の選定と出力量の制御

ファイル属性と内容の検索手段の選定、検索の範囲、出力量の制御を定める。

検索には対象資料へ届く読取手段と、CLIか検索用MCPが必要である。利用できる手段が提示された資料の読取だけの場合は、その資料の範囲で該当箇所を示し、未取得のリポジトリ全体についての不在や件数は未確認として残す。検索手段が無い場合は、対象と検索条件をClaude CodeまたはCodexで実行する形へ整理する。説明の文は[文章の品質基準](references/quality-standards.md)で確かめる。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

<!-- read-table-source: SKILL-delivery-1.md -->

## 手段の選定

- 属性探索（ファイル名、種類、更新時刻、ディレクトリ構造）は対象を限定した`find`
- 現在のGit作業ツリーの管理対象に限る内容検索と追跡ファイル全体の残存確認は`git grep`。`rg`は`--hidden`なしでは隠しディレクトリ配下の追跡ファイルを対象から外す。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/search/SKILL.md：検索手段の選定と出力量の制御：2026年9月8日（1）」にある
- 作業ツリー外の明示パス（別のGitリポジトリを含む）、Git管理外の対象、除外設定に従う内容検索は`rg`。隠し対象を含める場合は`--hidden`を付ける。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/search/SKILL.md：検索手段の選定と出力量の制御：2026年9月8日（2）」にある
- 除外設定を反映したファイル一覧は`rg --files`、構造化した集計と全体件数の上限を要する検索はMCPまたはCLIの`pyfltr grep`

`rg`も`git grep`も使えない場合は、`--include`・`--exclude`・`--exclude-dir`で対象を限定した`command grep`の出力をファイルへ保存してから必要な範囲だけ読む。
作業ツリー外のファイルから秘匿値と見られる値を探す検索は`agent-toolkit:secret-files`の値を出力しない形で行う。

## 引数とシェルの扱い

Claude CodeのBashでは`grep`がugrepへ転送するシェル関数になっている場合がある（`type grep`で確かめる。パイプの後段の`grep`も同じ）。ugrepはignoreやbinaryの除外で無出力・終了コード1を返し、上限付き繰り返しを2つ含む`-E`の式（`.{0,160}a.{0,160}`など）では複雑度エラーで終了コード2・標準出力空になる。不在・件数の確定や有界反復には`command grep`を対照に使う。監査記録は`docs/development/audit-records.md`「agent-toolkit/skills/search/SKILL.md：引数とシェルの扱い：2026年10月9日」にある。

`git grep`では`-F`・`-E`・`-P`を明示し、オプション、pattern、`--`、pathspecの順で置いて`grep.patternType`などのホスト設定から独立させる。メタ文字を含む文字列を`-F`なしで渡すと一致0件になる。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/search/SKILL.md：検索手段の選定と出力量の制御：2026年9月7日」にある。
`grep.lineNumber=true`の下では`-h`でも行番号が残り、`--no-line-number`で抑止できる。観測結果は`docs/development/audit-records.md`の「agent-toolkit/skills/search/SKILL.md：引数とシェルの扱い：2026年9月14日」にある。
grepとrgの単語境界（`-w`・`\b`）は日本語文字を単語構成文字として扱い、日本語に隣接する英単語へ一致しない。日本語文書では境界をパターン側で表す。

パイプでは`pipefail`などで全段の終了状態を取得する。`rg`は一致0件を終了コード1、パスの不在や引数の誤りを2と標準エラーで返す。`git grep`は一致しないpathspecにも1を返すため、推測したパスやglobは`git ls-files`か`rg --files`で実在を確かめてから渡す。

## 出力量の制御

リポジトリやディレクトリ全体の内容検索では、最初の取得を件数・ファイル名・要約（`-l`、`-c`、`pyfltr grep`の要約・上限指定）にして候補と出力量を確かめてから本文を取る（努力目標。一致本文を先に直列化するとコンテキストを消費する）。source mapやminifiedファイルの巨大な単一行にも注意する。
`-c`の値は一致行数であり、関数などの実体数として使う前に1実体と1行の対応を構造から確かめる。候補の列挙は所在を得る段階とし、完成条件が本文の判定を要する場合は共通定義・生成側・消費側の取得へ進む。全文と所在の読取範囲は受領した完成条件で区別する。
件数・ファイル名・要約から、本文の読取まで含めた調査がコンテキストを大きく消費すると見込んだら、委譲できるエージェントは自ら検索を続ける前に読み取り専用の探索委譲（`agents_server`の`start`の`explore`など）へ切り出すかを判定する。条件と手段は`agent-toolkit:delegation`の`references/routing.md`「コンテキスト消費が大きい調査の切り出し」に従う。

## 検索の範囲と母集団

通常の検索はリポジトリrootから`.gitignore`と各ツール固有の除外を保って実行する。依存環境や生成物は除外されるので、rootを対象にしても出力量はほぼ変わらない。
ディレクトリやglobで範囲を狭めるのは、対象がその範囲にしか存在しないことを別の観測で確定した場合に限る。見込みで狭めると範囲外の一致（テストなど）を検出しないまま結論が確定し、同じ値や識別子を持つ箇所の一部が未修正で残る。

意味で定まる集合は、定義元と利用側が値を取り出す方法（CLI出力、説明属性、辞書キーなど）から母集団を定め、検索語ごとの一致件数を記録し、語の一致がない取得先も確かめる。全体点検の処理群と担当範囲は`agent-toolkit:delegation`の`references/routing.md`「コンテキスト消費が大きい調査の切り出し」で対応付け、未走査の群がある間は部分の0件を調査全体の不在へ拡張しない。

## 省略された結果の扱い

不在・件数・網羅性・終端の結論には、母集団を覆い本文も省略しない取得を使う。件数・行長・ファイルサイズの上限や`head`・`cut`などで省略された結果は限定観測であり、結論の前に省略のない形で取り直す（全量が大きければ分離したコンテキストで取得し要約を受け取る）。一般則は`agent-toolkit:writing-standards`の`agent-basics:writing-standards/references/investigation.md`「不在の主張」に従う。
切り詰めと保存出力の読取は`agent-toolkit/share/agents-server-explore.md`の開始・継続・終端判定へ従い、自身で取得できる不足を担当内で補う。同書は軽量探索の起動時にも配送される。権限・到達不能・入力不足など自身で解消できない条件は、確認範囲と理由を分けて返す。
検索で資料の節を特定しても、読込表が全文読むと定める資料は全文読む（`agent-toolkit:writing-standards`の`agent-basics:writing-standards/references/agent-documents-basics.md`「読込表」）。
