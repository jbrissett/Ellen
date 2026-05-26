"""Generate `installer/ellen.ico` from the master PNG.

Pillow's ICO writer needs the source image + an explicit list of sizes.
We hand it the 256x256 master and ask for the standard Windows ICO
size set (16, 32, 48, 64, 128, 256). The result is a single multi-res
.ico file PyInstaller and Inno Setup can both consume.

Run once before each installer build. Re-running is safe — idempotent.
"""
from pathlib import Path

from PIL import Image


SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "traffic_intake" / "ui" / "assets" / "ellen_icon.png"
DEST = REPO / "installer" / "ellen.ico"


def main() -> None:
    master = Image.open(SRC).convert("RGBA")
    if master.size[0] < 256 or master.size[1] < 256:
        raise SystemExit(
            f"Expected a 256x256+ master at {SRC}, got {master.size}. "
            "Ico generation needs a high-res source so the 256x256 entry "
            "looks crisp."
        )
    master.save(DEST, format="ICO", sizes=SIZES)
    print(f"Wrote {DEST.relative_to(REPO)} ({DEST.stat().st_size} bytes, "
          f"sizes: {SIZES})")


if __name__ == "__main__":
    main()
