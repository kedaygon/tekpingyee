import os
import re
import sys
import shutil
import zipfile
import subprocess

APP = "철핑이"
VER = "1.0.0"
EXE = "철핑이"
ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist")
BUILD = os.path.join(ROOT, "build")
ICON = os.path.join(ROOT, "ping.ico")
OUT = os.path.join(ROOT, "release")

EXCLUDE = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngine",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.Qt3DCore",
    "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DAnimation",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtQuick",
    "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtBluetooth", "PySide6.QtNetworkAuth", "PySide6.QtPositioning",
    "PySide6.QtSerialPort", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtOpenGL", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "matplotlib", "numpy", "PIL", "tkinter", "cryptography", "IPython",
]

VERSION_RC = """
VSVersionInfo(
  ffi=FixedFileInfo(filevers=({v0}, {v1}, {v2}, 0),
                    prodvers=({v0}, {v1}, {v2}, 0),
                    mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041203B5', [
        StringStruct('FileDescription', '{app} - 철권8 회선 품질 오버레이'),
        StringStruct('FileVersion', '{ver}'),
        StringStruct('InternalName', '{app}'),
        StringStruct('OriginalFilename', '{exe}.exe'),
        StringStruct('ProductName', '{app}'),
        StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [1042, 949])])
  ]
)
"""


def sh(args):
    print(">", " ".join(args))
    r = subprocess.run(args)
    if r.returncode != 0:
        sys.exit(f"실패: {args[0]}")


def need(mod, pkg=None):
    try:
        __import__(mod)
        return True
    except ImportError:
        print(f"[!] {pkg or mod} 없음 -> 설치")
        sh([sys.executable, "-m", "pip", "install", pkg or mod, "-q"])
        return True


def need_psutil():
    try:
        import psutil
        if psutil.version_info[0] >= 6:
            return
        print(f"[!] psutil {psutil.__version__} -> 6.0 이상으로 업그레이드")
    except ImportError:
        print("[!] psutil 없음 -> 설치")
    sh([sys.executable, "-m", "pip", "install", "psutil>=6.0", "-q", "--upgrade"])


def make_ico():
    if os.path.exists(ICON):
        print(f"[=] {os.path.basename(ICON)} 있음")
        return
    need("PIL", "pillow")
    print("[+] 아이콘 생성")
    sh([sys.executable, os.path.join(ROOT, "make_icon.py")])
    if not os.path.exists(ICON):
        sys.exit("아이콘 생성 실패")


def write_version():
    p = os.path.join(ROOT, "version.txt")
    a, b, c = VER.split(".")
    with open(p, "w", encoding="utf-8") as f:
        f.write(VERSION_RC.format(v0=a, v1=b, v2=c, app=APP, ver=VER, exe=EXE))
    return p


def build():
    for d in (DIST, BUILD):
        shutil.rmtree(d, ignore_errors=True)
    vf = write_version()
    args = [sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--onefile", "--noconsole",
            "--uac-admin",
            "--name", EXE,
            "--icon", ICON,
            "--version-file", vf,
            "--collect-all", "scapy",
            "--hidden-import", "psutil",
            "--hidden-import", "t8ping_core"]
    for m in EXCLUDE:
        args += ["--exclude-module", m]
    args.append(os.path.join(ROOT, "t8ping.py"))
    sh(args)


def package():
    exe = os.path.join(DIST, EXE + ".exe")
    if not os.path.exists(exe):
        sys.exit("exe 없음")
    os.makedirs(OUT, exist_ok=True)
    zp = os.path.join(OUT, f"{EXE}_v{VER}.zip")
    readme = os.path.join(ROOT, "README.txt")
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(exe, EXE + ".exe")
        if os.path.exists(readme):
            z.write(readme, "사용법.txt")
        else:
            print("[!] README.txt 없음 - 압축에서 제외")
    mb = os.path.getsize(exe) / 1048576
    zmb = os.path.getsize(zp) / 1048576
    print(f"\n[완료] exe {mb:.1f}MB  zip {zmb:.1f}MB")
    print(f"       {zp}")


def main():
    for f in ("t8ping.py", "t8ping_core.py", "make_icon.py"):
        if not os.path.exists(os.path.join(ROOT, f)):
            sys.exit(f"{f} 가 이 폴더에 없습니다")
    need("PyInstaller", "pyinstaller")
    need("PySide6")
    need_psutil()
    need("scapy")
    make_ico()
    build()
    package()


if __name__ == "__main__":
    main()
