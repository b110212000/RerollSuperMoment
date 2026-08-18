#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reroll.py 的看門狗（支援雙開）

用途：
    長跑時 reroll.py 可能會卡住（遊戲閃退、畫面對不上、連續失敗自動停機）。
    這支程式每 30 秒檢查一次，發現不對就殺掉重開，不用有人守著。

    它只做機械性的判斷——「有沒有在動」和「有沒有中」。
    真正需要人（或 AI）判斷的情況它處理不了，例如卡在一個沒見過的新畫面，
    那要重新裁模板。這種時候它會把狀況寫進 watchdog.log 等人來看。

用法：
    python watchdog.py                              # 單開，行為與以前相同
    python watchdog.py --instance A --instance B     # 雙開，兩台同時跑
    python watchdog.py --instance A --max-restarts 5

    雙開時每個實例有自己的 log（reroll.A.log）和自己的停滯時鐘——共用一份
    log 的話，A 在寫檔就會讓 B 的停滯偵測永遠不觸發，卡住偵測會整個失效，
    而畫面上看起來一切正常。

    任一台中獎就停掉全部（中獎的帳號還停在翻牌畫面，要人去綁定）。

隨時 Ctrl+C 中斷（會一併結束底下的 reroll.py）。
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
WD_LOG = os.path.join(BASE, "watchdog.log")
DEFAULT_CONFIG = os.path.join(BASE, "reroll_config.json")

HIT_MARK = "★ 中了"
# 這兩個碼都代表「別重開」——重開會讓 reroll.py 走 app_reset，把帳號和現場
# 一起清掉。reroll.py 有同名常數，改的時候要一起改。
EXIT_USER_STOP = 130        # 使用者按 F12 / Ctrl+C
EXIT_NEEDS_HUMAN = 3        # 遇到要人判斷的狀況，畫面保留在原地等人看
NO_RESTART = {EXIT_USER_STOP: "是使用者主動停的",
              EXIT_NEEDS_HUMAN: "需要人看一下（畫面已保留，帳號沒被重置）"}
FAST_FAIL_SEC = 15          # 這麼快就結束＝根本沒跑起來，不是暫時性故障


def log(msg):
    line = f"[{datetime.now():%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(WD_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


class Child:
    """一台模擬器對應一個 reroll.py 子程序。"""

    def __init__(self, name, config, python_exe, stall):
        self.name = name                    # "" ＝單開
        self.config = config
        self.python = python_exe
        self.stall = stall
        self.log_path = os.path.join(
            BASE, f"reroll.{name}.log" if name else "reroll.log")

        self.proc = None
        self.restarts = 0
        self.done = False                   # 終結：中獎／使用者停／重開用完
        self.hit = False
        # 秒殺型失敗（serial 打錯、設定檔路徑錯、lock 被佔）每次都是瞬間結束。
        # 不分開算的話，30 次重開額度會在兩分鐘內被這種永久性錯誤燒光，
        # 之後真正的暫時性故障就沒有額度可用了。
        self.launched_at = 0.0
        self.fast_fails = 0

        # log 是 append 模式，中獎那行會永久留在檔案裡。只掃看門狗啟動之後
        # 追加的內容，否則中過一次以後每次啟動都會誤判「已經中了」而立刻收工。
        self.base = self._size()
        self.last_size = self.base
        self.last_change = time.time()

    @property
    def tag(self):
        return f"[{self.name}] " if self.name else ""

    def _size(self):
        try:
            return os.path.getsize(self.log_path)
        except OSError:
            return 0

    def new_text(self):
        """
        只回傳看門狗啟動後才寫進去的部分。

        一定要用二進位 seek：base 來自 getsize()，單位是位元組，而 log 裡有中文，
        位元組數遠大於字元數。拿位元組偏移去切已解碼的字串，會算出「檔案變短了」
        而把 base 歸零——那等於整個機制沒作用，舊的中獎標記又會被當成新的。
        """
        try:
            with open(self.log_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                if f.tell() < self.base:      # 檔案被砍掉重建，整份都算新的
                    self.base = 0
                f.seek(self.base)
                raw = f.read()
        except Exception:
            return ""
        return raw.decode("utf-8", "replace")

    def last_lines(self, n=6):
        lines = self.new_text().splitlines()
        return lines[-n:] if lines else []

    def start(self):
        argv = [self.python, os.path.join(BASE, "reroll.py"),
                "--config", self.config]
        if self.name:
            argv += ["--instance", self.name]
        argv += ["run", "--delay", "0"]
        self.proc = subprocess.Popen(
            argv,
            cwd=BASE,
            stdout=subprocess.DEVNULL,      # 內容都會進各自的 log，這裡不用重複收
            stderr=subprocess.STDOUT,
        )
        self.last_size = self._size()
        self.last_change = self.launched_at = time.time()
        return self.proc.pid

    def stop(self):
        if self.proc is None or self.proc.poll() is not None:
            return
        try:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        except Exception as e:
            log(f"  {self.tag}結束子程序時出錯（可忽略）：{e}")

    def stalled(self):
        return (time.time() - self.last_change) > self.stall

    def touch(self):
        """log 有長就把停滯時鐘歸零。"""
        size = self._size()
        if size != self.last_size:
            self.last_size, self.last_change = size, time.time()


def main():
    ap = argparse.ArgumentParser(description="reroll.py 看門狗（支援雙開）")
    ap.add_argument("--instance", action="append", default=None, metavar="NAME",
                    help="要看顧哪一台（對應 reroll_config.json 的 instances）。"
                         "可以重複給，例如 --instance A --instance B。"
                         "不給＝單開")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="設定檔路徑")
    ap.add_argument("--interval", type=int, default=30, help="幾秒檢查一次（預設 30）")
    ap.add_argument("--stall", type=int, default=300,
                    help="log 幾秒沒動就算卡住（預設 300。reroll 單步最長等 150 秒，要留餘裕）")
    ap.add_argument("--max-restarts", type=int, default=30,
                    help="每台最多重開幾次（預設 30）")
    args = ap.parse_args()

    names = args.instance if args.instance else [""]
    kids = [Child(n, args.config, sys.executable, args.stall) for n in names]

    log("=" * 54)
    who = "、".join(k.name for k in kids) if names != [""] else "單開"
    log(f"看門狗啟動（{who}）。每 {args.interval} 秒檢查，"
        f"log 停滯 {args.stall} 秒視為卡住")
    log(f"每台最多重開 {args.max_restarts} 次，任一台中獎就全部停")
    log("=" * 54)

    started_at = time.time()
    for k in kids:
        log(f"{k.tag}已啟動 reroll.py (pid {k.start()})")

    try:
        while True:
            time.sleep(args.interval)

            # --- 先看有沒有中。中獎的帳號還停在翻牌畫面，絕不能被重開流程
            #     的 pm clear 洗掉，所以這個判斷一定要在「子程序結束了」之前。
            winner = next((k for k in kids if HIT_MARK in k.new_text()), None)
            if winner:
                winner.hit = True
                log(f"★ {winner.tag}偵測到中獎，停止全部")
                for l in winner.last_lines(10):
                    log("    " + l)
                for k in kids:
                    k.stop()
                break

            for k in kids:
                if k.done:
                    continue

                k.touch()
                exited = k.proc.poll() is not None
                stalled = k.stalled()

                if not exited and not stalled:
                    continue        # 一切正常，安靜等下一輪

                # --- 需要處理了 ---
                if exited:
                    rc = k.proc.returncode
                    if rc in NO_RESTART:
                        # 重開會走 app_reset，把帳號和現場一起洗掉。
                        # （以前分不出使用者主動停，所以你按 F12 之後
                        #   看門狗又把它拉起來繼續跑。）
                        log(f"{k.tag}{NO_RESTART[rc]}（exit {rc}），不重開")
                        k.done = True
                        continue
                    log(f"{k.tag}reroll.py 已結束（exit code {rc}）")
                    if time.time() - k.launched_at < FAST_FAIL_SEC:
                        k.fast_fails += 1
                    else:
                        k.fast_fails = 0
                    if k.fast_fails >= 3:
                        log(f"！{k.tag}連續 3 次在 {FAST_FAIL_SEC} 秒內就結束，"
                            "這是設定或環境的問題，不是暫時性故障。")
                        log(f"  自己跑一次看錯誤訊息："
                            f"python reroll.py --config {k.config}"
                            + (f" --instance {k.name}" if k.name else "")
                            + " run --delay 0")
                        k.done = True
                        continue
                else:
                    log(f"{k.tag}reroll.py 沒有結束，但 log 已經 "
                        f"{int(time.time() - k.last_change)} 秒沒動，判定卡住")

                tail = k.last_lines(6)
                if tail:
                    log(f"  {k.tag}停下來前的最後幾行：")
                    for l in tail:
                        log("    " + l)
                else:
                    # log 一個字都沒寫＝連 reroll.py 的開頭都沒跑到，
                    # 通常是 import 失敗或參數就錯了
                    log(f"  {k.tag}log 沒有任何新內容，等於根本沒跑起來")

                if k.restarts >= args.max_restarts:
                    log(f"！{k.tag}已重開 {k.restarts} 次，達到上限，"
                        "這台停下。請人工檢查上面的紀錄。")
                    k.stop()
                    k.done = True
                    continue

                k.stop()
                k.restarts += 1
                elapsed = (time.time() - started_at) / 60
                log(f"  {k.tag}第 {k.restarts} 次重開（已運行 {elapsed:.0f} 分鐘）")
                time.sleep(5)
                log(f"  {k.tag}已重新啟動 (pid {k.start()})")

            if all(k.done for k in kids):
                log("所有實例都已終結，看門狗收工。")
                break

    except KeyboardInterrupt:
        log("Ctrl+C，結束看門狗")
        for k in kids:
            k.stop()

    total = (time.time() - started_at) / 60
    detail = "，".join(f"{k.name or '單開'} 重開 {k.restarts} 次"
                       + ("（中獎）" if k.hit else "") for k in kids)
    log(f"看門狗結束。總運行 {total:.1f} 分鐘。{detail}")


if __name__ == "__main__":
    main()
