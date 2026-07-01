# -*- coding: utf-8 -*-
"""
Skipferry - 破損ファイルを飛び越えるフォルダ コピー/移動ツール (Python / Tkinter)

アプリやOSを巻き込んで固まるタイプの破損ファイルがあっても、処理順で先頭 N 件を
スキップして健全なファイルだけを安全に運び切ることを主目的とする。

主な機能:
  - source -> dist へフォルダをコピー or 移動 (サブフォルダ含む / 同一・別ドライブ両対応)
  - ファイルのタイムスタンプ維持 (shutil.copy2)
  - フォルダのタイムスタンプもできる限り維持 (処理完了後に os.utime)
  - ファイル属性/副次ストリームは操作しない (システムデフォルトに従う)
  - ベリファイ (サイズ+更新日時 / SHA-256 ハッシュ)
  - 移動時に「コピーして元をごみ箱へ」を選択可能 (安全対策)
  - エラースキップ (1件失敗しても続行)
  - 無視リスト (ファイルマスク複数, fnmatch)
  - エラーになったファイルを自動で無視リストへ登録
  - 無視リストのテキスト エクスポート/インポート
  - 処理順で先頭 N 件をスキップ (固まる破損ファイル対策)

移動 (元削除) 時の注意:
  対象ファイルがエラー/無視で元に残ったフォルダは、空にならないため削除されません。
"""

import os
import sys
import shutil
import fnmatch
import hashlib
import threading
import queue
import time
import datetime
import traceback

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ドラッグ&ドロップ (tkinterdnd2 があれば有効。無ければ D&D なしで通常起動)
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES  # type: ignore
    HAS_DND = True
    _BaseTk = TkinterDnD.Tk
except Exception:
    HAS_DND = False
    DND_FILES = None
    _BaseTk = tk.Tk


# ---------------------------------------------------------------------------
# ごみ箱送り (send2trash 優先, 無ければ Windows SHFileOperation フォールバック)
# ---------------------------------------------------------------------------
def _recycle_via_send2trash(path):
    from send2trash import send2trash  # type: ignore
    send2trash(os.path.abspath(path))


def _recycle_via_winapi(path):
    """ctypes で SHFileOperation を呼び、ごみ箱へ送る (Windows 専用)。"""
    import ctypes
    from ctypes import wintypes

    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x0040       # ごみ箱へ (完全削除でなく)
    FOF_NOCONFIRMATION = 0x0010  # 確認ダイアログ抑制
    FOF_SILENT = 0x0004
    FOF_NOERRORUI = 0x0400

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    abspath = os.path.abspath(path)
    # pFrom はダブルNULL終端が必要
    p_from = abspath + "\x00\x00"
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = p_from
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI

    res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if res != 0:
        raise OSError(f"SHFileOperation failed (code={res}) for {abspath}")


def send_to_recycle_bin(path):
    """利用可能な手段でごみ箱へ送る。"""
    try:
        _recycle_via_send2trash(path)
        return
    except ImportError:
        pass
    if sys.platform.startswith("win"):
        _recycle_via_winapi(path)
    else:
        raise RuntimeError(
            "ごみ箱送りには send2trash が必要です (pip install Send2Trash)"
        )


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------
def sha256_of_file(path, chunk_size=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def is_ignored(relpath, basename, patterns):
    """無視マスクにマッチするか。区切りを含むパターンは相対パス全体、
    それ以外はファイル名に対して fnmatch で判定する。"""
    rp = relpath.replace("\\", "/")
    for pat in patterns:
        p = pat.strip()
        if not p or p.startswith("#"):
            continue
        if "/" in p or "\\" in p:
            if fnmatch.fnmatch(rp, p.replace("\\", "/")):
                return True
        else:
            if fnmatch.fnmatch(basename, p):
                return True
    return False


_WIN_INVALID_CHARS = set('<>:"|?*')
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL"} \
    | {f"COM{i}" for i in range(1, 10)} \
    | {f"LPT{i}" for i in range(1, 10)}


def check_dest_path_valid(dst_root):
    """コピー先パスにOS(主にWindows)でファイルパスとして扱えない文字/名前が
    含まれていないか検査する。問題があればエラー文字列、無ければ None を返す。
    ドライブレターの ':' やパス区切りは正当なので除外して各構成要素を見る。"""
    abspath = os.path.abspath(dst_root)
    drive, rest = os.path.splitdrive(abspath)
    parts = [p for p in rest.replace("/", "\\").split("\\") if p]

    win = sys.platform.startswith("win")
    for p in parts:
        # 制御文字 / NUL は全OSで不正
        ctrl = sorted({c for c in p if ord(c) < 32})
        if ctrl:
            codes = ", ".join(f"0x{ord(c):02X}" for c in ctrl)
            return f"コピー先パスに制御文字が含まれています（構成要素「{p}」/ {codes}）"
        if not win:
            continue
        bad = sorted({c for c in p if c in _WIN_INVALID_CHARS})
        if bad:
            return ("コピー先フォルダ名にWindowsで使用できない文字が含まれています："
                    f"{' '.join(bad)} （構成要素「{p}」）\n"
                    '使用不可: < > : " | ? *')
        if p != p.rstrip(" ."):
            return f"コピー先フォルダ名の末尾に空白またはドットは使用できません（構成要素「{p}」）"
        stem = p.split(".")[0].upper()
        if stem in _WIN_RESERVED:
            return f"コピー先フォルダ名にWindowsの予約名は使用できません（構成要素「{p}」）"
    return None


def plan_operation(cfg):
    """設定から処理計画を作成する。ワーカー本処理とプレビューで共有する。
    戻り値 dict:
      error        : エラー文字列 (None なら正常)
      src_root     : コピー元ルート (絶対パス)
      dst_root     : コピー先ルート (絶対パス, サブフォルダ作成設定を反映)
      file_list    : 処理対象 [(relpath, basename)] (無視/先頭スキップ適用後, ソート済)
      dir_list     : サブフォルダ相対パス一覧
      ignored_list : 無視された relpath 一覧
      skipped_list : 先頭スキップされた relpath 一覧
      skip_n       : 先頭スキップ件数
    """
    src_root = os.path.abspath(cfg["source"])
    dst_base = os.path.abspath(cfg["dest"])
    plan = {"src_root": src_root, "dst_root": None, "error": None,
            "file_list": [], "dir_list": [], "ignored_list": [],
            "skipped_list": [], "skip_n": 0}

    if not os.path.isdir(src_root):
        plan["error"] = f"コピー元フォルダが存在しません: {src_root}"
        return plan

    if cfg["make_subfolder"]:
        dst_root = os.path.join(dst_base, os.path.basename(src_root.rstrip("\\/")))
    else:
        dst_root = dst_base
    plan["dst_root"] = dst_root

    # コピー先パスに使用不可文字/予約名が無いか検査
    path_err = check_dest_path_valid(dst_root)
    if path_err:
        plan["error"] = path_err
        return plan

    if os.path.abspath(dst_root) == src_root or \
       os.path.abspath(dst_root).startswith(src_root + os.sep):
        plan["error"] = "コピー先がコピー元の内部/同一です。"
        return plan

    # ファイル/フォルダ一覧 (決定論的にソート)
    file_list = []
    dir_list = []
    for cur, dirs, files in os.walk(src_root):
        dirs.sort()
        files.sort()
        rel_dir = os.path.relpath(cur, src_root)
        if rel_dir == ".":
            rel_dir = ""
        if rel_dir:
            dir_list.append(rel_dir)
        for name in files:
            rel = os.path.join(rel_dir, name) if rel_dir else name
            file_list.append((rel, name))

    # 無視リスト適用
    patterns = cfg["ignore_patterns"]
    kept = []
    ignored_list = []
    for rel, name in file_list:
        if is_ignored(rel, name, patterns):
            ignored_list.append(rel)
        else:
            kept.append((rel, name))
    file_list = kept

    # 先頭 N 件スキップ (破損ファイル対策)
    skip_n = max(0, int(cfg.get("skip_first_n", 0) or 0))
    skipped_list = [rel for rel, _ in file_list[:skip_n]] if skip_n > 0 else []
    file_list = file_list[skip_n:]

    plan.update(dst_root=dst_root, file_list=file_list, dir_list=dir_list,
                ignored_list=ignored_list, skipped_list=skipped_list, skip_n=skip_n)
    return plan


# ---------------------------------------------------------------------------
# ワーカー (別スレッドで実行)
# ---------------------------------------------------------------------------
class CopyMoveWorker(threading.Thread):
    def __init__(self, cfg, msg_queue):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.q = msg_queue
        self.stop_flag = threading.Event()
        self.pause_flag = threading.Event()  # set=一時停止中
        self.logf = None  # ログファイルのハンドル (任意)

    def log(self, text, level="info"):
        self.q.put(("log", (level, text)))
        # ログファイルが有効なら時刻付きで書き出す
        if self.logf is not None:
            try:
                ts = datetime.datetime.now().strftime("%H:%M:%S")
                self.logf.write(f"[{ts}] {text}\n")
                self.logf.flush()
            except Exception:
                pass  # ファイル書き込み失敗は処理本体を止めない

    def progress(self, done, total):
        self.q.put(("progress", (done, total)))

    def ignore_add(self, pattern):
        self.q.put(("ignore_add", pattern))

    def _wait_if_paused(self):
        """一時停止中は再開/停止まで待機する。停止時は速やかに抜ける。
        戻り値: 停止要求が出ていれば True。"""
        announced = False
        while self.pause_flag.is_set() and not self.stop_flag.is_set():
            if not announced:
                self.log("一時停止中... （再開ボタンで続行）", "warn")
                announced = True
            time.sleep(0.1)
        if announced and not self.stop_flag.is_set():
            self.log("再開しました。", "info")
        return self.stop_flag.is_set()

    def _throttle_sleep(self):
        """OSやアプリを重くしないためのウェイトを挟む。停止/一時停止に応答する。"""
        ms = max(0, int(self.cfg.get("wait_ms", 0) or 0))
        if ms <= 0:
            return
        remaining = ms / 1000.0
        step = 0.1
        while remaining > 0 and not self.stop_flag.is_set():
            # ウェイト中に一時停止された場合はそちらで待機する
            if self.pause_flag.is_set():
                if self._wait_if_paused():
                    return
            time.sleep(min(step, remaining))
            remaining -= step

    def run(self):
        # ログファイル出力が指定されていれば開く
        log_path = self.cfg.get("log_file")
        if log_path:
            try:
                self.logf = open(log_path, "a", encoding="utf-8")
                stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.logf.write(f"\n===== Skipferry ログ開始 {stamp} =====\n")
                self.logf.flush()
                self.q.put(("log", ("info", f"ログをファイルへ出力: {log_path}")))
            except Exception as e:
                self.logf = None
                self.q.put(("log", ("error", f"ログファイルを開けません: {e}")))
        try:
            self._run()
        except Exception:
            self.log("致命的エラー:\n" + traceback.format_exc(), "error")
        finally:
            if self.logf is not None:
                try:
                    self.logf.write("===== Skipferry ログ終了 =====\n")
                    self.logf.close()
                except Exception:
                    pass
                self.logf = None
            self.q.put(("done", None))

    def _run(self):
        cfg = self.cfg

        # ---- 処理計画 (プレビューと共通) ----
        self.log("ファイル一覧を作成中...")
        plan = plan_operation(cfg)
        if plan["error"]:
            self.log(plan["error"] + " 中止します。", "error")
            return

        src_root = plan["src_root"]
        dst_root = plan["dst_root"]
        file_list = plan["file_list"]
        dir_list = plan["dir_list"]
        is_move = cfg["mode"] == "move"

        for rel in plan["ignored_list"]:
            self.log(f"[無視] {rel}")
        for rel in plan["skipped_list"]:
            self.log(f"[先頭スキップ] {rel}")

        total = len(file_list)
        self.log(f"処理対象: {total} ファイル "
                 f"(無視 {len(plan['ignored_list'])} / 先頭スキップ {plan['skip_n']})")

        # ---- フォルダ作成 ----
        os.makedirs(dst_root, exist_ok=True)
        for rel_dir in dir_list:
            os.makedirs(os.path.join(dst_root, rel_dir), exist_ok=True)

        # ---- ファイル処理 ----
        done = 0
        error_count = 0
        ok_count = 0
        # 元に残ったファイルがあるフォルダを追跡 (移動時のフォルダ削除判定用)
        dirs_with_残 = set()

        for rel, name in file_list:
            # 一時停止中は待機 (停止要求が来たら抜ける)
            if self._wait_if_paused():
                self.log("ユーザーにより停止されました。", "warn")
                break
            if self.stop_flag.is_set():
                self.log("ユーザーにより停止されました。", "warn")
                break

            done += 1
            self.progress(done, total)
            src_file = os.path.join(src_root, rel)
            dst_file = os.path.join(dst_root, rel)

            try:
                self.log(f"[{done}/{total}] {rel}")
                os.makedirs(os.path.dirname(dst_file), exist_ok=True)

                # コピー (メタデータ=タイムスタンプ維持)
                shutil.copy2(src_file, dst_file)

                # ベリファイ
                if cfg["verify"] != "none":
                    ok, detail = self._verify(src_file, dst_file, cfg["verify"])
                    if not ok:
                        raise IOError(f"ベリファイ失敗: {detail}")

                # 移動時の元削除
                if is_move:
                    if cfg["move_to_recycle"]:
                        send_to_recycle_bin(src_file)
                    else:
                        os.remove(src_file)

                ok_count += 1

            except Exception as e:
                error_count += 1
                self.log(f"  [エラー] {rel}: {e}", "error")

                # エラーファイルを自動で無視リストへ登録
                if cfg["auto_ignore_on_error"]:
                    pat = rel.replace("\\", "/")
                    self.ignore_add(pat)
                    self.log(f"  [自動無視登録] {pat}", "warn")

                # 移動時、元が残るフォルダを記録 (親も含む)
                if is_move:
                    self._mark_残(os.path.dirname(rel), dirs_with_残)

                if not cfg["skip_error"]:
                    self.log("エラースキップが無効のため中止します。", "error")
                    break

            # OS/アプリを重くしないためのウェイト
            self._throttle_sleep()

        # ---- フォルダのタイムスタンプ維持 (全ファイル処理後) ----
        self.log("フォルダのタイムスタンプを適用中...")
        for rel_dir in sorted(dir_list, reverse=True):
            self._apply_dir_time(src_root, dst_root, rel_dir)
        self._apply_dir_time(os.path.dirname(src_root), os.path.dirname(dst_root),
                             os.path.basename(dst_root), src_override=src_root)

        # ---- 移動時: 空になった元フォルダを削除 ----
        if is_move and not self.stop_flag.is_set():
            self.log("移動元の空フォルダを削除中...")
            self._cleanup_source_dirs(src_root, dirs_with_残)

        self.log(
            f"完了: 成功 {ok_count} / エラー {error_count} / 全 {total}",
            "error" if error_count else "info",
        )

    # -- 検証 --
    def _verify(self, src, dst, method):
        s = os.stat(src)
        d = os.stat(dst)
        if s.st_size != d.st_size:
            return False, f"サイズ不一致 src={s.st_size} dst={d.st_size}"
        if method == "size_time":
            if abs(s.st_mtime - d.st_mtime) > 2:  # FAT等の丸め許容
                return False, f"更新日時不一致 src={s.st_mtime} dst={d.st_mtime}"
            return True, "size_time ok"
        elif method == "hash":
            hs = sha256_of_file(src)
            hd = sha256_of_file(dst)
            if hs != hd:
                return False, f"ハッシュ不一致\n    src={hs}\n    dst={hd}"
            return True, "hash ok"
        return True, ""

    def _mark_残(self, rel_dir, dirs_set):
        """rel_dir とその全祖先を「残ったファイルあり」として記録。"""
        rel_dir = rel_dir.replace("\\", "/")
        while True:
            dirs_set.add(rel_dir)
            if not rel_dir or rel_dir == ".":
                break
            parent = os.path.dirname(rel_dir)
            if parent == rel_dir:
                break
            rel_dir = parent

    def _apply_dir_time(self, src_base, dst_base, rel_dir, src_override=None):
        try:
            src_dir = src_override if src_override else os.path.join(src_base, rel_dir)
            dst_dir = os.path.join(dst_base, rel_dir)
            if os.path.isdir(src_dir) and os.path.isdir(dst_dir):
                st = os.stat(src_dir)
                os.utime(dst_dir, (st.st_atime, st.st_mtime))
        except Exception as e:
            self.log(f"  [警告] フォルダ時刻設定失敗 {rel_dir}: {e}", "warn")

    def _cleanup_source_dirs(self, src_root, dirs_with_残):
        # 深い方から空フォルダを削除
        for cur, dirs, files in os.walk(src_root, topdown=False):
            rel = os.path.relpath(cur, src_root).replace("\\", "/")
            if rel == ".":
                rel = ""
            try:
                if not os.listdir(cur):
                    if cur != src_root:
                        os.rmdir(cur)
                        self.log(f"  [元フォルダ削除] {rel or '(root)'}")
                else:
                    self.log(f"  [残存] {rel or '(root)'} (ファイルが残っています)", "warn")
            except Exception as e:
                self.log(f"  [警告] フォルダ削除失敗 {rel}: {e}", "warn")
        # ルート自体
        try:
            if os.path.isdir(src_root) and not os.listdir(src_root):
                os.rmdir(src_root)
                self.log(f"  [元フォルダ削除] {src_root}")
        except Exception as e:
            self.log(f"  [警告] ルート削除失敗: {e}", "warn")


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class App(_BaseTk):
    def __init__(self):
        super().__init__()
        self.title("Skipferry - 破損ファイルを飛び越えるフォルダ コピー/移動ツール")
        self.geometry("820x780")
        self.msg_queue = queue.Queue()
        self.worker = None

        self._build_ui()
        self._setup_dnd()
        self.after(100, self._poll_queue)

    # ---- UI 構築 ----
    def _build_ui(self):
        pad = {"padx": 6, "pady": 3}

        # パス
        frm_path = ttk.LabelFrame(self, text="フォルダ指定")
        frm_path.pack(fill="x", padx=8, pady=6)

        self.var_src = tk.StringVar()
        self.var_dst = tk.StringVar()

        ttk.Label(frm_path, text="コピー元:").grid(row=0, column=0, sticky="e", **pad)
        self.ent_src = ttk.Entry(frm_path, textvariable=self.var_src, width=70)
        self.ent_src.grid(row=0, column=1, **pad)
        ttk.Button(frm_path, text="参照",
                   command=lambda: self._browse(self.var_src)).grid(row=0, column=2, **pad)

        ttk.Label(frm_path, text="コピー先:").grid(row=1, column=0, sticky="e", **pad)
        self.ent_dst = ttk.Entry(frm_path, textvariable=self.var_dst, width=70)
        self.ent_dst.grid(row=1, column=1, **pad)
        ttk.Button(frm_path, text="参照",
                   command=lambda: self._browse(self.var_dst)).grid(row=1, column=2, **pad)

        self.var_subfolder = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_path, text="コピー先にコピー元フォルダ名のサブフォルダを作成する",
                        variable=self.var_subfolder).grid(row=2, column=1, sticky="w", **pad)

        dnd_note = ("※ 各欄にフォルダをドラッグ&ドロップで設定できます"
                    if HAS_DND else
                    "※ ドラッグ&ドロップを使うには tkinterdnd2 が必要です "
                    "(pip install tkinterdnd2)")
        ttk.Label(frm_path, text=dnd_note, foreground="#666666").grid(
            row=3, column=1, sticky="w", **pad)

        # オプション
        frm_opt = ttk.LabelFrame(self, text="オプション")
        frm_opt.pack(fill="x", padx=8, pady=6)

        # モード
        self.var_mode = tk.StringVar(value="copy")
        ttk.Label(frm_opt, text="動作:").grid(row=0, column=0, sticky="e", **pad)
        ttk.Radiobutton(frm_opt, text="コピー", variable=self.var_mode,
                        value="copy", command=self._sync_states).grid(row=0, column=1, sticky="w", **pad)
        ttk.Radiobutton(frm_opt, text="移動", variable=self.var_mode,
                        value="move", command=self._sync_states).grid(row=0, column=2, sticky="w", **pad)

        self.var_recycle = tk.BooleanVar(value=True)
        self.chk_recycle = ttk.Checkbutton(
            frm_opt, text="移動時: 削除せずコピー後に元をごみ箱へ (安全)",
            variable=self.var_recycle)
        self.chk_recycle.grid(row=0, column=3, sticky="w", **pad)

        # 検証
        self.var_verify = tk.StringVar(value="size_time")
        ttk.Label(frm_opt, text="ベリファイ:").grid(row=1, column=0, sticky="e", **pad)
        ttk.Radiobutton(frm_opt, text="なし", variable=self.var_verify,
                        value="none").grid(row=1, column=1, sticky="w", **pad)
        ttk.Radiobutton(frm_opt, text="サイズ+更新日時", variable=self.var_verify,
                        value="size_time").grid(row=1, column=2, sticky="w", **pad)
        ttk.Radiobutton(frm_opt, text="SHA-256 ハッシュ", variable=self.var_verify,
                        value="hash").grid(row=1, column=3, sticky="w", **pad)

        # エラースキップ / 自動無視
        self.var_skip_error = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_opt, text="エラースキップ (1件失敗しても続行)",
                        variable=self.var_skip_error).grid(row=2, column=1, columnspan=2, sticky="w", **pad)

        self.var_auto_ignore = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_opt, text="エラーファイルを自動で無視リストへ登録",
                        variable=self.var_auto_ignore).grid(row=2, column=3, sticky="w", **pad)

        # 先頭スキップ
        ttk.Label(frm_opt, text="処理順で先頭からスキップする件数:").grid(row=3, column=0, columnspan=2, sticky="e", **pad)
        self.var_skip_n = tk.IntVar(value=0)
        ttk.Spinbox(frm_opt, from_=0, to=1000000, textvariable=self.var_skip_n,
                    width=10).grid(row=3, column=2, sticky="w", **pad)
        ttk.Label(frm_opt, text="(固まる破損ファイル回避用)").grid(row=3, column=3, sticky="w", **pad)

        # ファイルごとのウェイト (OS/アプリ負荷軽減)
        ttk.Label(frm_opt, text="ファイルごとのウェイト(ミリ秒):").grid(
            row=4, column=0, columnspan=2, sticky="e", **pad)
        self.var_wait_ms = tk.IntVar(value=0)
        ttk.Spinbox(frm_opt, from_=0, to=60000, increment=10,
                    textvariable=self.var_wait_ms, width=10).grid(
            row=4, column=2, sticky="w", **pad)
        ttk.Label(frm_opt, text="(0で無効 / OSが重くなる時に増やす)").grid(
            row=4, column=3, sticky="w", **pad)

        # 無視リスト
        frm_ig = ttk.LabelFrame(self, text="無視リスト (ファイルマスク: *.tmp, thumbs.db, sub/*.log 等 / 1行1件)")
        frm_ig.pack(fill="both", expand=False, padx=8, pady=6)

        self.txt_ignore = tk.Text(frm_ig, height=6, width=70)
        self.txt_ignore.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        sb = ttk.Scrollbar(frm_ig, command=self.txt_ignore.yview)
        sb.pack(side="left", fill="y")
        self.txt_ignore.config(yscrollcommand=sb.set)

        frm_ig_btn = ttk.Frame(frm_ig)
        frm_ig_btn.pack(side="left", fill="y", padx=6)
        ttk.Button(frm_ig_btn, text="インポート", command=self._import_ignore).pack(fill="x", pady=2)
        ttk.Button(frm_ig_btn, text="エクスポート", command=self._export_ignore).pack(fill="x", pady=2)
        ttk.Button(frm_ig_btn, text="クリア", command=lambda: self.txt_ignore.delete("1.0", "end")).pack(fill="x", pady=2)

        # ログファイル出力
        frm_lf = ttk.LabelFrame(self, text="ログファイル出力")
        frm_lf.pack(fill="x", padx=8, pady=6)
        self.var_logfile = tk.BooleanVar(value=False)
        self.var_log_path = tk.StringVar()
        ttk.Checkbutton(frm_lf, text="処理ログをファイルに出力する",
                        variable=self.var_logfile).grid(row=0, column=0, columnspan=3,
                                                        sticky="w", **pad)
        ttk.Label(frm_lf, text="出力先:").grid(row=1, column=0, sticky="e", **pad)
        ttk.Entry(frm_lf, textvariable=self.var_log_path, width=64).grid(row=1, column=1, **pad)
        ttk.Button(frm_lf, text="参照", command=self._browse_logfile).grid(row=1, column=2, **pad)
        ttk.Label(frm_lf, text="(空欄なら実行時にコピー先の隣へ自動生成)").grid(
            row=2, column=1, sticky="w", **pad)

        # 実行 / 進捗
        frm_run = ttk.Frame(self)
        frm_run.pack(fill="x", padx=8, pady=6)
        self.btn_run = ttk.Button(frm_run, text="実行", command=self._start)
        self.btn_run.pack(side="left", padx=4)
        self.btn_pause = ttk.Button(frm_run, text="一時停止", command=self._toggle_pause,
                                    state="disabled")
        self.btn_pause.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(frm_run, text="停止", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=4)
        self.progress = ttk.Progressbar(frm_run, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=8)
        self.lbl_prog = ttk.Label(frm_run, text="0 / 0")
        self.lbl_prog.pack(side="left", padx=4)

        # ログ
        frm_log = ttk.LabelFrame(self, text="ログ")
        frm_log.pack(fill="both", expand=True, padx=8, pady=6)

        frm_log_bar = ttk.Frame(frm_log)
        frm_log_bar.pack(fill="x", padx=6, pady=(4, 0))
        ttk.Button(frm_log_bar, text="ログを保存", command=self._save_log).pack(side="left")
        ttk.Button(frm_log_bar, text="ログをクリア",
                   command=lambda: self.txt_log.delete("1.0", "end")).pack(side="left", padx=4)

        frm_log_body = ttk.Frame(frm_log)
        frm_log_body.pack(fill="both", expand=True)
        self.txt_log = tk.Text(frm_log_body, height=12, wrap="none")
        self.txt_log.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        lsb = ttk.Scrollbar(frm_log_body, command=self.txt_log.yview)
        lsb.pack(side="left", fill="y")
        self.txt_log.config(yscrollcommand=lsb.set)
        self.txt_log.tag_config("error", foreground="red")
        self.txt_log.tag_config("warn", foreground="#b58900")
        self.txt_log.tag_config("info", foreground="black")

        self._sync_states()

    def _sync_states(self):
        state = "normal" if self.var_mode.get() == "move" else "disabled"
        self.chk_recycle.config(state=state)

    # ---- ドラッグ&ドロップ ----
    def _setup_dnd(self):
        if not HAS_DND:
            return
        for entry, var in ((self.ent_src, self.var_src),
                           (self.ent_dst, self.var_dst)):
            try:
                entry.drop_target_register(DND_FILES)
                entry.dnd_bind("<<Drop>>",
                               lambda e, v=var: self._on_drop(e, v))
            except Exception:
                pass  # D&D 登録に失敗しても通常操作は可能

    def _on_drop(self, event, var):
        # event.data はブレース括り等を含むためTkのsplitlistで安全に分解する
        try:
            paths = self.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]
        if not paths:
            return
        p = paths[0]
        # ファイルが落とされた場合はその親フォルダを採用
        if os.path.isfile(p):
            p = os.path.dirname(p)
        var.set(os.path.normpath(p))

    def _browse(self, var):
        # 現在設定されているフォルダを初期表示にする。
        # 無い場合は存在する最も近い親、それも無ければOS依存の既定へフォールバック。
        initial = self._resolve_initialdir(var.get().strip())
        d = filedialog.askdirectory(initialdir=initial) if initial \
            else filedialog.askdirectory()
        if d:
            var.set(os.path.normpath(d))

    @staticmethod
    def _resolve_initialdir(path):
        if not path:
            return None
        cur = os.path.abspath(path)
        while True:
            if os.path.isdir(cur):
                return cur
            parent = os.path.dirname(cur)
            if parent == cur:  # ルートまで遡っても無い
                return None
            cur = parent

    def _browse_logfile(self):
        cur = self.var_log_path.get().strip()
        initial_dir = self._resolve_initialdir(
            os.path.dirname(cur) if cur else self.var_dst.get().strip())
        path = filedialog.asksaveasfilename(
            title="ログファイルの出力先",
            defaultextension=".txt",
            initialdir=initial_dir or None,
            initialfile=os.path.basename(cur) if cur else "skipferry_log.txt",
            filetypes=[("テキスト", "*.txt"), ("すべて", "*.*")])
        if path:
            self.var_log_path.set(os.path.normpath(path))
            self.var_logfile.set(True)

    def _resolve_log_file(self, dst):
        """ログファイル出力が有効なら出力先パスを決める。無効なら None。
        空欄時はコピー先の隣に日時入りファイル名で自動生成する。"""
        if not self.var_logfile.get():
            return None
        path = self.var_log_path.get().strip()
        if not path:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            base = os.path.dirname(os.path.abspath(dst)) if dst else os.getcwd()
            if not os.path.isdir(base):
                base = os.getcwd()
            path = os.path.join(base, f"skipferry_log_{ts}.txt")
            self.var_log_path.set(path)  # 生成したパスを欄に反映
        # 出力先フォルダが無ければ作成を試みる
        d = os.path.dirname(os.path.abspath(path))
        if d and not os.path.isdir(d):
            try:
                os.makedirs(d, exist_ok=True)
            except Exception:
                pass
        return path

    def _save_log(self):
        content = self.txt_log.get("1.0", "end").rstrip("\n")
        if not content:
            messagebox.showinfo("情報", "保存するログがありません。")
            return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            title="ログを保存",
            defaultextension=".txt",
            initialfile=f"skipferry_log_{ts}.txt",
            filetypes=[("テキスト", "*.txt"), ("すべて", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content + "\n")
            self._log("info", f"ログを保存しました: {path}")
        except Exception as e:
            messagebox.showerror("エラー", f"ログ保存失敗: {e}")

    # ---- 無視リスト I/O ----
    def _get_ignore_patterns(self):
        text = self.txt_ignore.get("1.0", "end")
        return [ln for ln in text.splitlines()]

    def _import_ignore(self):
        path = filedialog.askopenfilename(
            filetypes=[("テキスト", "*.txt"), ("すべて", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                content = f.read()
            if self.txt_ignore.get("1.0", "end").strip():
                content = "\n" + content
            self.txt_ignore.insert("end", content)
            self._log("info", f"無視リストをインポート: {path}")
        except Exception as e:
            messagebox.showerror("エラー", f"インポート失敗: {e}")

    def _export_ignore(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("テキスト", "*.txt"), ("すべて", "*.*")])
        if not path:
            return
        try:
            lines = [ln for ln in self._get_ignore_patterns() if ln.strip()]
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            self._log("info", f"無視リストをエクスポート: {path}")
        except Exception as e:
            messagebox.showerror("エラー", f"エクスポート失敗: {e}")

    def _append_ignore_pattern(self, pattern):
        existing = set(p.strip() for p in self._get_ignore_patterns())
        if pattern not in existing:
            cur = self.txt_ignore.get("1.0", "end")
            if cur.strip() and not cur.endswith("\n"):
                self.txt_ignore.insert("end", "\n")
            self.txt_ignore.insert("end", pattern + "\n")

    # ---- 実行制御 ----
    def _start(self):
        if self.worker and self.worker.is_alive():
            return
        src = self.var_src.get().strip()
        dst = self.var_dst.get().strip()
        if not src or not dst:
            messagebox.showwarning("入力不足", "コピー元/コピー先を指定してください。")
            return
        if not os.path.isdir(src):
            messagebox.showerror("エラー", "コピー元フォルダが存在しません。")
            return

        cfg = {
            "source": src,
            "dest": dst,
            "make_subfolder": self.var_subfolder.get(),
            "mode": self.var_mode.get(),
            "move_to_recycle": self.var_recycle.get(),
            "verify": self.var_verify.get(),
            "skip_error": self.var_skip_error.get(),
            "auto_ignore_on_error": self.var_auto_ignore.get(),
            "skip_first_n": max(0, int(self.var_skip_n.get() or 0)),
            "wait_ms": max(0, int(self.var_wait_ms.get() or 0)),
            "ignore_patterns": self._get_ignore_patterns(),
            "log_file": self._resolve_log_file(dst),
        }

        # 処理計画を作成してプレビューを表示 (実行前確認)
        plan = plan_operation(cfg)
        if plan["error"]:
            messagebox.showerror("エラー", plan["error"])
            return
        if not self._show_preview(cfg, plan):
            return

        self.txt_log.delete("1.0", "end")
        self.progress.config(value=0)
        self.lbl_prog.config(text="0 / 0")
        self.btn_run.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.btn_pause.config(state="normal", text="一時停止")

        self.worker = CopyMoveWorker(cfg, self.msg_queue)
        self.worker.start()

    def _stop(self):
        if self.worker and self.worker.is_alive():
            self.worker.stop_flag.set()
            self.worker.pause_flag.clear()  # 一時停止中でも停止できるよう解除
            self._log("warn", "停止要求を送信しました...")

    def _toggle_pause(self):
        if not (self.worker and self.worker.is_alive()):
            return
        if self.worker.pause_flag.is_set():
            self.worker.pause_flag.clear()
            self.btn_pause.config(text="一時停止")
            self._log("info", "再開要求を送信しました...")
        else:
            self.worker.pause_flag.set()
            self.btn_pause.config(text="再開")
            self._log("warn", "一時停止要求を送信しました...")

    # ---- 実行前プレビュー ----
    def _show_preview(self, cfg, plan):
        """処理先ファイル(先頭10件)を提示し、実行可否を確認する。
        戻り値: True=実行 / False=キャンセル"""
        PREVIEW_N = 10
        dlg = tk.Toplevel(self)
        dlg.title("処理内容のプレビュー")
        dlg.geometry("780x520")
        dlg.transient(self)
        dlg.grab_set()
        result = {"ok": False}

        # 動作説明
        if cfg["mode"] == "move":
            del_txt = "ごみ箱へ" if cfg["move_to_recycle"] else "完全削除"
            mode_txt = f"移動（コピー後、元ファイルを{del_txt}）"
        else:
            mode_txt = "コピー"

        info = (
            f"動作: {mode_txt}\n"
            f"コピー元: {plan['src_root']}\n"
            f"コピー先ルート: {plan['dst_root']}\n"
            f"処理対象: {len(plan['file_list'])} ファイル"
            f"（無視 {len(plan['ignored_list'])} / 先頭スキップ {plan['skip_n']}）"
        )
        ttk.Label(dlg, text=info, justify="left").pack(anchor="w", padx=12, pady=(10, 4))

        # サブフォルダ作成オプションの効果を明示
        if cfg["make_subfolder"]:
            note = ("サブフォルダ作成: ON → コピー先の直下に「元フォルダ名」の"
                    "フォルダを作成し、その中へ展開します。")
        else:
            note = ("サブフォルダ作成: OFF → コピー先の直下へ中身を直接展開します"
                    "（元フォルダ名のフォルダは作りません）。")
        ttk.Label(dlg, text=note, foreground="#0066cc",
                  wraplength=740, justify="left").pack(anchor="w", padx=12, pady=(0, 6))

        ttk.Label(dlg, text=f"処理先ファイル（先頭 {PREVIEW_N} 件）:").pack(
            anchor="w", padx=12)

        frm = ttk.Frame(dlg)
        frm.pack(fill="both", expand=True, padx=12, pady=4)
        txt = tk.Text(frm, height=14, wrap="none")
        txt.pack(side="left", fill="both", expand=True)
        psb = ttk.Scrollbar(frm, command=txt.yview)
        psb.pack(side="left", fill="y")
        txt.config(yscrollcommand=psb.set)

        preview = plan["file_list"][:PREVIEW_N]
        if not preview:
            txt.insert("end", "（処理対象のファイルがありません）\n")
        for rel, _name in preview:
            dst_file = os.path.join(plan["dst_root"], rel)
            txt.insert("end", dst_file + "\n")
        remain = len(plan["file_list"]) - len(preview)
        if remain > 0:
            txt.insert("end", f"... 他 {remain} ファイル\n")
        txt.config(state="disabled")

        btns = ttk.Frame(dlg)
        btns.pack(fill="x", padx=12, pady=10)

        def do_ok():
            result["ok"] = True
            dlg.destroy()

        def do_cancel():
            dlg.destroy()

        run_btn = ttk.Button(btns, text="この内容で実行", command=do_ok)
        run_btn.pack(side="right", padx=(8, 0))
        ttk.Button(btns, text="キャンセル", command=do_cancel).pack(side="right")
        if not preview:
            run_btn.config(state="disabled")

        dlg.bind("<Escape>", lambda e: do_cancel())
        run_btn.focus_set()
        dlg.wait_window()
        return result["ok"]

    # ---- キュー処理 ----
    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    level, text = payload
                    self._log(level, text)
                elif kind == "progress":
                    done, total = payload
                    self.progress.config(maximum=max(total, 1), value=done)
                    self.lbl_prog.config(text=f"{done} / {total}")
                elif kind == "ignore_add":
                    self._append_ignore_pattern(payload)
                elif kind == "done":
                    self.btn_run.config(state="normal")
                    self.btn_stop.config(state="disabled")
                    self.btn_pause.config(state="disabled", text="一時停止")
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _log(self, level, text):
        self.txt_log.insert("end", text + "\n", level)
        self.txt_log.see("end")


if __name__ == "__main__":
    App().mainloop()
