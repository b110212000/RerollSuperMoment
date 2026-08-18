#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Steam 遊戲刷初始帳號腳本（單檔版）

安裝：
    pip install opencv-python numpy mss pydirectinput pygetwindow pytesseract
    OCR 另外要裝 Tesseract 本體（只用圖片判定可跳過）：
    https://github.com/UB-Mannheim/tesseract/wiki  安裝時勾選 Chinese (Traditional)

用法：
    python reroll.py shot                # 截圖，拿去裁模板
    python reroll.py test 按鈕.png       # 測模板比對得到嗎
    python reroll.py ocr                 # 測 OCR 讀不讀得到
    python reroll.py run                 # 正式跑
    python reroll.py run --max 3         # 先跑三輪試水溫

隨時按 F12 或 Ctrl+C 中斷。

第一次執行會自動產生 reroll_config.json，流程都寫在那裡面，改 JSON 就好，
不用動這支程式。設定檔裡的 "_說明" 有每種步驟的用法。
"""

import argparse
import ctypes
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

try:
    import numpy as np
    import cv2
    import mss
    import pydirectinput
    import pygetwindow as gw
except ImportError as e:
    name = getattr(e, "name", None) or str(e)
    sys.exit(
        f"缺套件：{name}\n"
        "請先執行： pip install opencv-python numpy mss pydirectinput pygetwindow pytesseract"
    )

try:
    import pytesseract
except ImportError:
    pytesseract = None

# 主控台印中文用，避免 cp950 爆掉。stderr 也要——sys.exit("中文訊息") 走的是
# stderr，只設 stdout 的話錯誤訊息會變亂碼，而那正是最需要看懂的時候。
for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

pydirectinput.FAILSAFE = False   # 停止一律靠 F12，不用滑鼠甩角落
pydirectinput.PAUSE = 0

VK_F12 = 0x7B
# 離開碼。看門狗靠這個決定要不要重開——這兩種都不能重開，因為重開會走
# app_reset 清掉帳號和現場。watchdog.py 有同名常數，改的時候要一起改。
EXIT_USER_STOP = 130      # 使用者按 F12 / Ctrl+C
EXIT_NEEDS_HUMAN = 3      # 遇到要人判斷的狀況，畫面保留在原地
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "reroll_config.json")
LOG_PATH = os.path.join(BASE_DIR, "reroll.log")

# 雙開時每台模擬器要有自己的 log 和截圖目錄。不分家的話兩個程序會交錯 append
# 同一個檔，而 log 行只有時分秒、沒有實例標記，事後根本分不出哪行是誰的；
# 看門狗的「檔案大小有沒有變」停滯偵測也會被另一台的寫入蓋掉而永遠不觸發。
# 由 --instance 在 main() 最早期呼叫 set_instance() 設定一次。
INSTANCE = ""            # 空字串＝單開，所有路徑維持原樣
_LOG_PATH = LOG_PATH


def set_instance(name):
    """記下實例名字，並把 log 導到該實例自己的檔案。"""
    global INSTANCE, _LOG_PATH
    INSTANCE = (name or "").strip()
    _LOG_PATH = (os.path.join(BASE_DIR, f"reroll.{INSTANCE}.log")
                 if INSTANCE else LOG_PATH)


# ---------------------------------------------------------------- 預設設定檔

DEFAULT_CONFIG = {
    "_說明": {
        "window_title": "遊戲視窗標題的一部分，比對時不分大小寫。留空字串＝直接抓整個螢幕。",
        "region": "手動指定擷取範圍 [x, y, 寬, 高]（螢幕絕對座標）。填了就不看 window_title。null＝自動。",
        "window_padding": "視窗四邊要切掉的邊框像素 [左, 上, 右, 下]，抓到標題列時用得到。",
        "match_threshold": "模板比對的及格分數，0~1。抓不到就調低（0.75），亂抓就調高（0.9）。",
        "scales": "模板縮放倍率清單。模板跟現在解析度一樣就維持 [1.0]；不一樣可填 [0.9, 1.0, 1.1]（會變慢）。",
        "move_method": "滑鼠移動方式：win32（多螢幕比較準）或 pydirectinput（少數遊戲才吃）。",
        "步驟種類": {
            "sleep": '{"do": "sleep", "sec": 2.0}',
            "click": '{"do": "click", "template": "開始.png", "timeout": 60, "offset": [0, 0]}  等圖出現再點它',
            "click_at": '{"do": "click_at", "pos": [640, 400]}  點擷取範圍內的固定座標',
            "key": '{"do": "key", "key": "esc", "times": 2, "interval": 0.3}',
            "wait_for": '{"do": "wait_for", "template": "標題.png", "timeout": 120}  等圖出現',
            "wait_gone": '{"do": "wait_gone", "template": "載入中.png", "timeout": 180}  等圖消失',
            "spam": '{"do": "spam", "template": "跳過.png", "until": "首抽.png", "timeout": 180, "interval": 1.0}  狂點某張圖直到 until 出現；不填 until 就點到它自己消失',
            "spam_at": '{"do": "spam_at", "pos": [640, 400], "until": "結果.png", "timeout": 120, "interval": 0.8}  狂點固定座標',
            "共用參數": '每一步都能加 "optional": true（逾時就跳過不算失敗）、"threshold": 0.8、"note": "備註"',
        },
    },

    "window_title": "",
    "region": None,
    "monitor": 1,
    "window_padding": [0, 0, 0, 0],
    "focus_window": True,

    "template_dir": "templates",
    "shot_dir": "shots",
    "hit_dir": "hits",

    "match_threshold": 0.85,
    "scales": [1.0],
    "poll_interval": 0.5,
    "action_delay": 0.35,
    "move_delay": 0.08,
    "click_hold": 0.06,
    "move_method": "win32",

    "tesseract_cmd": "",
    "ocr_lang": "chi_tra+eng",
    "ocr_region": None,

    "hit_templates": [],
    "hit_keywords": [],

    "stop_on_hit": True,
    "max_rounds": 0,
    "max_fails": 3,

    "flow": [
        {"do": "wait_for", "template": "標題.png", "timeout": 120, "note": "等遊戲讀到標題畫面"},
        {"do": "click", "template": "開始遊戲.png", "timeout": 60},
        {"do": "spam", "template": "跳過.png", "until": "首抽.png", "timeout": 240, "interval": 1.0,
         "note": "狂點跳過把開場劇情跳完"},
        {"do": "click", "template": "首抽.png", "timeout": 60},
        {"do": "sleep", "sec": 2.0},
        {"do": "spam_at", "pos": [640, 400], "until": "結果.png", "timeout": 120, "interval": 0.8,
         "note": "點畫面跳過抽卡動畫"},
    ],

    "reset_flow": [
        {"do": "click", "template": "設定.png", "timeout": 30},
        {"do": "click", "template": "刪除帳號.png", "timeout": 30},
        {"do": "click", "template": "確定.png", "timeout": 30},
        {"do": "sleep", "sec": 3.0},
    ],

    "recover_flow": [
        {"do": "key", "key": "esc", "times": 3, "interval": 0.5},
        {"do": "sleep", "sec": 2.0},
    ],
}


# ---------------------------------------------------------------- 小工具

class StopRequested(Exception):
    """使用者按了 F12。"""


class NeedsHuman(Exception):
    """
    這個狀況要人看一眼才知道怎麼處理，不要自動重置。

    存在的理由：預設的失敗處理是走復原流程，也就是 app_reset（清掉整個帳號）。
    對「畫面對不上」那類失敗這樣做沒問題，但有些狀況需要保留現場才問得出
    解法——洗掉之後就沒有線索了。標了 on_fail: "ask" 的步驟走這條路。
    """


def log(msg):
    stamp = f"[{datetime.now():%H:%M:%S}]"
    line = f"{stamp} {msg}"
    # console 加實例標記，兩個程序輸出到同一個終端才讀得懂。
    # 寫進檔案的維持原格式——每台已經各有自己的檔，再加標記是多餘的。
    print(f"{stamp}[{INSTANCE}] {msg}" if INSTANCE else line, flush=True)
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def enable_dpi_awareness():
    """沒開這個，螢幕縮放不是 100% 時抓到的座標會整個偏掉。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def f12_pressed():
    # 0x8000＝現在按著，0x0001＝上次檢查後按過。兩個都看，短按才不會漏掉。
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(VK_F12) & 0x8001)
    except Exception:
        return False


def smtp_password(cfg):
    """
    取出信箱密碼。找不到回空字串。

    先看程序自己的環境變數；沒有的話再去讀 HKCU\\Environment（也就是「使用者
    環境變數」那個store）。需要第二條路是因為 Windows 不會把環境變數的變更推給
    已經在跑的程序——從一個開得比較早的終端啟動時，就算變數設好了也讀不到。
    這種情況下信會安靜地寄不出去，而寄不出去的那一刻正是最需要通知的時候
    （中獎、或卡住要人處理）。密碼仍然只存在系統的環境變數裡，不進設定檔。
    """
    env_name = (cfg.get("notify_mail") or {}).get("password_env",
                                                  "REROLL_SMTP_PASS")
    pwd = os.environ.get(env_name, "")
    if pwd:
        return pwd
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return winreg.QueryValueEx(k, env_name)[0] or ""
    except Exception:
        return ""


def send_mail(cfg, subject, body, attach=None):
    """
    中了就寄信通知。密碼一律從環境變數讀，不寫進設定檔——設定檔是純文字，
    而且你可能會把它連同模板一起備份或分享出去。
    回傳 True 表示寄出成功。
    """
    conf = cfg.get("notify_mail") or {}
    if not conf.get("enabled"):
        return False

    env_name = conf.get("password_env", "REROLL_SMTP_PASS")
    pwd = smtp_password(cfg)
    if not pwd:
        log(f"！想寄信但找不到 {env_name} 的值（程序環境變數和使用者環境變數都沒有），跳過通知")
        return False

    try:
        import smtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = conf.get("from") or conf["user"]
        msg["To"] = conf["to"]
        msg.set_content(body)

        if attach and os.path.exists(attach):
            with open(attach, "rb") as f:
                msg.add_attachment(f.read(), maintype="image", subtype="png",
                                   filename=os.path.basename(attach))

        host, port = conf["smtp_host"], int(conf.get("smtp_port", 587))
        timeout = float(conf.get("timeout", 30))
        if int(port) == 465:
            with smtplib.SMTP_SSL(host, port, timeout=timeout) as s:
                s.login(conf["user"], pwd)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as s:
                if conf.get("use_tls", True):
                    s.starttls()
                s.login(conf["user"], pwd)
                s.send_message(msg)
        log(f"已寄出通知信給 {conf['to']}")
        return True
    except Exception as e:
        log(f"！寄信失敗：{type(e).__name__}: {e}")
        return False


def _integrity_level(pid):
    """回傳該程序的完整性等級數值，查不到回 None。"""
    k32, a32 = ctypes.windll.kernel32, ctypes.windll.advapi32
    a32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    a32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    a32.GetSidSubAuthority.restype = ctypes.POINTER(ctypes.c_ulong)
    a32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, ctypes.c_ulong]

    h = k32.OpenProcess(0x1000, False, pid)      # QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        tok = ctypes.c_void_p()
        if not a32.OpenProcessToken(h, 0x0008, ctypes.byref(tok)):
            # token 開不了，本身就代表對方權限比我們高
            return 0x3000
        try:
            sz = ctypes.c_ulong()
            a32.GetTokenInformation(tok, 25, None, 0, ctypes.byref(sz))
            buf = ctypes.create_string_buffer(sz.value)
            if not a32.GetTokenInformation(tok, 25, buf, sz, ctypes.byref(sz)):
                return None
            sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
            n = a32.GetSidSubAuthorityCount(sid)[0]
            return int(a32.GetSidSubAuthority(sid, n - 1)[0])
        finally:
            k32.CloseHandle(tok)
    finally:
        k32.CloseHandle(h)


def _check_adb_ready(adb):
    """
    ADB 模式的開跑前檢查。回傳 True 代表可以跑。

    最重要的是解析度：模板全部是在 900x1600 / 240dpi 上裁的，換一台解析度不同
    的模擬器，42 個模板會一起掉到門檻以下。那種失敗看起來像「流程壞了」，
    很難聯想到解析度，所以寧可在這裡直接擋掉。
    """
    log(f"ADB 目標：{adb.serial}（ldconsole index {adb.ld_index}）")
    if not adb.alive():
        log("=" * 46)
        log(f"！連不到 {adb.serial}")
        log("  模擬器開了嗎？serial 對嗎？用這個看目前有哪些：")
        log(f"  \"{adb.exe}\" devices")
        log("=" * 46)
        return False

    size = adb.shell("wm size").strip()
    dens = adb.shell("wm density").strip()
    log(f"  裝置畫面：{size or '讀不到'}／{dens or '讀不到'}")

    want = adb.cfg_device_size
    if want:
        got = re.search(r"(\d+)x(\d+)", size)
        if got and {int(got.group(1)), int(got.group(2))} != set(want):
            log("=" * 46)
            log(f"！解析度不對：預期 {want[0]}x{want[1]}，實際 {got.group(0)}")
            log("  模板是在預期解析度上裁的，這樣跑下去每一步都會對不上。")
            log("  用 ldconsole 改：")
            log(f"  ldconsole modify --index {adb.ld_index} "
                f"--resolution {want[1]},{want[0]},240")
            log("=" * 46)
            return False
    return True


def check_input_permission(cap):
    """
    遊戲用管理員權限跑、腳本沒有的話，Windows 的 UIPI 會把我們送的點擊靜默丟掉，
    畫面截得到、游標移得動，就是點不動。開跑前先擋下來，別讓它空轉一整晚。
    回傳 True 代表可以送輸入。
    """
    # ADB 模式完全不經過 Windows 的輸入層，UIPI 無關；而且這裡呼叫 _window()
    # 會去按標題列舉視窗，雙開時兩台標題一樣，抓到哪台是隨機的。
    # 改成檢查真正會擋住我們的東西：serial 連不連得到、解析度對不對。
    if cap.adb:
        return _check_adb_ready(cap.adb)

    try:
        w = cap._window()
    except Exception:
        return True
    if w is None:
        return True

    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(w._hWnd, ctypes.byref(pid))
    game = _integrity_level(pid.value)
    mine = _integrity_level(os.getpid())
    if game is None or mine is None or game <= mine:
        return True

    log("=" * 46)
    log("！遊戲是用管理員權限跑的，這個腳本不是。")
    log("  Windows 會把我們送出去的點擊直接丟掉，不會有任何錯誤訊息，")
    log("  畫面截得到、游標也移得動，但遊戲完全不會有反應。")
    log("")
    log("  兩個解法擇一：")
    log("  1. 用管理員身分開 PowerShell 再跑這支腳本（最快）")
    log("  2. 讓遊戲別用管理員跑：完全關掉 Steam，改用一般權限重開，")
    log("     並確認遊戲執行檔的「相容性」沒有勾選「以系統管理員身分執行」")
    log("=" * 46)
    return False


def imread_unicode(path, flags=cv2.IMREAD_UNCHANGED):
    """cv2.imread 在 Windows 讀不到中文檔名，繞過去。"""
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except Exception:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)


def imwrite_unicode(path, img):
    ext = os.path.splitext(path)[1] or ".png"
    ok, buf = cv2.imencode(ext, img)
    if ok:
        buf.tofile(path)
    return ok


def load_config(path, allow_create=True):
    if not os.path.exists(path):
        # 只有在用預設路徑時才自動產生。明確指定了 --config 卻找不到，幾乎都是
        # 路徑打錯——這時候安靜產生一份預設檔特別危險：DEFAULT_CONFIG 裡沒有
        # input_mode，那個實例會退回滑鼠模式，開始搶使用者的滑鼠。
        if not allow_create:
            sys.exit(f"找不到設定檔：{path}\n路徑是不是打錯了？（不會自動產生）")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        log(f"沒找到設定檔，已產生預設的：{path}")
        log("裡面的模板檔名都是範例，記得先用 shot 截圖裁好模板再改。")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)
    return merged


def apply_instance(cfg, name):
    """
    把 instances.<name> 的設定套進 cfg。

    只覆寫每台模擬器真正不同的東西（serial、ld_index），流程、模板、命中條件
    全部共用一份設定檔——分成兩份的話兩邊會各自漂移，改了一邊忘了另一邊。
    """
    if not name:
        return cfg
    table = cfg.get("instances") or {}
    if name not in table:
        have = "、".join(sorted(table)) or "（設定檔裡沒有 instances 區塊）"
        sys.exit(f"設定檔裡沒有實例 {name!r}。目前有：{have}")

    over = dict(table[name] or {})
    # adb 底下的鍵要逐個蓋，不能整塊換掉。load_config 的 merge 是淺層的
    # dict.update，整塊換會把 exe、scale、offset、package 這些一起弄掉。
    adb = dict(cfg.get("adb") or {})
    for k in ("serial", "ld_index", "exe", "package", "activity"):
        if k in over:
            adb[k] = over.pop(k)
    cfg["adb"] = adb
    cfg.update(over)          # 其餘鍵（若有）當一般覆寫
    return cfg


def abs_dir(cfg, key, per_instance=False):
    d = cfg.get(key) or "."
    if not os.path.isabs(d):
        d = os.path.join(BASE_DIR, d)
    # 模板是唯讀共用的，不能分家（42 個模板不該複製兩份）；
    # 只有會寫入的目錄才要每台一份。
    if per_instance and INSTANCE:
        d = os.path.join(d, INSTANCE)
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------- 螢幕擷取

class Capture:
    def __init__(self, cfg):
        self.cfg = cfg
        # mss 10 之後改叫 MSS，舊版沒有，兩邊都相容
        self._sct = getattr(mss, "MSS", None) or mss.mss
        self._sct = self._sct()
        self._win = None
        # 走 ADB 的話完全不碰視窗，使用者可以同時用電腦
        self.adb = Adb(cfg) if cfg.get("input_mode") == "adb" else None

    def _window(self):
        # ADB 模式下畫面和輸入都不經過視窗，沒有任何理由去列舉或搬動它。
        # 而且雙開時兩台模擬器標題一樣，下面的 wins[0] 抓到哪台是隨機的，
        # 兩個程序又都想把「自己那台」搬到同一個 window_pos——結果是互相
        # 搬對方的視窗，而且會在使用者用電腦的時候亂動視窗。
        if self.adb:
            return None

        title = (self.cfg.get("window_title") or "").strip()
        if not title:
            return None

        if self._win is not None:
            try:
                if self._win.title and title.lower() in self._win.title.lower():
                    self._enforce_pos()     # 快取路徑也要檢查，否則視窗被移動就不會歸位
                    return self._win
            except Exception:
                pass
            self._win = None

        wins = [w for w in gw.getWindowsWithTitle(title) if (w.title or "").strip()]
        if not wins:
            raise RuntimeError(
                f"找不到標題含「{title}」的視窗。確認遊戲開著，"
                "或把設定檔的 window_title 改成空字串直接抓全螢幕。"
            )
        self._win = wins[0]
        self._enforce_pos()
        return self._win

    def _enforce_pos(self):
        """
        把視窗固定在指定位置。模板全都綁定視窗座標，視窗一旦被移動、
        底部被 Windows 工作列蓋住，擷取到的就會混進工作列，整組模板全失效。

        只在視窗尺寸符合遊戲該有的樣子時才移動——遊戲閃退後模擬器會
        轉成橫向，那個尺寸硬移到同一個座標會有一半跑到螢幕外。
        """
        pos = self.cfg.get("window_pos")
        if not pos or self._win is None:
            return
        want_size = self.cfg.get("game_window_size")
        if want_size:
            try:
                if (self._win.width, self._win.height) != tuple(want_size):
                    return          # 尺寸不對＝遊戲沒在跑，別動它
            except Exception:
                return
        want = (int(pos[0]), int(pos[1]))
        try:
            if (self._win.left, self._win.top) != want:
                log(f"視窗在 ({self._win.left},{self._win.top})，移回 {want}")
                self._win.moveTo(*want)
                time.sleep(0.6)
        except Exception as e:
            log(f"！移動視窗失敗：{e}")

    def region(self):
        """回傳 (left, top, width, height)，螢幕絕對座標。"""
        r = self.cfg.get("region")
        if r:
            return tuple(int(v) for v in r)

        w = self._window()
        if w is None:
            m = self._sct.monitors[int(self.cfg.get("monitor", 1))]
            return (m["left"], m["top"], m["width"], m["height"])

        try:
            if w.isMinimized:
                w.restore()
                time.sleep(0.4)
        except Exception:
            pass

        pl, pt, pr, pb = self.cfg.get("window_padding") or [0, 0, 0, 0]
        left = int(w.left) + int(pl)
        top = int(w.top) + int(pt)
        width = int(w.width) - int(pl) - int(pr)
        height = int(w.height) - int(pt) - int(pb)
        if width <= 0 or height <= 0:
            raise RuntimeError(f"算出來的擷取範圍不合理：{width}x{height}，檢查 window_padding。")
        return (left, top, width, height)

    def focus(self):
        if self.adb or not self.cfg.get("focus_window", True):
            return
        w = self._window()
        if w is None:
            return
        try:
            w.activate()
        except Exception:
            # pygetwindow 的 activate 在某些全螢幕遊戲會丟例外，忽略即可
            try:
                ctypes.windll.user32.SetForegroundWindow(w._hWnd)
            except Exception:
                pass
        time.sleep(0.2)

    def ensure_foreground(self):
        """
        mss 擷取的是螢幕上那塊座標，不是視窗內容——別的視窗蓋上來就會
        拍到那個視窗。長時間無人值守時這會讓整輪靜默跑錯，所以每次擷取
        前先確認模擬器在前景。ADB 模式不需要，畫面直接從模擬器來。
        """
        if self.adb or not self.cfg.get("focus_window", True):
            return
        try:
            w = self._window()
            if w is None:
                return
            if ctypes.windll.user32.GetForegroundWindow() != w._hWnd:
                self._lost_focus = getattr(self, "_lost_focus", 0) + 1
                if self._lost_focus in (1, 10) or self._lost_focus % 50 == 0:
                    log(f"！模擬器不在前景（第 {self._lost_focus} 次），搶回焦點")
                self.focus()
        except Exception:
            pass

    def grab(self):
        """回傳 (BGR 影像, region)。"""
        if self.adb:
            # ADB 模式：畫面已對齊舊座標系，region 原點固定為 0
            img = self.adb.grab()
            if img is None:
                raise RuntimeError("ADB 截圖失敗，模擬器可能沒開或 ADB 斷線")
            return img, (0, 0, img.shape[1], img.shape[0])

        self.ensure_foreground()
        left, top, width, height = self.region()
        raw = self._sct.grab({"left": left, "top": top, "width": width, "height": height})
        frame = cv2.cvtColor(np.asarray(raw), cv2.COLOR_BGRA2BGR)
        return frame, (left, top, width, height)


# ---------------------------------------------------------------- 模板比對

class Match:
    __slots__ = ("score", "x", "y", "w", "h", "scale")

    def __init__(self, score, x, y, w, h, scale):
        self.score, self.x, self.y = score, x, y
        self.w, self.h, self.scale = w, h, scale

    def __repr__(self):
        return f"<Match {self.score:.3f} @({self.x},{self.y}) {self.w}x{self.h} x{self.scale}>"


_TEMPLATE_CACHE = {}


def load_template(path):
    """回傳 (bgr, mask)；mask 只有 PNG 帶透明度才有。讀不到回 (None, None)。"""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None, None

    cached = _TEMPLATE_CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1], cached[2]

    img = imread_unicode(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        return None, None

    mask = None
    if img.ndim == 3 and img.shape[2] == 4:
        alpha = img[:, :, 3]
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        if alpha.min() < 255:
            mask = cv2.merge([alpha, alpha, alpha])
    elif img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    _TEMPLATE_CACHE[path] = (mtime, img, mask)
    return img, mask


def match_template(frame, tmpl, mask, scales):
    """在 frame 裡找 tmpl，回傳分數最高的 Match（不管有沒有過門檻）。"""
    best = None
    fh, fw = frame.shape[:2]

    # 越接近原尺寸的先試。分數打平時（例如整片同色）才不會被縮小的模板搶走
    for s in sorted(scales, key=lambda v: abs(v - 1.0)):
        if s == 1.0:
            t, m = tmpl, mask
        else:
            nw, nh = max(1, int(tmpl.shape[1] * s)), max(1, int(tmpl.shape[0] * s))
            t = cv2.resize(tmpl, (nw, nh), interpolation=cv2.INTER_AREA)
            m = cv2.resize(mask, (nw, nh), interpolation=cv2.INTER_AREA) if mask is not None else None

        th, tw = t.shape[:2]
        if th > fh or tw > fw:
            continue

        try:
            res = cv2.matchTemplate(frame, t, cv2.TM_CCOEFF_NORMED, mask=m)
        except cv2.error:
            # 部分 OpenCV 版本的 CCOEFF 不吃 mask，退回 CCORR
            try:
                res = cv2.matchTemplate(frame, t, cv2.TM_CCORR_NORMED, mask=m)
            except cv2.error:
                res = cv2.matchTemplate(frame, t, cv2.TM_CCOEFF_NORMED)

        res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)
        _, maxv, _, maxloc = cv2.minMaxLoc(res)
        # 帶 mask 比對在整片同色的地方會算出略大於 1 的分數，夾回來
        maxv = min(1.0, float(maxv))
        if best is None or maxv > best.score + 1e-6:
            best = Match(maxv, maxloc[0] + tw // 2, maxloc[1] + th // 2, tw, th, s)

    return best


# ---------------------------------------------------------------- 滑鼠鍵盤

def set_clipboard(text):
    """
    用 Win32 API 寫剪貼簿。不要用 tkinter——它採延遲提供的擁有權模型，
    別的程式（例如雷電模擬器）取不到內容，貼上會是空的。
    """
    CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

    data = text.encode("utf-16-le") + b"\x00\x00"
    if not u32.OpenClipboard(None):
        raise RuntimeError("開不了剪貼簿，可能被別的程式佔用")
    try:
        u32.EmptyClipboard()
        h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        p = k32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        k32.GlobalUnlock(h)
        u32.SetClipboardData(CF_UNICODETEXT, h)
    finally:
        u32.CloseClipboard()


class Adb:
    """
    走 ADB 直接對 Android 下指令，不碰實體滑鼠鍵盤，也不需要視窗在前景。
    這樣使用者可以一邊用電腦，腳本一邊在背景跑。

    ADB 抓到的是 Android 原始畫面（900x1600），跟原本用視窗擷取的
    座標系不同。為了讓既有的模板全部沿用，擷取後會縮放平移回舊座標系，
    送點擊時再反推回 Android 座標。
    """

    def __init__(self, cfg):
        a = cfg.get("adb", {})
        self.exe = a.get("exe", r"C:\LDPlayer\LDPlayer14\adb.exe")
        self.serial = a.get("serial", "emulator-5554")
        self.scale = float(a.get("scale", 0.62))
        self.ox = int(a.get("offset", [3, 40])[0])
        self.oy = int(a.get("offset", [3, 40])[1])
        self.canvas = tuple(a.get("canvas", [603, 1031]))
        self.package = a.get("package", "")
        self.activity = a.get("activity", "")
        # ldconsole 路徑：跟 adb.exe 同目錄
        ld_dir = os.path.dirname(self.exe)
        self.ldconsole = a.get("ldconsole", os.path.join(ld_dir, "ldconsole.exe"))
        # ld_index 一定要跟 serial 指的是同一台。沒對齊的話 input_text 會把文字
        # 打到另一台模擬器的視窗裡——雙開時這種錯很難查，所以設定檔要明確寫。
        self.ld_index = int(a.get("ld_index", 0))
        # 開跑前用來驗證解析度，見 _check_adb_ready()
        self.cfg_device_size = a.get("device_size") or None

    # --- 底層 ---------------------------------------------------

    def _run(self, args, binary=False, timeout=30):
        cmd = [self.exe, "-s", self.serial] + args
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return r.stdout if binary else r.stdout.decode("utf-8", "replace")

    def shell(self, cmd, timeout=30):
        return self._run(["shell"] + cmd.split(), timeout=timeout)

    def alive(self):
        try:
            out = self._run(["get-state"], timeout=10).strip()
            return out == "device"
        except Exception:
            return False

    # --- 畫面 ---------------------------------------------------

    def grab(self):
        """回傳已對齊舊座標系的 BGR 影像，失敗回 None。"""
        try:
            raw = self._run(["exec-out", "screencap", "-p"], binary=True, timeout=30)
        except Exception as e:
            log(f"！ADB 截圖失敗：{e}")
            return None
        if not raw:
            return None
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None
        return self.to_canvas(img)

    def to_canvas(self, img):
        W, H = self.canvas
        sw, sh = int(img.shape[1] * self.scale), int(img.shape[0] * self.scale)
        small = cv2.resize(img, (sw, sh), interpolation=cv2.INTER_AREA)
        out = np.zeros((H, W, 3), np.uint8)
        ex, ey = min(self.ox + sw, W), min(self.oy + sh, H)
        out[self.oy:ey, self.ox:ex] = small[:ey - self.oy, :ex - self.ox]
        return out

    def to_device(self, x, y):
        """舊座標系 -> Android 實際座標"""
        return int((x - self.ox) / self.scale), int((y - self.oy) / self.scale)

    # --- 輸入 ---------------------------------------------------

    def tap(self, x, y):
        dx, dy = self.to_device(x, y)
        self.shell(f"input tap {dx} {dy}")

    def swipe(self, x1, y1, x2, y2, ms=400):
        a = self.to_device(x1, y1)
        b = self.to_device(x2, y2)
        self.shell(f"input swipe {a[0]} {a[1]} {b[0]} {b[1]} {int(ms)}")

    def key(self, keycode):
        self.shell(f"input keyevent {keycode}")

    def launch_app(self):
        if self.activity:
            self.shell(f"am start -n {self.activity}")
        elif self.package:
            self.shell(f"monkey -p {self.package} 1")

    def app_alive(self, pkg):
        """行程是否還在（不管它有沒有在最前面）。"""
        try:
            return bool(self.shell(f"pidof {pkg}", timeout=15).strip())
        except Exception:
            return False

    def current_app(self):
        out = self.shell("dumpsys window", timeout=20)
        for line in out.splitlines():
            if "mCurrentFocus" in line or "mFocusedApp" in line:
                return line.strip()
        return ""

    def input_text(self, text):
        """用 ldconsole 直接輸入文字（支援中文），不需要視窗焦點或剪貼簿。"""
        cmd = [self.ldconsole, "action", "--index", str(self.ld_index),
               "--key", "call.input", "--value", text]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=15)
            return r.returncode == 0
        except Exception as e:
            log(f"！ldconsole 輸入文字失敗：{e}")
            return False


def move_mouse(x, y, cfg):
    if cfg.get("move_method", "win32") == "win32":
        ctypes.windll.user32.SetCursorPos(int(x), int(y))
    else:
        pydirectinput.moveTo(int(x), int(y))


def click_screen(x, y, cfg):
    move_mouse(x, y, cfg)
    time.sleep(float(cfg.get("move_delay", 0.08)))
    pydirectinput.mouseDown()
    time.sleep(float(cfg.get("click_hold", 0.06)))
    pydirectinput.mouseUp()
    time.sleep(float(cfg.get("action_delay", 0.35)))


# ---------------------------------------------------------------- OCR

def setup_ocr(cfg):
    """回傳 True 表示 OCR 可用。"""
    if pytesseract is None:
        return False
    cmd = (cfg.get("tesseract_cmd") or "").strip()
    if not cmd:
        for guess in (
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ):
            if os.path.exists(guess):
                cmd = guess
                break
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _prep_for_ocr(binary, scale=3.0):
    """放大 + 反白 + 外圍留白。Tesseract 沒有留白時常常整段讀不到。"""
    m = cv2.resize(binary, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    m = cv2.bitwise_not(m)          # 轉成黑字白底
    return cv2.copyMakeBorder(m, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)


def ocr_text(img, lang):
    """
    讀小塊文字。主要手段是白字遮罩（高亮度＋低飽和度）——遊戲卡面的
    白色字疊在會發光變色的背景上，用灰階 Otsu 會被背景亮度帶偏，
    改成只挑「接近白色」的像素就跟背景顏色無關了。讀不到再退回 Otsu。
    """
    if pytesseract is None:
        return ""

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    white = cv2.inRange(hsv, np.array([0, 0, 200], np.uint8),
                             np.array([179, 30, 255], np.uint8))

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    best = ""
    for variant in (white, otsu, cv2.bitwise_not(otsu)):
        try:
            txt = pytesseract.image_to_string(_prep_for_ocr(variant),
                                              lang=lang, config="--psm 7")
        except Exception as e:
            log(f"OCR 失敗：{e}")
            return ""
        if len(txt.strip()) > len(best.strip()):
            best = txt
        if best.strip():
            break               # 白字遮罩通常一次就中，不用每種都跑
    return best


def ocr_digits(img):
    """
    只讀數字（卡片左上角的 OVR）。限定字元集能大幅提升準確度——
    不限定的話 6 容易被讀成 b、8 讀成 B。讀不到回 None。
    """
    if pytesseract is None:
        return None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([0, 0, 200], np.uint8),
                            np.array([179, 40, 255], np.uint8))
    prepped = _prep_for_ocr(mask, scale=4.0)
    try:
        txt = pytesseract.image_to_string(
            prepped, lang="eng",
            config="--psm 7 -c tessedit_char_whitelist=0123456789")
    except Exception as e:
        log(f"數字 OCR 失敗：{e}")
        return None
    digits = "".join(c for c in txt if c.isdigit())
    return int(digits) if digits else None


def crop_region(frame, box):
    """box 是擷取範圍內的相對座標 [x, y, w, h]；None 就整張。"""
    if not box:
        return frame
    x, y, w, h = (int(v) for v in box)
    fh, fw = frame.shape[:2]
    x, y = max(0, x), max(0, y)
    w, h = min(w, fw - x), min(h, fh - y)
    if w <= 0 or h <= 0:
        return frame
    return frame[y:y + h, x:x + w]


# ---------------------------------------------------------------- 主流程

class Bot:
    def __init__(self, cfg, cap):
        self.cfg = cfg
        self.cap = cap
        self.tpl_dir = abs_dir(cfg, "template_dir")
        self.hit_dir = abs_dir(cfg, "hit_dir", per_instance=True)
        self.scales = [float(s) for s in cfg.get("scales", [1.0])] or [1.0]
        self.ocr_ok = setup_ocr(cfg)
        self._warned = set()
        # 使用者按 F12 / Ctrl+C 主動停的話要記下來，讓 main() 用離開碼 130
        # 告訴看門狗「這不是意外結束，別重開」。以前分不出來，所以你按 F12
        # 停掉之後看門狗又把它拉起來繼續跑。
        self.stopped_by_user = False
        # 遇到要人判斷的狀況（見 NeedsHuman）。也不能重開——重開就會走
        # app_reset 把現場洗掉。
        self.needs_human = False
        self._fail_where = ""      # 最後失敗在哪一步，由 run_flow 填
        self._fail_ask = False     # 那一步有沒有標 on_fail: ask

    # --- 基本動作 -------------------------------------------------

    def check_stop(self):
        if f12_pressed():
            raise StopRequested()

    def grab(self):
        self.check_stop()
        return self.cap.grab()

    def template_path(self, name):
        if os.path.isabs(name) and os.path.exists(name):
            return name
        for p in (os.path.join(self.tpl_dir, name),
                  os.path.join(self.tpl_dir, name + ".png"),
                  os.path.join(BASE_DIR, name)):
            if os.path.exists(p):
                return p
        return os.path.join(self.tpl_dir, name)

    def find(self, name, frame, threshold=None):
        """找到且過門檻就回 Match，否則回 None。"""
        path = self.template_path(name)
        tmpl, mask = load_template(path)
        if tmpl is None:
            if path not in self._warned:
                self._warned.add(path)
                log(f"！讀不到模板：{path}")
            return None
        thr = float(threshold if threshold is not None else self.cfg.get("match_threshold", 0.85))
        m = match_template(frame, tmpl, mask, self.scales)
        return m if (m and m.score >= thr) else None

    def _swipe(self, region, step):
        """
        模擬觸控滑動。分段移動是必要的——直接跳到終點的話，
        模擬器會判定成點擊而不是滑動。
        """
        x1, y1 = step["from"]
        x2, y2 = step["to"]
        steps_n = int(step.get("steps", 20))
        hold = float(step.get("hold", 0.02))
        left, top = region[0], region[1]

        if self.cap.adb:
            self.cap.adb.swipe(x1, y1, x2, y2, ms=int(steps_n * hold * 1000) + 300)
            return
        move_mouse(left + x1, top + y1, self.cfg)
        time.sleep(0.15)
        pydirectinput.mouseDown()
        time.sleep(0.12)
        for i in range(1, steps_n + 1):
            move_mouse(left + x1 + (x2 - x1) * i / steps_n,
                       top + y1 + (y2 - y1) * i / steps_n, self.cfg)
            time.sleep(hold)
        time.sleep(0.12)
        pydirectinput.mouseUp()

    def click_in_region(self, x, y, region, offset=(0, 0)):
        tx, ty = int(x) + int(offset[0]), int(y) + int(offset[1])
        if self.cap.adb:
            self.cap.adb.tap(tx, ty)
            time.sleep(float(self.cfg.get("action_delay", 0.35)))
            return
        click_screen(region[0] + tx, region[1] + ty, self.cfg)

    def dismiss_popups(self, frame, region, skip=None):
        """
        關掉會在不特定時機跳出來的彈窗（例如綁定帳號）。這種東西沒辦法
        寫成流程裡的某一步，只能在每次等待時順手檢查。
        回傳 True 表示這次有關掉東西。
        """
        for p in self.cfg.get("dismiss_popups", []):
            name = p.get("template")
            if not name or name == skip:
                continue
            m = self.find(name, frame, p.get("threshold"))
            if m:
                log(f"    關閉彈窗：{p.get('note') or name}")
                self.click_in_region(m.x, m.y, region, p.get("offset", [0, 0]))
                time.sleep(float(p.get("wait", 2.0)))
                return True
        return False

    def wait_template(self, name, timeout, threshold=None, want=True):
        """want=True 等它出現，False 等它消失。回傳 Match / True / None。"""
        deadline = time.time() + float(timeout)
        interval = float(self.cfg.get("poll_interval", 0.5))
        while time.time() < deadline:
            frame, region = self.grab()
            m = self.find(name, frame, threshold)
            if want and m:
                return m, region
            if not want and not m:
                return True, region
            # 等的東西還沒出現時，順手把擋路的彈窗關掉；
            # skip 是避免「正在等的正好就是那個彈窗」時自己把自己關掉
            if self.dismiss_popups(frame, region, skip=name):
                continue
            time.sleep(interval)
        return None, None

    # --- 單一步驟 -------------------------------------------------

    def do_step(self, step):
        """回傳 True＝這步完成，False＝逾時沒完成。"""
        do = step.get("do")
        timeout = float(step.get("timeout", 60))
        thr = step.get("threshold")
        note = step.get("note", "")
        label = f"{do} {step.get('template') or step.get('pos') or step.get('key') or ''}"
        if note:
            label += f"（{note}）"
        log(f"  → {label}")

        if do == "sleep":
            end = time.time() + float(step.get("sec", 1.0))
            while time.time() < end:
                self.check_stop()
                time.sleep(min(0.2, max(0.0, end - time.time())))
            return True

        if do == "key":
            k = str(step.get("key", "esc"))
            for _ in range(int(step.get("times", 1))):
                self.check_stop()
                if self.cap.adb:
                    # Android 沒有 ESC，等價的是返回鍵
                    self.cap.adb.key({"esc": 4, "back": 4, "home": 3,
                                      "enter": 66}.get(k, 4))
                else:
                    pydirectinput.press(k)
                time.sleep(float(step.get("interval", 0.3)))
            return True

        if do == "app_restart":
            # 只是把 App 關掉重開，不動資料。帳號、進度、已授權的權限都留著，
            # 所以不必再走一次條款／球隊瀏覽／訪客登入（那要四分半）。
            # 卡在奇怪畫面時先試這個，不要動不動就清資料。
            if not self.cap.adb:
                log("！app_restart 只能在 ADB 模式下用")
                return False
            pkg = self.cfg.get("adb", {}).get("package", "")
            if not pkg:
                log("！設定檔沒填 adb.package")
                return False
            log(f"    重開 {pkg}（不清資料）…")
            self.cap.adb.shell(f"am force-stop {pkg}", timeout=30)
            time.sleep(float(step.get("gap", 2.0)))
            self.cap.adb.launch_app()
            return True

        if do == "app_reset":
            # 用 ADB 清掉 App 資料再重開＝全新帳號。比在遊戲介面裡走十幾步
            # 可靠得多，也不需要打中文。代價是冷啟動比較久。
            if not self.cap.adb:
                log("！app_reset 只能在 ADB 模式下用")
                return False
            pkg = self.cfg.get("adb", {}).get("package", "")
            if not pkg:
                log("！設定檔沒填 adb.package")
                return False
            log(f"    清除 {pkg} 的資料…")
            out = self.cap.adb.shell(f"pm clear {pkg}", timeout=90)
            if "Success" not in out:
                log(f"！pm clear 失敗：{out.strip()[:100]}")
                return False
            # pm clear 會把 runtime 權限一起撤銷，重開時 Android 就彈出授權
            # 對話框擋在遊戲前面。那個對話框屬於 permissioncontroller，不是
            # 遊戲，所以連 game_running() 都會誤判成閃退，然後白等到逾時。
            # 先用 pm grant 補回去，對話框就根本不會出現——比事後去點它可靠，
            # 也不用多一個模板。
            for perm in self.cfg.get("adb", {}).get("grant_permissions", []):
                self.cap.adb.shell(f"pm grant {pkg} {perm}", timeout=20)
            time.sleep(2.0)
            self.cap.adb.launch_app()
            log("    已重新啟動，等待首次啟動流程…")
            return True

        if do == "click_color":
            # 依顏色找目標，不綁特定圖案。用在「點那張綠色的卡」這種
            # 每個帳號內容都不同、但顏色固定的情境。
            deadline = time.time() + timeout
            hue = step.get("hue", [35, 85])
            sat_min = int(step.get("sat_min", 100))
            val_min = int(step.get("val_min", 60))
            min_area = int(step.get("min_area", 500))
            # 上限是防呆：畫面還沒轉場完時，背景草地會變成一大塊綠色，
            # 面積比卡片大一個數量級，沒有上限就會誤點。
            max_area = int(step.get("max_area", 0)) or None
            box = step.get("region")

            while time.time() < deadline:
                frame, region = self.grab()
                sub = crop_region(frame, box)
                ox, oy = (int(box[0]), int(box[1])) if box else (0, 0)

                hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
                mask = cv2.inRange(hsv,
                                   np.array([hue[0], sat_min, val_min], np.uint8),
                                   np.array([hue[1], 255, 255], np.uint8))
                mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))

                n, _, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
                best, best_area = None, 0
                for i in range(1, n):
                    area = stats[i, cv2.CC_STAT_AREA]
                    if area < min_area or (max_area and area > max_area):
                        continue
                    if area > best_area:
                        best_area, best = area, cent[i]

                if best is not None:
                    cx, cy = int(best[0]) + ox, int(best[1]) + oy
                    log(f"    找到色塊 面積={best_area} 中心=({cx},{cy})")
                    self.click_in_region(cx, cy, region, step.get("offset", [0, 0]))
                    return True
                time.sleep(float(self.cfg.get("poll_interval", 0.5)))
            return False

        if do == "swipe":
            _, region = self.grab()
            self._swipe(region, step)
            time.sleep(float(step.get("settle", 1.0)))
            return True

        if do == "swipe_until":
            # 滑動有慣性，同樣的距離每次停的位置不一樣。所以小步滑、
            # 每滑一次就找一次目標，找到就停。比寫死滑動距離可靠得多。
            target = step["template"]
            max_swipes = int(step.get("max_swipes", 12))
            settle = float(step.get("settle", 1.2))
            deadline = time.time() + timeout

            for i in range(max_swipes + 1):
                self.check_stop()
                frame, region = self.grab()
                if self.find(target, frame, thr):
                    if i:
                        log(f"    滑了 {i} 次找到 {target}")
                    return True
                if i == max_swipes or time.time() > deadline:
                    break
                self._swipe(region, step)
                time.sleep(settle)

            log(f"    滑了 {max_swipes} 次仍找不到 {target}")
            return False

        if do == "paste":
            # 模擬器打不進中文，走剪貼簿。
            # ADB 模式：用 ldconsole action --key call.input 直接輸入，
            #           不需要視窗焦點、不需要剪貼簿、中文也沒問題。
            # 視窗模式：寫 Windows 剪貼簿 → Ctrl+V。
            m, region = self.wait_template(step["template"], timeout, thr, want=True)
            if not m:
                return False
            text = str(step.get("text", ""))
            self.click_in_region(m.x, m.y, region)
            time.sleep(float(step.get("focus_wait", 1.5)))
            if self.cap.adb:
                if not self.cap.adb.input_text(text):
                    # 這裡刻意不退回剪貼簿 + Ctrl+V。ADB 模式的前提是使用者
                    # 正在用這台電腦做別的事——往他當下的作用視窗送 Ctrl+V，
                    # 會把文字貼進他正在打的東西裡。讓這一步失敗、交給復原
                    # 流程處理，比污染使用者的視窗好。
                    log("！ldconsole 輸入失敗（ld_index 對嗎？）。"
                        "ADB 模式不退回剪貼簿，這步算失敗。")
                    return False
            else:
                set_clipboard(text)
                time.sleep(0.3)
                pydirectinput.keyDown("ctrl")
                time.sleep(0.08)
                pydirectinput.press("v")
                time.sleep(0.08)
                pydirectinput.keyUp("ctrl")
            time.sleep(float(step.get("paste_wait", 2.0)))
            return True

        if do == "click_at":
            _, region = self.grab()
            pos = step.get("pos", [0, 0])
            self.click_in_region(pos[0], pos[1], region)
            return True

        if do == "wait_for":
            m, _ = self.wait_template(step["template"], timeout, thr, want=True)
            return m is not None

        if do == "wait_gone":
            ok, _ = self.wait_template(step["template"], timeout, thr, want=False)
            return ok is not None

        if do == "click":
            m, region = self.wait_template(step["template"], timeout, thr, want=True)
            if not m:
                return False
            self.click_in_region(m.x, m.y, region, step.get("offset", [0, 0]))
            return True

        if do in ("spam", "spam_at"):
            # until 可以給一個或一串。給一串代表「這幾種畫面出現任一個都算成功」，
            # 用在結果不只一種的情況——例如跳過教學有時會跳確認框、有時直接進主畫面。
            until = step.get("until")
            untils = [until] if isinstance(until, str) and until else (until or [])
            interval = float(step.get("interval", 1.0))
            deadline = time.time() + timeout
            while time.time() < deadline:
                frame, region = self.grab()
                hit = next((u for u in untils if self.find(u, frame, thr)), None)
                if hit:
                    if len(untils) > 1:
                        log(f"    以 {hit} 結束")
                    return True
                # 這種長等待也可能被彈窗擋住
                if self.dismiss_popups(frame, region, skip=step.get("template")):
                    continue
                if do == "spam_at":
                    pos = step.get("pos", [0, 0])
                    self.click_in_region(pos[0], pos[1], region)
                else:
                    m = self.find(step["template"], frame, thr)
                    if m:
                        self.click_in_region(m.x, m.y, region, step.get("offset", [0, 0]))
                    elif not untils:
                        return True      # 按鈕自己不見了＝點完了
                time.sleep(interval)
            # 逾時。spam_at 沒設 until 就是「固定點滿這段時間」，算正常結束
            return do == "spam_at" and not untils

        log(f"！不認識的步驟：{do}，跳過")
        return True

    def run_flow(self, steps, label):
        """
        整段流程都做完才回 True。

        失敗時把「是哪一步」和「這步有沒有標 on_fail: ask」記在 self 上，
        交給 run_round 決定要重置還是問人——那邊才知道遊戲有沒有閃退，
        而這個判斷決定了能不能自動處理。
        """
        self._fail_where = ""
        self._fail_ask = False
        if not steps:
            return True
        log(f"[{label}] 共 {len(steps)} 步")
        for i, step in enumerate(steps, 1):
            ok = self.do_step(step)
            if ok:
                continue
            if step.get("optional"):
                log(f"  ~ 第 {i} 步逾時，但標了 optional，繼續")
                continue
            log(f"  ✗ 第 {i} 步逾時：{step}")
            self._fail_where = (f"[{label}] 第 {i} 步："
                                f"{step.get('note') or step.get('template')}")
            self._fail_ask = step.get("on_fail") == "ask"
            return False
        return True

    # --- 判定 -----------------------------------------------------

    def judge(self, frame):
        for name in self.cfg.get("hit_templates", []):
            m = self.find(name, frame)
            if m:
                return True, f"圖片命中 {name}（分數 {m.score:.3f}）"

        # 可以列多組目標，任一組滿足就算命中。每組是「名字關鍵字 ＋ 指定 OVR」。
        targets = self.cfg.get("hit_targets") or []
        if not targets and self.cfg.get("hit_keywords"):      # 舊格式相容
            targets = [{"keywords": self.cfg["hit_keywords"],
                        "ovr": self.cfg.get("hit_ovr"), "note": "目標"}]
        if not targets:
            return False, "沒設定任何目標"

        if not self.ocr_ok:
            log("！設了名字目標但 OCR 不能用")
            return False, "OCR 不可用"

        text = ocr_text(crop_region(frame, self.cfg.get("ocr_region")),
                        self.cfg.get("ocr_lang", "eng"))
        flat = " ".join(text.split())
        if flat:
            log(f"    OCR 讀到：{flat[:80]}")

        misses = []
        for tg in targets:
            kw = next((k for k in tg.get("keywords", []) if k and k in text), None)
            if not kw:
                continue
            label = tg.get("note") or kw
            want = tg.get("ovr")
            if not want:
                return True, f"命中 {label}"
            ovr = ocr_digits(crop_region(frame, self.cfg.get("ovr_region")))
            log(f"    名字命中 {label}，卡片 OVR 讀到：{ovr}")
            if ovr is not None and int(ovr) == int(want):
                return True, f"命中 {label} 且 OVR={ovr}"
            misses.append(f"{label} 的 OVR 是 {ovr}，要 {want}")

        return False, ("；".join(misses) if misses else "沒有符合的目標")

    def save_hit(self, frame, idx):
        path = os.path.join(self.hit_dir, f"hit_{datetime.now():%Y%m%d_%H%M%S}_r{idx:03d}.png")
        imwrite_unicode(path, frame)
        return path

    # --- 遊戲閃退偵測與重開 ----------------------------------------

    def game_running(self):
        """
        遊戲跑起來時模擬器是直向的；遊戲閃退回桌面會轉成橫向。
        用視窗尺寸判斷比截圖比對快得多，也不會被載入畫面騙。
        """
        if self.cap.adb:
            pkg = self.cfg.get("adb", {}).get("package", "")
            if not pkg:
                return True
            if not self.cap.adb.alive():
                return False
            # 問「行程還活著嗎」，不是問「它在不在最前面」。系統對話框
            # （權限授權、Play 服務更新之類）蓋上來時前景套件不是遊戲，
            # 但遊戲根本沒死——照前景判斷會誤報閃退，然後去重開一個
            # 活著的遊戲，把畫面推到更奇怪的狀態。
            return self.cap.adb.app_alive(pkg)
        want = self.cfg.get("game_window_size")
        if not want:
            return True
        try:
            _, _, w, h = self.cap.region()
        except Exception:
            return False
        return (w, h) == tuple(want)

    def relaunch_game(self):
        """
        遊戲閃退後回模擬器桌面點圖示重開。回傳 True 表示遊戲已載入。
        """
        log("偵測到遊戲不在執行中，嘗試重新開啟…")

        # ADB 模式直接用 am start，比在桌面找圖示點擊可靠得多
        if self.cap.adb:
            if not self.cap.adb.alive():
                log("  ADB 連不上模擬器，無法重開")
                return False
            self.cap.adb.launch_app()
            deadline = time.time() + float(self.cfg.get("relaunch_timeout", 240))
            while time.time() < deadline:
                self.check_stop()
                time.sleep(5.0)
                if not self.game_running():
                    continue
                for name in ("創立球隊.png", "主畫面_已選.png", "Skip.png"):
                    m, _ = self.wait_template(name, 40, want=True)
                    if m:
                        log(f"  遊戲已載入（認出 {name}）")
                        return True
                log("  遊戲起來了但認不出畫面")
                return False
            log("！重開遊戲逾時")
            return False

        icon = self.cfg.get("launcher_icon", "遊戲圖示.png")
        timeout = float(self.cfg.get("relaunch_timeout", 240))
        deadline = time.time() + timeout

        # 桌面可能停在別的分頁（例如被開起來的 Play 商店），先切回第一個分頁
        try:
            _, _, w, h = self.cap.region()
            self.cap.focus()
            time.sleep(0.5)
            _, region = self.grab()
            self.click_in_region(75, 21, region)     # 左上第一個分頁
            time.sleep(2.0)
        except Exception as e:
            log(f"  切分頁失敗（不影響後續）：{e}")

        while time.time() < deadline:
            self.check_stop()
            if self.game_running():
                log("  視窗已轉回直向，等待遊戲載入…")
                # 等到能認出遊戲的任一個已知畫面為止
                for name in ("創立球隊.png", "主畫面_已選.png", "Skip.png"):
                    m, _ = self.wait_template(name, 60, want=True)
                    if m:
                        log(f"  遊戲已載入（認出 {name}）")
                        return True
                log("  視窗轉直向了但認不出畫面")
                return False

            frame, region = self.grab()
            m = self.find(icon, frame)
            if m:
                log(f"  找到遊戲圖示，點擊啟動（分數 {m.score:.3f}）")
                self.click_in_region(m.x, m.y, region)
                time.sleep(8.0)
            else:
                time.sleep(2.0)

        log("！重開遊戲逾時")
        return False

    # --- 一輪 -----------------------------------------------------

    def run_round(self, idx):
        """回傳 'hit' / 'miss' / 'fail'。"""
        log(f"===== 第 {idx} 輪 =====")
        self.cap.focus()

        # 開跑前先確認遊戲活著，閃退就重開
        if not self.game_running():
            if not self.relaunch_game():
                return "fail"

        if not self.run_flow(self.cfg.get("flow", []), "開場流程"):
            # 步驟失敗最常見的原因就是遊戲中途閃退，先分清楚是哪一種
            if not self.game_running():
                log("步驟失敗是因為遊戲閃退了")
                self._relaunch_and_reset()
                return "fail"
            self._ask_or_recover("開場流程沒跑完")
            return "fail"

        frame, _ = self.grab()
        hit, why = self.judge(frame)
        if hit:
            path = self.save_hit(frame, idx)
            log(f"★ 中了！{why}")
            log(f"  截圖存到：{path}")
            # 主旨和內文都要說清楚是哪一台。雙開時兩台會寄出長得一樣的信，
            # 沒有標記的話你不知道該去哪台模擬器綁定帳號。
            tag = f"[{INSTANCE}]" if INSTANCE else ""
            where = (f"模擬器：{INSTANCE}"
                     f"（{self.cfg.get('adb', {}).get('serial', '')}）\n"
                     if INSTANCE else "")
            send_mail(
                self.cfg,
                subject=f"[MLB 刷初始]{tag} 中了！第 {idx} 輪",
                body=(f"第 {idx} 輪抽到目標卡片。\n\n"
                      f"{where}"
                      f"判定依據：{why}\n"
                      f"截圖：{path}\n\n"
                      "遊戲已停在翻牌畫面，卡片還沒按確認，回來自己收下。"),
                attach=path,
            )
            return "hit"

        log(f"沒中（{why}），重置帳號重跑")
        if not self.run_flow(self.cfg.get("reset_flow", []), "重置流程"):
            # 這裡也要先分辨是不是閃退。沒分辨的話，復原流程會對著
            # 已經關掉的遊戲一路點下去，白跑十幾步才失敗。
            if not self.game_running():
                log("重置流程失敗是因為遊戲閃退了")
                self._relaunch_and_reset()
                return "fail"
            self._ask_or_recover("重置流程卡住了")
            return "fail"
        return "miss"

    def _relaunch_and_reset(self):
        """
        閃退後重開遊戲，並把帳號重置回創立球隊。

        重開本身不夠：閃退不會清掉任何東西，帳號的球隊還在，畫面會停在主畫面。
        下一輪的 A1 在等創立球隊，那個畫面已經不存在了，於是永遠等不到——
        看起來像「重開成功了但還是壞掉」。所以重開之後要接一次遊戲內重置。
        """
        if not self.relaunch_game():
            return
        # relaunch_game 認得三種畫面：創立球隊、主畫面、教學中的 Skip。
        # 只有後兩種代表帳號還帶著進度需要重置；已經在創立球隊就什麼都別做，
        # 不然反而會把一個乾淨的起點again 重置一遍、白花三十秒。
        frame, _ = self.grab()
        if self.find("創立球隊.png", frame):
            log("  重開後就在創立球隊，不用重置")
            return
        log("  重開後帳號還帶著進度（閃退不會清資料），先在遊戲內重置回創立球隊")
        self.run_flow(self.cfg.get("recover_flow", []), "復原流程")

    def self_heal(self):
        """
        關掉 App 直接重開（不刪資料），看能不能自己回到創立球隊。回傳 True＝解決了。

        重開之後有兩種可能：帳號還沒建隊就會停在創立球隊，那就成了；
        已經建隊的話會停在主畫面，得再走一次遊戲內重置才回得去。
        兩種都不碰 App 資料。
        """
        adb = self.cap.adb
        pkg = self.cfg.get("adb", {}).get("package", "")
        if not adb or not pkg:
            log("    自救只能在 ADB 模式下做")
            return False

        log(f"    關掉 {pkg} 再重開（不刪資料）…")
        adb.shell(f"am force-stop {pkg}", timeout=30)
        time.sleep(2.0)
        adb.launch_app()

        deadline = time.time() + float(self.cfg.get("relaunch_timeout", 240))
        while time.time() < deadline:
            self.check_stop()
            time.sleep(5.0)
            if self.game_running():
                break
        else:
            log("    重開後遊戲沒起來")
            return False

        if self.wait_template("創立球隊.png", 60, want=True)[0]:
            return True

        log("    重開後不在創立球隊（帳號還帶著進度），走一次遊戲內重置")
        if not self.run_flow(self.cfg.get("recover_flow", []), "自救重置"):
            return False
        frame, _ = self.grab()
        return bool(self.find("創立球隊.png", frame))

    def wait_for_human(self, why):
        """
        卡住時的處理：先自己試著救，救不起來才通知人並等他處理。

        自救＝關掉 App 重開（不刪資料），必要時再走一次遊戲內重置。
        試滿設定的次數都沒用，才寄信打擾人。

        通知之後就停著等，契約很單純：
        **你只要把畫面弄回創立球隊，它就會自己繼續。**
        不必回來下指令、不必記得重開——人可能在外面收到信，回到家才處理。
        """
        self.needs_human = True
        interval = float(self.cfg.get("human_check_interval", 20))
        limit = float(self.cfg.get("human_wait_timeout", 0))   # 0＝一直等

        log("=" * 46)
        log(f"！卡住了：{why}")

        # 先拍現場再自救。順序反過來的話，自救會關掉 App、重置帳號，
        # 等到真的要通知你時，該給你看的畫面早就不見了。
        shot = ""
        try:
            frame, _ = self.cap.grab()
            shot = os.path.join(
                self.hit_dir, f"needhelp_{datetime.now():%Y%m%d_%H%M%S}.png")
            imwrite_unicode(shot, frame)
            log(f"  現場截圖：{shot}")
        except Exception:
            pass

        tries = int(self.cfg.get("self_heal_attempts", 2))
        for i in range(1, tries + 1):
            log(f"  自救第 {i}/{tries} 次")
            try:
                if self.self_heal():
                    log(f"  自救成功（第 {i} 次），繼續跑，不打擾你。")
                    log("=" * 46)
                    self.needs_human = False
                    return True
            except StopRequested:
                raise
            except Exception as e:
                log(f"    自救出錯：{e}")
        log(f"  自救 {tries} 次都沒用，通知你。")
        log("  畫面保留在原地，帳號沒有被清除。")

        if not self.cfg.get("wait_for_human", True):
            log("  設定是不等人（wait_for_human=false），直接收工。")
            log("=" * 46)
            return False

        log(f"  處理好之後把畫面弄回「創立球隊」，它每 {interval:.0f} 秒檢查一次，"
            "看到就自己繼續。")
        log("=" * 46)

        tag = f"[{INSTANCE}]" if INSTANCE else ""
        send_mail(
            self.cfg,
            subject=f"[MLB 刷初始]{tag} 卡住了，自己救不起來",
            body=(f"{why}\n\n"
                  f"{'模擬器：' + INSTANCE + chr(10) if INSTANCE else ''}"
                  f"已經自己試過 {tries} 次「關掉 App 重開（不刪資料）」，都沒有回到創立球隊，\n"
                  "所以才通知你。附圖是卡住當下的畫面（自救前拍的）。\n\n"
                  "處理方式：把畫面弄回「創立球隊」就好，程式會自己偵測到並繼續，\n"
                  "不用回來重新下指令。"),
            attach=shot or None,
        )

        started = last_beat = time.time()
        while True:
            self.check_stop()          # F12 隨時可以放棄
            time.sleep(interval)
            frame, _ = self.cap.grab()
            if self.find("創立球隊.png", frame):
                waited = (time.time() - started) / 60
                log(f"偵測到畫面已回到創立球隊（等了 {waited:.1f} 分鐘），繼續跑。")
                self.needs_human = False
                send_mail(
                    self.cfg,
                    subject=f"[MLB 刷初始]{tag} 已恢復，繼續跑了",
                    body=f"等了 {waited:.1f} 分鐘，畫面回到創立球隊，已自動繼續。",
                )
                return True
            if limit and (time.time() - started) > limit:
                log(f"！等了超過 {limit / 60:.0f} 分鐘還是沒回到創立球隊，收工。")
                return False
            # log 要持續有動靜，否則看門狗會當成卡住（門檻預設 300 秒）把它殺掉重開，
            # 重開就會走復原流程，把留給你看的現場洗掉。
            if time.time() - last_beat > 120:
                last_beat = time.time()
                log(f"  還在等人處理…（已等 {(time.time()-started)/60:.0f} 分鐘）")

    def _ask_or_recover(self, what):
        """
        遊戲還活著、但流程走不下去時要怎麼辦。

        預設是停下來問人。理由：唯一的自動處理手段是 recover_flow，而它是
        app_reset——會把整個帳號清掉。遊戲沒閃退就代表現場還在，那是唯一
        能看出「為什麼卡住」的機會，洗掉就沒了。
        設定 auto_recover: true 可以回到自動重置（標了 on_fail: ask 的步驟
        仍然一律問人）。
        """
        if self._fail_ask or not self.cfg.get("auto_recover", False):
            raise NeedsHuman(self._fail_where or what)
        log(f"{what}，走復原程序")
        self.run_flow(self.cfg.get("recover_flow", []), "復原流程")

    def run(self, max_rounds):
        max_rounds = int(max_rounds or self.cfg.get("max_rounds", 0))
        max_fails = int(self.cfg.get("max_fails", 3))
        stop_on_hit = bool(self.cfg.get("stop_on_hit", True))

        log("=" * 46)
        log(f"開跑。上限 {max_rounds if max_rounds else '無限'} 輪，按 F12 或 Ctrl+C 中斷。")
        log(f"OCR：{'可用' if self.ocr_ok else '不可用（只靠圖片判定）'}")
        targets = self.cfg.get("hit_targets") or []
        if targets:
            log("命中條件（任一成立即停）：")
            for t in targets:
                ovr = f"OVR={t['ovr']}" if t.get("ovr") else "不限 OVR"
                log(f"  · {t.get('note') or t['keywords'][0]}  {ovr}")
        elif not self.cfg.get("hit_templates") and not any(self.cfg.get("hit_keywords", [])):
            log("！沒設定任何命中目標，判定永遠不會過，會一直重跑。")
            log("  只是要測流程的話沒差，要真的刷就先設好 hit_targets。")
        # 通知管道要在開跑前就確認，不能等到中獎那一刻才發現寄不出去——
        # 那時候已經來不及了。之前就這樣安靜地跑了 42 輪。
        conf = self.cfg.get("notify_mail") or {}
        if conf.get("enabled"):
            if smtp_password(self.cfg):
                log(f"中獎通知：會寄到 {conf.get('to', '?')}")
            else:
                log(f"！中獎通知寄不出去——找不到 "
                    f"{conf.get('password_env', 'REROLL_SMTP_PASS')} 的值。")
                log("  現在照樣會跑，但中了只會寫進 log 和截圖，不會通知你。")
        log("=" * 46)

        started = time.time()
        rounds = hits = misses = fails = 0
        streak = 0

        try:
            while True:
                self.check_stop()
                if max_rounds and rounds >= max_rounds:
                    log(f"跑滿 {max_rounds} 輪，停。")
                    break

                rounds += 1
                try:
                    result = self.run_round(rounds)
                except NeedsHuman as e:
                    # 停下來等人，處理好會自己接著跑。放在迴圈裡面而不是外面，
                    # 就是為了能繼續——在外面接的話這個 run() 就結束了。
                    if not self.wait_for_human(e):
                        break
                    fails += 1
                    streak = 0        # 人介入過了，不算連續失敗
                    continue

                if result == "hit":
                    hits += 1
                    streak = 0
                    if stop_on_hit:
                        log("設定是中了就停，收工。")
                        break
                elif result == "miss":
                    misses += 1
                    streak = 0
                else:
                    fails += 1
                    streak += 1
                    if streak >= max_fails:
                        log(f"連續 {streak} 輪卡住，停下來讓你看一下是哪一步對不上。")
                        break
        except StopRequested:
            log("偵測到 F12，中斷。")
            self.stopped_by_user = True
        except KeyboardInterrupt:
            log("Ctrl+C，中斷。")
            self.stopped_by_user = True

        elapsed = time.time() - started
        log("-" * 46)
        log(f"共 {rounds} 輪：中 {hits}、沒中 {misses}、卡住 {fails}")
        log(f"耗時 {elapsed / 60:.1f} 分鐘" + (f"，平均每輪 {elapsed / rounds:.1f} 秒" if rounds else ""))
        return hits


# ---------------------------------------------------------------- 子命令

def countdown(sec):
    for i in range(int(sec), 0, -1):
        print(f"  {i}...", end="\r", flush=True)
        time.sleep(1)
    print("     ", end="\r")


def cmd_shot(cfg, args):
    cap = Capture(cfg)
    shot_dir = abs_dir(cfg, "shot_dir", per_instance=True)
    if args.delay > 0:
        print(f"{args.delay} 秒後截圖，快切到遊戲畫面：")
        countdown(args.delay)

    cap.focus()
    frame, region = cap.grab()
    path = os.path.join(shot_dir, f"shot_{datetime.now():%Y%m%d_%H%M%S}.png")
    imwrite_unicode(path, frame)

    print(f"截好了：{path}")
    print(f"擷取範圍：x={region[0]} y={region[1]} 寬={region[2]} 高={region[3]}")
    print()
    print("接下來：用小畫家之類的把要辨識的按鈕從這張圖裁出來，存進 "
          f"{abs_dir(cfg, 'template_dir')}，")
    print("然後跑 python reroll.py test 你的檔名.png 確認比對得到。")
    print("注意：模板一定要從這張圖裁，不能另外重新截，解析度不一樣就對不上。")


def cmd_test(cfg, args):
    cap = Capture(cfg)
    bot = Bot(cfg, cap)
    shot_dir = abs_dir(cfg, "shot_dir", per_instance=True)

    path = bot.template_path(args.template)
    tmpl, mask = load_template(path)
    if tmpl is None:
        sys.exit(f"讀不到模板：{path}")

    if args.delay > 0:
        print(f"{args.delay} 秒後比對，切到遊戲畫面：")
        countdown(args.delay)

    cap.focus()
    frame, region = cap.grab()
    thr = args.threshold if args.threshold is not None else float(cfg.get("match_threshold", 0.85))
    m = match_template(frame, tmpl, mask, bot.scales)

    print(f"模板：{path}  ({tmpl.shape[1]}x{tmpl.shape[0]}"
          f"{'，有透明遮罩' if mask is not None else ''})")
    print(f"畫面：{frame.shape[1]}x{frame.shape[0]}   門檻：{thr}")

    variance = float(cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY).astype(np.float32).var())
    if variance < 50:
        print(f"！這張模板幾乎是純色的（變異數 {variance:.0f}），畫面上任何一塊同色區域都會滿分，")
        print("  等於隨便亂中。重裁一塊有按鈕文字或圖示、看得出特徵的區域。")

    if m is None:
        sys.exit("比對不出結果（模板可能比畫面還大）。")

    print(f"最高分：{m.score:.4f}  中心點：({m.x}, {m.y})"
          + (f"  縮放：x{m.scale}" if m.scale != 1.0 else ""))
    print(f"對應螢幕座標：({region[0] + m.x}, {region[1] + m.y})")

    if m.score >= thr:
        print("→ 過門檻，可以用。")
        # 比對得到不代表點得動，順便提醒
        check_input_permission(cap)
    elif m.score >= 0.6:
        # 只有接近門檻才值得談調門檻；再低就是根本沒找到，調下去只會一直誤判
        print("→ 差一點。如果這張圖確實在畫面上，可以把 match_threshold 調到 "
              f"{m.score - 0.03:.2f} 左右。")
        print("   分數不高通常是遊戲畫質設定或視窗大小跟裁模板時不一樣。")
    else:
        print("→ 差很多，這張圖現在應該根本不在畫面上。")
        print("   不要為了這個去調低 match_threshold，那只會讓它到處亂中。")
        print("   先確認：模板是從 shot 那張圖裁的嗎？現在畫面真的停在有這顆按鈕的地方嗎？")

    x1, y1 = max(0, m.x - m.w // 2), max(0, m.y - m.h // 2)
    vis = frame.copy()
    color = (0, 200, 0) if m.score >= thr else (0, 0, 255)
    cv2.rectangle(vis, (x1, y1), (x1 + m.w, y1 + m.h), color, 3)
    cv2.putText(vis, f"{m.score:.3f}", (x1, max(24, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
    stem = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(shot_dir, f"test_{stem}_{datetime.now():%H%M%S}.png")
    imwrite_unicode(out, vis)
    print(f"標好框的圖：{out}")


def cmd_ocr(cfg, args):
    if pytesseract is None:
        sys.exit("沒裝 pytesseract：pip install pytesseract")
    if not setup_ocr(cfg):
        sys.exit(
            "找不到 Tesseract 本體。到 https://github.com/UB-Mannheim/tesseract/wiki 裝，\n"
            "安裝時勾 Chinese (Traditional)，裝好後把路徑填到設定檔的 tesseract_cmd。"
        )

    cap = Capture(cfg)
    shot_dir = abs_dir(cfg, "shot_dir", per_instance=True)
    box = args.region if args.region else cfg.get("ocr_region")

    if args.delay > 0:
        print(f"{args.delay} 秒後辨識，切到有文字的畫面：")
        countdown(args.delay)

    cap.focus()
    frame, region = cap.grab()
    crop = crop_region(frame, box)

    out = os.path.join(shot_dir, f"ocr_{datetime.now():%H%M%S}.png")
    imwrite_unicode(out, crop)

    lang = cfg.get("ocr_lang", "chi_tra+eng")
    print(f"範圍：{box if box else '整個擷取區'}   語言：{lang}")
    print(f"送去辨識的圖：{out}")
    print("-" * 40)
    text = ocr_text(crop, lang)
    print(text.strip() if text.strip() else "（什麼都沒讀到）")
    print("-" * 40)
    if not text.strip():
        print("讀不到通常是範圍太大。用 shot 的圖量出文字那一小塊的 x/y/寬/高，")
        print("填到設定檔的 ocr_region，只框文字會準很多。")


def cmd_mail(cfg, args):
    conf = cfg.get("notify_mail") or {}
    env_name = conf.get("password_env", "REROLL_SMTP_PASS")

    print("目前的寄信設定：")
    for k in ("enabled", "smtp_host", "smtp_port", "use_tls", "user", "to"):
        print(f"  {k:12s} = {conf.get(k)}")
    print(f"  密碼來源     = 環境變數 {env_name}"
          f"（{'已設定' if os.environ.get(env_name) else '尚未設定'}）")
    print()

    if not conf.get("enabled"):
        sys.exit("notify_mail.enabled 是 false，先在設定檔改成 true。")
    if not os.environ.get(env_name):
        print(f"環境變數 {env_name} 沒有值。在 PowerShell 執行下面這行後，")
        print("在同一個視窗裡再跑一次（關掉視窗就會失效，只存在於該視窗）：")
        print(f'  $env:{env_name} = "你的信箱密碼"')
        sys.exit(1)

    ok = send_mail(cfg, "[MLB 刷初始] 測試信",
                   "這是測試信。收到就代表中獎通知會正常送達。")
    print("寄出成功，去收信確認。" if ok else "寄送失敗，看上面的錯誤訊息。")


def acquire_lock(cfg):
    """
    一台模擬器只允許一個程序操作，用 lock 檔擋住第二個。

    鎖的鍵是 serial，不是 --instance 的名字——真正被搶的資源是那台模擬器。
    用名字當鍵的話，`--instance A` 和不帶參數的單開會拿到兩把不同的鎖，
    卻同時對著 emulator-5554 點下去。撞到的畫面會像遊戲自己壞掉：兩邊交錯
    點擊、每一步都對不上，完全看不出是兩個程序在打架。

    回傳開著的檔案物件（要留著參照，被 GC 掉鎖就放了）。
    """
    import msvcrt
    key = (cfg.get("adb", {}).get("serial") if cfg.get("input_mode") == "adb"
           else INSTANCE)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key or "default")
    path = os.path.join(BASE_DIR, f".lock.{safe}")
    f = open(path, "a+")
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        f.close()
        sys.exit(f"{key} 已經有一個程序在操作了（{path}）。\n"
                 "同一台模擬器被兩個程序操作會互相打斷，所以不啟動。\n"
                 "如果你確定沒有別的程序在跑，把上面那個檔案刪掉再試。")
    return f


def cmd_run(cfg, args):
    lock = acquire_lock(cfg)       # 要留著參照，被 GC 掉鎖就放了
    cap = Capture(cfg)
    bot = Bot(cfg, cap)

    if not cfg.get("flow"):
        sys.exit("設定檔的 flow 是空的，沒東西可以跑。")

    if not check_input_permission(cap):
        sys.exit("開跑前檢查沒過，先照上面的方法處理，不然跑了也只是空轉。")

    if args.delay > 0:
        print(f"{args.delay} 秒後開始，切到遊戲畫面：")
        countdown(args.delay)

    bot.run(args.max)
    lock.close()
    # 這兩種都不該被看門狗重開：重開就會走 app_reset，把現場洗掉
    if bot.needs_human:
        return EXIT_NEEDS_HUMAN
    if bot.stopped_by_user:
        return EXIT_USER_STOP
    return 0


# ---------------------------------------------------------------- 進入點

def main():
    enable_dpi_awareness()

    p = argparse.ArgumentParser(
        description="Steam 遊戲刷初始帳號腳本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="設定檔路徑")
    p.add_argument("--instance", default="", metavar="NAME",
                   help="要操作哪一台模擬器（對應設定檔 instances 裡的名字）。"
                        "雙開時每個程序帶不同的名字，log 和截圖會各自分開。"
                        "不給＝單開，行為與以前完全相同")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("shot", help="截圖，拿去裁模板")
    s.add_argument("--delay", type=int, default=3, help="幾秒後截圖（預設 3）")

    s = sub.add_parser("test", help="測模板比對得到嗎")
    s.add_argument("template", help="模板檔名，可以只給檔名（會去 templates 找）")
    s.add_argument("--threshold", type=float, default=None, help="這次測試用的門檻")
    s.add_argument("--delay", type=int, default=3)

    s = sub.add_parser("ocr", help="測 OCR 讀不讀得到")
    s.add_argument("--region", type=int, nargs=4, metavar=("X", "Y", "W", "H"),
                   default=None, help="臨時指定辨識範圍")
    s.add_argument("--delay", type=int, default=3)

    s = sub.add_parser("run", help="正式跑")
    s.add_argument("--max", type=int, default=0, help="最多跑幾輪，0＝不限")
    s.add_argument("--delay", type=int, default=5, help="幾秒後開始（預設 5）")

    sub.add_parser("mail", help="寄一封測試信，確認中獎通知會不會送達")

    args = p.parse_args()
    # 要在任何 log() 之前設定好，不然開頭幾行會寫到錯的檔案
    set_instance(args.instance)
    cfg = load_config(args.config,
                      allow_create=(args.config == DEFAULT_CONFIG_PATH))
    cfg = apply_instance(cfg, INSTANCE)

    try:
        code = {"shot": cmd_shot, "test": cmd_test, "ocr": cmd_ocr,
                "run": cmd_run, "mail": cmd_mail}[args.cmd](cfg, args)
    except StopRequested:
        print("\n偵測到 F12，中斷。")
        sys.exit(EXIT_USER_STOP)
    except KeyboardInterrupt:
        print("\nCtrl+C，中斷。")
        sys.exit(EXIT_USER_STOP)
    except RuntimeError as e:
        sys.exit(str(e))
    sys.exit(code or 0)


if __name__ == "__main__":
    main()
