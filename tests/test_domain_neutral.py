"""The package describes a generic lock engine: no domain's vocabulary appears in its code or comments as an example.

Provenance is kept on purpose: lines that say where something was extracted from (they name OBSERVE or observe_consolidated.py) may use the
original system's words, because that is true history. Tests and examples may use domain strings as data; this guard covers only the package.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOMAIN_WORDS = re.compile(r"patient|sepsis|septic|discharge|vitals?\b|tachy|fever|hypovol|respiratory|oxygen|neonat|infant", re.IGNORECASE)
PROVENANCE = re.compile(r"OBSERVE|observe_consolidated")


def test_the_package_uses_no_domain_example_outside_provenance_lines():
    offenders = []
    for path in sorted((ROOT / "zeta").glob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if DOMAIN_WORDS.search(line) and not PROVENANCE.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, offenders


def test_the_readme_examples_name_no_domain():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    start = text.index("```python")
    block = text[start:text.index("```", start + 9)]
    assert not DOMAIN_WORDS.search(block), DOMAIN_WORDS.findall(block)
