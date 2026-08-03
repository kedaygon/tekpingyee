import os
import sys
import json
import time
import ctypes
import ctypes.wintypes as wt

from PySide6.QtCore import Qt, QTimer, QPoint, QRectF
from PySide6.QtGui import (QPainter, QColor, QFont, QBrush, QPainterPath, QIcon,
                           QPixmap)
from PySide6.QtWidgets import QApplication, QWidget, QMessageBox, QMenu

import t8ping_core as core

BG = QColor(16, 18, 22, 242)
BG_DIM = QColor(16, 18, 22, 205)
EDGE = QColor(255, 255, 255, 26)
TXT = QColor(232, 234, 238)
SUB = QColor(138, 143, 152)
FAINT = QColor(126, 131, 140)
COL = {"GOOD": QColor(62, 207, 142), "WARN": QColor(232, 179, 57),
       "BAD": QColor(226, 84, 74), None: QColor(120, 125, 134)}

APP_NAME = "철핑이 v1"

CFG_DIR = os.path.join(os.environ.get("APPDATA", "."), "t8ping")
CFG = os.path.join(CFG_DIR, "config.json")
LOG = os.path.join(CFG_DIR, "log.txt")
LOG_MAX = 1048576

SWP_FLAGS = 0x0001 | 0x0002 | 0x0010
SW_SHOWNOACTIVATE = 4
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x8
DEBUG = "--debug" in sys.argv

U = ctypes.WinDLL("user32", use_last_error=True)
U.IsWindowVisible.argtypes = [wt.HWND]
U.IsWindowVisible.restype = wt.BOOL
U.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
U.ShowWindow.restype = wt.BOOL
U.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                           ctypes.c_int, ctypes.c_int, ctypes.c_uint]
U.SetWindowPos.restype = wt.BOOL
U.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
U.GetWindowLongW.restype = ctypes.c_long
HWND_TOPMOST = wt.HWND(-1)
HWND_NOTOPMOST = wt.HWND(-2)


def setup_log():
    if not getattr(sys, "frozen", False):
        return
    try:
        os.makedirs(CFG_DIR, exist_ok=True)
        mode = "a"
        try:
            if os.path.getsize(LOG) > LOG_MAX:
                mode = "w"
        except OSError:
            pass
        f = open(LOG, mode, encoding="utf-8", buffering=1)
        sys.stdout = f
        sys.stderr = f
        print(f"\n===== {APP_NAME} 시작 =====")
    except Exception:
        pass


def load_cfg():
    try:
        with open(CFG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg(d):
    try:
        os.makedirs(CFG_DIR, exist_ok=True)
        with open(CFG, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


def make_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(2, 2, 60, 60), 14, 14)
    p.fillPath(path, QColor(15, 42, 56))
    p.setPen(QColor(79, 217, 196))
    f = QFont("Malgun Gothic", 34)
    f.setWeight(QFont.Bold)
    p.setFont(f)
    p.drawText(QRectF(2, 0, 60, 64), Qt.AlignCenter, "핑")
    p.end()
    return QIcon(pm)


class Overlay(QWidget):
    def __init__(self, eng):
        super().__init__()
        self.eng = eng
        self.cfg = load_cfg()
        self.compact = bool(self.cfg.get("compact", False))
        self.drag = None
        self.s = {}
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.miss = 0
        self.menu_open = False
        self.setWindowTitle(APP_NAME)
        self.apply_size()
        self.restore_pos()
        self.t = QTimer(self)
        self.t.timeout.connect(self.tick)
        self.t.start(250)
        self.show()

    def apply_size(self):
        self.resize(300, 46) if self.compact else self.resize(340, 118)

    def restore_pos(self):
        pos = self.cfg.get("pos")
        if pos:
            try:
                pt = QPoint(int(pos[0]), int(pos[1]))
            except Exception:
                pt = None
            if pt is not None:
                for s in QApplication.screens():
                    g = s.availableGeometry()
                    if g.adjusted(0, 0, -40, -20).contains(pt):
                        self.move(pt)
                        return
        self.move(QPoint(60, 60))

    def toggle(self):
        self.compact = not self.compact
        self.apply_size()
        self.cfg["compact"] = self.compact
        save_cfg(self.cfg)
        self.update()

    def tick(self):
        if self.menu_open:
            return
        self.s = self.eng.snapshot()
        vis = self.compact or self.s.get("phase") != "ingame"
        if vis:
            if not self.isVisible():
                self.show()
            self.pin()
            self.update()
        elif self.isVisible():
            self.hide()
            self.miss = 0

    def pin(self):
        if self.menu_open:
            return
        try:
            hwnd = wt.HWND(int(self.winId()))
        except Exception:
            return
        shown = bool(U.IsWindowVisible(hwnd))
        if DEBUG:
            ex = U.GetWindowLongW(hwnd, GWL_EXSTYLE)
            g = self.geometry()
            print(f"[dbg] qt={self.isVisible()} win={shown} "
                  f"top={bool(ex & WS_EX_TOPMOST)} "
                  f"pos=({g.x()},{g.y()}) miss={self.miss}")
        if not shown:
            self.miss += 1
            U.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            if self.miss >= 3:
                self.miss = 0
                self.hide()
                self.show()
                return
        else:
            self.miss = 0
        ok = U.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS)
        if DEBUG and not ok:
            print(f"[dbg] SetWindowPos 실패 err={ctypes.get_last_error()}")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self.drag and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self.drag)

    def mouseReleaseEvent(self, e):
        self.drag = None
        p = self.pos()
        self.cfg["pos"] = [p.x(), p.y()]
        save_cfg(self.cfg)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.toggle()

    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.setStyleSheet(
            "QMenu{background:#14161a;color:#e8eaee;border:1px solid #2c2f36;"
            "padding:4px;font-family:'Malgun Gothic';font-size:12px;}"
            "QMenu::item{padding:6px 20px;border-radius:4px;}"
            "QMenu::item:selected{background:#262a31;}")
        m.setWindowFlags(m.windowFlags() | Qt.WindowStaysOnTopHint)
        a1 = m.addAction("일반 모드로" if self.compact else "컴팩트 모드로")
        m.addSeparator()
        a2 = m.addAction("종료")
        self.menu_open = True
        try:
            U.SetWindowPos(wt.HWND(int(self.winId())), HWND_NOTOPMOST,
                           0, 0, 0, 0, SWP_FLAGS)
        except Exception:
            pass
        act = m.exec(e.globalPos())
        self.menu_open = False
        if act is a1:
            self.toggle()
        elif act is a2:
            QApplication.quit()

    def rtt_text(self):
        r = self.s.get("rtt")
        if not r:
            return "...", SUB
        if r["src"] == "trying":
            return "측정중", SUB
        if r["src"] == "dead":
            return "불가", SUB
        tag = "~" if r["src"] == "hop" else ""
        return f"{tag}{r['med']:.0f}ms", TXT

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        idle = not self.s.get("connected")
        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 10, 10)
        p.fillPath(path, QBrush(BG_DIM if idle else BG))
        p.setPen(EDGE)
        p.drawPath(path)
        if self.compact:
            self.paint_compact(p, w, h, idle)
        else:
            self.paint_full(p, w, h, idle)

    def paint_compact(self, p, w, h, idle):
        lag = self.s.get("lag")
        stb = self.s.get("stb")
        rt, rc = self.rtt_text()
        off = self.s.get("game") == "no_game"
        if idle:
            lagt = stbt = "·"
            rt = "off" if off else "·"
            rc = FAINT
        else:
            lagt = lag or "..."
            est = bool((self.s.get("sta") or {}).get("est"))
            stbt = (f"~{stb}" if est else stb) if stb else "..."
        cells = [("지연", lagt, COL[lag] if not idle else FAINT),
                 ("안정", stbt, COL[stb] if not idle else FAINT),
                 ("RTT", rt, rc if not idle else FAINT)]
        cw = w / 3.0
        for i, (lab, val, c) in enumerate(cells):
            x = i * cw
            f = QFont("Malgun Gothic", 8)
            p.setFont(f)
            p.setPen(FAINT if idle else SUB)
            p.drawText(QRectF(x, 6, cw, 14), Qt.AlignCenter, lab)
            f = QFont("Malgun Gothic", 12)
            f.setWeight(QFont.DemiBold)
            p.setFont(f)
            p.setPen(c)
            p.drawText(QRectF(x, 19, cw, 20), Qt.AlignCenter, val)

    def paint_full(self, p, w, h, idle=False):
        allv = self.s.get("all")
        sta = self.s.get("sta")
        rt, _ = self.rtt_text()
        f = QFont("Malgun Gothic", 20)
        f.setWeight(QFont.Bold)
        p.setFont(f)
        p.setPen(FAINT if idle else COL[allv])
        p.drawText(QRectF(14, 8, w - 28, 32), Qt.AlignLeft | Qt.AlignVCenter,
                   allv or ("게임 미실행"
                            if self.s.get("game") == "no_game"
                            else ("대기 중" if idle else "측정 중")))
        r = self.s.get("rtt")
        fr = "·"
        if r and r["src"] in ("echo", "hop"):
            fr = f"{core.frames_of(r['med']):.1f}f"
        stb = self.s.get("stb")
        est = bool((sta or {}).get("est"))
        stbt = (f"~{stb}" if est else stb) if stb else None
        rows = [
            ("지연", self.s.get("lag"), self.s.get("lag"),
             [("RTT", "·" if idle else rt), ("편도", "·" if idle else fr)]),
            ("안정", stb, stbt,
             [("stall", "·" if not sta else f"{sta['spm']:.0f}/분"),
              ("loss", "·" if not sta else f"{sta['loss']:.1f}%")]),
        ]
        y = 44
        for lab, v, vtxt, detail in rows:
            f = QFont("Malgun Gothic", 9)
            p.setFont(f)
            p.setPen(SUB)
            p.drawText(QRectF(14, y, 34, 18), Qt.AlignLeft | Qt.AlignVCenter, lab)
            f.setWeight(QFont.DemiBold)
            p.setFont(f)
            p.setPen(FAINT if idle else COL[v])
            p.drawText(QRectF(50, y, 52, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       vtxt or ("·" if idle else "..."))
            x = 100
            for lab2, val2 in detail:
                p.setFont(QFont("Malgun Gothic", 8))
                p.setPen(FAINT)
                p.drawText(QRectF(x, y, 42, 18),
                           Qt.AlignLeft | Qt.AlignVCenter, lab2)
                p.setFont(QFont("Malgun Gothic", 9))
                p.setPen(FAINT if idle else TXT)
                p.drawText(QRectF(x + 34, y, 74, 18),
                           Qt.AlignLeft | Qt.AlignVCenter, val2)
                x += 110
            y += 22
        p.setFont(QFont("Malgun Gothic", 8))
        p.setPen(FAINT)
        game = self.s.get("game")
        if self.s.get("connected"):
            who = "매칭됨"
            if game == "no_perm":
                who += " (포트 미확인)"
        elif game == "no_game":
            who = "게임 미실행"
        elif game == "no_perm":
            who = "게임 포트 확인 불가"
        else:
            who = "매칭 대기 중"
        p.drawText(QRectF(14, h - 22, w - 28, 16),
                   Qt.AlignLeft | Qt.AlignVCenter, f"{who}   ·   우클릭 메뉴")


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


NPCAP_OPTS = (
    "&nbsp;&nbsp;· Install Npcap in WinPcap API-compatible Mode &nbsp;<b>체크</b><br>"
    "&nbsp;&nbsp;· Support raw 802.11 traffic &nbsp;<b>해제</b><br>"
    "&nbsp;&nbsp;· Restrict Npcap driver's access to Administrators only "
    "&nbsp;<b>해제</b><br>")

NPCAP_MISSING = (
    "<b>1. Npcap 설치</b><br>"
    "패킷 캡처 드라이버 Npcap이 설치되어 있지 않습니다.<br>"
    "<a href='https://npcap.com/#download'>https://npcap.com/#download</a><br><br>"
    "설치 중 아래 옵션을 확인하세요.<br>" + NPCAP_OPTS +
    "설치 후 재부팅이 필요합니다.")

NPCAP_NOCOMPAT = (
    "<b>1. Npcap 재설치</b><br>"
    "Npcap은 설치되어 있으나 <b>WinPcap 호환 모드</b>가 꺼져 있습니다.<br>"
    "이 상태로는 패킷을 읽을 수 없습니다.<br><br>"
    "제어판에서 Npcap을 제거한 뒤 아래 옵션으로 다시 설치해 주세요.<br>"
    "<a href='https://npcap.com/#download'>https://npcap.com/#download</a><br>"
    + NPCAP_OPTS + "설치 후 재부팅이 필요합니다.")

ADMIN_HELP = (
    "<b>2. 관리자 권한으로 실행</b><br>"
    "지연(RTT) 측정에 관리자 권한이 필요합니다.<br>"
    "실행 파일을 <b>우클릭 → 관리자 권한으로 실행</b>해 주세요.<br>"
    "매번 자동으로 하려면 우클릭 → 속성 → 호환성 →<br>"
    "&nbsp;&nbsp;<b>관리자 권한으로 이 프로그램 실행</b> 체크.")


def fatal(title, text):
    m = QMessageBox()
    m.setWindowTitle(APP_NAME)
    m.setIcon(QMessageBox.Critical)
    m.setText(title)
    m.setInformativeText(text)
    m.setTextFormat(Qt.RichText)
    m.setTextInteractionFlags(Qt.TextBrowserInteraction)
    m.exec()


def preflight():
    missing = []
    st = core.npcap_state()
    if st == "missing":
        missing.append(NPCAP_MISSING)
    elif st == "no_compat":
        missing.append(NPCAP_NOCOMPAT)
    if not is_admin():
        missing.append(ADMIN_HELP)
    if not missing:
        return True
    head = "실행 전 준비가 필요합니다" if len(missing) > 1 else "실행할 수 없습니다"
    fatal(head, "<br><br>".join(missing))
    return False


def main():
    setup_log()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(make_icon())

    if not preflight():
        sys.exit(1)

    eng = core.Engine()
    eng.start_all()
    if eng.error:
        fatal("시작할 수 없습니다", eng.error +
              "<br><br>관리자 권한으로 실행했는지 확인해 주세요.")
        sys.exit(1)

    ov = Overlay(eng)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
