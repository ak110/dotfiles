# 監査記録: 言語とテスト

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-basics/skills/writing-standards/references/drizzle.md：Drizzle ORM／Drizzle Kit記述スタイル：2026年9月16日

2026年9月16日、参考実利用バージョンの取得元を確認した。
`~/glatasks/package.json`は`drizzle-orm`を`^0.45.2`、`drizzle-kit`を`^0.31.10`で指定する。
再検証は同ファイルから、この2つの依存の版指定を取得する。
いずれかの依存が更新されたときに再検証する。

## agent-basics/skills/writing-standards/references/notation-rules.md：逐語引用の検出範囲：2026年9月5日

本表は2026年9月5日に実際に動かして確かめた結果である。
次の1文を地の文、引用ブロック、フェンス付きコードブロックへ置いた3つのサンプルファイルを作成し、pyfltr 3.17.8の`textlint`・`colloquial-check`にかけた。

```text
警告を出すと思う。
```

地の文では口語表現チェックとtextlintの弱い表現がいずれも検出され、引用ブロックではtextlintの弱い表現だけが検出され、フェンス付きコードブロックではいずれも検出されなかった。
em-dash（U+2014）を含む同じ形のサンプルを`scripts/check_dash.py`にかけたところ、引用ブロックでは検出され、フェンス付きコードブロックでは検出されなかった。
再検証は同じ3つのサンプルファイルを再度作成し、同じ手順で検出の有無を対にして確認する。

## agent-basics/skills/writing-standards/references/python.md：実行環境：2026年8月17日

2026年8月17日、Linux・uv 0.12.3で、依存メタデータが同一でパスだけが異なるPEP 723スクリプトを`uv run --script`で実行すると、スクリプトごとにvenvを再構築することを確認した。
パッケージと解決結果のキャッシュは共有され、ウォーム状態での再構築は1秒未満だった。
再検証は同じ`# /// script`ブロックを持つスクリプトを2つのパスへ置き、`uv --version`を記録したうえで順に実行し、uvのキャッシュディレクトリ配下の環境の数と所要時間を比べる。

## agent-basics/skills/writing-standards/references/python.md：静的解析の誤検出と抑制：2026年10月20日

- 観測日: 既存調査記録を2026年10月20日に確認した。
  元の観測日は未確認。
  同記録はLinuxで`sys.platform`による`msvcrt`と`fcntl`の分岐importを抑止なしで解析し、`msvcrt`だけに診断1件が出たと報告している。
  今回の文書追随では同じ解析を再実行していない
- 観測した版数: pylint 4.1.3、astroid 4.3.4
- 再検証手段: 同じ分岐importを一時Pythonファイルへ保存し、対象プロジェクトの`uv run --frozen pyfltr run --commands=pylint --no-fix <絶対パス>`で解析する。
  Windowsでは`fcntl`側の診断を対照取得する。
  pylintの`checkers/imports.py`にある`_ignore_import_failure`がOS分岐を除外しないことも確認する

## agent-basics/skills/writing-standards/references/python.md：テストコード（pytest）：2026年10月20日

- 観測日: 既存調査記録を2026年10月20日に確認した。
  元の観測日は未確認。
  同記録はpyfltrのWindows CI実行39222530624で`Popen.pid`と子の自己申告PIDの不一致を観測したと報告している。
  修正commit `60161d7`後の実行39227766095は成功とされる。
  今回の文書追随ではWindows上で再現していない
- 観測した版数: uvが作成したWindows runnerの仮想環境。
  観測したPythonの正確な版は未確認。
  Python公式`Doc/library/venv.rst`の3.7.2の変更記録はredirectorの仕様の根拠であり、観測版とは区別する
- 再検証手段: Windows仮想環境の`sys.executable`で子を起動し、`Popen.pid`と子の`os.getpid()`の標準出力を比べる。
  子の自己申告PIDとの一致を求める試験と、`Popen.pid`をプロセスの木の根として停止・生存確認に使う試験を分けて確認する

## agent-basics/skills/writing-standards/references/sqlalchemy.md：問い合わせと属性代入の順序：2026年9月14日

2026年9月14日、SQLAlchemy 2.0.52の公式文書で、`autoflush`を無効にしていない`Session`がORM対応の問い合わせ前に保留変更をflushすることと、`Session.flush()`で明示的にflushできることを確認した。
2026年9月13日のAWIは、SQLAlchemy 2.0.51を使うアプリケーションで、保留中のUPDATEが一意制約へ違反するケースと、保留中のINSERTが`NOT NULL`制約へ違反するケースを確認した記録を持つ。
同記録ではautoflushの無効化が同じ処理単位で追加した設定を読むテストを失敗させ、入力検証前の無条件な問い合わせが`Session`未開始のテストを失敗させた。
再検証ではSQLAlchemy 2.0系の公式文書にある`Session Basics`の`Flushing`節と`Session.flush()`のAPI説明を確認する。
あわせて、保留中のUPDATEとINSERTの後にORMへ問い合わせるテストで、問い合わせ前に各制約違反が送出されることを確認する。
問い合わせを属性代入前かつ入力検証後へ移し、保留変更の反映が必要な箇所だけ明示的にflushした状態で、入力エラーと同じ処理単位の読み取りを対にして確認する。

## agent-basics/skills/writing-standards/references/sub-agents.md：Claude Codeのfrontmatter：2026年8月19日

2026年8月19日、`skills`で宣言したスキルの本文がサブエージェントへ注入される際に、所在ディレクトリの絶対パスの表示が付随することをサブエージェントの記録で確認した。
公式ドキュメントはこの表示を記載していない。
再検証は`skills`を宣言した定義から起動したサブエージェントの記録を読み、注入されたスキル本文の直前または直後に所在の絶対パスが現れるかを確認する。

## agent-basics/skills/writing-standards/references/sub-agents.md：コンテキスト境界：2026年9月13日

2026年9月13日時点で、`main`と名前付きエージェントを列挙した名簿はClaude Code v2.1.206以降の版で付くことを確認した。
再検証は`tools`に`SendMessage`を含む定義を、ほかの名前付きエージェントが稼働するセッションから起動し、サブエージェントの記録に名簿が現れるかと`claude --version`の版数を確認する。

## agent-basics/skills/writing-standards/references/textlint-violations.md：文体と箇条書き：2026年9月10日

2026年9月10日、pyfltr 3.17.9のtextlintと本リポジトリの`.textlintrc.yaml`（`preset-jtf-style`の`1.1.3.箇条書き`を`shouldUsePoint: false`で運用する設定）で確認した。
全ての項目が句点で終わる順序付きリストは同ルールへ一致0件であった。
同じリストの1項目へ空行で区切った入れ子の箇条書きと段落を追加すると、句点で終わる他の4項目へ箇条書きの文末から句点を外す指摘が返った。
入れ子を独立した節へ移して各項目を1行に戻すと、再び一致0件であった。
再検証は同じ順序付きリストについて入れ子を含む写しと含まない写しを作成し、`--commands=textlint`を指定した同じコマンドで一致件数を比べる。

## agent-basics/skills/writing-standards/references/host-official-references.md：子Claudeへ環境値を渡す：2026年10月22日

Claude Codeの[公式環境変数資料](https://code.claude.com/docs/en/env-vars)と[settings資料](https://code.claude.com/docs/en/settings)を参照する。
settingsのenvは変数を削除できず、空の値はBash・MCPサーバー・hookへ継承される。
SDK 0.2.168の起動環境はos.environへoptions.envを重ねるため、agents_serverの起動時に隔離対象を取り除く。
2026年10月21日の観測では、設定に接続変数が無い環境でも、委譲先のBashへ26変数が空の値で渡った。
再検証はAPI環境ファイルを設定し、user・project・local設定のenvへ同じキーを持つ場合と持たない場合で、新規・再開した子の変数名と空値の件数だけを比較する。
SDK・process-loop・可用性確認の起動環境と合成settingsを合わせた結果は、claude_connection_test.pyとruntime_isolation_test.pyの回帰検証で確認する。
本記録の過去の実機観測はリポジトリ外の作業記録に記された結果であり、今回の原稿作成担当による認証を伴う実機再測定は未実施である。
