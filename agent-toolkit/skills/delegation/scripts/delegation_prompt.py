"""役割宣言と既存の起動・セッション記録から組み込み委譲用の最小本文を生成する。"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Literal

import pydantic

from agent_toolkit._agents_server import launch_requests
from agent_toolkit._common import session_registry
from agent_toolkit._common.next_action import ActionableError


class PromptRequest(pydantic.BaseModel):
    """生成だけを行う要求。history_fileは既存記録のJSONへの参照。"""

    model_config = pydantic.ConfigDict(extra="forbid", strict=True)
    kind: Literal["initial", "continuation"]
    subagent_md_path: str | None = None
    session_id: str | None = None
    history_file: str | None = None
    instruction: str | None = None
    extra_params: dict[str, str] | None = None
    prompt: str | None = None


def generate(request: PromptRequest) -> dict[str, str]:
    """サーバーと同じ検査を通した本文を返す。委譲の起動・配送は行わない。"""
    if request.kind == "initial":
        if not request.subagent_md_path or any((request.session_id, request.history_file, request.instruction, request.prompt)):
            raise ValueError("初回はsubagent_md_pathとextra_paramsだけを指定する")
        prompt = launch_requests.builtin_task_prompt(request.subagent_md_path, request.extra_params or {})
    else:
        if request.session_id and request.history_file:
            raise ValueError("session_idとhistory_fileは排他的に指定する")
        history = session_registry.LaunchInfo()
        if request.session_id:
            resolution = session_registry.resolve(request.session_id)
            info = resolution.resume_info or resolution.handoff_info
            if info is None:
                raise ValueError("session_idの起動記録を解決できない。役割文書か既存記録を指定する")
            history = info.launch_info
        elif request.history_file:
            path = pathlib.Path(request.history_file)
            if not path.is_absolute():
                raise ValueError("history_fileは既存の起動・セッション記録JSONの絶対パスを指定する")
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("history_fileはpromptとfollowup_promptsを保持するJSONオブジェクトが必要")
            history = session_registry.LaunchInfo.from_record(payload)
        if request.subagent_md_path:
            if history.prompt:
                raise ValueError("保持済みの役割とsubagent_md_pathは同時に指定しない")
            document = launch_requests.resolve_task_document_path(request.subagent_md_path)
            history = session_registry.LaunchInfo(prompt=f"{document}の手順を実行せよ。")
        prompt = launch_requests.continuation_prompt(
            history.prompt,
            history.followup_prompts,
            prompt=request.prompt,
            instruction=request.instruction,
            extra_params=request.extra_params,
        )
    return {"kind": request.kind, "prompt": prompt}


def main(argv: list[str] | None = None) -> int:
    """要求JSONの絶対パスから生成結果JSONを返す。"""
    parser = argparse.ArgumentParser(
        description=(
            (__doc__ or "")
            + " 要求JSONはkindをinitialまたはcontinuationにする。initialはsubagent_md_pathと任意のextra_paramsを指定する。"
            "continuationはsession_idまたはhistory_fileの一方を指定し、instructionとextra_params、またはpromptを指定する。"
            "session_idとhistory_fileは併用できず、保持済みの役割があるcontinuationへsubagent_md_pathは指定できない。"
            "生成結果は起動せず、startまたは既存の委譲手段へ渡す本文だけを返す。"
        )
    )
    parser.add_argument("--input", type=pathlib.Path, metavar="PATH", required=True, help="要求JSONの絶対パス")
    args = parser.parse_args(argv)
    try:
        if not args.input.is_absolute():
            raise ValueError("--inputへ絶対パスを指定する")
        request = PromptRequest.model_validate_json(args.input.read_text(encoding="utf-8"))
        result = generate(request)
    except (OSError, ValueError) as error:
        print(
            json.dumps(
                {
                    "error": str(error),
                    "next_action": error.next_action
                    if isinstance(error, ActionableError)
                    else "違反した入力を直し、同じ生成CLIを再実行する",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
