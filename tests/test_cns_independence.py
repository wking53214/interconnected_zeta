"""zeta is independent of CNS, and these tests hold whether or not CNS is installed.

Each runs in a fresh interpreter in which ``cns`` is blocked outright
(``sys.modules['cns'] = None`` makes any import of it fail), so the result does not
depend on what the test environment happens to contain. They never skip.
"""
import ast
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "zeta"
BLOCK = "import sys; sys.modules['cns'] = None; sys.modules['cns.gate'] = None\n"
ENV = {"PYTHONPATH": str(ROOT), "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1"}


def run(code: str, *, block: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", (BLOCK if block else "") + textwrap.dedent(code)],
                          capture_output=True, text=True, env=ENV, timeout=120)


def ok(done: subprocess.CompletedProcess) -> None:
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().endswith("ok"), done.stdout


def test_the_package_and_its_connector_import_with_cns_blocked():
    ok(run("import zeta, zeta.cns_connector; print('ok')"))


def test_zeta_still_does_its_job_with_cns_blocked():
    ok(run("""
        from datetime import datetime
        from zeta import Combination, Key, KeySet, LockEvaluator, LockRegistry, LockSpec
        registry = LockRegistry([LockSpec(lock_id="l", required_keys=("a", "b"), combination=Combination.OR, force=True)])
        ev = LockEvaluator(registry)
        result = ev.evaluate("e", KeySet([Key("a", True)]), "l", datetime(2026, 1, 1))
        assert result.open and result.forced and result.changed, result
        assert ev.state_store.snapshot("e")["l"].open
        print('ok')
    """))


def test_the_connector_says_what_is_missing_when_cns_is_blocked():
    ok(run("""
        from datetime import datetime
        from zeta import Key, KeySet, LockEvaluator, LockRegistry, LockSpec
        from zeta.cns_connector import (
            CnsNotInstalled, binds, cns_available, lock_result_content, lock_result_digest, snapshot_content,
            snapshot_digest)
        assert cns_available() is False
        ev = LockEvaluator(LockRegistry([LockSpec(lock_id="l", required_keys=("a",))]))
        result = ev.evaluate("e", KeySet([Key("a", True)]), "l", datetime(2026, 1, 1))
        snap = ev.state_store.snapshot("e")
        assert lock_result_content(result)["lock_id"] == "l"      # content is pure and needs no cns
        assert "l" in snapshot_content(snap)["locks"]
        for call in (lambda: lock_result_digest(result), lambda: snapshot_digest(snap),
                     lambda: binds(object(), result), lambda: binds(object(), snap)):
            try:
                call()
            except CnsNotInstalled as e:
                assert isinstance(e, ImportError)
                assert "pip install 'zeta[cns]" in str(e) and "zeta itself works without it" in str(e)
            else:
                raise SystemExit("computed a digest without cns")
        print('ok')
    """))


def test_importing_the_package_does_not_load_cns_even_when_it_is_installed():
    ok(run("""
        import sys
        import zeta, zeta.cns_connector
        assert not [m for m in sys.modules if m == "cns" or m.startswith("cns.")], "cns was loaded"
        print('ok')
    """, block=False))


def test_no_module_of_the_package_imports_cns_by_statement():
    offenders = {}
    modules = sorted(PACKAGE.glob("*.py"))
    assert "cns_connector.py" in [p.name for p in modules]
    for path in modules:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module else []
            bad = [n for n in names if n == "cns" or n.startswith("cns.")]
            if bad:
                offenders.setdefault(path.name, []).extend(bad)
    assert offenders == {}


def _toml_list(section: str, key: str) -> list:
    """The quoted items of ``key = [...]`` inside ``[section]``; a text check, as tomllib is 3.11+."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    body, inside = [], False
    for line in text.splitlines():
        header = re.match(r"^\[([^\]]+)\]\s*$", line)
        if header:
            inside = header.group(1).strip() == section
        elif inside:
            body.append(line)
    found = re.search(r"^%s\s*=\s*\[(.*?)\]\s*$" % re.escape(key), "\n".join(body), re.S | re.M)
    assert found, f"{key} not found in [{section}]"
    return re.findall(r'"([^"]*)"', re.sub(r"#[^\n]*", "", found.group(1)))


def test_the_package_declares_no_runtime_dependency_on_cns_and_pins_the_cns_extra():
    runtime = _toml_list("project", "dependencies")
    assert not [d for d in runtime if re.match(r"\s*cns\b", d, re.I)], runtime
    assert _toml_list("project.optional-dependencies", "cns") == ["cns @ git+https://github.com/wking53214/CNS.git@v1.8.0"]
    assert not [d for d in _toml_list("project.optional-dependencies", "dev") if re.match(r"\s*cns\b", d, re.I)]
