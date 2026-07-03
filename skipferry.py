# -*- coding: utf-8 -*-
"""
Skipferry - 破損ファイルを飛び越えるフォルダ コピー/移動ツール (Python / Tkinter)
Skipferry - Folder copy/move tool that skips over corrupt files (Python / Tkinter)

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
  - UI/ログの言語切替 (日本語 / English)

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
import configparser
import multiprocessing

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
# 国際化 (i18n) — 日本語 / 英語
# ---------------------------------------------------------------------------
# 現在の言語。既定は日本語 (主対象が日本語Windowsのため)。
CURRENT_LANG = "ja"

# 対応言語 (コード, 表示名)。UI の言語切替に使用。
LANGUAGES = [("ja", "日本語"), ("en", "English")]

TRANSLATIONS = {
    "ja": {
        # ---- 全般 / ウィンドウ ----
        "app_title": "Skipferry - 破損ファイルを飛び越えるフォルダ コピー/移動ツール",
        "lang_label": "言語 / Language:",
        # ---- フォルダ指定 ----
        "frame_folders": "フォルダ指定",
        "lbl_source": "コピー元:",
        "lbl_dest": "コピー先:",
        "btn_browse": "参照",
        "chk_subfolder": "コピー先にコピー元フォルダ名のサブフォルダを作成する",
        "dnd_on": "※ 各欄にフォルダをドラッグ&ドロップで設定できます",
        "dnd_off": "※ ドラッグ&ドロップを使うには tkinterdnd2 が必要です "
                   "(pip install tkinterdnd2)",
        # ---- オプション ----
        "frame_options": "オプション",
        "lbl_action": "動作:",
        "rb_copy": "コピー",
        "rb_move": "移動",
        "chk_recycle": "移動時: 削除せずコピー後に元をごみ箱へ (安全)",
        "lbl_verify": "ベリファイ:",
        "rb_verify_none": "なし",
        "rb_verify_size": "サイズ+更新日時",
        "rb_verify_hash": "SHA-256 ハッシュ",
        "chk_skip_error": "エラースキップ (1件失敗しても続行)",
        "chk_auto_ignore": "エラーファイルを自動で無視リストへ登録",
        "lbl_skip_n": "処理順で先頭からスキップする件数:",
        "lbl_skip_n_note": "(固まる破損ファイル回避用)",
        "lbl_wait": "ファイルごとのウェイト(ミリ秒):",
        "lbl_wait_note": "(0で無効 / OSが重くなる時に増やす)",
        "lbl_read_timeout": "リード タイムアウト(秒):",
        "lbl_read_timeout_note": "(0で無効 / 固まる破損ファイルを指定秒で打ち切り)",
        # ---- 無視リスト ----
        "frame_ignore": "無視リスト (ファイルマスク: *.tmp, thumbs.db, sub/*.log 等 / 1行1件)",
        "btn_import": "インポート",
        "btn_export": "エクスポート",
        "btn_clear": "クリア",
        # ---- ログファイル出力 ----
        "frame_logfile": "ログファイル出力",
        "chk_logfile": "処理ログをファイルに出力する",
        "lbl_logout": "出力先:",
        "lbl_logout_note": "(空欄なら実行時にコピー先の隣へ自動生成)",
        # ---- 実行 / 進捗 ----
        "btn_run": "実行",
        "btn_pause": "一時停止",
        "btn_resume": "再開",
        "btn_stop": "停止",
        # ---- ログ ----
        "frame_log": "ログ",
        "btn_save_log": "ログを保存",
        "btn_clear_log": "ログをクリア",
        # ---- メッセージボックス タイトル ----
        "title_info": "情報",
        "title_error": "エラー",
        "title_warn": "警告",
        "title_input": "入力不足",
        # ---- ダイアログ / メッセージ ----
        "msg_need_src_dst": "コピー元/コピー先を指定してください。",
        "msg_src_not_exist": "コピー元フォルダが存在しません。",
        "msg_no_log_to_save": "保存するログがありません。",
        "msg_log_saved": "ログを保存しました: {path}",
        "msg_log_save_fail": "ログ保存失敗: {e}",
        "msg_import_fail": "インポート失敗: {e}",
        "msg_export_fail": "エクスポート失敗: {e}",
        # ---- ファイルダイアログ ----
        "fd_logfile_title": "ログファイルの出力先",
        "fd_save_log_title": "ログを保存",
        "ft_text": "テキスト",
        "ft_all": "すべて",
        # ---- ログ (GUI操作) ----
        "log_import_ignore": "無視リストをインポート: {path}",
        "log_export_ignore": "無視リストをエクスポート: {path}",
        # ---- プレビュー ----
        "pv_title": "処理内容のプレビュー",
        "pv_del_recycle": "ごみ箱へ",
        "pv_del_permanent": "完全削除",
        "pv_mode_move": "移動（コピー後、元ファイルを{del_txt}）",
        "pv_mode_copy": "コピー",
        "pv_info": "動作: {mode}\nコピー元: {src}\nコピー先ルート: {dst}\n"
                   "処理対象: {n} ファイル（無視 {ig} / 先頭スキップ {skip}）",
        "pv_note_on": "サブフォルダ作成: ON → コピー先の直下に「元フォルダ名」の"
                      "フォルダを作成し、その中へ展開します。",
        "pv_note_off": "サブフォルダ作成: OFF → コピー先の直下へ中身を直接展開します"
                       "（元フォルダ名のフォルダは作りません）。",
        "pv_list_header": "処理先ファイル（先頭 {n} 件）:",
        "pv_none": "（処理対象のファイルがありません）",
        "pv_more": "... 他 {n} ファイル",
        "pv_btn_run": "この内容で実行",
        "pv_btn_cancel": "キャンセル",
        # ---- 計画 / 検査エラー ----
        "err_src_not_exist": "コピー元フォルダが存在しません: {src}",
        "err_dst_inside_src": "コピー先がコピー元の内部/同一です。",
        "err_ctrl_char": "コピー先パスに制御文字が含まれています（構成要素「{p}」/ {codes}）",
        "err_invalid_char": "コピー先フォルダ名にWindowsで使用できない文字が含まれています："
                            "{bad} （構成要素「{p}」）\n使用不可: < > : \" | ? *",
        "err_trailing": "コピー先フォルダ名の末尾に空白またはドットは使用できません"
                        "（構成要素「{p}」）",
        "err_reserved": "コピー先フォルダ名にWindowsの予約名は使用できません（構成要素「{p}」）",
        # ---- 設定内容の書き出し ----
        "log_settings_header": "----- 設定内容 -----",
        "val_on": "有効",
        "val_off": "無効",
        "set_source": "コピー元: {v}",
        "set_dest": "コピー先: {v}",
        "set_subfolder": "サブフォルダ作成: {v}",
        "set_mode": "動作: {v}",
        "set_recycle": "移動時の元ファイル: {v}",
        "set_verify": "ベリファイ: {v}",
        "set_skip_error": "エラースキップ: {v}",
        "set_auto_ignore": "エラー時自動無視登録: {v}",
        "set_skip_n": "先頭スキップ件数: {v}",
        "set_wait": "ファイルごとのウェイト: {v} ミリ秒",
        "set_read_timeout": "リード タイムアウト: {v} 秒",
        "set_ignore_count": "無視パターン数: {v}",
        "set_logfile": "ログファイル: {v}",
        # ---- ワーカー / 処理ログ ----
        "log_building_list": "ファイル一覧を作成中...",
        "log_abort": "{error} 中止します。",
        "log_ignored": "[無視] {rel}",
        "log_lead_skip": "[先頭スキップ] {rel}",
        "log_target_count": "処理対象: {total} ファイル (無視 {ig} / 先頭スキップ {skip})",
        "log_item": "[{done}/{total}] {rel}",
        "log_item_copied": " [コピー完了]",
        "log_item_removed": " [元ファイル削除]",
        "log_item_recycled": " [元ファイルをゴミ箱へ]",
        "err_verify_fail": "ベリファイ失敗: {detail}",
        "err_read_timeout": "リード タイムアウト ({sec}秒) により打ち切り",
        "log_item_err": " [エラー] {e}",
        "log_auto_ignore": "  [自動無視登録] {pat}",
        "log_skip_error_off": "エラースキップが無効のため中止します。",
        "log_retrying": "  [再試行] {rel}",
        "log_aborted_by_user": "ユーザーが「終了」を選択しました。中止します。",
        # ---- エラー確認ダイアログ (エラースキップ無効時) ----
        "dlg_error_title": "エラー — 処理を中断中",
        "dlg_error_msg": "ファイルの処理でエラーが発生しました。どうしますか？",
        "dlg_error_file": "ファイル: {rel}",
        "dlg_error_detail": "エラー: {err}",
        "dlg_btn_retry": "再試行",
        "dlg_btn_skip": "スキップ",
        "dlg_btn_abort": "終了",
        "log_stopped": "ユーザーにより停止されました。",
        "log_applying_dir_time": "フォルダのタイムスタンプを適用中...",
        "log_removing_empty": "移動元の空フォルダを削除中...",
        "log_done": "完了: 成功 {ok} / エラー {err} / 全 {total}",
        "log_paused": "一時停止中... （再開ボタンで続行）",
        "log_resumed": "再開しました。",
        # ---- 検証詳細 ----
        "vf_size_mismatch": "サイズ不一致 src={s} dst={d}",
        "vf_time_mismatch": "更新日時不一致 src={s} dst={d}",
        "vf_size_ok": "size_time ok",
        "vf_hash_mismatch": "ハッシュ不一致\n    src={hs}\n    dst={hd}",
        "vf_hash_ok": "hash ok",
        # ---- フォルダ後処理 ----
        "warn_dir_time_fail": "  [警告] フォルダ時刻設定失敗 {rel}: {e}",
        "log_dir_removed": "  [元フォルダ削除] {rel}",
        "warn_dir_residual": "  [残存] {rel} (ファイルが残っています)",
        "warn_dir_remove_fail": "  [警告] フォルダ削除失敗 {rel}: {e}",
        "warn_root_remove_fail": "  [警告] ルート削除失敗: {e}",
        "log_root_placeholder": "(root)",
        # ---- ログファイル ----
        "logf_start": "===== Skipferry ログ開始 {stamp} =====",
        "logf_end": "===== Skipferry ログ終了 =====",
        "log_to_file": "ログをファイルへ出力: {path}",
        "log_cant_open": "ログファイルを開けません: {e}",
        "log_fatal": "致命的エラー:\n{tb}",
        # ---- 実行制御 ----
        "log_stop_requested": "停止要求を送信しました...",
        "log_resume_requested": "再開要求を送信しました...",
        "log_pause_requested": "一時停止要求を送信しました...",
        # ---- ごみ箱 ----
        "err_need_send2trash": "ごみ箱送りには send2trash が必要です (pip install Send2Trash)",
    },
    "en": {
        # ---- General / window ----
        "app_title": "Skipferry - Folder copy/move tool that skips over corrupt files",
        "lang_label": "言語 / Language:",
        # ---- Folders ----
        "frame_folders": "Folders",
        "lbl_source": "Source:",
        "lbl_dest": "Destination:",
        "btn_browse": "Browse",
        "chk_subfolder": "Create a subfolder named after the source folder in the destination",
        "dnd_on": "* You can drag & drop a folder onto each field",
        "dnd_off": "* Drag & drop requires tkinterdnd2 (pip install tkinterdnd2)",
        # ---- Options ----
        "frame_options": "Options",
        "lbl_action": "Action:",
        "rb_copy": "Copy",
        "rb_move": "Move",
        "chk_recycle": "On move: send source to Recycle Bin after copy (safe)",
        "lbl_verify": "Verify:",
        "rb_verify_none": "None",
        "rb_verify_size": "Size + mtime",
        "rb_verify_hash": "SHA-256 hash",
        "chk_skip_error": "Skip errors (continue on failure)",
        "chk_auto_ignore": "Auto-add error files to the ignore list",
        "lbl_skip_n": "Number of leading files to skip (in processing order):",
        "lbl_skip_n_note": "(to avoid hang-inducing corrupt files)",
        "lbl_wait": "Wait per file (ms):",
        "lbl_wait_note": "(0 = off / increase if the OS slows down)",
        "lbl_read_timeout": "Read timeout (sec):",
        "lbl_read_timeout_note": "(0 = off / abort hang-inducing files after N sec)",
        # ---- Ignore list ----
        "frame_ignore": "Ignore list (file masks: *.tmp, thumbs.db, sub/*.log, etc. / one per line)",
        "btn_import": "Import",
        "btn_export": "Export",
        "btn_clear": "Clear",
        # ---- Log file output ----
        "frame_logfile": "Log file output",
        "chk_logfile": "Write the processing log to a file",
        "lbl_logout": "Output:",
        "lbl_logout_note": "(if blank, auto-created next to the destination at run time)",
        # ---- Run / progress ----
        "btn_run": "Run",
        "btn_pause": "Pause",
        "btn_resume": "Resume",
        "btn_stop": "Stop",
        # ---- Log ----
        "frame_log": "Log",
        "btn_save_log": "Save log",
        "btn_clear_log": "Clear log",
        # ---- Message box titles ----
        "title_info": "Information",
        "title_error": "Error",
        "title_warn": "Warning",
        "title_input": "Missing input",
        # ---- Dialogs / messages ----
        "msg_need_src_dst": "Please specify both source and destination.",
        "msg_src_not_exist": "The source folder does not exist.",
        "msg_no_log_to_save": "There is no log to save.",
        "msg_log_saved": "Log saved: {path}",
        "msg_log_save_fail": "Failed to save log: {e}",
        "msg_import_fail": "Import failed: {e}",
        "msg_export_fail": "Export failed: {e}",
        # ---- File dialogs ----
        "fd_logfile_title": "Log file output location",
        "fd_save_log_title": "Save log",
        "ft_text": "Text",
        "ft_all": "All files",
        # ---- Log (GUI actions) ----
        "log_import_ignore": "Imported ignore list: {path}",
        "log_export_ignore": "Exported ignore list: {path}",
        # ---- Preview ----
        "pv_title": "Preview",
        "pv_del_recycle": "send to Recycle Bin",
        "pv_del_permanent": "permanently delete",
        "pv_mode_move": "Move (after copy, {del_txt} the source)",
        "pv_mode_copy": "Copy",
        "pv_info": "Action: {mode}\nSource: {src}\nDestination root: {dst}\n"
                   "To process: {n} files (ignored {ig} / leading-skipped {skip})",
        "pv_note_on": "Create subfolder: ON -> creates a folder named after the source "
                      "under the destination and expands into it.",
        "pv_note_off": "Create subfolder: OFF -> expands the contents directly under the "
                       "destination (no source-named folder is created).",
        "pv_list_header": "Destination files (first {n}):",
        "pv_none": "(no files to process)",
        "pv_more": "... and {n} more files",
        "pv_btn_run": "Run with these settings",
        "pv_btn_cancel": "Cancel",
        # ---- Plan / validation errors ----
        "err_src_not_exist": "Source folder does not exist: {src}",
        "err_dst_inside_src": "The destination is inside or the same as the source.",
        "err_ctrl_char": "The destination path contains control characters "
                         "(component \"{p}\" / {codes})",
        "err_invalid_char": "The destination folder name contains characters not allowed on "
                            "Windows: {bad} (component \"{p}\")\nNot allowed: < > : \" | ? *",
        "err_trailing": "The destination folder name must not end with a space or a dot "
                        "(component \"{p}\")",
        "err_reserved": "The destination folder name must not be a Windows reserved name "
                        "(component \"{p}\")",
        # ---- Settings dump ----
        "log_settings_header": "----- Settings -----",
        "val_on": "on",
        "val_off": "off",
        "set_source": "Source: {v}",
        "set_dest": "Destination: {v}",
        "set_subfolder": "Create subfolder: {v}",
        "set_mode": "Action: {v}",
        "set_recycle": "Source after move: {v}",
        "set_verify": "Verify: {v}",
        "set_skip_error": "Skip errors: {v}",
        "set_auto_ignore": "Auto-ignore on error: {v}",
        "set_skip_n": "Leading skip count: {v}",
        "set_wait": "Wait per file: {v} ms",
        "set_read_timeout": "Read timeout: {v} sec",
        "set_ignore_count": "Ignore patterns: {v}",
        "set_logfile": "Log file: {v}",
        # ---- Worker / processing log ----
        "log_building_list": "Building file list...",
        "log_abort": "{error} Aborting.",
        "log_ignored": "[ignored] {rel}",
        "log_lead_skip": "[leading-skip] {rel}",
        "log_target_count": "To process: {total} files (ignored {ig} / leading-skipped {skip})",
        "log_item": "[{done}/{total}] {rel}",
        "log_item_copied": " [copied]",
        "log_item_removed": " [source deleted]",
        "log_item_recycled": " [source to Recycle Bin]",
        "err_verify_fail": "Verification failed: {detail}",
        "err_read_timeout": "Aborted by read timeout ({sec}s)",
        "log_item_err": " [error] {e}",
        "log_auto_ignore": "  [auto-ignored] {pat}",
        "log_skip_error_off": "Error-skip is off, so aborting.",
        "log_retrying": "  [Retry] {rel}",
        "log_aborted_by_user": "User chose \"Abort\". Stopping.",
        # ---- Error dialog (when error-skip is off) ----
        "dlg_error_title": "Error — processing paused",
        "dlg_error_msg": "An error occurred while processing a file. What do you want to do?",
        "dlg_error_file": "File: {rel}",
        "dlg_error_detail": "Error: {err}",
        "dlg_btn_retry": "Retry",
        "dlg_btn_skip": "Skip",
        "dlg_btn_abort": "Abort",
        "log_stopped": "Stopped by the user.",
        "log_applying_dir_time": "Applying folder timestamps...",
        "log_removing_empty": "Removing empty source folders...",
        "log_done": "Done: OK {ok} / errors {err} / total {total}",
        "log_paused": "Paused... (press Resume to continue)",
        "log_resumed": "Resumed.",
        # ---- Verify details ----
        "vf_size_mismatch": "size mismatch src={s} dst={d}",
        "vf_time_mismatch": "mtime mismatch src={s} dst={d}",
        "vf_size_ok": "size_time ok",
        "vf_hash_mismatch": "hash mismatch\n    src={hs}\n    dst={hd}",
        "vf_hash_ok": "hash ok",
        # ---- Folder post-processing ----
        "warn_dir_time_fail": "  [warn] failed to set folder time {rel}: {e}",
        "log_dir_removed": "  [source folder removed] {rel}",
        "warn_dir_residual": "  [residual] {rel} (files remain)",
        "warn_dir_remove_fail": "  [warn] failed to remove folder {rel}: {e}",
        "warn_root_remove_fail": "  [warn] failed to remove root: {e}",
        "log_root_placeholder": "(root)",
        # ---- Log file ----
        "logf_start": "===== Skipferry log start {stamp} =====",
        "logf_end": "===== Skipferry log end =====",
        "log_to_file": "Writing log to file: {path}",
        "log_cant_open": "Cannot open log file: {e}",
        "log_fatal": "Fatal error:\n{tb}",
        # ---- Run control ----
        "log_stop_requested": "Stop request sent...",
        "log_resume_requested": "Resume request sent...",
        "log_pause_requested": "Pause request sent...",
        # ---- Recycle bin ----
        "err_need_send2trash": "Sending to the Recycle Bin requires send2trash "
                               "(pip install Send2Trash)",
    },
}


def t(key, lang=None, **kwargs):
    """翻訳文字列を返す。lang 未指定なら現在の言語。欠落キーは日本語→キーの順でフォールバック。"""
    lang = lang or CURRENT_LANG
    table = TRANSLATIONS.get(lang, TRANSLATIONS["ja"])
    s = table.get(key)
    if s is None:
        s = TRANSLATIONS["ja"].get(key, key)
    if kwargs:
        try:
            s = s.format(**kwargs)
        except Exception:
            pass
    return s


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
        raise RuntimeError(t("err_need_send2trash"))


# ---------------------------------------------------------------------------
# 設定ファイル (ini) — 実行時に保存し、次回起動時に読み込む
# ---------------------------------------------------------------------------
# スクリプト (フリーズ時は実行ファイル) と同じフォルダに置くポータブル方式。
CONFIG_FILENAME = "skipferry.ini"


def _config_path():
    """設定ファイル (skipferry.ini) の絶対パスを返す。"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        try:
            base = os.path.dirname(os.path.abspath(__file__))
        except NameError:
            base = os.getcwd()
    return os.path.join(base, CONFIG_FILENAME)


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


def check_dest_path_valid(dst_root, lang=None):
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
            return t("err_ctrl_char", lang=lang, p=p, codes=codes)
        if not win:
            continue
        bad = sorted({c for c in p if c in _WIN_INVALID_CHARS})
        if bad:
            return t("err_invalid_char", lang=lang, bad=" ".join(bad), p=p)
        if p != p.rstrip(" ."):
            return t("err_trailing", lang=lang, p=p)
        stem = p.split(".")[0].upper()
        if stem in _WIN_RESERVED:
            return t("err_reserved", lang=lang, p=p)
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
    lang = cfg.get("lang")
    src_root = os.path.abspath(cfg["source"])
    dst_base = os.path.abspath(cfg["dest"])
    plan = {"src_root": src_root, "dst_root": None, "error": None,
            "file_list": [], "dir_list": [], "ignored_list": [],
            "skipped_list": [], "skip_n": 0}

    if not os.path.isdir(src_root):
        plan["error"] = t("err_src_not_exist", lang=lang, src=src_root)
        return plan

    if cfg["make_subfolder"]:
        dst_root = os.path.join(dst_base, os.path.basename(src_root.rstrip("\\/")))
    else:
        dst_root = dst_base
    plan["dst_root"] = dst_root

    # コピー先パスに使用不可文字/予約名が無いか検査
    path_err = check_dest_path_valid(dst_root, lang=lang)
    if path_err:
        plan["error"] = path_err
        return plan

    if os.path.abspath(dst_root) == src_root or \
       os.path.abspath(dst_root).startswith(src_root + os.sep):
        plan["error"] = t("err_dst_inside_src", lang=lang)
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


class _Stopped(Exception):
    """リード タイムアウト待機中に停止要求が来たことを示す内部例外。"""


def _copy_worker_main(in_q, out_q):
    """リード タイムアウト用の子プロセス本体。

    親から (src, dst) を受け取り `shutil.copy2` を実行して結果を返すだけの
    ループ。破損ファイルの read で固まった場合はこのプロセスごと親から
    terminate される (スレッドは kill できないが、プロセスは kill できる)。
    None を受け取ったら正常終了する。
    ベリファイ (ハッシュ再読込) は copy 成功後 = 元が読めた後なので親スレッド側に
    残す (二重の hang リスクは実質無いため、ここでは copy のみ担当する)。
    """
    while True:
        try:
            task = in_q.get()
        except (EOFError, OSError):
            return
        if task is None:
            return
        src, dst = task
        try:
            shutil.copy2(src, dst)
            out_q.put((True, ""))
        except Exception as e:  # 破損以外のエラーもメッセージにして親へ返す
            out_q.put((False, f"{type(e).__name__}: {e}"))


# ---------------------------------------------------------------------------
# ワーカー (別スレッドで実行)
# ---------------------------------------------------------------------------
class CopyMoveWorker(threading.Thread):
    def __init__(self, cfg, msg_queue):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.q = msg_queue
        self.lang = cfg.get("lang") or CURRENT_LANG  # 実行中の言語を固定
        self.stop_flag = threading.Event()
        self.pause_flag = threading.Event()  # set=一時停止中
        self.logf = None  # ログファイルのハンドル (任意)
        # リード タイムアウト用の常駐子プロセス (timeout>0 のときだけ生成し、
        # hang して kill したら次回に再生成する)
        self._copy_proc = None
        self._copy_in = None
        self._copy_out = None
        # エラースキップ無効時のエラー確認ダイアログとの協調 (GUI スレッドが応答を書く)
        self.error_event = threading.Event()   # GUI が選択したら set
        self.error_response = None             # "retry" | "skip" | "abort"

    def tr(self, key, **kwargs):
        """開始時に固定した言語で翻訳する (実行中に切替えてもログはぶれない)。"""
        return t(key, lang=self.lang, **kwargs)

    def log(self, text, level="info", newline=True):
        """1行分のログを出す。newline=False で行を開いたままにし、
        続けて log_append で同じ行の末尾へ追記できる (処理開始行→完了/エラー用)。"""
        end = "\n" if newline else ""
        self.q.put(("log", (level, text, end)))
        # ログファイルが有効なら時刻付きで書き出す
        if self.logf is not None:
            try:
                ts = datetime.datetime.now().strftime("%H:%M:%S")
                self.logf.write(f"[{ts}] {text}{end}")
                self.logf.flush()
            except Exception:
                pass  # ファイル書き込み失敗は処理本体を止めない

    def log_append(self, text, level="info", newline=True):
        """log(newline=False) で開いた行の末尾へ追記する (時刻プレフィックス無し)。"""
        end = "\n" if newline else ""
        self.q.put(("log", (level, text, end)))
        if self.logf is not None:
            try:
                self.logf.write(f"{text}{end}")
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
                self.log(self.tr("log_paused"), "warn")
                announced = True
            time.sleep(0.1)
        if announced and not self.stop_flag.is_set():
            self.log(self.tr("log_resumed"), "info")
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

    # -- リード タイムアウト用の子プロセス管理 --
    def _ensure_copy_proc(self):
        """コピー用の子プロセスが生きていることを保証する (無ければ生成)。"""
        if self._copy_proc is not None and self._copy_proc.is_alive():
            return
        self._copy_in = multiprocessing.Queue()
        self._copy_out = multiprocessing.Queue()
        self._copy_proc = multiprocessing.Process(
            target=_copy_worker_main, args=(self._copy_in, self._copy_out),
            daemon=True)
        self._copy_proc.start()

    def _kill_copy_proc(self):
        """子プロセスを強制終了する (hang 時)。キューも破棄し次回に作り直す。"""
        p = self._copy_proc
        self._copy_proc = None
        self._copy_in = None
        self._copy_out = None
        if p is None:
            return
        try:
            p.terminate()
        except Exception:
            pass
        try:
            p.join(timeout=2.0)
        except Exception:
            pass

    def _shutdown_copy_proc(self):
        """処理終了時に子プロセスを後始末する (正常終了を試み、駄目なら kill)。"""
        p = self._copy_proc
        if p is None:
            return
        try:
            if self._copy_in is not None:
                self._copy_in.put(None)  # 正常終了の合図
        except Exception:
            pass
        try:
            p.join(timeout=1.0)
        except Exception:
            pass
        if p.is_alive():
            self._kill_copy_proc()
        else:
            self._copy_proc = None
            self._copy_in = None
            self._copy_out = None

    @staticmethod
    def _remove_partial(path):
        """タイムアウトで途中まで書かれたコピー先ファイルを掃除する。"""
        try:
            if os.path.isfile(path):
                os.remove(path)
        except Exception:
            pass  # 消せなくても処理本体は止めない

    def _copy_with_timeout(self, src, dst, timeout_s):
        """子プロセスで copy2 を実行し、timeout_s 秒以内に終わらなければ
        プロセスごと kill して TimeoutError を送出する。停止要求が来たら
        _Stopped を送出する。停止/タイムアウトの応答性のため 0.1 秒刻みで待つ。"""
        self._ensure_copy_proc()
        self._copy_in.put((src, dst))
        steps = max(1, int(round(timeout_s / 0.1)))
        for _ in range(steps):
            if self.stop_flag.is_set():
                self._kill_copy_proc()
                raise _Stopped()
            try:
                ok, msg = self._copy_out.get(timeout=0.1)
            except queue.Empty:
                continue
            if ok:
                return
            raise IOError(msg)  # 破損以外のコピー失敗 (通常のエラー扱い)
        # 時間切れ: 子プロセスごと kill し、途中まで書かれた dst を掃除する
        self._kill_copy_proc()
        self._remove_partial(dst)
        raise TimeoutError(self.tr("err_read_timeout", sec=timeout_s))

    def _ask_error_action(self, rel, err_text):
        """エラースキップ無効時、GUI にエラー確認ダイアログを依頼して応答を待つ。
        戻り値: "retry"=再試行 / "skip"=このファイルを飛ばす / "abort"=終了。
        GUI が無い/停止要求時は "abort" とみなす (0.1 秒刻みで停止に応答)。"""
        self.error_event.clear()
        self.error_response = None
        self.q.put(("ask_error", (rel, err_text)))
        while not self.error_event.is_set():
            if self.stop_flag.is_set():
                return "abort"
            time.sleep(0.1)
        return self.error_response or "abort"

    def run(self):
        # ログファイル出力が指定されていれば開く
        log_path = self.cfg.get("log_file")
        if log_path:
            try:
                self.logf = open(log_path, "a", encoding="utf-8")
                stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.logf.write("\n" + self.tr("logf_start", stamp=stamp) + "\n")
                self.logf.flush()
                self.q.put(("log", ("info", self.tr("log_to_file", path=log_path), "\n")))
            except Exception as e:
                self.logf = None
                self.q.put(("log", ("error", self.tr("log_cant_open", e=e), "\n")))
        try:
            self._run()
        except Exception:
            self.log(self.tr("log_fatal", tb=traceback.format_exc()), "error")
        finally:
            self._shutdown_copy_proc()  # リード タイムアウト用の子プロセスを後始末
            if self.logf is not None:
                try:
                    self.logf.write(self.tr("logf_end") + "\n")
                    self.logf.close()
                except Exception:
                    pass
                self.logf = None
            self.q.put(("done", None))

    def _log_settings(self):
        """処理開始時にオプション設定内容をログへ書き出す。"""
        cfg = self.cfg
        on_off = lambda b: self.tr("val_on") if b else self.tr("val_off")
        is_move = cfg["mode"] == "move"

        self.log(self.tr("log_settings_header"))
        self.log(self.tr("set_source", v=cfg["source"]))
        self.log(self.tr("set_dest", v=cfg["dest"]))
        self.log(self.tr("set_subfolder", v=on_off(cfg["make_subfolder"])))

        mode_txt = self.tr("rb_move") if is_move else self.tr("rb_copy")
        self.log(self.tr("set_mode", v=mode_txt))
        if is_move:
            recycle_txt = (self.tr("pv_del_recycle") if cfg["move_to_recycle"]
                           else self.tr("pv_del_permanent"))
            self.log(self.tr("set_recycle", v=recycle_txt))

        verify_map = {"none": "rb_verify_none", "size_time": "rb_verify_size",
                      "hash": "rb_verify_hash"}
        self.log(self.tr("set_verify",
                         v=self.tr(verify_map.get(cfg["verify"], "rb_verify_none"))))
        self.log(self.tr("set_skip_error", v=on_off(cfg["skip_error"])))
        self.log(self.tr("set_auto_ignore", v=on_off(cfg["auto_ignore_on_error"])))
        self.log(self.tr("set_skip_n", v=max(0, int(cfg.get("skip_first_n", 0) or 0))))
        self.log(self.tr("set_wait", v=max(0, int(cfg.get("wait_ms", 0) or 0))))
        self.log(self.tr("set_read_timeout",
                         v=max(0, int(cfg.get("read_timeout_s", 0) or 0))))
        ig_count = len([p for p in cfg.get("ignore_patterns", [])
                        if p.strip() and not p.strip().startswith("#")])
        self.log(self.tr("set_ignore_count", v=ig_count))
        self.log(self.tr("set_logfile", v=cfg.get("log_file") or self.tr("val_off")))

    def _run(self):
        cfg = self.cfg

        # ---- 設定内容の書き出し ----
        self._log_settings()

        # ---- 処理計画 (プレビューと共通) ----
        self.log(self.tr("log_building_list"))
        plan = plan_operation(cfg)
        if plan["error"]:
            self.log(self.tr("log_abort", error=plan["error"]), "error")
            return

        src_root = plan["src_root"]
        dst_root = plan["dst_root"]
        file_list = plan["file_list"]
        dir_list = plan["dir_list"]
        is_move = cfg["mode"] == "move"
        # リード タイムアウト (秒)。0 なら従来どおりスレッド内で直接コピーする。
        timeout_s = max(0, int(cfg.get("read_timeout_s", 0) or 0))

        for rel in plan["ignored_list"]:
            self.log(self.tr("log_ignored", rel=rel))
        for rel in plan["skipped_list"]:
            self.log(self.tr("log_lead_skip", rel=rel))

        total = len(file_list)
        self.log(self.tr("log_target_count", total=total,
                         ig=len(plan["ignored_list"]), skip=plan["skip_n"]))

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
                self.log(self.tr("log_stopped"), "warn")
                break
            if self.stop_flag.is_set():
                self.log(self.tr("log_stopped"), "warn")
                break

            done += 1
            self.progress(done, total)
            src_file = os.path.join(src_root, rel)
            dst_file = os.path.join(dst_root, rel)

            # 1 ファイルを処理する。エラースキップ無効時はダイアログの選択で
            # 再試行できるよう、この単位を再試行ループで囲む。
            aborted = False
            while True:
                try:
                    # 処理開始行は改行しない (完了/エラーを同じ行の末尾へ追記する)。
                    # 固まった場合もログ最終行が原因ファイルを指す (主目的)。
                    self.log(self.tr("log_item", done=done, total=total, rel=rel),
                             newline=False)
                    os.makedirs(os.path.dirname(dst_file), exist_ok=True)

                    # コピー (メタデータ=タイムスタンプ維持)。リード タイムアウトが
                    # 有効なら子プロセスで実行し、固まったら kill して打ち切る。
                    if timeout_s > 0:
                        self._copy_with_timeout(src_file, dst_file, timeout_s)
                    else:
                        shutil.copy2(src_file, dst_file)

                    # ベリファイ
                    if cfg["verify"] != "none":
                        ok, detail = self._verify(src_file, dst_file, cfg["verify"])
                        if not ok:
                            raise IOError(self.tr("err_verify_fail", detail=detail))

                    # コピー成功を同じ行へ追記。移動時は元削除を別記録として分ける。
                    if is_move:
                        self.log_append(self.tr("log_item_copied"), newline=False)
                        if cfg["move_to_recycle"]:
                            send_to_recycle_bin(src_file)
                            self.log_append(self.tr("log_item_recycled"))
                        else:
                            os.remove(src_file)
                            self.log_append(self.tr("log_item_removed"))
                    else:
                        self.log_append(self.tr("log_item_copied"))

                    ok_count += 1
                    break  # 成功 → 再試行ループを抜けて次のファイルへ

                except _Stopped:
                    # リード タイムアウト待機中に停止要求。開いている行を閉じて終了。
                    self.log_append("", newline=True)
                    self.log(self.tr("log_stopped"), "warn")
                    aborted = True
                    break

                except Exception as e:
                    # 開いている処理開始行の末尾へエラーを追記して行を閉じる
                    self.log_append(self.tr("log_item_err", e=e), "error")

                    # エラースキップ有効なら従来どおり即スキップ。無効なら
                    # 確認ダイアログで 再試行/スキップ/終了 を選ばせる。
                    if cfg["skip_error"]:
                        action = "skip"
                    else:
                        action = self._ask_error_action(rel, str(e))

                    if action == "retry":
                        self.log(self.tr("log_retrying", rel=rel), "warn")
                        continue  # 同じファイルを最初からやり直す (log_item も出し直す)

                    # skip / abort = このファイルは運べないことが確定
                    error_count += 1
                    # エラーファイルを自動で無視リストへ登録
                    if cfg["auto_ignore_on_error"]:
                        pat = rel.replace("\\", "/")
                        self.ignore_add(pat)
                        self.log(self.tr("log_auto_ignore", pat=pat), "warn")
                    # 移動時、元が残るフォルダを記録 (親も含む)
                    if is_move:
                        self._mark_残(os.path.dirname(rel), dirs_with_残)
                    if action == "abort":
                        self.log(self.tr("log_aborted_by_user"), "error")
                        aborted = True
                    break

            if aborted:
                break

            # OS/アプリを重くしないためのウェイト
            self._throttle_sleep()

        # ---- フォルダのタイムスタンプ維持 (全ファイル処理後) ----
        self.log(self.tr("log_applying_dir_time"))
        for rel_dir in sorted(dir_list, reverse=True):
            self._apply_dir_time(src_root, dst_root, rel_dir)
        self._apply_dir_time(os.path.dirname(src_root), os.path.dirname(dst_root),
                             os.path.basename(dst_root), src_override=src_root)

        # ---- 移動時: 空になった元フォルダを削除 ----
        if is_move and not self.stop_flag.is_set():
            self.log(self.tr("log_removing_empty"))
            self._cleanup_source_dirs(src_root, dirs_with_残)

        self.log(
            self.tr("log_done", ok=ok_count, err=error_count, total=total),
            "error" if error_count else "info",
        )

    # -- 検証 --
    def _verify(self, src, dst, method):
        s = os.stat(src)
        d = os.stat(dst)
        if s.st_size != d.st_size:
            return False, self.tr("vf_size_mismatch", s=s.st_size, d=d.st_size)
        if method == "size_time":
            if abs(s.st_mtime - d.st_mtime) > 2:  # FAT等の丸め許容
                return False, self.tr("vf_time_mismatch", s=s.st_mtime, d=d.st_mtime)
            return True, self.tr("vf_size_ok")
        elif method == "hash":
            hs = sha256_of_file(src)
            hd = sha256_of_file(dst)
            if hs != hd:
                return False, self.tr("vf_hash_mismatch", hs=hs, hd=hd)
            return True, self.tr("vf_hash_ok")
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
            self.log(self.tr("warn_dir_time_fail", rel=rel_dir, e=e), "warn")

    def _cleanup_source_dirs(self, src_root, dirs_with_残):
        root_ph = self.tr("log_root_placeholder")
        # 深い方から空フォルダを削除
        for cur, dirs, files in os.walk(src_root, topdown=False):
            rel = os.path.relpath(cur, src_root).replace("\\", "/")
            if rel == ".":
                rel = ""
            try:
                if not os.listdir(cur):
                    if cur != src_root:
                        os.rmdir(cur)
                        self.log(self.tr("log_dir_removed", rel=rel or root_ph))
                else:
                    self.log(self.tr("warn_dir_residual", rel=rel or root_ph), "warn")
            except Exception as e:
                self.log(self.tr("warn_dir_remove_fail", rel=rel, e=e), "warn")
        # ルート自体
        try:
            if os.path.isdir(src_root) and not os.listdir(src_root):
                os.rmdir(src_root)
                self.log(self.tr("log_dir_removed", rel=src_root))
        except Exception as e:
            self.log(self.tr("warn_root_remove_fail", e=e), "warn")


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class App(_BaseTk):
    def __init__(self):
        super().__init__()
        self.geometry("820x820")
        self.msg_queue = queue.Queue()
        self.worker = None

        self._loaded_ignore = None  # 起動時に ini から読んだ無視リスト (build 後に反映)
        self._init_vars()
        self._load_settings()       # ini から各設定を復元 (CURRENT_LANG も含む)
        self._build_ui()
        self._setup_dnd()
        self._apply_loaded_ignore()  # 無視リストは build 後に流し込む
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_queue)

    # ---- Tk 変数 (言語切替の再構築をまたいで保持する) ----
    def _init_vars(self):
        self.var_lang = tk.StringVar(value=CURRENT_LANG)
        self.var_src = tk.StringVar()
        self.var_dst = tk.StringVar()
        self.var_subfolder = tk.BooleanVar(value=True)
        self.var_mode = tk.StringVar(value="copy")
        self.var_recycle = tk.BooleanVar(value=True)
        self.var_verify = tk.StringVar(value="size_time")
        self.var_skip_error = tk.BooleanVar(value=True)
        self.var_auto_ignore = tk.BooleanVar(value=True)
        self.var_skip_n = tk.IntVar(value=0)
        self.var_wait_ms = tk.IntVar(value=0)
        self.var_read_timeout = tk.IntVar(value=0)
        self.var_logfile = tk.BooleanVar(value=False)
        self.var_log_path = tk.StringVar()

    # ---- 設定の永続化 (ini) ----
    @staticmethod
    def _var_int(var, default=0):
        """IntVar/Spinbox が空・不正でも例外にせず int を返す。"""
        try:
            return max(0, int(var.get() or 0))
        except Exception:
            return default

    def _load_settings(self):
        """起動時に ini から設定を復元する。ファイルが無ければ何もしない。
        言語 (CURRENT_LANG) はここで確定し、以降の UI 構築へ反映される。
        無視リストはウィジェット未生成のため退避のみ (build 後に反映)。"""
        global CURRENT_LANG
        path = _config_path()
        if not os.path.isfile(path):
            return
        cp = configparser.ConfigParser(interpolation=None)
        try:
            cp.read(path, encoding="utf-8")
        except Exception:
            return  # 壊れた ini でも既定値で起動する

        def s(key, default=""):
            return cp.get("General", key, fallback=default)

        def b(key, default):
            try:
                return cp.getboolean("General", key)
            except Exception:
                return default

        def i(key, default):
            try:
                return max(0, cp.getint("General", key))
            except Exception:
                return default

        lang = s("lang", CURRENT_LANG)
        if lang in dict(LANGUAGES):
            CURRENT_LANG = lang
            self.var_lang.set(lang)

        self.var_src.set(s("source"))
        self.var_dst.set(s("dest"))
        self.var_subfolder.set(b("make_subfolder", True))
        mode = s("mode", "copy")
        self.var_mode.set(mode if mode in ("copy", "move") else "copy")
        self.var_recycle.set(b("move_to_recycle", True))
        verify = s("verify", "size_time")
        self.var_verify.set(verify if verify in ("none", "size_time", "hash")
                            else "size_time")
        self.var_skip_error.set(b("skip_error", True))
        self.var_auto_ignore.set(b("auto_ignore_on_error", True))
        self.var_skip_n.set(i("skip_first_n", 0))
        self.var_wait_ms.set(i("wait_ms", 0))
        self.var_read_timeout.set(i("read_timeout_s", 0))
        self.var_logfile.set(b("logfile", False))
        self.var_log_path.set(s("log_path"))

        # 無視リストは [Ignore] セクションにファイル出現順で格納 (build 後に流し込む)
        if cp.has_section("Ignore"):
            self._loaded_ignore = [val for _key, val in cp.items("Ignore")]

    def _apply_loaded_ignore(self):
        """_load_settings で退避した無視リストを Text ウィジェットへ反映する。"""
        if not self._loaded_ignore:
            return
        self.txt_ignore.delete("1.0", "end")
        self.txt_ignore.insert("1.0", "\n".join(self._loaded_ignore))
        self._loaded_ignore = None

    def _save_settings(self):
        """現在の設定を ini へ保存する。保存失敗は動作を妨げない。"""
        cp = configparser.ConfigParser(interpolation=None)
        cp["General"] = {
            "lang": CURRENT_LANG,
            "source": self.var_src.get(),
            "dest": self.var_dst.get(),
            "make_subfolder": str(self.var_subfolder.get()),
            "mode": self.var_mode.get(),
            "move_to_recycle": str(self.var_recycle.get()),
            "verify": self.var_verify.get(),
            "skip_error": str(self.var_skip_error.get()),
            "auto_ignore_on_error": str(self.var_auto_ignore.get()),
            "skip_first_n": str(self._var_int(self.var_skip_n)),
            "wait_ms": str(self._var_int(self.var_wait_ms)),
            "read_timeout_s": str(self._var_int(self.var_read_timeout)),
            "logfile": str(self.var_logfile.get()),
            "log_path": self.var_log_path.get(),
        }
        # 無視リストは末尾の空行を落として出現順に格納
        lines = self._get_ignore_patterns()
        while lines and not lines[-1].strip():
            lines.pop()
        cp["Ignore"] = {str(idx): ln for idx, ln in enumerate(lines)}
        try:
            with open(_config_path(), "w", encoding="utf-8") as f:
                cp.write(f)
        except Exception:
            pass  # 保存失敗は処理本体を止めない

    def _on_close(self):
        """終了時にも設定を保存してからウィンドウを閉じる。"""
        self._save_settings()
        self.destroy()

    # ---- 言語切替 ----
    def _on_lang_change(self):
        global CURRENT_LANG
        lang = self.var_lang.get()
        if lang == CURRENT_LANG:
            return
        # 実行中は切替不可 (ワーカーの言語が固定されているため)
        if self.worker and self.worker.is_alive():
            self.var_lang.set(CURRENT_LANG)
            return
        # Text ウィジェットの内容を退避 (再構築で失われるため)
        ignore_content = self.txt_ignore.get("1.0", "end").rstrip("\n")
        log_content = self.txt_log.get("1.0", "end").rstrip("\n")

        CURRENT_LANG = lang
        for w in self.winfo_children():
            w.destroy()
        self._build_ui()
        self._setup_dnd()

        if ignore_content:
            self.txt_ignore.insert("1.0", ignore_content)
        if log_content:
            self.txt_log.insert("1.0", log_content + "\n")
            self.txt_log.see("end")

    # ---- UI 構築 ----
    def _build_ui(self):
        self.title(t("app_title"))
        pad = {"padx": 6, "pady": 3}

        # 言語切替
        frm_lang = ttk.Frame(self)
        frm_lang.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Label(frm_lang, text=t("lang_label")).pack(side="left", padx=(0, 4))
        for code, name in LANGUAGES:
            ttk.Radiobutton(frm_lang, text=name, value=code,
                            variable=self.var_lang,
                            command=self._on_lang_change).pack(side="left", padx=2)

        # パス
        frm_path = ttk.LabelFrame(self, text=t("frame_folders"))
        frm_path.pack(fill="x", padx=8, pady=6)

        ttk.Label(frm_path, text=t("lbl_source")).grid(row=0, column=0, sticky="e", **pad)
        self.ent_src = ttk.Entry(frm_path, textvariable=self.var_src, width=70)
        self.ent_src.grid(row=0, column=1, **pad)
        ttk.Button(frm_path, text=t("btn_browse"),
                   command=lambda: self._browse(self.var_src)).grid(row=0, column=2, **pad)

        ttk.Label(frm_path, text=t("lbl_dest")).grid(row=1, column=0, sticky="e", **pad)
        self.ent_dst = ttk.Entry(frm_path, textvariable=self.var_dst, width=70)
        self.ent_dst.grid(row=1, column=1, **pad)
        ttk.Button(frm_path, text=t("btn_browse"),
                   command=lambda: self._browse(self.var_dst)).grid(row=1, column=2, **pad)

        ttk.Checkbutton(frm_path, text=t("chk_subfolder"),
                        variable=self.var_subfolder).grid(row=2, column=1, sticky="w", **pad)

        dnd_note = t("dnd_on") if HAS_DND else t("dnd_off")
        ttk.Label(frm_path, text=dnd_note, foreground="#666666").grid(
            row=3, column=1, sticky="w", **pad)

        # オプション
        frm_opt = ttk.LabelFrame(self, text=t("frame_options"))
        frm_opt.pack(fill="x", padx=8, pady=6)

        # モード
        ttk.Label(frm_opt, text=t("lbl_action")).grid(row=0, column=0, sticky="e", **pad)
        ttk.Radiobutton(frm_opt, text=t("rb_copy"), variable=self.var_mode,
                        value="copy", command=self._sync_states).grid(row=0, column=1, sticky="w", **pad)
        ttk.Radiobutton(frm_opt, text=t("rb_move"), variable=self.var_mode,
                        value="move", command=self._sync_states).grid(row=0, column=2, sticky="w", **pad)

        self.chk_recycle = ttk.Checkbutton(
            frm_opt, text=t("chk_recycle"), variable=self.var_recycle)
        self.chk_recycle.grid(row=0, column=3, sticky="w", **pad)

        # 検証
        ttk.Label(frm_opt, text=t("lbl_verify")).grid(row=1, column=0, sticky="e", **pad)
        ttk.Radiobutton(frm_opt, text=t("rb_verify_none"), variable=self.var_verify,
                        value="none").grid(row=1, column=1, sticky="w", **pad)
        ttk.Radiobutton(frm_opt, text=t("rb_verify_size"), variable=self.var_verify,
                        value="size_time").grid(row=1, column=2, sticky="w", **pad)
        ttk.Radiobutton(frm_opt, text=t("rb_verify_hash"), variable=self.var_verify,
                        value="hash").grid(row=1, column=3, sticky="w", **pad)

        # エラースキップ / 自動無視
        ttk.Checkbutton(frm_opt, text=t("chk_skip_error"),
                        variable=self.var_skip_error).grid(row=2, column=1, columnspan=2, sticky="w", **pad)

        ttk.Checkbutton(frm_opt, text=t("chk_auto_ignore"),
                        variable=self.var_auto_ignore).grid(row=2, column=3, sticky="w", **pad)

        # 先頭スキップ
        ttk.Label(frm_opt, text=t("lbl_skip_n")).grid(row=3, column=0, columnspan=2, sticky="e", **pad)
        ttk.Spinbox(frm_opt, from_=0, to=1000000, textvariable=self.var_skip_n,
                    width=10).grid(row=3, column=2, sticky="w", **pad)
        ttk.Label(frm_opt, text=t("lbl_skip_n_note")).grid(row=3, column=3, sticky="w", **pad)

        # ファイルごとのウェイト (OS/アプリ負荷軽減)
        ttk.Label(frm_opt, text=t("lbl_wait")).grid(
            row=4, column=0, columnspan=2, sticky="e", **pad)
        ttk.Spinbox(frm_opt, from_=0, to=60000, increment=10,
                    textvariable=self.var_wait_ms, width=10).grid(
            row=4, column=2, sticky="w", **pad)
        ttk.Label(frm_opt, text=t("lbl_wait_note")).grid(
            row=4, column=3, sticky="w", **pad)

        # リード タイムアウト (固まる破損ファイルを指定秒で打ち切る)
        ttk.Label(frm_opt, text=t("lbl_read_timeout")).grid(
            row=5, column=0, columnspan=2, sticky="e", **pad)
        ttk.Spinbox(frm_opt, from_=0, to=3600, increment=1,
                    textvariable=self.var_read_timeout, width=10).grid(
            row=5, column=2, sticky="w", **pad)
        ttk.Label(frm_opt, text=t("lbl_read_timeout_note")).grid(
            row=5, column=3, sticky="w", **pad)

        # 無視リスト
        frm_ig = ttk.LabelFrame(self, text=t("frame_ignore"))
        frm_ig.pack(fill="both", expand=False, padx=8, pady=6)

        self.txt_ignore = tk.Text(frm_ig, height=6, width=70)
        self.txt_ignore.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        sb = ttk.Scrollbar(frm_ig, command=self.txt_ignore.yview)
        sb.pack(side="left", fill="y")
        self.txt_ignore.config(yscrollcommand=sb.set)

        frm_ig_btn = ttk.Frame(frm_ig)
        frm_ig_btn.pack(side="left", fill="y", padx=6)
        ttk.Button(frm_ig_btn, text=t("btn_import"), command=self._import_ignore).pack(fill="x", pady=2)
        ttk.Button(frm_ig_btn, text=t("btn_export"), command=self._export_ignore).pack(fill="x", pady=2)
        ttk.Button(frm_ig_btn, text=t("btn_clear"),
                   command=lambda: self.txt_ignore.delete("1.0", "end")).pack(fill="x", pady=2)

        # ログファイル出力
        frm_lf = ttk.LabelFrame(self, text=t("frame_logfile"))
        frm_lf.pack(fill="x", padx=8, pady=6)
        ttk.Checkbutton(frm_lf, text=t("chk_logfile"),
                        variable=self.var_logfile).grid(row=0, column=0, columnspan=3,
                                                        sticky="w", **pad)
        ttk.Label(frm_lf, text=t("lbl_logout")).grid(row=1, column=0, sticky="e", **pad)
        ttk.Entry(frm_lf, textvariable=self.var_log_path, width=64).grid(row=1, column=1, **pad)
        ttk.Button(frm_lf, text=t("btn_browse"), command=self._browse_logfile).grid(row=1, column=2, **pad)
        ttk.Label(frm_lf, text=t("lbl_logout_note")).grid(
            row=2, column=1, sticky="w", **pad)

        # 実行 / 進捗
        frm_run = ttk.Frame(self)
        frm_run.pack(fill="x", padx=8, pady=6)
        self.btn_run = ttk.Button(frm_run, text=t("btn_run"), command=self._start)
        self.btn_run.pack(side="left", padx=4)
        self.btn_pause = ttk.Button(frm_run, text=t("btn_pause"), command=self._toggle_pause,
                                    state="disabled")
        self.btn_pause.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(frm_run, text=t("btn_stop"), command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=4)
        self.progress = ttk.Progressbar(frm_run, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=8)
        self.lbl_prog = ttk.Label(frm_run, text="0 / 0")
        self.lbl_prog.pack(side="left", padx=4)

        # ログ
        frm_log = ttk.LabelFrame(self, text=t("frame_log"))
        frm_log.pack(fill="both", expand=True, padx=8, pady=6)

        frm_log_bar = ttk.Frame(frm_log)
        frm_log_bar.pack(fill="x", padx=6, pady=(4, 0))
        ttk.Button(frm_log_bar, text=t("btn_save_log"), command=self._save_log).pack(side="left")
        ttk.Button(frm_log_bar, text=t("btn_clear_log"),
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

    def _filetypes(self):
        return [(t("ft_text"), "*.txt"), (t("ft_all"), "*.*")]

    def _browse_logfile(self):
        cur = self.var_log_path.get().strip()
        initial_dir = self._resolve_initialdir(
            os.path.dirname(cur) if cur else self.var_dst.get().strip())
        path = filedialog.asksaveasfilename(
            title=t("fd_logfile_title"),
            defaultextension=".txt",
            initialdir=initial_dir or None,
            initialfile=os.path.basename(cur) if cur else "skipferry_log.txt",
            filetypes=self._filetypes())
        if path:
            self.var_log_path.set(os.path.normpath(path))
            self.var_logfile.set(True)

    def _resolve_log_file(self):
        """ログファイル出力が有効なら出力先パスを決める。無効なら None。
        空欄時はプログラム(ini と同じ = _config_path のフォルダ)に日時入りの
        ファイル名で自動生成する。"""
        if not self.var_logfile.get():
            return None
        path = self.var_log_path.get().strip()
        if not path:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            base = os.path.dirname(_config_path())  # プログラム(ini)と同じフォルダ
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
            messagebox.showinfo(t("title_info"), t("msg_no_log_to_save"))
            return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            title=t("fd_save_log_title"),
            defaultextension=".txt",
            initialfile=f"skipferry_log_{ts}.txt",
            filetypes=self._filetypes())
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content + "\n")
            self._log("info", t("msg_log_saved", path=path))
        except Exception as e:
            messagebox.showerror(t("title_error"), t("msg_log_save_fail", e=e))

    # ---- 無視リスト I/O ----
    def _get_ignore_patterns(self):
        text = self.txt_ignore.get("1.0", "end")
        return [ln for ln in text.splitlines()]

    def _import_ignore(self):
        path = filedialog.askopenfilename(filetypes=self._filetypes())
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                content = f.read()
            if self.txt_ignore.get("1.0", "end").strip():
                content = "\n" + content
            self.txt_ignore.insert("end", content)
            self._log("info", t("log_import_ignore", path=path))
        except Exception as e:
            messagebox.showerror(t("title_error"), t("msg_import_fail", e=e))

    def _export_ignore(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt", filetypes=self._filetypes())
        if not path:
            return
        try:
            lines = [ln for ln in self._get_ignore_patterns() if ln.strip()]
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            self._log("info", t("log_export_ignore", path=path))
        except Exception as e:
            messagebox.showerror(t("title_error"), t("msg_export_fail", e=e))

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
            messagebox.showwarning(t("title_input"), t("msg_need_src_dst"))
            return
        if not os.path.isdir(src):
            messagebox.showerror(t("title_error"), t("msg_src_not_exist"))
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
            "read_timeout_s": max(0, int(self.var_read_timeout.get() or 0)),
            "ignore_patterns": self._get_ignore_patterns(),
            "log_file": self._resolve_log_file(),
            "lang": CURRENT_LANG,
        }

        # 処理計画を作成してプレビューを表示 (実行前確認)
        plan = plan_operation(cfg)
        if plan["error"]:
            messagebox.showerror(t("title_error"), plan["error"])
            return
        if not self._show_preview(cfg, plan):
            return

        # 実行内容を確定したので、この設定を次回起動用に保存する
        self._save_settings()

        self.txt_log.delete("1.0", "end")
        self.progress.config(value=0)
        self.lbl_prog.config(text="0 / 0")
        self.btn_run.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.btn_pause.config(state="normal", text=t("btn_pause"))

        self.worker = CopyMoveWorker(cfg, self.msg_queue)
        self.worker.start()

    def _stop(self):
        if self.worker and self.worker.is_alive():
            self.worker.stop_flag.set()
            self.worker.pause_flag.clear()  # 一時停止中でも停止できるよう解除
            self._log("warn", t("log_stop_requested"))

    def _toggle_pause(self):
        if not (self.worker and self.worker.is_alive()):
            return
        if self.worker.pause_flag.is_set():
            self.worker.pause_flag.clear()
            self.btn_pause.config(text=t("btn_pause"))
            self._log("info", t("log_resume_requested"))
        else:
            self.worker.pause_flag.set()
            self.btn_pause.config(text=t("btn_resume"))
            self._log("warn", t("log_pause_requested"))

    # ---- エラー確認ダイアログ (エラースキップ無効時) ----
    def _show_error_dialog(self, rel, err_text):
        """エラーになったファイルについて 再試行/スキップ/終了 を選ばせる
        モーダルダイアログ。戻り値は "retry" / "skip" / "abort"。
        ×で閉じた場合は "abort" (安全側)。GUI スレッドから呼ぶこと。"""
        dlg = tk.Toplevel(self)
        dlg.title(t("dlg_error_title"))
        dlg.transient(self)
        dlg.resizable(False, False)
        result = {"action": "abort"}

        frm = ttk.Frame(dlg, padding=14)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=t("dlg_error_msg")).pack(anchor="w")
        ttk.Label(frm, text=t("dlg_error_file", rel=rel),
                  wraplength=520, justify="left").pack(anchor="w", pady=(8, 0))
        ttk.Label(frm, text=t("dlg_error_detail", err=err_text),
                  wraplength=520, justify="left", foreground="#b00020").pack(
            anchor="w", pady=(2, 12))

        btns = ttk.Frame(frm)
        btns.pack(fill="x")

        def choose(action):
            result["action"] = action
            dlg.destroy()

        # 既定 (Enter/フォーカス) は再試行。ボタンは 再試行 / スキップ / 終了 の順。
        btn_retry = ttk.Button(btns, text=t("dlg_btn_retry"),
                               command=lambda: choose("retry"))
        btn_retry.pack(side="left", padx=(0, 6))
        ttk.Button(btns, text=t("dlg_btn_skip"),
                   command=lambda: choose("skip")).pack(side="left", padx=6)
        ttk.Button(btns, text=t("dlg_btn_abort"),
                   command=lambda: choose("abort")).pack(side="left", padx=6)

        dlg.protocol("WM_DELETE_WINDOW", lambda: choose("abort"))
        dlg.bind("<Return>", lambda e: choose("retry"))
        dlg.bind("<Escape>", lambda e: choose("skip"))
        dlg.grab_set()

        # 親の中央あたりへ配置してからフォーカス
        dlg.update_idletasks()
        try:
            px, py = self.winfo_rootx(), self.winfo_rooty()
            pw, ph = self.winfo_width(), self.winfo_height()
            w, h = dlg.winfo_width(), dlg.winfo_height()
            dlg.geometry(f"+{px + max(0, (pw - w) // 2)}+{py + max(0, (ph - h) // 2)}")
        except Exception:
            pass
        btn_retry.focus_set()
        self.wait_window(dlg)  # モーダルに待つ
        return result["action"]

    # ---- 実行前プレビュー ----
    def _show_preview(self, cfg, plan):
        """処理先ファイル(先頭10件)を提示し、実行可否を確認する。
        戻り値: True=実行 / False=キャンセル"""
        PREVIEW_N = 10
        dlg = tk.Toplevel(self)
        dlg.title(t("pv_title"))
        dlg.geometry("780x520")
        dlg.transient(self)
        dlg.grab_set()
        result = {"ok": False}

        # 動作説明
        if cfg["mode"] == "move":
            del_txt = t("pv_del_recycle") if cfg["move_to_recycle"] else t("pv_del_permanent")
            mode_txt = t("pv_mode_move", del_txt=del_txt)
        else:
            mode_txt = t("pv_mode_copy")

        info = t("pv_info", mode=mode_txt, src=plan["src_root"], dst=plan["dst_root"],
                 n=len(plan["file_list"]), ig=len(plan["ignored_list"]), skip=plan["skip_n"])
        ttk.Label(dlg, text=info, justify="left").pack(anchor="w", padx=12, pady=(10, 4))

        # サブフォルダ作成オプションの効果を明示
        note = t("pv_note_on") if cfg["make_subfolder"] else t("pv_note_off")
        ttk.Label(dlg, text=note, foreground="#0066cc",
                  wraplength=740, justify="left").pack(anchor="w", padx=12, pady=(0, 6))

        ttk.Label(dlg, text=t("pv_list_header", n=PREVIEW_N)).pack(anchor="w", padx=12)

        frm = ttk.Frame(dlg)
        frm.pack(fill="both", expand=True, padx=12, pady=4)
        txt = tk.Text(frm, height=14, wrap="none")
        txt.pack(side="left", fill="both", expand=True)
        psb = ttk.Scrollbar(frm, command=txt.yview)
        psb.pack(side="left", fill="y")
        txt.config(yscrollcommand=psb.set)

        preview = plan["file_list"][:PREVIEW_N]
        if not preview:
            txt.insert("end", t("pv_none") + "\n")
        for rel, _name in preview:
            dst_file = os.path.join(plan["dst_root"], rel)
            txt.insert("end", dst_file + "\n")
        remain = len(plan["file_list"]) - len(preview)
        if remain > 0:
            txt.insert("end", t("pv_more", n=remain) + "\n")
        txt.config(state="disabled")

        btns = ttk.Frame(dlg)
        btns.pack(fill="x", padx=12, pady=10)

        def do_ok():
            result["ok"] = True
            dlg.destroy()

        def do_cancel():
            dlg.destroy()

        run_btn = ttk.Button(btns, text=t("pv_btn_run"), command=do_ok)
        run_btn.pack(side="right", padx=(8, 0))
        ttk.Button(btns, text=t("pv_btn_cancel"), command=do_cancel).pack(side="right")
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
                    level, text, end = payload
                    self._log(level, text, end)
                elif kind == "progress":
                    done, total = payload
                    self.progress.config(maximum=max(total, 1), value=done)
                    self.lbl_prog.config(text=f"{done} / {total}")
                elif kind == "ignore_add":
                    self._append_ignore_pattern(payload)
                elif kind == "ask_error":
                    # エラースキップ無効時のエラー確認。ダイアログの選択を
                    # ワーカーへ返す (event で協調)。
                    rel, err_text = payload
                    action = self._show_error_dialog(rel, err_text)
                    if self.worker is not None:
                        self.worker.error_response = action
                        self.worker.error_event.set()
                elif kind == "done":
                    self.btn_run.config(state="normal")
                    self.btn_stop.config(state="disabled")
                    self.btn_pause.config(state="disabled", text=t("btn_pause"))
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _log(self, level, text, end="\n"):
        # end="" のとき行を閉じずに追記できる (処理開始行→完了/エラーを同じ行へ)
        self.txt_log.insert("end", text + end, level)
        self.txt_log.see("end")


if __name__ == "__main__":
    # フリーズ (PyInstaller 等) 時に子プロセスが GUI を再起動しないための保護。
    # リード タイムアウト機能が multiprocessing を使うため必須。
    multiprocessing.freeze_support()
    App().mainloop()
