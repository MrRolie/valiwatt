"""`valiwatt all --departure` reaches the 2024 replay as --departure; without it, the replay runs plain. Every step is
a stand-in: no data and no store are touched."""

from valiwatt import cli
from valiwatt.forecast import freeze
from valiwatt.monitor import run


def test_all_passes_departure_to_the_2024_replay_only(monkeypatch):
    replays = []
    for name in ("cmd_fetch", "cmd_dq", "cmd_validate", "cmd_breaks", "cmd_report"):
        monkeypatch.setattr(cli, name, lambda a: 0)
    monkeypatch.setattr(freeze, "run_freeze", lambda year: 0)
    monkeypatch.setattr(run, "run_replay", lambda year, departure=False: replays.append((year, departure)) or 0)
    assert cli.main(["all", "--departure"]) == 0
    assert replays == [(2023, False), (2024, True)]
    replays.clear()
    assert cli.main(["all"]) == 0
    assert replays == [(2023, False), (2024, False)]
