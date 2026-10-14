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
