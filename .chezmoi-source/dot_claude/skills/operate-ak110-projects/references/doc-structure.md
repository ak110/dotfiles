# ドキュメント章構成の統一

各プロジェクトのREADME.md・AGENTS.md・docs/development/development.mdは章構成・章順を揃える。
共通化できる部分（コマンド例、典型的な節の説明文など）は一字一句揃え、プロジェクト固有の部分は無理に揃えず必要な情報を書く。
単独のプロジェクトでの章の追加と章順の変更は避け、`operate-ak110-projects`スキルの実行時に逸脱を検出する。

## README.mdの標準章構成

1. プロジェクト名・1行概要・バッジ
2. 特徴
3. 前提条件（任意）
4. インストール（任意）
5. 設定（任意）
6. 使い方（任意）
7. ドキュメント

## AGENTS.mdの標準章構成

Claude CodeとCodexが共通で読む`AGENTS.md`に本章構成を適用する。
観測記録はdotfilesリポジトリの`docs/development/audit-records-project-instructions.md`の「プロジェクト指示のAGENTS.md対応：2026年9月20日」にある。
手元に`CLAUDE.local.md`を置くとClaude Codeが`AGENTS.md`を読まなくなる。
その場合は`# CLAUDE.md`と`@AGENTS.md`の2行の`CLAUDE.md`アダプターを置き、`.git/info/exclude`へ`/CLAUDE.md`を加えて追跡対象から外す（`atk setup-project`と同じ扱い）。
観測記録は同文書の「プロジェクト指示のCLAUDE.mdアダプター：2026年9月26日」にある。

1. プロジェクト概要パラグラフ（必須）
2. 開発手順（必須）
3. アーキテクチャの参照先（任意。docs/development/architecture.mdへの誘導）
4. 実装上の不変条件・コーディング規約（任意。「実装上の不変条件」「コーディング規約」のどちらか一方の章名だけで書いてよい）
5. サブエージェント・スキル連携（任意）
6. 注意点（必須）
7. プロジェクト固有章（任意）

## docs/development/development.mdの標準章構成

1. 開発環境の構築手順
2. 開発コマンド
3. サプライチェーン攻撃対策
4. ドキュメントサイト運用（任意）
5. プロジェクト固有章（任意。glatasksのDocker構成・CI/CD・バックアップとリストア、lcのAnalyzerルールの導入・.NET SDKの更新・環境制限など）
6. リリース手順（リリースサイクルを持つプロジェクトのみ）

「開発コマンド」章にチェック実行コマンド（`make test`・`uvx --exclude-newer-package pyfltr=false pyfltr run <path>`等）を含める。

「サプライチェーン攻撃対策」章ではロック尊重・公開待機・ピン留め運用の3点を要約として書き、脆弱性検知を設ける場合はこれを加えた4点を要約として書く。
次の必須事項を共通の文面で揃える。

- Pythonパッケージ配布プロジェクト（dotfiles・pyfltr・pytilpack・smpr）はUV_FROZEN=1の常時有効化を採用する。
- glatasksはPython依存を持たないためUV_FROZENの常時有効化を行わず、`exclude-newer`だけを採用する。
- 依存パッケージの脆弱性検知（Dependabot alerts等）の要否は配布形態で決める。
  ライブラリとして配布するプロジェクトは、ロックファイルが開発専用でエンドユーザーの実行環境へ波及しないため設けない。
  実行可能なツール・アプリケーションとして配布するプロジェクトは、依存がエンドユーザーの実行環境へ波及するため設ける。
  設ける場合はDependabot alertsを有効化し、自動修正PRの作成を無効化し、定期監査ワークフローを配置する。
  採用した方針と根拠は、各プロジェクトの開発者向け文書のサプライチェーン攻撃対策節へ書く。

## docs/development/配下の記述基準

development.md・architecture.md・concept.md等、docs/development/配下の全ファイルに共通する基準である。

- 設定ファイルや他の文書が定義する内容と、公式資料で説明される一般知識（CLI・技術スタック・公開パターンなど）は再掲せず、設定の詳細は設定ファイルのコメントへ委ねる。
- プロジェクト固有の判断・運用・設計理由と、エンドユーザーや開発者が実行するコピペ可能なコマンドを残す。
- 設定ファイルの現在の値を前提とする判定結果（その設定が変われば無効になる結論）は、その設定ファイルの該当箇所のコメントへ書き、開発者向け文書には設定値が変わっても成り立つ判定手順だけを書く。

## docs/guide/index.mdの扱い

ドキュメント生成ツールの自動生成ナビ（nav・sidebar）でガイド一覧を提示できる場合、guide/index.mdは任意配置とする。
配置する場合は見出しを「利用者向けガイド」で揃える。
docs/development/index.mdの見出しは「開発者向けガイド」で揃える。

## バッジ記法

README.mdのCI・Release・Deployバッジは参照形式（`[![Name][name-badge]][name-url]`）で書き、キー名は`ci-url`・`release-url`・`deploy-url`に揃える。
PyPIなど外部サービスのバッジはインライン形式のままでよい。

## コミュニティ健全性ファイル

`ak110/.github` は `SECURITY.md`、`CONTRIBUTING.md`、`.github/ISSUE_TEMPLATE/`（不具合・アクセシビリティの障壁・案内設定）、`.github/PULL_REQUEST_TEMPLATE.md` の原本を持つ。
共通のアクセシビリティ方針は既定ファイルにせず、`policies/accessibility.md` に置く。
原本のリポジトリには役割を示す `README.md` と自身の `LICENSE` を置く。
各リポジトリが自前のIssueテンプレートを1つでも置くと既定のテンプレートが全て使われないため、原則として原本だけに置く。

各製品のルートの `ACCESSIBILITY.md` には、次の製品固有の事実を書く。

- 対象面（Web画面、デスクトップ画面、CLI、ドキュメントサイト、ライブラリAPIのうち該当するもの）
- 確認した環境、版、観測日、結果と未検証の範囲
- 既知の障壁と回避策
- 報告先（その製品の `https://github.com/ak110/<repo>/issues/new/choose`）
- [共通方針](https://github.com/ak110/.github/blob/main/policies/accessibility.md)へのリンク（目標とする基準、報告で求めないこと、見直す契機の文面は複製しない）

要素構造と実際の支援技術の観測を区別し、実測した版と操作を超える適合を宣言しない。
既定ファイルはcloneに含まれないため、README.mdの「ドキュメント」章から `ACCESSIBILITY.md` と共通の `CONTRIBUTING.md`・`SECURITY.md` へリンクする。
公開リポジトリではPrivate vulnerability reportingを有効にする。
非公開のsmprには公開向けの導線を求めない。
既定ファイルの表示は許容する。
