"""core/backups.py: архив uploads/ создаётся только при изменении состава."""
from __future__ import annotations

import zipfile

from core.backups import backup_uploads, fingerprint, prune_old_upload_archives


def _make_uploads(root, files: dict[str, str]):
    src = root / "uploads"
    src.mkdir(exist_ok=True)
    for name, body in files.items():
        (src / name).write_text(body, encoding="utf-8")
    return src


def test_archives_contents(tmp_path):
    src = _make_uploads(tmp_path, {"catalog.md": "YCW3", "passport.txt": "YCM3-250"})
    out = tmp_path / "backups"

    dest = backup_uploads(src=src, backup_dir=out)

    assert dest is not None and dest.exists()
    with zipfile.ZipFile(dest) as archive:
        assert sorted(archive.namelist()) == ["catalog.md", "passport.txt"]
        assert archive.read("catalog.md").decode("utf-8") == "YCW3"


def test_skips_when_nothing_changed(tmp_path):
    src = _make_uploads(tmp_path, {"catalog.md": "YCW3"})
    out = tmp_path / "backups"

    first = backup_uploads(src=src, backup_dir=out)
    second = backup_uploads(src=src, backup_dir=out)

    assert first is not None
    # 57 МБ ежедневно ради неизменных файлов — трата места, поэтому None.
    assert second is None
    assert len(list(out.glob("uploads_*.zip"))) == 1


def test_archives_again_after_change(tmp_path):
    src = _make_uploads(tmp_path, {"catalog.md": "YCW3"})
    out = tmp_path / "backups"
    backup_uploads(src=src, backup_dir=out)

    (src / "new_passport.txt").write_text("YCB9", encoding="utf-8")
    again = backup_uploads(src=src, backup_dir=out)

    assert again is not None
    assert len(list(out.glob("uploads_*.zip"))) == 2


def test_fingerprint_reacts_to_size(tmp_path):
    src = _make_uploads(tmp_path, {"catalog.md": "YCW3"})
    before = fingerprint(src)
    (src / "catalog.md").write_text("YCW3 расширенный", encoding="utf-8")
    assert fingerprint(src) != before


def test_missing_uploads_dir_is_not_fatal(tmp_path):
    # Свежая установка: каталога ещё нет — бэкап bot.db не должен падать из-за этого.
    assert backup_uploads(src=tmp_path / "absent", backup_dir=tmp_path / "backups") is None


def test_prune_keeps_newest(tmp_path):
    out = tmp_path / "backups"
    out.mkdir()
    import os, time
    for i in range(7):
        archive = out / f"uploads_2026010{i}_000000.zip"
        archive.write_bytes(b"x")
        os.utime(archive, (time.time() + i, time.time() + i))

    removed = prune_old_upload_archives(keep=5, backup_dir=out)

    assert removed == 2
    assert len(list(out.glob("uploads_*.zip"))) == 5
