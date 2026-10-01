"""Key: a named, scored signal detected in entity data.

Generalizes the ad hoc named booleans already computed inline in the
governance stack (e.g. the hard-rule and syndrome flags in the original
private implementation) into a first-class, reusable unit that a Lock can
reference by name instead of each caller re-deriving its own booleans.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Set


@dataclass(frozen=True)
class Key:
    """One detected pattern, with a confidence score and the reason it fired."""

    name: str
    present: bool
    confidence: float = 1.0
    reason: str = ""

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"Key '{self.name}': confidence must be in [0.0, 1.0], got {self.confidence}"
            )


class KeySet:
    """All Keys observed for one entity at one reference time.

    `min_confidence` lets a caller require a minimum confidence before a
    Key counts as "present" for lock evaluation, without needing to filter
    Keys before construction.
    """

    def __init__(self, keys: Iterable[Key], min_confidence: float = 0.0) -> None:
        if not (0.0 <= min_confidence <= 1.0):
            raise ValueError(f"min_confidence must be in [0.0, 1.0], got {min_confidence}")
        self.min_confidence = min_confidence
        self._by_name: Dict[str, Key] = {}
        for k in keys:
            self._by_name[k.name] = k

    def get(self, name: str) -> Optional[Key]:
        return self._by_name.get(name)

    def is_present(self, name: str) -> bool:
        k = self._by_name.get(name)
        if k is None:
            return False
        return k.present and k.confidence >= self.min_confidence

    def names_present(self) -> Set[str]:
        return {
            name
            for name, k in self._by_name.items()
            if k.present and k.confidence >= self.min_confidence
        }

    def __len__(self) -> int:
        return len(self._by_name)

    def __repr__(self) -> str:
        return f"KeySet({sorted(self.names_present())})"
