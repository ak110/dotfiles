# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""合流で記録不足になった重なりの案内が、そのとおりに直せば受理される記録へ導くことを検証する。"""

import json
import pathlib

import pytest
import yaml
from selection_merge_test import _cost, _item, _merge_into_existing, _overlap
from selection_merge_test import fixture_inputs as fixture_inputs


def _guided_records(err: str) -> list[dict[str, object]]:
    """記録不足の案内の括弧内の組を、括弧内のレーン名のとおりに記録する。"""
    records = []
    for line in err.splitlines():
        if "（未判定の組: " not in line:
            continue
        for pair in line.split("（未判定の組: ", 1)[1].removesuffix("）").split("、"):
            left, right = (member.split("（")[1].removesuffix("）") for member in pair.split("と", 1))
            records.append(_overlap(left, right))
    return records


def test_merge_missing_record_guidance_leads_to_accepted_record(
    inputs: tuple[pathlib.Path, pathlib.Path, pathlib.Path], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """合流後の名前で記録しても未判定の組は残り、案内が示す組と仮名のとおりに記録すると合流できる。"""
    repo, existing, added = inputs
    addition = yaml.safe_load(added.read_text(encoding="utf-8"))
    addition["選定"] = [_item("c.md", "lane-03"), _item("d.md", "lane-04")]
    addition["レーンの所要時間"] = [_cost("lane-03"), _cost("lane-04")]
    # 合流後の名前で書いた記録は、合流前の配置では既存のa-bの組を判定するだけになる。
    addition["レーン間の重なり"] = [_overlap("lane-01", "lane-02")]
    added.write_text(yaml.safe_dump(addition, allow_unicode=True), encoding="utf-8")
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"lane-03": "lane-01", "lane-04": "lane-02"}), encoding="utf-8")
    capsys.readouterr()
    assert _merge_into_existing(repo, existing, added, mapping) == 1
    err = capsys.readouterr().err
    assert (
        "（未判定の組: a.md（lane-01）とd.md（lane-04）、b.md（lane-02）とc.md（lane-03）、c.md（lane-03）とd.md（lane-04））"
        in err
    )
    addition["レーン間の重なり"] = _guided_records(err)
    added.write_text(yaml.safe_dump(addition, allow_unicode=True), encoding="utf-8")
    assert _merge_into_existing(repo, existing, added, mapping) == 0, capsys.readouterr().err


__all__ = ["fixture_inputs"]
