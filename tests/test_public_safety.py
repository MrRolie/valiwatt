"""The repository goes public with its history: the scan must pass now, and must fire on what it guards against."""

import struct
import subprocess
import zlib

import pytest

from valiwatt import attribution
from valiwatt.paths import REPO_ROOT
from valiwatt.report.render import ReportRefused, assert_public_safe, fmt_num
from valiwatt.safety import (
    PNG_SIGNATURE,
    ScanError,
    main,
    png_text,
    private_patterns,
    scan,
    scan_history,
    scan_index,
    scan_names,
    scan_text,
)


def test_staged_tree_is_public_safe():
    assert scan_index(REPO_ROOT, private_patterns(REPO_ROOT)) == []


def test_commit_history_is_public_safe():
    # Fails while any file, commit message or tag message in the history holds something private; the fix is a
    # history rewrite the owner approves.
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


def chunk(kind, body):
    return len(body).to_bytes(4, "big") + kind + body + zlib.crc32(kind + body).to_bytes(4, "big")


def ihdr(width=1, height=1, depth=8, colour=0, interlace=0):
    return struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0, interlace)


IDAT = zlib.compress(b"\0\0")  # the one row of a 1x1 8-bit grey image: filter type 0, then one pixel


def png(*chunks, tail=b"", header=ihdr(), idat=IDAT, iend=b""):
    """IHDR, the given chunks, IDAT, then IEND; header=None or idat=None leaves that chunk out."""
    return (PNG_SIGNATURE + (chunk(b"IHDR", header) if header is not None else b"")
            + b"".join(chunk(k, b) for k, b in chunks) + (chunk(b"IDAT", idat) if idat is not None else b"")
            + chunk(b"IEND", iend) + tail)


def test_a_report_figure_passes_only_when_well_formed_with_clean_metadata(repo):
    """The report's PNG figures are the one binary the repository carries: a figure whose metadata holds a private
    path (plain or compressed), one with bytes after its end, and a PNG anywhere else are all refused."""
    root, git = repo
    path = ("/ho" + "me/someone/notes").encode()
    figures = root / "docs" / "report" / "figures" / "en"
    figures.mkdir(parents=True)
    cases = {"clean": png((b"tEXt", b"Software\0Matplotlib")), "plain": png((b"tEXt", b"Comment\0" + path)),
             "compressed": png((b"zTXt", b"Comment\0\0" + zlib.compress(path))),
             "international": png((b"iTXt", b"Comment\0\x01\0\0\0" + zlib.compress(path))),
             "tail": png(tail=b"hidden")}
    for name, data in cases.items():
        (figures / f"{name}.png").write_bytes(data)
    (root / "elsewhere.png").write_bytes(cases["clean"])
    git("add", "-f", "docs/report/figures", "elsewhere.png")
    issues = scan_index(root)
    flagged = lambda name: [i for i in issues if i.startswith(name)]  # noqa: E731
    assert flagged("docs/report/figures/en/clean.png") == []
    for name in ("plain", "compressed", "international"):
        assert flagged(f"docs/report/figures/en/{name}.png") == [f"docs/report/figures/en/{name}.png:1: home path"]
    assert flagged("docs/report/figures/en/tail.png") == ["docs/report/figures/en/tail.png: binary file tracked"]
    assert flagged("elsewhere.png") == ["elsewhere.png: binary file tracked"]


def test_a_figure_with_an_unknown_chunk_or_a_bad_checksum_is_refused(repo):
    """Only the standard chunk types are read or allowed: an eXIf block or a private chunk would carry bytes the scan
    never reads, and a chunk whose checksum is wrong is not well formed."""
    root, git = repo
    figures = root / "docs" / "report" / "figures"
    figures.mkdir(parents=True)
    clean = png((b"tEXt", b"Software\0Matplotlib"))
    bad_crc = bytearray(clean)
    text_end = clean.index(b"tEXt") + 4 + len(b"Software\0Matplotlib") + 4
    bad_crc[text_end - 1] ^= 0xFF  # the last byte of the tEXt chunk's checksum
    cases = {"standard": png((b"sRGB", b"\0"), (b"pHYs", bytes(9))), "exif": png((b"eXIf", b"MM\0*")),
             "private": png((b"prVt", b"x")), "crc": bytes(bad_crc)}
    for name, data in cases.items():
        (figures / f"{name}.png").write_bytes(data)
    assert png_text(clean).split("\n")[0] == "Software Matplotlib" and png_text(bytes(bad_crc)) is None
    git("add", "-f", "docs/report/figures")
    issues = scan_index(root)
    assert issues == [f"docs/report/figures/{n}.png: binary file tracked" for n in ("crc", "exif", "private")]


def test_a_figure_whose_structure_breaks_the_png_rules_is_refused(repo):
    """The scan reads every byte of a figure as text except its image data, so the structure must leave nothing to hide:
    a payload in IEND, in a fixed-length chunk or in IHDR, a missing or second IHDR, missing or split image data, and
    image data that is not one zlib stream inflating to exactly the size IHDR implies are all refused. The controls, a
    grey image with text after its image data and a palette image, pass."""
    root, git = repo
    path = ("/ho" + "me/someone/notes").encode()
    cases = {
        "iend_payload": png(iend=path),
        "phys_oversized": png((b"pHYs", bytes(9) + path)),
        "ihdr_oversized": png(header=ihdr() + path),
        "no_ihdr": png((b"tEXt", b"Software\0Matplotlib"), header=None),
        "only_iend": png(header=None, idat=None, iend=path),
        "two_ihdr": png((b"IHDR", ihdr())),
        "two_phys": png((b"pHYs", bytes(9)), (b"pHYs", bytes(9))),
        "no_idat": png(idat=None),
        "idat_split": png((b"IDAT", IDAT[:4]), (b"tEXt", b"a\0b"), idat=IDAT[4:]),
        "phys_after_idat": PNG_SIGNATURE + chunk(b"IHDR", ihdr()) + chunk(b"IDAT", IDAT) + chunk(b"pHYs", bytes(9))
        + chunk(b"IEND", b""),
        "idat_raw_text": png(idat=path),
        "idat_compressed_text": png(idat=zlib.compress(path)),
        "idat_short": png(idat=zlib.compress(b"\0")),
        "idat_trailing": png(idat=IDAT + path),
        "idat_bad_filter": png(idat=zlib.compress(b"\x05\0")),
        "zero_width": png(header=ihdr(width=0), idat=zlib.compress(b"\0")),  # the one filter byte a 0-wide row has
        "interlaced": png(header=ihdr(interlace=1)),
        "bad_depth": png(header=ihdr(depth=3)),
        "palette_without_plte": png(header=ihdr(colour=3)),
        "ztxt_trailing": png((b"zTXt", b"Comment\0\0" + zlib.compress(b"clean") + path)),
        "ztxt_too_large": png((b"zTXt", b"Comment\0\0" + zlib.compress(b"a" * (2 << 20)))),
        "itxt_trailing": png((b"iTXt", b"C\0\x01\0\0\0" + zlib.compress(b"x") + path)),
        "itxt_bad_method": png((b"iTXt", b"C\0\x01\x01\0\0" + zlib.compress(b"x"))),
        "trns_before_plte": png((b"tRNS", b"\xff"), (b"PLTE", bytes(3)), header=ihdr(colour=3)),
        "srgb_after_plte": png((b"PLTE", bytes(3)), (b"sRGB", b"\0"), header=ihdr(colour=3)),
        "palette_too_large": png((b"PLTE", bytes(9)), header=ihdr(depth=1, colour=3)),
    }
    controls = {
        "text_after_idat": PNG_SIGNATURE + chunk(b"IHDR", ihdr()) + chunk(b"IDAT", IDAT)
        + chunk(b"tEXt", b"Software\0Matplotlib") + chunk(b"IEND", b""),
        "palette": png((b"PLTE", bytes(3)), (b"tRNS", b"\xff"), header=ihdr(colour=3)),
    }
    for name, data in cases.items():
        assert png_text(data) is None, name
    for name, data in controls.items():
        assert png_text(data) is not None, name
    figures = root / "docs" / "report" / "figures"
    figures.mkdir(parents=True)
    for name, data in {**cases, **controls}.items():
        (figures / f"{name}.png").write_bytes(data)
    git("add", "-f", "docs/report/figures")
    assert scan_index(root) == [f"docs/report/figures/{n}.png: binary file tracked" for n in sorted(cases)]


def test_a_fixed_length_chunk_is_read_by_the_scan(repo):
    """The bytes of a chunk other than IDAT and the text chunks are read too: a home path squeezed into the 32 bytes of
    a cHRM chunk is caught."""
    path = ("/ho" + "me/someone/n").encode().ljust(32, b"x")
    assert scan_text("f.png", png_text(png((b"cHRM", path)))) == ["f.png:2: home path"]


def test_scan_fires_on_home_shorthands_but_not_on_prose():
    for text in ("~" + "/notes", "cd ~" + "/.config", "$HO" + "ME/project", "${HO" + "ME}/project"):
        assert scan_text("f.md", f"line\n{text}\n") == ["f.md:2: home path"], text
    for text in ("about ~3 GW", "~ /", "~" + "/ alone", "$HO" + "ME is set", "${HO" + "ME}", "a/b~c"):
        assert scan_text("f.md", text) == [], text


def test_history_scan_reads_deleted_files_and_leftover_refs(repo):
    """A file deleted or untracked later, a forbidden path, a binary file and a commit only a leftover ref reaches all
    stay in the history; the staged tree no longer shows any of them."""
    root, git = repo
    (root / "data").mkdir()
    (root / "notes.md").write_text("see /ho" + "me/someone/notes\n")
    (root / "data" / "raw.csv").write_text("x\n")
    (root / "a.md").write_text("x\n")  # the same blob as data/raw.csv, under a path that may be tracked
    (root / "blob.bin").write_bytes(b"\xff\xfe\x00binary")
    git("add", "-f", "notes.md", "data/raw.csv", "a.md", "blob.bin")
    git("commit", "-q", "-m", "first")
    git("rm", "-q", "--cached", "notes.md", "data/raw.csv", "blob.bin")
    git("commit", "-q", "-m", "second")
    (root / "c.md").write_text("token gh" + "p_" + "c" * 36 + "\n")
    git("add", "c.md")
    git("commit", "-q", "-m", "third")
    git("update-ref", "refs/original/refs/heads/leftover", "HEAD")
    git("reset", "-q", "--hard", "HEAD~1")
    assert scan_index(root) == []
    issues = scan_history(root)
    expected = ["notes.md (blob ", "data/raw.csv (in the history): must never be tracked", "blob.bin (blob ",
                "c.md (blob "]
    for start in expected:
        assert any(i.startswith(start) for i in issues), start
    assert any(i.startswith("notes.md") and i.endswith(":1: home path") for i in issues)
    assert any(i.startswith("blob.bin") and i.endswith(": binary file in the history") for i in issues)
    assert any(i.startswith("c.md") and i.endswith(":1: GitHub token") for i in issues)
    assert not any(i.startswith("a.md") for i in issues)  # control: the clean path to the same blob passes


def test_history_scan_reads_annotated_tag_messages(repo):
    root, git = repo
    (root / "a.md").write_text("x\n")
    git("add", "a.md")
    git("commit", "-q", "-m", "init")
    git("tag", "-a", "v1", "-m", "frozen\n\nmade at /ho" + "me/someone/x")
    git("tag", "light")
    assert scan_history(root) == ["tag v1 message:3: home path"]


def test_an_agent_session_url_in_a_commit_message_is_caught(repo):
    """A commit message ending with an agent session's address is caught in the history, and so is the address in a
    staged file or a tag message; a message without one passes. No allowlist: no such address may remain."""
    root, git = repo
    url = "https://claude" + ".ai/code/" + "session_" + "01AbCdEfGhIjKlMnOpQrStUv"
    (root / "a.md").write_text("x\n")
    git("add", "a.md")
    git("commit", "-q", "-m", "clean subject\n\nbody\n\nCo-Authored-By: someone <x@example.invalid>")
    assert scan_history(root) == []
    git("commit", "-q", "--allow-empty", "-m", f"subject\n\nbody\n\nClaude-Session: {url}")
    git("tag", "-a", "v1", "-m", f"see {url}")
    (root / "b.md").write_text(f"made in {url}\n")
    git("add", "b.md")
    history = scan_history(root)
    assert any(i.endswith("message:5: agent session URL") and i.startswith("commit ") for i in history), history
    assert "tag v1 message:1: agent session URL" in history
    assert scan_index(root) == ["b.md:1: agent session URL"]
    assert scan_text("m", "claude" + ".ai/code/" + "SESSION_x") and not scan_text("m", "claude" + ".ai/code/")


def test_a_private_name_in_a_path_a_ref_or_an_author_line_is_caught(repo):
    """Names go public with the history as much as content does: a private name in a file path, a branch name or a
    commit's author line is caught, and a clean name is not."""
    import re

    root, git = repo
    private = {"private name": re.compile("acme" + "corp", re.IGNORECASE)}
    (root / "docs").mkdir()
    (root / "docs" / ("acme" + "corp-notes.md")).write_text("x\n")
    (root / "clean.md").write_text("x\n")
    git("add", "-A")
    git("-c", "user.name=" + "Acme" + "corp Person", "commit", "-q", "-m", "init")
    git("branch", "acme" + "corp-port")
    staged, history = scan_index(root, private), scan_history(root, private)
    assert staged == ["path docs/acme" + "corp-notes.md:1: private name"]
    assert "path docs/acme" + "corp-notes.md (in the history):1: private name" in history
    assert any(i.startswith("ref names") for i in history)
    assert any(" identities" in i for i in history)
    assert not any("clean.md" in i for i in staged + history)
    assert scan_history(root) == []  # without the private pattern, nothing here is unsafe


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
