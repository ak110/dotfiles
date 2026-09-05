"""公開hookが返すコーディングエージェント向け通知の日本語を検証する。"""

import json
import os
import pathlib
import re
import subprocess

import _fork_runner
import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parent / "hook.py"
_ALLOWED_BARE_IDENTIFIERS = frozenset(
    {
        "WebFetch",
        "PreToolUse",
        "PostToolUse",
        "Stop",
        "SubagentStop",
        "AskUserQuestion",
        "SendMessage",
        "TaskStop",
        "agent-toolkit",
        "Claude",
        "Codex",
        "Git",
        "GitHub",
        "Markdown",
        "JSON",
        "YAML",
        "SSE",
        "URL",
        "ID",
        "PID",
        "OID",
        "CI",
        "PR",
        "Python",
        "PowerShell",
        "UWI",
        "AWI",
    }
)
_JAPANESE_RE = re.compile(r"[぀-ゟ゠-ヿ一-鿿]")
_LATIN_RE = re.compile(r"[A-Za-z]")

# 計画で確定した通知本文と解消手段の正本。公開入口の代表入力とは独立に、
# 全テンプレートへ同じ日本語判定を適用する。
_CONFIRMED_NOTICE_TEMPLATES: tuple[tuple[str, str], ...] = (
    (
        "pretooluse.py:1053 本文",
        "{tool_name}による編集で「`X`を根拠に`Y`しない」「`X`を理由に`Y`しない」形のメタ規範表現が増加した。"
        "対象: {file_path}。この形は「`X`でなければ`Y`してよい」と読み違えられる。"
        "全称否定形（「いかなる理由（例: `X`）があっても`Y`しない」）への書き換えを検討する。",
    ),
    (
        "pretooluse.py:1158 本文",
        "規範文書の本文が持つ節参照が実在しない可能性がある（{tool_name}、対象: {file_path}）: "
        "{'; '.join(reasons)}。参照先のファイルと節名が一致することを確認する。",
    ),
    (
        "pretooluse.py:1228 本文",
        "warning: `agent-toolkit:plan-mode`スキルを起動せずに計画ファイルを編集している。"
        "自身で計画を起草する場合は、同スキルを起動し、計画ファイルの編集を続ける前に`Phase 1`（初期理解）からやり直す。"
        "委譲した計画をレビューし、成果物と根拠から一意に定まる値だけを訂正する場合は、"
        "訂正内容と根拠を`## 変更履歴`へ記録したうえで、`plan-mode`をやり直さずに続行する。"
        "計画を確定する前に、`plan-mode`の直接委譲の手順でこの警告を解消して検証する。",
    ),
    (
        "pretooluse.py:1516 本文",
        "WebFetchは要約モデルを経由するため、その出力は逐語引用の根拠にならない。"
        "逐語で引用する場合は、同じURLの生データをagent-toolkitの管理対象一時領域へ保存し、"
        "保存した本文から該当箇所だけを引用する。",
    ),
    (
        "pretooluse.py:1529 本文",
        "エージェント種別名はSendMessageの到達可能な宛先ではない。通常の完了報告はツール結果として1回返し、"
        "即時通知は実行環境が渡した呼び出し元識別子へだけ送る。",
    ),
    (
        "pretooluse.py:1588 本文",
        "blocked: TaskStop。背景タスクの停止は、ユーザーの明示的な即時停止要求があるか、停滞検知の手順を完了した場合に限る。"
        "進行が遅いことや非効率に見"
        "えることだけでは停止の指示にならない。"
        "意図の解釈が複数残る場合は、停止の前にAskUserQuestionで確認する。"
        "ユーザーの介入があった場合は、既定では稼働中の委譲先へ追加指示を送る。停止するのは、当該介入が委譲範囲または前提を無効にし、"
        "継続すると誤った成果物が確定する場合に限る。詳細は`agent-toolkit:delegation`「継続と新規起動」が定める。",
    ),
    ("pretooluse.py:1588 解消手段", "停止の根拠を確認済みであれば、5分以内にTaskStopを再実行すると続行できる。"),
    ("pretooluse.py:1691 本文", "blocked: {op}。作業ディレクトリを表す式{event.unresolved_expression!r}を静的に解決できない。"),
    (
        "pretooluse.py:1691 解消手段",
        "先に`git -C <絶対パス> log --oneline --decorate`を実行し、履歴の書き換えを`git -C <絶対パス>`で再実行する。",
    ),
    ("pretooluse.py:1697 本文", "blocked: {op}。コマンドが未解決のシェル展開によって作業ディレクトリを変更している。"),
    (
        "pretooluse.py:1697 解消手段",
        "先に対象リポジトリで`git log --oneline --decorate`を実行し、静的に解決できる作業ディレクトリで再実行する。",
    ),
    ("pretooluse.py:1724 本文", "blocked: {op}。`amend`・`rebase`の前に`commit`の状態を確認する必要がある。"),
    (
        "pretooluse.py:1724 解消手段",
        "`amend`・`rebase`の前に`git log --oneline --decorate`を実行して`commit`の状態を確認する"
        "（特に`push`済みの`commit`を`amend`・`rebase`しない）。同じ`Bash`コマンド内の`git log`はこの検査を満たさない。"
        "同じ実効作業ディレクトリに対して、先行する別の`Bash`呼び出しで実行する。",
    ),
    (
        "pretooluse.py:1770 本文",
        "blocked: `amend`・`fixup`の後の`git push`で、作業ディレクトリを表す式{event.unresolved_expression!r}を解決できない。",
    ),
    ("pretooluse.py:1770 解消手段", "対象リポジトリを確認したうえで、`git -C <絶対パス> push ...`の形で再実行する。"),
    ("pretooluse.py:1775 本文", "blocked: `amend`・`fixup`の後の`git push`で、作業ディレクトリを解決できない。"),
    ("pretooluse.py:1775 解消手段", "`amend`の状態を確認し、静的に解決できる作業ディレクトリで再実行する。"),
    (
        "pretooluse.py:1791 本文",
        "blocked: {event.cwd}に追跡対象の未コミット変更が残ったまま、"
        "`git commit --amend`・`--fixup`の後に`git push`しようとしている。",
    ),
    (
        "pretooluse.py:1791 解消手段",
        "`git status`で内容を確認し、`git add`と`git commit --amend`（または`--fixup=<sha>`）で残りの差分を"
        "`amend`済み`commit`へ取り込むか、`push`の前に後続の`commit`を作"
        "る。",
    ),
    (
        "pretooluse.py:1947 本文",
        "warn: 一括`stage`に、当該セッションのファイル編集ツールによる編集記録が無いファイルが含まれている。"
        "シェルコマンドや生成器が変更したファイルは記録されないため、`stage`の前に所有を確認する。"
        "候補: {sample}。ファイル単位の`stage`（`git add <file>`）への切り替えを検討する。",
    ),
    (
        "pretooluse.py:2568 本文",
        "blocked: パターン一致によるプロセス終了（`pkill`／`killall`）は、対象プロセスの所有を確認できないため禁止する。",
    ),
    ("pretooluse.py:2568 解消手段", "自身が起動しPIDで特定したプロセスに対して`kill <PID>`を使う。"),
    (
        "pretooluse.py:_UV_RUN_PYTHON_BLOCK_MSG 本文",
        "blocked: `python`トークンの前に`--script`も`--no-project`も指定しない`uv run python`呼び出しである"
        "（`python`の後にパスが続く場合も`-c`が続く場合も同じ）。Pythonプロジェクトでない場所"
        "（`[project]`節を持たない`pyproject.toml`、または`pyproject.toml`が無い場所）では、`uv`がカレントディレクトリを"
        "プロジェクトとして扱い、副作用として`.venv`と`uv.lock`を生成する。プロジェクトに依存しない形を明示しない限り、"
        "この呼び出しは安全に続行できない。",
    ),
    (
        "pretooluse.py:_UV_RUN_PYTHON_FIX 解消手段",
        "`PEP 723`スクリプトは`uv run --script <パス>`を使うか、実行可能な`shebang`を直接呼び出す。"
        "カレントディレクトリのプロジェクト解決を省く場合は`uv run --no-project python ...`を使う。いずれでもない場合は、"
        "カレントディレクトリまたはその祖先で最初に見つかる`pyproject.toml`が`[project]`節を持つディレクトリで実行する。"
        "静的に解決できる`cd`の遷移先は実効作業ディレクトリとして評価する。作業ディレクトリの変更に未解決のシェル展開があると、"
        "プロジェクト種別を確認できないためこの呼び出しを遮断する。",
    ),
    ("pretooluse.py:2540 本文", "block: 前景の`sleep`に別のコマンドが続く呼び出しを、当該セッションで再び検出した。"),
    (
        "pretooluse.py:2547 本文",
        "warn: 前景の`sleep`の後に別のコマンドが続いており、反復ポーリングになる可能性がある。\\n{guidance}",
    ),
    (
        "pretooluse.py:2534 解消手段",
        "完了通知を受け取るか、背景ジョブの機械可読な完了標識を使うか、`atk watch`で委譲先の作業を観測し、\\n"
        "待機表明でターンを終える。",
    ),
    (
        "pretooluse.py:2815 本文",
        "blocked: 検証コマンドの出力を`tail`・`head`へ流"
        "して切り詰めている。"
        "ライブ出力は切り詰めた時点で失われ、後から全量を取得できない。",
    ),
    (
        "pretooluse.py:2809 解消手段",
        "先に全量を保存し（例: `tee /tmp/<name>.log`）保存したファイルから抽出するか、"
        "構造化出力から必要なレコード種別を指定して抽出するか、"
        "`agents_server`の`start_shell`ツールで分離したコンテキストで実行する。",
    ),
    (
        "pretooluse.py:2885 本文",
        "warn: 出力を切り詰める検証パイプラインの直後の`$?`は、検証コマンドではなく`head`・`tail`の終了状態を返す。"
        "出力を切り詰める前に検証コマンドの終了状態を保存する。",
    ),
    (
        "pretooluse.py:2998 本文",
        "warn: 再帰検索が容量の大きいユーザーディレクトリを対象にしている。`rg`や再帰的な`grep`を使う前に、"
        "対象ディレクトリを絞"
        "るか、不要な領域を除外するか、走査対象と出力量を制限するか、"
        "分離した実行コンテキストで検索する。",
    ),
    (
        "pretooluse.py:3082・3089 本文",
        "テストを実行せずに`commit`しようとしている。`01-agent.md`の検証してから`commit`する手順に従い、先にテストを実行する。",
    ),
    (
        "pretooluse.py:3159 本文",
        "`agent-toolkit/`配下のファイルを`stage`しているが、この`commit`と未`push`の範囲で"
        "`agent-toolkit/.claude-plugin/plugin.json`の`version`が変わっていない。利用者から見"
        "えるふるまい"
        "（`hook`スクリプト・スキル・エージェント定義・ルールファイル等）が変わる場合は、`commit`の前に"
        "`plugin.json`の`version`を更新し、`.claude-plugin/marketplace.json`も同期させる。",
    ),
    (
        "pretooluse.py:3231 本文",
        "`codex exec`を実行しようとしている。この実行が計画ファイルをレビューへ出"
        "すものである場合は、"
        "ユーザーの確認ではなく推測で確定した判断が無いかを確認し、未解決の確認事項をユーザーと解消してから進める。",
    ),
    (
        "pretooluse.py:3285 本文",
        "blocked: `agents_server`の`start`は、空でない絶対パスの`cwd`パラメーターを要求する（受領値: {actual}）。"
        "指定が無いとCodexは、要求した`worktree`ではなく`App Server`プロセスから作業ディレクトリを解決する。",
    ),
    ("pretooluse.py:3285 解消手段", "`cwd`へ対象作業ディレクトリの絶対パスを設定して再実行する。"),
    ("pretooluse.py:3303 本文", "blocked: {display_name}は空でない`prompt`を要求する。"),
    ("pretooluse.py:3303 解消手段", "空でない`prompt`を指定して再実行する。"),
    ("pretooluse.py:3313 本文", "blocked: {display_name}は空でない`session_id`を要求する。"),
    (
        "pretooluse.py:3313 解消手段",
        "`codex_start`が返した`session_id`を使うか、`codex_start`で新しい`session`を開始する。",
    ),
    (
        "pretooluse.py:3324 本文",
        "blocked: `session_id`に対応する絶対パスの`cwd`が保存されていないため、{display_name}を継続できない。",
    ),
    (
        "pretooluse.py:3324 解消手段",
        "この`session`を継続せず、絶対パスの`cwd`を指定した`agents_server`の`start`で新しい`session`を開始する。",
    ),
    (
        "pretooluse.py:662 本文",
        "blocked: {tool_name}.{field}の日本語テキストへ、日本語以外の文字体系（ハングル・キリル文字）が混入している。"
        "該当箇所: {ascii(value[start:end])}。",
    ),
    ("pretooluse.py:662 解消手段", "意図した日本語の文字へ置き換える。"),
    ("pretooluse.py:683 本文", "blocked: {tool_name}.{field}に文字化け（`U+FFFD`）を検出した。該当箇所: {sample!r}"),
    ("pretooluse.py:683 解消手段", "`U+FFFD`を意図した文字へ置き換えて再実行する。"),
    (
        "pretooluse.py:707 本文",
        "blocked: {tool_name}.{field}の内容が`LF`だけの改行になっている。"
        "`PowerShell` 5.1は`LF`改行の`.ps1`ファイルを解析できず、"
        "`CRLF`が必要である。対象: {file_path}",
    ),
    (
        "pretooluse.py:707 解消手段",
        "既存ファイルは`Edit`ツールで編集する（`CRLF`をそのまま保つ）。"
        "新規ファイルは`Bash`経由で`UTF-8 BOM`と`CRLF`改行を付けて書き"
        "出"
        "す"
        "（例: `printf '\\xEF\\xBB\\xBF' > file.ps1 && ... | sed 's/$/\\r/' >> file.ps1`）。",
    ),
    ("pretooluse.py:763 本文", "blocked: {tool_name}による{label}の直接編集は禁止する。対象: {file_path}"),
    ("pretooluse.py:763 解消手段", "このパスは編集せず、パッケージマネージャーで再生成する。"),
    ("pretooluse.py:_LOCKFILE_RULES uv.lock", "依存の追加は`uv add`、削除は`uv remove`を使う。"),
    ("pretooluse.py:_LOCKFILE_RULES pnpm-lock.yaml", "依存の追加は`pnpm add`、削除は`pnpm remove`を使う。"),
    ("pretooluse.py:_LOCKFILE_RULES package-lock.json", "依存の追加は`npm install <pkg>`を使う。"),
    ("pretooluse.py:_LOCKFILE_RULES yarn.lock", "依存の追加は`yarn add`を使う。"),
    ("pretooluse.py:_LOCKFILE_RULES Cargo.lock", "依存の追加は`cargo add`を使う。"),
    ("pretooluse.py:_LOCKFILE_RULES mise.lock", "ツールの管理は`mise use`・`mise install`を使う。"),
    ("pretooluse.py:_LOCKFILE_RULES .venv/", "仮想環境のファイルを直接編集せず、`uv`等で再構築する。"),
    ("pretooluse.py:_LOCKFILE_RULES node_modules/", "`node_modules`は生成ディレクトリであり、直接編集しない。"),
    (
        "pretooluse.py:819 本文",
        "blocked: {tool_name}による秘匿ファイル・鍵ファイルの直接編集は禁止する。"
        "誤編集はサービス停止や情報漏洩を招く。対象: {file_path}",
    ),
    (
        "pretooluse.py:_ENV_FILE_GUIDANCE 解消手段",
        "`git worktree`を実行可能にする場合は、`Bash`の`cp`で複製元から複製する。"
        "動作確認のために値を追加・変更・削除する場合は、"
        "編集ツールでファイルを書き換えず、`Bash`で行を追記または編集する（`echo ... >>`・`sed -i`）。",
    ),
    ("pretooluse.py:819 鍵・証明書の解消手段", "鍵ファイルと証明書ファイルは編集せず、この編集を取りやめる。"),
    ("pretooluse.py:860 本文", "{tool_name}で{label}を編集しようとしている。{hint}"),
    (
        "pretooluse.py:_MANIFEST_RULES pyproject.toml",
        "`[project.dependencies]`・`[project.optional-dependencies]`の編集は、"
        "`uv.lock`を同期させるため`uv add`・`uv remove`を使う。"
        "`[tool.*]`と版数の編集はそのまま進めてよい。",
    ),
    (
        "pretooluse.py:_MANIFEST_RULES package.json",
        "依存の編集は、`pnpm-lock.yaml`を同期させるため`pnpm add`・`pnpm remove`を使う。"
        "`scripts`とメタデータの編集はそのまま進めてよい。",
    ),
    (
        "pretooluse.py:940 本文",
        "{tool_name}.{field}にホームディレクトリの絶対パス（{home}）を検出した。バージョン管理下のファイルでは、"
        "環境に依存するパスを避けるため`~`・`$HOME`・`pathlib.Path.home()`を使う。該当箇所: {sample!r}",
    ),
    (
        "pretooluse.py:1000 本文",
        "{tool_name}.{field}に口語表現を検出した。一致: {len(hits)}件（{listed}）。"
        "`agent-toolkit:writing-standards`「日本語の書き方」節に従い、"
        "検出した表現を含む文を、標準的な専門用語・終止形・比喩的でない動詞による書き言葉へ文ごと書き換える。"
        "検出語を同義語へ置き換えるだけにせず、文を組み立て直す。{target}",
    ),
    (
        "pretooluse.py:1447 本文",
        "blocked: `plan-mode`スキルの起動後、計画ファイルを作成しないまま、`agent-toolkit/`配下のファイルを対象とする"
        "`Write`・`Edit`・`MultiEdit`が連続で{new_count}回実行された。",
    ),
    (
        "pretooluse.py:1447 解消手段",
        "`agent-toolkit/`配下のファイルを編集する前に、`~/.claude/plans/`配下へ計画ファイルを作成する。",
    ),
    (
        "pretooluse.py:1456 本文",
        "warn: `plan-mode`スキルの起動後、計画ファイルを作成しないまま、`agent-toolkit/`配下のファイルを対象とする"
        "`Write`・`Edit`・`MultiEdit`が連続で{new_count}回実行された。次の同種の編集は遮断される。"
        "先に`~/.claude/plans/`配下へ計画ファイルを作成する。",
    ),
    (
        "posttooluse.py:588 本文",
        "計画ファイル{file_path}を書き込んだ。書き込み後の検査を実行する: "
        "`uv run --script {shlex.quote(str(check_script))}{work_dir_option} {shlex.quote(file_path)}`。"
        "計画がセッションの作業ディレクトリ以外のリポジトリを対象とする場合は`--work-dir`を差し替える。",
    ),
    ("posttooluse.py:686 本文", "warn: {display_name}の応答に{', '.join(missing)}が欠けているか不正である。"),
    (
        "quality_checkpoint.py:QUALITY_CHECKPOINT_NOTICE",
        "本来のユーザーから見"
        "える目的を明示に保つ。要求されたシナリオに十分な最小限の設計を選ぶ。"
        "エージェント向けの会話上の誘導と、成果物へ残す恒久的な文脈を分け、会話限りの指示を成果物へ持ち込まない。"
        "要件を満たせない場合は明示的に失敗させる。裏付けの無いフォールバック・旧経路・互換経路は保持せず除去する。"
        "`AGENTS.md`と`agent-toolkit`のルールを正本として扱う。",
    ),
    (
        "autonomous_exit.py:108 解消手段",
        "列挙した前提工程をすべて完了してから、`/agent-toolkit:exit-session`を起動する。",
    ),
    (
        "autonomous_exit.py:_REASON_BODY",
        "このセッションには常駐ループの終了保証が適用される。`agent-toolkit:process-wi`の全工程を完了し、"
        "`agent-toolkit:completion-report`で完了報告した後に、`agent-toolkit:exit-session`を起動する。"
        "未完了の工程がある場合は、その工程へ戻ってから終了を再検討する。",
    ),
    (
        "plan_save_advisor.py:101 本文",
        "当該セッションが所有する計画バンドルが計画作業`root`に残っている: {path_list}\\n"
        "実装レビューが収束した計画バンドルだけを`private-notes`へ移す。残りはそのまま置いてターンを終える。",
    ),
    (
        "plan_save_advisor.py:101 解消手段",
        "収束した計画ごとに`atk plans commit <計画作業rootにある計画ファイル（メイン）のファイル名>`を実行する。"
        "収束したものが無い場合はターンを終える。",
    ),
    (
        "subagent_stop_advisor.py:47 本文",
        "停止する前に、空でない完了報告を出力する。呼び出し元は遮断された報告本文を保持しない。",
    ),
    ("subagent_stop_advisor.py:47 解消手段", "空でない完了報告を書いてから、あらためて停止する。"),
    (
        "_uwi_completion.py:build_notice",
        "リポジトリ{target_repo}に新しく回答されたUWIがある: {filenames}。"
        "当該セッションが終わる前に、これらを取り込むかを判断する。"
        "各項目を`atk wi show <ファイル名>`で読み、記録された回答に従い、回答と矛盾する暫定判断を修正する。"
        "回答が現在の作業と無関係な場合は、次回の`agent-toolkit:process-wi`へ残して続行する。",
    ),
    (
        "_response_language_check.py:BLOCK_BODY",
        "英語主体の応答が2ターン連続で検出された。ユーザーは英語の発話を読まないため、日本語での応答に切り替えること。",
    ),
    (
        "_response_language_check.py:WARNING_BODY",
        "直前のアシスタント応答の地の文が英語主体と判定された。地の文が日本語主体でも、"
        "冒頭が`Now`・`Next`・`Then`などの英語の語で始まる応答は同じ判定になる。"
        "ユーザーは英語の発話を読まないため、次の応答は冒頭の1文から日本語で書くこと。"
        "`01-agent.md`に従い、進捗報告・判断・ステータス更新をツール呼び出し前後の短文ステータスも含めて日本語で記述すること。"
        "日本語で応答し直せば、以後のターンはこの検査によって遮断されない。",
    ),
    (
        "pending_question_advisor.py:BLOCK_BODY",
        "地の文で利用者へ判断を求めたままターンを終えようとしている。判断を求める場合はAskUserQuestionで確認し、"
        "確認が不要な場合は当該問いかけを本文から除いて応答を書き直すこと。",
    ),
    ("pending_question_advisor.py:_BLOCK_FIX", "AskUserQuestionで確認するか、当該問いかけを本文から除いて応答を書き直す。"),
    (
        "agents_server_session_advisor.py:_WARNING_BODY",
        "`agents_server`の`session`に、観測を試みていない作業が残っている。`wait(session_id)`で観測するか、"
        "結果が不要なら`kill(session_id)`で破棄してから終了する。`send_message`は新しい作業を配送するだけで観測しないため、"
        "この警告は解消しない。観測しないまま終了すると、当該作業の成果を回収する主体が残らない。",
    ),
)


def _is_japanese_notice(text: str) -> bool:
    """通知の自然言語部分が日本語だけで構成される場合に真を返す。"""
    body = re.sub(r"\[auto-generated:[^\]]+\](?:\[[^\]]+\])?", "", text)
    body = body.replace("（自動生成のhook通知。行動する前に会話コンテキストとの関連性を評価すること。）", "")
    body = re.sub(r"(?m)^\s*(?:warn|warning|block|blocked):\s*", "", body)
    body = re.sub(r"(?m)^\s*Fix:\s*", "", body)
    body = re.sub(r"\{[^{}]*\}", "", body)
    body = re.sub(r"`[^`]*`", "", body)
    body = re.sub(r"https?://\S+", "", body)
    body = re.sub(r"\\[ntr]", "", body)
    for identifier in sorted(_ALLOWED_BARE_IDENTIFIERS, key=len, reverse=True):
        body = re.sub(rf"(?<![A-Za-z]){re.escape(identifier)}(?![A-Za-z])", "", body)
    return _LATIN_RE.search(body) is None and _JAPANESE_RE.search(body) is not None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Retry.", False),
        ("blocked: pattern-based process termination is prohibited.", False),
        ("{tool_name}による編集で対象を確認する。対象: {file_path}", True),
        ("{tool_name}による編集で Retry.", False),
        ("`pkill`／`killall`は使わない。", True),
    ],
)
def test_japanese_notice_judgment(text: str, expected: bool) -> None:
    assert _is_japanese_notice(text) is expected


def test_confirmed_notice_templates_are_japanese() -> None:
    """確定訳の全本文と全解消手段が通知言語契約を満たすことを検証する。"""
    failures = [source for source, text in _CONFIRMED_NOTICE_TEMPLATES if not _is_japanese_notice(text)]
    assert failures == []


def _run(payload: dict, tmp_path: pathlib.Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CLAUDE_AGENT_TOOLKIT_STATE_DIR"] = str(tmp_path / "state")
    return _fork_runner.run_script(
        _SCRIPT,
        argv=("pretooluse",),
        input=json.dumps(payload, ensure_ascii=False),
        env=env,
    )


def _write_english_transcript(tmp_path: pathlib.Path, message_id: str) -> pathlib.Path:
    transcript = tmp_path / "transcript.jsonl"
    entry = {
        "type": "assistant",
        "message": {
            "id": message_id,
            "role": "assistant",
            "content": [{"type": "text", "text": "This response is written entirely in English for the language checker."}],
            "stop_reason": "end_turn",
        },
    }
    transcript.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
    return transcript


def test_process_termination_block_is_japanese(tmp_path: pathlib.Path) -> None:
    command = "p" + "kill -f myserver"
    result = _run({"tool_name": "Bash", "tool_input": {"command": command}}, tmp_path)
    assert result.returncode == 2
    assert _is_japanese_notice(result.stderr)


@pytest.mark.parametrize(
    ("file_name", "expected_notice"),
    [
        (
            "pyproject.toml",
            "`Write`で`pyproject.toml`を編集しようとしている。"
            "`[project.dependencies]`・`[project.optional-dependencies]`の編集は、"
            "`uv.lock`を同期させるため`uv add`・`uv remove`を使う。"
            "`[tool.*]`と版数の編集はそのまま進めてよい。",
        ),
        (
            "package.json",
            "`Write`で`package.json`を編集しようとしている。"
            "依存の編集は、`pnpm-lock.yaml`を同期させるため`pnpm add`・`pnpm remove`を使う。"
            "`scripts`とメタデータの編集はそのまま進めてよい。",
        ),
    ],
)
def test_manifest_edit_warning_uses_confirmed_japanese_notice(
    tmp_path: pathlib.Path,
    file_name: str,
    expected_notice: str,
) -> None:
    """manifest編集警告の確定訳を公開hook入口で検証する。"""
    result = _run(
        {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / file_name), "content": ""}},
        tmp_path,
    )
    assert result.returncode == 0
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert expected_notice in context
    assert _is_japanese_notice(context)


def test_response_language_warning_is_japanese(tmp_path: pathlib.Path) -> None:
    transcript = _write_english_transcript(tmp_path, "message-1")
    result = _run(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "session_id": "language-warning",
            "transcript_path": str(transcript),
        },
        tmp_path,
    )
    assert result.returncode == 0
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert _is_japanese_notice(context)


def test_response_language_block_is_japanese(tmp_path: pathlib.Path) -> None:
    transcript = _write_english_transcript(tmp_path, "message-1")
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "session_id": "language-block",
        "transcript_path": str(transcript),
    }
    assert _run(payload, tmp_path).returncode == 0
    _write_english_transcript(tmp_path, "message-2")
    result = _run(payload, tmp_path)
    assert result.returncode == 2
    assert _is_japanese_notice(result.stderr)
