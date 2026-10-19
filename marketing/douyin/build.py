"""重新生成抖音素材（出图 + 合成带字幕的视频）。

改完 cards.html 里的文案后跑这个脚本即可，不用记 ffmpeg 那一长串参数。

用法（在项目根目录）：
    .venv/Scripts/python.exe marketing/douyin/build.py

依赖：Chrome（截图）、ffmpeg（含 libass，用于烧字幕）。
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

SEG = 2.6          # 每张卡片停留秒数
XFADE = 0.35       # 转场时长
FPS = 30
N = 9


def find_chrome() -> str:
    for p in CHROME_CANDIDATES:
        if pathlib.Path(p).exists():
            return p
    found = shutil.which("chrome") or shutil.which("google-chrome")
    if found:
        return found
    sys.exit("找不到 Chrome，请把路径写进 CHROME_CANDIDATES")


def shoot(chrome: str, i: int, vertical: bool) -> None:
    """截一张卡片。竖版 1080×1920（视频用），横版 1080×1440（图文用）。"""
    w, h = (1080, 1920) if vertical else (1080, 1440)
    name = f"v-0{i}.png" if vertical else f"card-0{i}.png"
    url = f"file:///{(HERE / 'cards.html').as_posix()}?i={i}" + ("&v=v" if vertical else "")
    subprocess.run(
        [
            chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            f"--window-size={w},{h}", "--virtual-time-budget=1500",
            f"--screenshot={(HERE / name).as_posix()}", url,
        ],
        check=True, capture_output=True,
    )
    print("  出图", name)


def build_video() -> None:
    """9 张竖版卡 → 缓推 + 交叉转场 + 烧字幕。"""
    inputs: list[str] = []
    for i in range(1, N + 1):
        inputs += ["-loop", "1", "-t", str(SEG), "-framerate", str(FPS), "-i", f"v-0{i}.png"]

    parts = []
    for i in range(N):
        # zoompan 用输出帧号 on 递增缩放，从 1.0 缓推到 1.055：镜头在动，画面就不"死"
        parts.append(
            f"[{i}:v]scale=1080:1920,"
            f"zoompan=z='min(1+0.0007*on,1.055)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s=1080x1920:fps={FPS},setsar=1,format=yuv420p[a{i}]"
        )
    prev = "a0"
    for i in range(1, N):
        off = round(i * (SEG - XFADE), 2)
        parts.append(f"[{prev}][a{i}]xfade=transition=fade:duration={XFADE}:offset={off}[x{i}]")
        prev = f"x{i}"
    # 字幕放最后：保证压在所有转场之上，转场时不会闪一下再出现
    parts.append(f"[{prev}]ass=subs.ass[v]")

    cmd = ["ffmpeg", "-y", *inputs,
           "-filter_complex", ";".join(parts),
           "-map", "[v]", "-c:v", "libx264", "-crf", "19", "-preset", "medium",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", "demo-pro.mp4"]
    subprocess.run(cmd, check=True, cwd=HERE)
    print("  合成 demo-pro.mp4")


def main() -> None:
    chrome = find_chrome()
    print("Chrome:", chrome)
    for i in range(1, N + 1):
        shoot(chrome, i, vertical=False)
    for i in range(1, N + 1):
        shoot(chrome, i, vertical=True)
    build_video()
    print("完成。产物都在", HERE)


if __name__ == "__main__":
    main()
