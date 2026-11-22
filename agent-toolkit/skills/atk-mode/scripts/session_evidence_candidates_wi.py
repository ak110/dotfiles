from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

import session_evidence_execution
import session_evidence_static
from agent_toolkit._atk.wi import constants as _wi_constants
from agent_toolkit._atk.wi import layout as _wi_layout
from agent_toolkit._atk.wi import sections as _wi_sections
from agent_toolkit._atk.wi import uwi_scan as _uwi_scan
from agent_toolkit._atk.wi.frontmatter import parse_frontmatter as _parse_wi_frontmatter
from agent_toolkit._common.shell_segments import resolve_execution_segment as _resolve_execution_segment
from agent_toolkit._common.shell_tokens import is_atk_command as _is_atk_command
from session_evidence_extract import _CollectedRecord
from session_evidence_tool_calls import _record_tool_calls, _recorded_input

_ADHOC_SHELL_TOOLS = frozenset({"bash", "exec", "exec_command", "shell", "local_shell", "shell_command"})
"""シェルのコマンドを実行するツール名の末尾部分。Codexの`exec`はJavaScript本文から`exec_command`を呼ぶ形でコマンドを渡す。"""


_REPORT_UWI_QUESTION = re.compile(r"の作業結果と振り返りについて、この対応で問題ありませんか？$")
"""`agent-toolkit:atk-mode/playbooks/report-completion.md`の手順が投入する報告用UWIの冒頭の問い。報告用UWIは確認ではないため候補から除く。"""


_WI_PROCESSING_OPERATIONS = frozenset({"start-processing", "adopt", "reject", "unhold"})
"""対象セッションが処理したWIとみなす`atk wi`のサブコマンド。`show`などで読んだだけのWIは含めない。"""


_WI_FILENAME = re.compile(r"\d{8}-\d{6}-\d{3}\.md")


_YES_NO_CHOICES = ("はい", "いいえ")
"""`question_type: yes-no`のUWIで回答画面が示す選択肢。"""


def _processed_wi_filenames(collected: list[_CollectedRecord]) -> set[str]:
    names: set[str] = set()
    for item in collected:
        for call in _record_tool_calls(item.records):
            if call.tool.casefold().rsplit("__", maxsplit=1)[-1].rsplit(".", maxsplit=1)[-1] not in _ADHOC_SHELL_TOOLS:
                continue
            for command in session_evidence_execution.command_inputs(call.tool, call.text):
                commands = session_evidence_execution.shell_simple_commands(command)
                values = {
                    source: session_evidence_static.StaticValues(
                        [simple.words for simple in commands if simple.source == source], source
                    )
                    for source in {simple.source for simple in commands}
                }
                for simple in commands:
                    for words in values[simple.source].variants(simple.words):
                        segment = _resolve_execution_segment(list(words))
                        tokens = list(segment.tokens)
                        if not segment.resolved or len(tokens) < 3:
                            continue
                        if not _is_atk_command(tokens) or tokens[1] != "wi":
                            continue
                        if tokens[2] in _WI_PROCESSING_OPERATIONS:
                            names.update(Path(token).name for token in tokens[3:] if _WI_FILENAME.fullmatch(Path(token).name))
    return names


def _wi_entry(text: str, section: str) -> str:
    lines = [
        line
        for line in _wi_sections.h2_sections(text).get(section, "").splitlines()
        if not (line.strip().startswith("<!--") and line.strip().endswith("-->"))
    ]
    return "\n".join(lines).strip()


def _wi_choices(metadata: dict[str, Any]) -> tuple[str, ...]:
    if metadata.get("question_type") == _wi_constants.QUESTION_TYPE_YES_NO:
        return _YES_NO_CHOICES
    choices = metadata.get("choices")
    if isinstance(choices, str):
        return tuple(choice.strip() for choice in choices.split(",") if choice.strip())
    if isinstance(choices, list):
        return tuple(str(choice).strip() for choice in choices)
    return ()


def _wi_candidate_events(collected: list[_CollectedRecord], session_id: str) -> list[dict[str, Any]]:
    """private-notesのWIから、対象セッションが投入したUWIと、WIの記入欄で行われた是正を返す。

    `submitted-uwi`は`submitter_session`が対象セッションと一致するUWIで、報告用UWIを除く。
    確認をUWIへ送った判断の要否を振り返るため、回答の有無によらず載せる。
    `wi-user-response`は、対象セッションが投入したUWIと`_processed_wi_filenames`が返すWIのうち、
    準備の時点で記入を持つものとする。UWIの`## 回答`は選択肢のいずれかと完全に一致する記入を除き、
    AWIの`## ユーザーコメント`は空でなければ載せる。記録位置を持たないため、`record`へWIのファイル名を示す値を置く。
    private-notesを解決できない環境では空を返す。
    """
    root = _uwi_scan.private_notes_root()
    if root is None:
        return []
    processed = _processed_wi_filenames(collected)
    events: list[dict[str, Any]] = []
    for path in _wi_layout.iter_entry_paths(root):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError, UnicodeError:
            continue
        state = _wi_layout.entry_state_of(path, text)
        parsed = _parse_wi_frontmatter(text)
        metadata = parsed[0] if parsed is not None else {}
        is_uwi = _wi_constants.normalized_wi_type(metadata.get("type")) == _wi_constants.WI_TYPE_UWI
        submitted = is_uwi and metadata.get("submitter_session") == session_id
        question = _wi_sections.title(text) if is_uwi else ""
        if submitted and _REPORT_UWI_QUESTION.search(question):
            continue
        location = {"record": f"wi:{path.name}", "line": 0, "wi_file": path.name, "wi_state": state}
        if submitted:
            events.append({"kind": "submitted-uwi", **location, "tag": "submitted-uwi", "text": question})
        if not submitted and path.name not in processed:
            continue
        section = "回答" if is_uwi else "ユーザーコメント"
        entry = _wi_entry(text, section)
        if entry and not (is_uwi and entry in _wi_choices(metadata)):
            events.append({"kind": "wi-user-response", **location, "tag": section, "wi_section": section, "text": entry})
    return events


def _adhoc_processing_events(collected: list[_CollectedRecord]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in collected:
        entries = {record.line: record.entry for record in item.records}
        for call in _record_tool_calls(item.records):
            inputs = _recorded_input(entries[call.line], call.call_id)
            raw = inputs.get("cmd", inputs.get("command"))
            command = shlex.join(raw) if isinstance(raw, list) and all(isinstance(value, str) for value in raw) else call.text
            if not _is_adhoc_processing(call.tool, command):
                continue
            events.append(
                {
                    "kind": "adhoc-processing",
                    "record": item.record_id,
                    "line": call.line,
                    "timestamp": call.timestamp,
                    "tool": call.tool,
                    "text": call.text,
                }
            )
    return events


def _is_adhoc_processing(tool: str, command: str) -> bool:
    """シェル呼び出しが、その場のコードによる加工に当たるかを判定する。

    対象はシェルのコマンドを実行するツール（Claude Codeの`Bash`、Codexのコマンド実行）の呼び出しとし、
    終了コードによらず成功した呼び出しも含む。次のいずれかに当たるコマンドを加工とする。

    - インタプリタへその場で書いたコードを渡す実行: `python`・`python3`・`uv run … python`への`-c`の引数または
      ヒアドキュメントの標準入力、managed-tempへ書いたスクリプトファイルの`python`・`uv run`による実行、`node -e`
    - 保存されたツール出力の後加工: `atk`が標準出力の代わりに示す保存先（`atk-output-`を含むパス）、
      `atk agents wait`の結果ファイル（`agents-wait-`を含むパス）、ホストが退避したツール結果（`/tool-results/`）と
      バックグラウンドタスクの出力（`/tasks/`配下の`.output`）を、`jq`・`awk`・`cut`・`sed`・Pythonで加工する呼び出し

    リポジトリのファイルや保存出力を`cat`・`rg`で読むだけの呼び出しと、保存出力を含まないパイプラインの
    `cut`・`sed`（検索結果の整形など）は含めない。
    """
    leaf = tool.casefold().rsplit("__", maxsplit=1)[-1].rsplit(".", maxsplit=1)[-1]
    if leaf not in _ADHOC_SHELL_TOOLS:
        return False
    return any(
        session_evidence_execution.processes_output(value) for value in session_evidence_execution.command_inputs(tool, command)
    )
