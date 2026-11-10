# 実装時のコーディング品質：編集前の読解

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 指摘・エラーの原因分析と類似見直しを行う前 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `design-time-implementation.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-time-implementation.md` |
| 「コードの簡潔さ」で処理の共通化を判断する時 | `design-heuristics-responsibility.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-heuristics-responsibility.md` |
| 内部の引数や選択肢の整理でテストを統合する前 | `testing-priorities.md`、`testing-selection.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/testing-priorities.md`、`read:agent-basics:*/skills/writing-standards/references/testing-selection.md` |
| 外部通信の時間予算や再試行を選ぶ前 | `design-runtime-jobs.md`、`design-runtime-services.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-runtime-jobs.md`、`read:agent-basics:*/skills/writing-standards/references/design-runtime-services.md` |

## 編集前の読解

- 編集対象のファイルは、編集作業に入る前に全体を一通り読み込む（一部だけを読むと、全体のスタイルから逸脱したり、リファクタリングの必要性を見落としたりするため）。
  実装の正確な仕様はコード本体から読み取り、docstringはあくまで補助的な情報として扱う。

## 記述順序と命名

- 全体の構造を上位から自然にたどれる順序を意識し、関連する定義を近くに配置する。
  また、新しく追加する箇所の命名や用語は、既存コードの命名規則に合わせる（努力目標）。

## コードの簡潔さ

- 1か所からしか呼ばない補助関数を先回りで切り出さず、呼び出し箇所に書く。
  再利用される処理、複雑な境界を隠す処理、または実在の概念を名指しして呼び出し側を読みやすくする処理は切り出してよい。
  同じ理由で変更される重複は、`design-heuristics-responsibility.md`「責務帰属（SRP）分析の観点」に従って共通化する。
  検証や補足の分岐が多い関数では、主処理を正常系の流れとして読み、補助関数は主処理の後ろに置く。
- 内部の引数や選択肢は、全呼出側で固定される値、所有者が導出できる値、未使用の選択肢を整理し、内部の呼出側を同時に移して不要な旧経路を撤去する。
  人間が使うCLIや設定など公開契約の存廃は、互換性と認可の基準に従う。
  整理後の契約を通して必要な振る舞いを検証し、テストの統合は`testing-priorities.md`、`testing-selection.md`に従って、別の異常条件・安全性・並行性の保証を保つ。

```python
# 良い例: 1か所でしか使わない処理を呼び出し箇所に置く。
if config_path.exists():
    config = json.loads(config_path.read_text())

# 悪い例: 再利用も実在の概念もない処理を切り出す。
def read_if_exists(path):
    return json.loads(path.read_text()) if path.exists() else None

config = read_if_exists(config_path)
```

- returnで抜けた分岐の後にelseを書かず、早期returnで分岐を平らにする。
  条件によって値を変えるだけの再代入より、三項演算子やif式で1回だけ束縛する形を優先する。
  Rustの`let value = if enabled { a } else { b };`のような式としてのif・elseは推奨する形に当たる。

```typescript
// 良い例
if (!enabled) return;
const label = debug ? "debug" : "normal";

// 悪い例
if (!enabled) return;
else run();
let label;
if (debug) label = "debug";
else label = "normal";
```

- 一度しか使わない値は、変数名が単位、役割、または判定の意図を式以上に加えない限り、変数にせず使う箇所に式を書く。

```python
# 良い例
logger.info(response.status_code)

# 悪い例
status = response.status_code
logger.info(status)
```

- 型の分からない外部の値は受け取る境界で一度だけ検証し、内側へは型の付いた値として渡す。
  スキーマ、コンストラクター、または内部の型が保証する値を防御的に検証し直さない。
  信頼境界と入力検証の詳細は`implementation-time-errors.md`「セキュリティ・ロギング・エラー処理」を参照する。
  外部CLI・SDK・サービスの出力は、自分が所有する保存形式と分けて扱う。
  外形と選別に使う欄を確認し、利用対象を選んでから、その対象の利用欄へ必要な制約を課す。
  未利用項目・欄や未知の追加欄の形式差を主機能全体の失敗にしない。
  利用対象の欠落・不正、相関識別子、安全性や完全性の不成立は診断可能な失敗とする。
  試験では、対象外項目の欠落・変形した欄と、正常/不正な利用対象を混在させ、選別前に利用時専用の制約を課していないことを確認する。

```typescript
// 良い例
const settings = SettingsSchema.parse(await response.json());
start(settings);

// 悪い例
const settings = await response.json();
if (typeof settings === "object" && settings !== null && "port" in settings) start(settings);
```

## 言語横断のツールと構文

プロジェクト側で個別の指定がない場合は、次の標準ツールを使用する。

| 言語 | 依存管理・ビルド | lint・format・補助ツール |
| --- | --- | --- |
| Python | `uv` | `pyfltr`、`pytilpack` |
| TypeScript | `pnpm`（一度限りのツール実行は`pnpx`） | `Biome`（Biomeが未対応のルールが必要な場合に限りESLintとPrettierを併用） |
| Rust | `cargo` | `cargo clippy -D warnings`、`cargo fmt` |
| C# | `dotnet` CLI | `dotnet format`、Roslynアナライザー、`Microsoft.CodeAnalysis.NetAnalyzers` |
| Bash | 該当なし | `shellcheck`、`shfmt` |
| PowerShell | 該当なし | PSScriptAnalyzer |

公開互換性として宣言されている範囲内で利用できる新しい言語機能は、積極的に活用する。
利用できるかどうかは、対象プロジェクトが指定している言語バージョンと公式リリースノートを突き合わせて確認する。
これにはPythonの`requires-python`、TypeScriptのバージョン、Rustの`rust-version`やedition、C#の`LangVersion`や`TargetFramework`などが含まれる。
判断にあたっては、サポート対象として宣言されているすべてのバージョンで動作するかを考慮する。

- Python: <https://docs.python.org/3/whatsnew/index.html>
- TypeScript: <https://www.typescriptlang.org/docs/handbook/release-notes/overview.html>
- Rust: <https://doc.rust-lang.org/releases.html>
- C#: <https://learn.microsoft.com/dotnet/csharp/whats-new/>

同等の品質を実現できる場合は、外部パッケージを追加するよりも標準ライブラリの利用を優先する（検討の優先順位は`design-time-implementation.md`「実装方針の選択」を参照）。
プロジェクト固有の個別ルールがある場合は、各リポジトリの`CLAUDE.md`や`.claude/rules/`に記載する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 実装時のコーディング品質：セキュリティ・ロギング・エラー処理の基準を適用する前 | `implementation-time-errors.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/implementation-time-errors.md` |
