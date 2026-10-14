"""The repository goes public with its history: the scan must pass now, and must fire on what it guards against."""

import subprocess

import pytest

from valiwatt import attribution
from valiwatt.paths import REPO_ROOT
from valiwatt.report.render import ReportRefused, assert_public_safe, fmt_num
from valiwatt.safety import ScanError, main, private_patterns, scan, scan_history, scan_index, scan_names, scan_text


def test_staged_tree_is_public_safe():
    assert scan_index(REPO_ROOT, private_patterns(REPO_ROOT)) == []


def test_commit_history_is_public_safe():
    # Fails while any commit message names something private; the fix is a history rewrite the owner approves.
    assert scan_history(REPO_ROOT, private_patterns(REPO_ROOT)) == []


def test_scan_fires_on_private_content():
    samples = [
        "/ho" + "me/someone/project", "gh" + "p_" + "a" * 36, "-----BEGIN " + "RSA PRIVATE KEY-----",
        "/tmp/clau" + "de-1000/x", "s" + "k-" + "a" * 24, "AK" + "IA" + "A" * 16, "xo" + "xb-" + "1" * 12,
        "C:" + "\\Users\\x", "/Us" + "ers/someone", "github" + "_pat_" + "a" * 22,
    ]
    for text in samples:
        assert scan_text("f.md", f"line\n{text}\n"), text


def test_scan_fires_on_forbidden_files():
    names = ["data/raw/eccc/7025251/2019-01.csv", "ESTATE.local.md", "docs/audits/run.md", "x.args.json",
             "mlruns/mlflow.db", "mlartifacts/x", "reports/dq.fr.md", "data/interim/demand.parquet", ".env",
             "prod.env", ".env.local", ".private-patterns"]
    assert len(scan_names(names)) == len(names)
    assert scan_names(["src/valiwatt/cli.py", "docs/data-licences.md"]) == []


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    return tmp_path, git


def test_scan_reads_the_staged_blob_not_the_working_copy(repo):
    root, git = repo
    (root / "a.md").write_text("token = gh" + "p_" + "b" * 36 + "\n")
    git("add", "a.md")
    (root / "a.md").write_text("clean\n")
    assert scan_index(root), "a staged secret hidden by a clean working copy passed the scan"


def test_scan_reads_commit_messages_and_private_patterns(repo):
    root, git = repo
    (root / "a.md").write_text("x\n")
    git("add", "a.md")
    git("commit", "-q", "-m", "made from the " + "secret-place seat")
    (root / ".private-patterns").write_text("# comment\nsecret-place\n")
    issues = scan_history(root, private_patterns(root))
    assert issues and "message" in issues[0]
    assert scan_history(root, {}) == []


def test_rendered_report_refuses_a_local_path():
    assert_public_safe("clean text, ratio 4:1")
    for leak in (f"{REPO_ROOT}/mlruns", "/ho" + "me/x/y", "/tmp/x", "file:///x", "C:\\Users\\x", "D:\\data"):
        with pytest.raises(ReportRefused):
            assert_public_safe(f"artifact at {leak}")


def test_public_surfaces_carry_the_attribution_lines():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for line in attribution.lines("en"):
        assert line in readme, line


def test_number_format():
    assert fmt_num(42472.83, 2, "fr") == "42\u202f472,83"
    assert fmt_num(-26.1, 1, "fr") == "\u221226,1"
    assert fmt_num(-0.04, 1, "en") == "0.0"


def test_signed_zero_has_no_sign():
    from valiwatt.report.render import filters

    signed = filters("fr")["signed"]
    assert signed(-0.046, 1) == "0,0" and signed(-4.1, 1) == "\u22124,1" and signed(0.6, 1) == "+0,6"


def test_untracked_code_marks_a_run_dirty(repo, monkeypatch):
    from valiwatt import tracking

    root, git = repo
    (root / "README.md").write_text("x\n")
    git("add", "README.md")
    git("commit", "-q", "-m", "init")
    monkeypatch.setattr(tracking, "REPO_ROOT", root)
    assert tracking.git_state()["git_dirty"] == "false"
    (root / "src").mkdir()
    (root / "src" / "new.py").write_text("print(1)\n")
    state = tracking.git_state()
    assert state["git_dirty"] == "true" and len(state["code_sha256"]) == 64


def test_scan_flags_forbidden_binary_and_missing_staged_files(repo):
    root, git = repo
    (root / "data").mkdir()
    (root / "data" / "x.parquet").write_bytes(b"\xff\xfe\x00binary")
    (root / "a.md").write_text("x\n")
    git("add", "-f", "data/x.parquet", "a.md")
    (root / "a.md").unlink()
    issues = "\n".join(scan_index(root))
    assert "must never be tracked" in issues and "binary file tracked" in issues and "staged but missing" in issues


def test_full_scan_includes_the_history(repo):
    root, git = repo
    (root / "a.md").write_text("x\n")
    git("add", "a.md")
    git("commit", "-q", "-m", "path /ho" + "me/someone/x")
    assert any("message" in i for i in scan(root))


def test_scan_refuses_outside_a_repository(tmp_path):
    with pytest.raises(ScanError):
        scan(tmp_path)


def test_cli_refuses_without_private_patterns(monkeypatch):
    import valiwatt.safety as sf

    monkeypatch.setattr(sf, "private_patterns", lambda root=None: {})
    assert main() == 2
