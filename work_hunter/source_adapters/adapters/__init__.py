from __future__ import annotations

from .careerspace import CareerspaceAdapter
from .geekjob import GeekJobAdapter
from .getmatch import GetmatchAdapter
from .habr import HabrAdapter
from .hh import HHAdapter
from .hirehi import HirehiAdapter
from .jabka import JabkaAdapter

__all__ = [
    "HHAdapter",
    "GeekJobAdapter",
    "HabrAdapter",
    "GetmatchAdapter",
    "HirehiAdapter",
    "CareerspaceAdapter",
    "JabkaAdapter",
]
