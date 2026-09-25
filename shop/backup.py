"""Backup / restore of the whole site: SQLite database + uploaded media, as one zip."""
import datetime
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.db import connections
from django.utils.translation import gettext as _

REQUIRED_TABLES = {"django_migrations", "shop_sitesettings", "shop_product"}
MAX_UNPACKED = 2 * 1024 ** 3  # guard against zip bombs


def db_path():
    return Path(connections["default"].settings_dict["NAME"])  # в тестах — тестовая база


def backups_dir():
    d = settings.DATA_DIR / "backups"
    d.mkdir(exist_ok=True)
    return d


def _snapshot_db(dst):
    """Consistent copy of the live database (works while the site is running, WAL included)."""
    src = sqlite3.connect(db_path())
    out = sqlite3.connect(dst)
    with out:
        src.backup(out)
    out.close()
    src.close()


def export_zip(with_media=True):
    """Build a backup zip in a temp file and return its path."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    tmpdir = Path(tempfile.mkdtemp(prefix="gl-export-"))
    db_copy = tmpdir / "db.sqlite3"
    _snapshot_db(db_copy)
    zpath = tmpdir / f"gluckstal-backup-{stamp}{'' if with_media else '-db-only'}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(db_copy, "db.sqlite3")
        media = Path(settings.MEDIA_ROOT)
        if with_media and media.exists():
            for f in media.rglob("*"):
                if f.is_file():
                    # photos are already compressed — store them as is
                    z.write(f, f"media/{f.relative_to(media).as_posix()}", compress_type=zipfile.ZIP_STORED)
    db_copy.unlink()
    return zpath


def _check_db(path):
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
    except sqlite3.DatabaseError as e:
        raise ValueError(_("это не база SQLite: %s") % e)
    if ok != "ok":
        raise ValueError(_("база повреждена: %s") % ok)
    missing = REQUIRED_TABLES - tables
    if missing:
        raise ValueError(_("это не база этого сайта (нет таблиц: %s)") % ", ".join(sorted(missing)))


def import_upload(uploaded):
    """Restore from an uploaded .zip (db.sqlite3 [+ media/]) or a bare .sqlite3 file.

    Returns (auto_backup_path, restored_media: bool). Raises ValueError with a user-facing message.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="gl-import-"))
    try:
        src = tmpdir / "upload"
        with open(src, "wb") as f:
            for chunk in uploaded.chunks():
                f.write(chunk)

        new_db, new_media = tmpdir / "db.sqlite3", None
        if zipfile.is_zipfile(src):
            with zipfile.ZipFile(src) as z:
                names = z.namelist()
                if "db.sqlite3" not in names:
                    raise ValueError(_("в архиве нет db.sqlite3"))
                if sum(i.file_size for i in z.infolist()) > MAX_UNPACKED:
                    raise ValueError(_("архив слишком большой"))
                for name in names:
                    p = PurePosixPath(name)
                    if p.is_absolute() or ".." in p.parts:
                        raise ValueError(_("недопустимый путь в архиве: %s") % name)
                z.extract("db.sqlite3", tmpdir)
                media_names = [n for n in names if n.startswith("media/") and not n.endswith("/")]
                if media_names:
                    new_media = tmpdir / "unpacked"
                    for n in media_names:
                        z.extract(n, new_media)
                    new_media = new_media / "media"
        else:
            shutil.move(src, new_db)
        _check_db(new_db)

        # 1) safety net: full backup of the current state
        auto = export_zip(with_media=True)
        auto_dst = backups_dir() / ("before-import-" + auto.name.replace("gluckstal-backup-", ""))
        shutil.move(auto, auto_dst)
        shutil.rmtree(auto.parent, ignore_errors=True)
        _prune_backups()

        # 2) replace the database page by page (no file swap under the running process)
        connections.close_all()
        src_con, dst_con = sqlite3.connect(new_db), sqlite3.connect(db_path())
        with dst_con:
            src_con.backup(dst_con)
        src_con.close()
        dst_con.close()
        connections.close_all()

        # 3) replace media, if the archive has it
        if new_media is not None:
            media = Path(settings.MEDIA_ROOT)
            old = media.with_name("media.old")
            shutil.rmtree(old, ignore_errors=True)
            if media.exists():
                media.rename(old)
            shutil.move(str(new_media), media)
            shutil.rmtree(old, ignore_errors=True)

        call_command("migrate", interactive=False, verbosity=0)  # older backup -> current schema
        cache.clear()
        return auto_dst, new_media is not None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _prune_backups(keep=5):
    files = sorted(backups_dir().glob("before-import-*.zip"), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in files[keep:]:
        f.unlink(missing_ok=True)


def list_backups():
    return sorted(backups_dir().glob("*.zip"), key=lambda f: f.stat().st_mtime, reverse=True)
