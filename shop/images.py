"""Уменьшенные webp-копии загруженных фото: <имя>-<ширина>.webp рядом с оригиналом."""
from pathlib import Path

from PIL import Image, ImageOps


def variant_path(path, width):
    p = Path(path)
    return p.with_name(f"{p.stem}-{width}.webp")


def variant_url(field, width):
    """URL уменьшенной копии для ImageField (или оригинала, если копии нет)."""
    if not field:
        return ""
    url = field.url
    return url.rsplit(".", 1)[0] + f"-{width}.webp"


def make_variants(path, widths, og=False):
    src = Path(path)
    if not src.exists():
        return
    im = ImageOps.exif_transpose(Image.open(src))
    im = im.convert("RGBA" if im.mode in ("P", "RGBA", "LA") else "RGB")
    for w in widths:
        dst = variant_path(src, w)
        if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
            continue
        v = im.resize((w, round(im.height * w / im.width)), Image.LANCZOS) if im.width > w else im
        v.save(dst, "WEBP", quality=80, method=6)
    if og:  # превью для соцсетей/мессенджеров 1200×630
        dst = src.parent / "og.jpg"
        ImageOps.fit(im.convert("RGB"), (1200, 630), Image.LANCZOS).save(dst, "JPEG", quality=82, optimize=True)


def remove_variants(path):
    p = Path(path)
    for f in p.parent.glob(f"{p.stem}-*.webp"):
        f.unlink(missing_ok=True)
