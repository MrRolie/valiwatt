"""The breaks run counts a break as fired only when its test ran and passed."""

from valiwatt.breaks import BREAKS, GROUPS, Break, _outcomes, run_tests


def test_a_break_that_does_not_exist_is_not_counted_as_fired():
    rows = run_tests([Break("lookahead", "tests/test_guards.py::test_no_such_break", "x", "x")])
    assert rows[0]["outcome"] == "not_run"


def test_junit_outcomes_read_failures_and_parameters(tmp_path):
    xml = tmp_path / "r.xml"
    xml.write_text('<testsuites><testsuite>'
                   '<testcase classname="tests.test_a" name="test_x[1]"/>'
                   '<testcase classname="tests.test_a" name="test_y"><failure message="m"/></testcase>'
                   '<testcase classname="tests.test_a" name="test_z"><skipped/></testcase>'
                   '</testsuite></testsuites>')
    assert _outcomes(xml) == {"tests/test_a.py::test_x[1]": "passed", "tests/test_a.py::test_y": "failed",
                              "tests/test_a.py::test_z": "skipped"}


def test_every_break_names_a_known_group_and_both_languages():
    assert len({b.test for b in BREAKS}) == len(BREAKS)
    assert all(b.group in GROUPS and b.fr and b.en for b in BREAKS)


def test_every_break_names_a_test_node_pytest_collects():
    """A break's id must be a node pytest reports under that exact name (a parametrized test needs its parameter id):
    the breaks run looks each id up in the junit outcomes, and an id that matches no node counts as not fired."""
    import subprocess
    import sys

    from valiwatt.paths import REPO_ROOT

    files = sorted({b.test.split("::")[0] for b in BREAKS})
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *files],
                         cwd=REPO_ROOT, capture_output=True, text=True, check=False).stdout
    collected = {line.strip() for line in out.splitlines() if "::" in line}
    assert collected, out[-2000:]
    assert sorted(b.test for b in BREAKS if b.test not in collected) == []


def test_a_breaks_run_with_a_break_that_did_not_fire_is_marked_failed(tmp_path, monkeypatch):
    from functools import partial

    from valiwatt import breaks, tracking

    monkeypatch.setattr(tracking, "MLRUNS", tmp_path / "mlruns")
    missing = Break("lookahead", "tests/test_guards.py::test_no_such_break", "x", "x")
    monkeypatch.setattr(breaks, "run_tests", partial(run_tests, [missing]))
    assert breaks.run_breaks() == 1
    run = tracking.latest_run(breaks.EXPERIMENT)
    assert run.data.tags["status"] == "failed"
    assert run.data.metrics["fired"] == 0 and run.data.metrics["not_fired"] == 1
