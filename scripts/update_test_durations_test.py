"""JUnit全分割からの所要生成の契約を確かめる。"""

import json
import sys
from pathlib import Path

import pytest
import update_test_durations


def _inputs(tmp_path: Path) -> list[Path]:
    paths = []
    for index, duration in enumerate((0.999, 1.0, 3.0)):
        path = tmp_path / f"{index}.xml"
        path.write_text(
            f'<testsuites><testsuite><testcase classname="pkg.sample_test.Class" '
            f'name="test_value[{index}]" time="{duration}"/></testsuite></testsuites>',
            encoding="utf-8",
        )
        paths.append(path)
    return paths


def test_generate_threshold_and_reproducibility(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """1秒境界と順序非依存を公開CLI入口から確認する。"""
    paths = _inputs(tmp_path)
    output = tmp_path / "durations.json"
    monkeypatch.setattr(sys, "argv", ["update_test_durations", *map(str, paths), "--output", str(output)])
    assert update_test_durations.main() == 0
    first = output.read_bytes()
    assert json.loads(first) == {
        "pkg/sample_test.py::Class::test_value[1]": 1.0,
        "pkg/sample_test.py::Class::test_value[2]": 3.0,
    }
    monkeypatch.setattr(sys, "argv", ["update_test_durations", *map(str, reversed(paths)), "--output", str(output)])
    assert update_test_durations.main() == 0
    assert output.read_bytes() == first


@pytest.mark.parametrize("value", ["-1", "nan", "inf", "bad"])
def test_invalid_duration_rejected(
    tmp_path: Path, value: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """有限の非負所要以外を記録へ混ぜない。"""
    paths = _inputs(tmp_path)
    paths[0].write_text(paths[0].read_text().replace('time="0.999"', f'time="{value}"'))
    monkeypatch.setattr(sys, "argv", ["update_test_durations", *map(str, paths), "--output", str(tmp_path / "out.json")])
    with pytest.raises(SystemExit) as error:
        update_test_durations.main()
    assert error.value.code == 2
    message = capsys.readouterr().err
    assert "試験所要が不正" in message
    assert "成功run" in message and message.count("再実行してください") == 1


@pytest.mark.parametrize(
    "mode",
    ["duplicate-path", "duplicate-test", "missing-shard", "xml", "empty", "failed", "identifier", "read", "save"],
)
def test_invalid_inputs_rejected(
    tmp_path: Path, mode: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """不完全・重複・破損・未成功の入力を診断する。"""
    paths = _inputs(tmp_path)
    output = tmp_path / "out.json"
    if mode == "duplicate-path":
        paths[1] = paths[0]
    elif mode == "duplicate-test":
        paths[1].write_bytes(paths[0].read_bytes())
    elif mode == "missing-shard":
        paths.pop()
    elif mode == "read":
        paths[0].unlink()
    elif mode == "save":
        output = tmp_path / "missing" / "out.json"
    else:
        text = {
            "xml": "<",
            "empty": "<testsuites/>",
            "failed": '<testsuite><testcase classname="x_test" name="test_x" time="1"><failure/></testcase></testsuite>',
            "identifier": '<testsuite><testcase classname="invalid" name="test_x" time="1"/></testsuite>',
        }[mode]
        paths[0].write_text(text, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["update_test_durations", *map(str, paths), "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        update_test_durations.main()
    assert error.value.code == 2
    message = capsys.readouterr().err
    causes = {
        "duplicate-path": "異なる全3shard",
        "duplicate-test": "重複",
        "missing-shard": "required",
        "xml": "XMLが不正",
        "empty": "試験がありません",
        "failed": "未成功",
        "identifier": "識別子が不正",
        "read": "読み取れません",
        "save": "保存できません",
    }
    assert causes[mode] in message
    if mode == "missing-shard":
        assert "junit junit junit" in message
    else:
        assert message.count("再実行してください") == 1
        assert {"read": "読取権限", "save": "書込権限"}.get(mode, "再取得") in message
