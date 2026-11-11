# 監査記録: 検索

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-basics/skills/search/SKILL.md：検索手段の選定と出力量の制御：2026年9月7日

2026年9月7日、git 2.43.0で`assert expanded_common_job_count + len(statusline_jobs) == 7`を検索する`git grep -n`が一致0件を返し、同じ文字列を検索する`git grep -nF`が1件返すことを確認した。
再検証は正規表現のメタ文字を含む行を対象リポジトリの追跡ファイルから1件選び、`git grep -n`と`git grep -nF`の一致件数を比べる。

## agent-basics/skills/search/SKILL.md：検索手段の選定と出力量の制御：2026年9月8日（1）

2026年9月8日、ripgrep 15.2.0で実際に動かして確かめた。
隠しディレクトリ配下の追跡ファイルだけが含む文字列に対し、リポジトリrootを対象とする`rg -n --fixed-strings`は一致0件を返した。
同じ文字列を対象とする`git grep -nF`はその文字列を含む追跡ファイルを返した。
再検証は隠しディレクトリ配下の追跡ファイルだけが含む文字列を1件選び、両コマンドの一致件数を比べる。

## agent-basics/skills/search/SKILL.md：検索手段の選定と出力量の制御：2026年9月8日（2）

2026年9月8日、ripgrep 15.2.0で、隠しディレクトリ配下のファイルに存在する文字列が、`--hidden`の無い実行で一致0件となり、付けた実行で一致することを実際に動かして確かめた。
再検証は隠しディレクトリ配下のファイルへ一意な文字列を1件置き、`--hidden`の有無で一致件数を比べる。

## agent-basics/skills/search/SKILL.md：引数とシェルの扱い：2026年9月14日

2026年9月14日、Git 2.43.0で実際に動かして確かめた。
`grep.lineNumber=true`を指定した`git grep -h -m 1 -F -e <固定文字列> -- AGENTS.md`は`3:<本文>`を返し、同じ検索へ`--no-line-number`を指定すると`<本文>`だけを返した。
再検証は`git -c grep.lineNumber=true grep -h -m 1 -F -e <固定文字列> -- <追跡ファイル>`と、`-h`を`--no-line-number`へ置き換えた検索の出力を比較する。

## agent-basics/skills/search/SKILL.md：引数とシェルの扱い：2026年10月9日

2026年10月9日、Claude Code 2.1.295のBashで`type grep`がシェル関数を返し、ugrep 7.8.4へ転送することを確認した記録に基づく。
GNU grepは3.11だった。
ignore・binaryの除外で一致する内容を持つ対象でも標準出力が空・終了コード1となった。
`.{0,160}a.{0,160}`はugrepで複雑度エラーを標準エラーへ出力し、標準出力は空・終了コード2だった。
上限を小さくした`.{0,30}a.{0,30}`も終了コード2で、繰り返しを片側だけにした`a.{0,400}`は終了コード0だった。
同じテキストへの`command grep -o -E '.{0,160}a.{0,160}'`は`a`を出力して終了コード0だった。
この節のClaude関数の観測は入力WIが保持する対照記録に由来する。

再検証ではClaude Bashで`type grep`と`grep --version`、`command grep --version`を取得する。
専用領域に`a`を含む通常テキスト、ignore対象とbinary対象を用意し、関数の`grep`と`command grep`へ同じ対象・パターンを渡す。
各標準出力・標準エラー・終了コードを保存して比較し、無出力の理由とパイプの後段で使う起動実体を区別する。
