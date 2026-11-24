import pytest

from agent_toolkit._common import operation_approvals as approvals


def _request() -> dict:
    return {
        "request_id": "r1",
        "tool_name": "Bash",
        "tool_input": {"command": "echo ok"},
        "cwd": "/repo",
        "target_repo": "example/repo",
        "scope": "once",
        "allow_choice": "許可する",
    }


def test_exact_input_and_consumption() -> None:
    request = _request()
    metadata = {"permission_request": request, "choices": ["許可する", "拒否する"]}
    question = approvals.request_display(request)
    approvals.bind_answer(metadata, question, "許可する")
    assert approvals.matches(metadata, question, "許可する", request)
    assert not approvals.matches(metadata, question, "許可する", {**request, "tool_input": {"command": "echo other"}})
    assert approvals.consume(metadata, question, "許可する", request)
    approvals.bind_answer(metadata, question, "許可する")
    assert not approvals.matches(metadata, question, "許可する", request)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tool_name", "PowerShell"),
        ("tool_input", {"command": "echo other"}),
        ("cwd", "/other"),
        ("target_repo", "example/other"),
    ],
)
def test_approval_does_not_match_operation_differing_in_one_field(field: str, value: object) -> None:
    request = _request()
    metadata = {"permission_request": request, "choices": ["許可する", "拒否する"]}
    question = approvals.request_display(request)
    approvals.bind_answer(metadata, question, "許可する")
    other = {**request, field: value}
    assert not approvals.matches(metadata, question, "許可する", other)
    assert not approvals.consume(metadata, question, "許可する", other)
    assert approvals.matches(metadata, question, "許可する", request)


def test_question_and_free_text_cannot_grant() -> None:
    request = _request()
    metadata = {"permission_request": request, "choices": ["許可する", "拒否する"]}
    question = approvals.request_display(request)
    assert not approvals.matches(metadata, question, "許可する", request)
    for answer in ("", "続けて", "拒否する"):
        approvals.bind_answer(metadata, question, answer)
        assert not approvals.matches(metadata, question, answer, request)


def test_bounded_repeat_and_public_choice_format() -> None:
    request = {
        "request_id": "repeat",
        "tool_name": "REPL",
        "tool_input": {"code": "run()"},
        "cwd": "/repo",
        "target_repo": "example/repo",
        "scope": "count:2",
        "allow_choice": "許可する (Recommended)",
    }
    metadata = {"permission_request": request, "choices": "許可する (Recommended),拒否する"}
    question = approvals.request_display(request)
    answer = "許可する"
    approvals.bind_answer(metadata, question, answer)
    assert approvals.consume(metadata, question, answer, request)
    approvals.bind_answer(metadata, question, answer)
    assert approvals.consume(metadata, question, answer, request)
    approvals.bind_answer(metadata, question, answer)
    assert not approvals.consume(metadata, question, answer, request)
    assert not approvals.matches(metadata, question, answer, {**request, "tool_input": {"code": "other()"}})


def test_legacy_approval_requires_one_verbatim_operation() -> None:
    request = {
        "request_id": "legacy",
        "tool_name": "Bash",
        "tool_input": {"command": "rm /repo/item"},
        "cwd": "/repo",
        "target_repo": "example/repo",
        "scope": "once",
        "allow_choice": "許可する",
    }
    metadata = {"choices": "許可する,拒否する"}
    question = "`Bash`で`rm /repo/item`を`/repo`から実行する今回の操作を許可するか。"
    assert approvals.legacy_request(metadata, question, "許可する", [request]) == request
    for answer in ("", "続けて", "拒否する", "許可しない"):
        assert approvals.legacy_request(metadata, question, answer, [request]) is None
    for changed in (
        {**request, "tool_input": {"command": "rm /repo/ite"}},
        {**request, "cwd": "/other", "target_repo": "other/repo"},
        {**request, "scope": "count:2"},
        {**request, "tool_input": {"command": "rm /repo/item", "run_in_background": True}},
    ):
        assert approvals.legacy_request(metadata, question, "許可する", [changed]) is None
    assert approvals.legacy_request(metadata, question, "許可する", [request, {**request, "request_id": "other"}]) is None
    assert (
        approvals.legacy_request(
            {**metadata, "legacy_operation_approval": {"state": "consumed"}},
            question,
            "許可する",
            [request],
        )
        is None
    )
