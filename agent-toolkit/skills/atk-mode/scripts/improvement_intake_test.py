"""同じ受付資料と独立した観測を、正式WIを作らずに区別する。"""

import json

import improvement_intake


def test_capture_retry_and_explicit_decision(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(improvement_intake, "state_dir", lambda: tmp_path / "state")
    body = tmp_path / "input.json"
    report = {"target_repo": "github.com/example/repo", "intent": "試験", "observation": "停止した", "impact": "結果が出ない"}

    def capture(observation):
        body.write_text(json.dumps({**report, "observation": observation}), encoding="utf-8")
        assert improvement_intake.main(["capture", "--body-file", str(body)]) == 0
        return json.loads(capsys.readouterr().out)["id"]

    first = capture("停止した")
    assert capture("停止した") == first
    second = capture("別の入力でも停止した")
    assert second != first
    query = ["list", "--target-repo", report["target_repo"]]
    assert improvement_intake.main(query) == 0
    assert all("decision" not in row for row in json.loads(capsys.readouterr().out))
    update = [
        "decide",
        "--id",
        first,
        "--id",
        second,
        "--status",
        "covered",
        "--wi",
        "20261122-000000-001.md",
        "--reason",
        "既存の項目に観測範囲がある",
    ]
    assert improvement_intake.main(update) == 0
    assert improvement_intake.main(update) == 0
    capsys.readouterr()
    assert improvement_intake.main(query) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {row["id"] for row in rows} == {first, second}
    assert {row["decision"]["wi"] for row in rows} == {"20261122-000000-001.md"}


def test_capture_failed_write_is_not_reported_as_saved(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(improvement_intake, "state_dir", lambda: tmp_path / "state")

    def fail(*_args, **_kwargs):
        raise OSError("観測用の保存障害")

    monkeypatch.setattr(improvement_intake, "atomic_write", fail)
    body = tmp_path / "input.json"
    body.write_text('{"target_repo":"repo","intent":"試験","observation":"停止","impact":"遅延"}', encoding="utf-8")
    assert improvement_intake.main(["capture", "--body-file", str(body)]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert "完了できない" in captured.err
