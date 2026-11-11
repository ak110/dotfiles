# Python記述スタイル：静的解析の誤検出と抑制

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 静的解析の誤検出と抑制

- lintの指摘への対応は`implementation-time-checks.md`「lintと機械チェック」に従う。
  Pythonでは複数のチェッカーが同じ箇所に対して重複して警告を出すことが多いため、`assert`や`del`などの標準的な構文を用いてコード側で解決できる場合は、抑制コメントよりも構文による解決を優先する。
- `subprocess.run(..., capture_output=True)`の戻り値`proc.stdout`は、型チェッカーによって`None`を取り得る型（`bytes | None`など）として推論される（`text=True`を指定した場合でも同様に`None`の可能性が残る）。
  これを使用する前に、`assert isinstance(proc.stdout, bytes)`などで型を絞り込む。
- `sys.path.insert()`などを用いて動的にパスを変更した直後に`import`文を記述すると、pylintが`wrong-import-position`と`import-error`の両方の警告を誤って出すことがある。
  この場合は`# pylint: disable=wrong-import-position,import-error`を併記して抑制する。
- 意図的に関数内で`import`を行う場合は、`# noqa: PLC0415  # pylint: disable=import-outside-toplevel`を併記して警告を抑制する。
- `ty`と`mypy`の両方が有効になっている環境で型の不整合に関する警告を抑制する場合は、`# type: ignore[<mypy-code>]  # ty: ignore[<ty-rule>]`のように同一行に両方の抑制コメントを併記する。

実行環境のOSに存在しない標準モジュールのインポートでは、OS判定による分岐の両側のブロックに加え、特定OS専用のモジュール内やテストコード内であっても、`# pylint: disable=import-error`とその理由をコメントに明記する。
pylintは`sys.platform`や`os.name`による条件分岐をimportエラーの無視条件として認識せず、解析を実行しているOS上に存在しないモジュールを検出するとエラーを報告するためである。
一方のOSでのみ警告を抑制した場合、別のOS上でpylintを実行した際に初めてエラーが検出されることになる。
監査記録は`docs/development/audit-records.md`「agent-basics/skills/writing-standards/references/python.md：静的解析の誤検出と抑制：2026年10月20日」を参照。
