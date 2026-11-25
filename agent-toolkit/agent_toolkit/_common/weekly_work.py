"""週次の作業の定義と、各作業に共通の識別子。  pyfltr: allow-comment"""

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

REVIEW_SESSION_SCOPE = "agent-toolkit:review-session"
RESEARCH_FAILURE_REPORT_THRESHOLD = 2


@dataclass(frozen=True)
class WeeklyWork:
    mode: str
    title: str
    default_enabled: bool
    failure_policy: Literal["sync-before-start", "count-failures"]

    require_saved_result: bool = False

    @property
    def setting(self) -> str:
        return f"weekly_{self.mode.replace('-', '_')}_auto_start"

    @property
    def scope(self) -> str:
        return f"{REVIEW_SESSION_SCOPE} {self.mode}"


DEFINITIONS = MappingProxyType(
    {
        work.mode: work
        for work in (
            WeeklyWork("meta", "メタ分析", True, "sync-before-start"),
            WeeklyWork("research", "外部情報の調査", False, "count-failures"),
            WeeklyWork("architecture", "設計の点検", False, "count-failures", require_saved_result=True),
            WeeklyWork("ui-sweep", "画面の巡回", False, "count-failures", require_saved_result=True),
        )
    }
)
REQUIRED_RESULT_SCOPES = frozenset(work.scope for work in DEFINITIONS.values() if work.require_saved_result)

_SLOT_NAMES = "|".join(map(re.escape, DEFINITIONS))
SLOT_PATTERN = re.compile(rf"(?P<work>{_SLOT_NAMES})@(?P<date>\d{{4}}-\d{{2}}-\d{{2}})")
