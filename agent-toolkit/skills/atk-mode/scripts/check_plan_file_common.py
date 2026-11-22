from __future__ import annotations

import typing

type _WarningKind = typing.Literal["migration", "advisory"]


type _ClassifiedWarning = tuple[_WarningKind, str]
