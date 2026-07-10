"""«Собрать файл без отправки»: команда дочернего процесса + обмен результатом.

Дочерний процесс не запускается по-настоящему — launcher подставляется. Проверяем
главное: команда правильная, свежий результат читается, а НЕСВЕЖИЙ (от прошлого
запуска) никогда не выдаётся за новый.
"""
import subprocess

from app.services.cell_runner import PreviewResult, preview_json_path
from app.services.preview import build_preview_command, run_preview


def test_build_preview_command_dev_module():
    cmd = build_preview_command(7)
    assert cmd[-2:] == ["--preview-cell", "7"]
    assert "app.gui" in cmd


def test_preview_result_json_round_trip(tmp_path):
    original = PreviewResult(cell_id=3, ok=True, rows=5, zero_quantity=1, zero_price=2,
                             file_path="p.xlsx", sample=[["B", "A-1", "Д", 1, 2.0]],
                             message="ok")
    path = original.write(tmp_path)
    assert path == preview_json_path(tmp_path, 3)

    import json
    restored = PreviewResult.from_dict(json.loads(path.read_text(encoding="utf-8")))
    assert restored == original


def test_run_preview_reads_child_result(tmp_path):
    launched = {}

    def launcher(cmd, timeout):
        launched["cmd"] = cmd
        PreviewResult(cell_id=3, ok=True, rows=5, zero_quantity=1,
                      file_path="p.xlsx").write(tmp_path)

    res = run_preview(3, tmp_path, launcher=launcher)

    assert res.ok is True and res.rows == 5 and res.zero_quantity == 1
    assert launched["cmd"][-2:] == ["--preview-cell", "3"]


def test_run_preview_never_returns_a_stale_result(tmp_path):
    # A previous preview left its JSON behind; this child crashes without writing one.
    PreviewResult(cell_id=3, ok=True, rows=999, message="старый").write(tmp_path)

    def crashing_launcher(cmd, timeout):
        pass                                   # child died, wrote nothing

    res = run_preview(3, tmp_path, launcher=crashing_launcher)

    assert res.ok is False
    assert res.rows == 0                       # the stale 999 must not surface
    assert "аварийно" in res.message


def test_run_preview_maps_timeout(tmp_path):
    def hanging_launcher(cmd, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    res = run_preview(3, tmp_path, launcher=hanging_launcher)

    assert res.ok is False
    assert "лимит" in res.message
    assert "ничего не отправлено" in res.message


def test_run_preview_handles_corrupt_result(tmp_path):
    def launcher(cmd, timeout):
        path = preview_json_path(tmp_path, 3)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ не json", encoding="utf-8")

    res = run_preview(3, tmp_path, launcher=launcher)
    assert res.ok is False
    assert "Повреждён" in res.message


def test_run_preview_survives_launcher_failure(tmp_path):
    def launcher(cmd, timeout):
        raise OSError("не удалось создать процесс")

    res = run_preview(3, tmp_path, launcher=launcher)
    assert res.ok is False
    assert "запустить проверку" in res.message
