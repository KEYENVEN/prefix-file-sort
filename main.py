import os
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from tkinter.ttk import Progressbar
from tkinterdnd2 import TkinterDnD, DND_FILES
from pathlib import Path
import shutil
import json
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Set


class FileGrouper:
    """Core business logic - separated from GUI"""

    def __init__(self, source_path: Path):
        self.source_path = source_path
        self.log_path = source_path / "move_log.json"

    @staticmethod
    def get_allowed_extensions(filter_text: str) -> Optional[List[str]]:
        exts = [ext.strip().lower() for ext in filter_text.split(',') if ext.strip()]
        return exts if exts else None

    def get_filtered_files(self, filter_text: str) -> List[Path]:
        allowed = self.get_allowed_extensions(filter_text)
        files = []
        for item in self.source_path.iterdir():
            if (item.is_file() and
                    item.name != "move_log.json" and
                    '-' in item.name):
                if allowed is None or item.suffix.lower() in allowed:
                    files.append(item)
        return files

    def preview_groups(self, filter_text: str, suffix: str, use_numbering: bool, custom_names: List[str]) -> Dict[
        str, List[Path]]:
        files = self.get_filtered_files(filter_text)
        temp_groups: Dict[str, List[Path]] = {}

        for item in files:
            prefix = item.name.split('-', 1)[0].strip()
            if prefix:
                temp_groups.setdefault(prefix, []).append(item)

        sorted_prefixes = sorted(temp_groups.keys())
        final_groups: Dict[str, List[Path]] = {}

        for i, prefix in enumerate(sorted_prefixes):
            if use_numbering and custom_names and i < len(custom_names):
                folder_name = f"{i:02d}{custom_names[i]}"
            elif use_numbering:
                folder_name = f"{i:02d} - {prefix}{suffix}"
            else:
                folder_name = f"{prefix}{suffix}"
            final_groups[folder_name] = temp_groups[prefix]

        return final_groups

    def execute_move(self, filter_text: str, suffix: str, use_numbering: bool, custom_names: List[str]) -> Tuple[
        int, int]:
        groups = self.preview_groups(filter_text, suffix, use_numbering, custom_names)
        if not groups:
            return 0, 0

        existing_log = self._load_log()
        moved_count = 0
        skipped_count = 0
        total = sum(len(files) for files in groups.values())
        current = 0

        for folder_name, file_list in groups.items():
            target_folder = self.source_path / folder_name
            target_folder.mkdir(exist_ok=True)

            for file_path in file_list:
                current += 1
                dst_path = target_folder / file_path.name
                if dst_path.exists():
                    skipped_count += 1
                    continue
                try:
                    shutil.move(str(file_path), str(dst_path))
                    existing_log.append({
                        "original": str(file_path.resolve()),
                        "moved_to": str(dst_path.resolve()),
                        "group": folder_name,
                        "extension": file_path.suffix.lower(),
                        "timestamp": datetime.now().isoformat()
                    })
                    moved_count += 1
                except Exception as e:
                    print(f"Move error: {file_path.name} - {e}")

        self._save_log(existing_log)
        return moved_count, skipped_count

    def undo_last_move(self, filter_text: str) -> Tuple[int, int]:
        """Undo + full cleanup of ALL empty group folders (fixes multi-batch undo issue)"""
        if not self.log_path.exists():
            return 0, 0

        log_data = self._load_log()
        allowed = self.get_allowed_extensions(filter_text)
        to_keep = []
        undone_count = 0
        folders_to_cleanup: Set[Path] = set()

        for item in log_data:
            dst = Path(item["moved_to"])
            original = Path(item["original"])
            ext = item.get("extension", "").lower()

            if (allowed is None or ext in allowed) and dst.exists():
                try:
                    shutil.move(str(dst), str(original))
                    undone_count += 1
                    # 精确记录本次操作涉及的所有文件夹
                    folders_to_cleanup.add(dst.parent)
                except Exception:
                    to_keep.append(item)
            else:
                to_keep.append(item)

        self._save_log(to_keep)

        # 将精确追踪到的文件夹集合传入清理函数，同时兼容编号模式扫描
        self._cleanup_all_empty_group_folders(folders_to_cleanup)

        return undone_count, len(to_keep)

    def _load_log(self) -> List[dict]:
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []

    def _save_log(self, data: List[dict]):
        if data:
            with open(self.log_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        elif self.log_path.exists():
            self.log_path.unlink(missing_ok=True)

    def _cleanup_all_empty_group_folders(self, known_folders: Set[Path] = None):
        """
        Delete ALL empty group folders (works for both Simple Mode and Numbered Custom Mode).

        known_folders: 由 undo_last_move 精确追踪到的文件夹集合（含 Simple Mode 文件夹）。
                       这是修复"按类型逐批归类再逐批撤回，最后一批撤回后残留文件夹"的关键。
        """
        candidates: Set[Path] = set()

        # ① 优先使用精确追踪到的文件夹（同时覆盖 Simple Mode 和 Numbered Mode）
        if known_folders:
            candidates.update(known_folders)

        # ② 兜底：扫描源目录下所有以两位数字开头的文件夹（Numbered Custom Mode）
        for folder in self.source_path.iterdir():
            if not folder.is_dir():
                continue
            name = folder.name
            if len(name) >= 2 and name[0].isdigit() and name[1].isdigit():
                candidates.add(folder)

        # 统一处理：只删除真正为空的候选文件夹
        for folder in candidates:
            try:
                if folder.is_dir() and not any(folder.iterdir()):
                    folder.rmdir()
            except OSError:
                pass  # 通常是 OneDrive / 系统锁定


class FileGrouperApp:
    def __init__(self, master):
        self.master = master

        # Variables
        self.source_var = tk.StringVar()
        self.suffix_var = tk.StringVar(value="series")
        self.filter_var = tk.StringVar(value="")
        self.numbering_var = tk.BooleanVar(value=False)
        self.source_path: Optional[Path] = None
        self.grouper: Optional[FileGrouper] = None

        # Widgets
        self.folder_entry: Optional[tk.Entry] = None
        self.suffix_entry: Optional[tk.Entry] = None
        self.filter_entry: Optional[tk.Entry] = None
        self.numbering_check: Optional[tk.Checkbutton] = None
        self.custom_names_text: Optional[scrolledtext.ScrolledText] = None
        self.browse_btn: Optional[tk.Button] = None
        self.preview_btn: Optional[tk.Button] = None
        self.execute_btn: Optional[tk.Button] = None
        self.undo_btn: Optional[tk.Button] = None
        self.export_btn: Optional[tk.Button] = None
        self.progress_label: Optional[tk.Label] = None
        self.progress_bar: Optional[Progressbar] = None
        self.preview_text: Optional[scrolledtext.ScrolledText] = None
        self.status_label: Optional[tk.Label] = None

        self.master.title("File Grouper by Prefix - v9 Enterprise Edition")
        self.master.geometry("980x780")
        self.master.resizable(True, True)

        self.create_widgets()

    def create_widgets(self):
        title_label = tk.Label(self.master, text="File Grouper by Prefix", font=("Arial", 18, "bold"))
        title_label.pack(pady=12)

        source_frame = tk.Frame(self.master)
        source_frame.pack(fill="x", padx=20, pady=5)
        tk.Label(source_frame, text="Source Folder:", font=("Arial", 10)).pack(anchor="w")
        self.folder_entry = tk.Entry(source_frame, textvariable=self.source_var, font=("Arial", 10))
        self.folder_entry.pack(fill="x", pady=5)

        suffix_frame = tk.Frame(self.master)
        suffix_frame.pack(fill="x", padx=20, pady=5)
        tk.Label(suffix_frame, text="Default Suffix (Simple Mode only - ignored when Numbered Custom Mode is enabled):",
                 font=("Arial", 10)).pack(anchor="w")
        self.suffix_entry = tk.Entry(suffix_frame, textvariable=self.suffix_var, font=("Arial", 10))
        self.suffix_entry.pack(fill="x", pady=5)

        filter_frame = tk.Frame(self.master)
        filter_frame.pack(fill="x", padx=20, pady=5)
        tk.Label(filter_frame, text="File Extensions Filter (blank = ALL):", font=("Arial", 10)).pack(anchor="w")
        self.filter_entry = tk.Entry(filter_frame, textvariable=self.filter_var, font=("Arial", 10))
        self.filter_entry.pack(fill="x", pady=5)

        # Numbered Mode - 去掉 ✅ 符号
        numbered_frame = tk.Frame(self.master)
        numbered_frame.pack(fill="x", padx=20, pady=8)
        self.numbering_check = tk.Checkbutton(
            numbered_frame,
            text="Enable Numbered Custom Mode (e.g. 00ACS, 01Delta, 02ED...)",
            variable=self.numbering_var,
            font=("Arial", 10, "bold")
        )
        self.numbering_check.pack(anchor="w")

        custom_frame = tk.Frame(self.master)
        custom_frame.pack(fill="x", padx=20, pady=5)
        tk.Label(custom_frame,
                 text="Custom Folder Names (one per line):\nThe program will automatically add '00', '01' etc. in alphabetical prefix order.",
                 font=("Arial", 10)).pack(anchor="w")
        self.custom_names_text = scrolledtext.ScrolledText(custom_frame, height=6, font=("Consolas", 10))
        self.custom_names_text.pack(fill="x", pady=5)
        self.custom_names_text.insert(tk.END, "ACS\nDelta\nED\nOP\nBGM")

        self.master.drop_target_register(DND_FILES)
        self.master.dnd_bind('<<Drop>>', self.on_drop)

        btn_frame = tk.Frame(self.master)
        btn_frame.pack(pady=12)
        self.browse_btn = tk.Button(btn_frame, text="Browse...", command=self.browse_folder, width=12)
        self.browse_btn.pack(side="left", padx=5)
        self.preview_btn = tk.Button(btn_frame, text="Preview Groups", command=self.preview_groups, width=15,
                                     bg="#4CAF50", fg="white")
        self.preview_btn.pack(side="left", padx=5)
        self.execute_btn = tk.Button(btn_frame, text="Execute Move", command=self.execute_move, width=15, bg="#f44336",
                                     fg="white", state="disabled")
        self.execute_btn.pack(side="left", padx=5)
        self.undo_btn = tk.Button(btn_frame, text="Undo Last Move", command=self.undo_last_move, width=15, bg="#FF9800",
                                  fg="white", state="disabled")
        self.undo_btn.pack(side="left", padx=5)
        self.export_btn = tk.Button(btn_frame, text="Export Audit Log", command=self.export_log, width=15, bg="#2196F3",
                                    fg="white")
        self.export_btn.pack(side="left", padx=5)

        progress_frame = tk.Frame(self.master)
        progress_frame.pack(fill="x", padx=20, pady=5)
        self.progress_label = tk.Label(progress_frame, text="Progress: Ready", font=("Arial", 9))
        self.progress_label.pack(anchor="w")
        self.progress_bar = Progressbar(progress_frame, mode='determinate', length=800)
        self.progress_bar.pack(fill="x", pady=2)

        log_label = tk.Label(self.master, text="Enterprise Audit Log:", font=("Arial", 11, "bold"))
        log_label.pack(anchor="w", padx=20, pady=(10, 0))
        self.preview_text = scrolledtext.ScrolledText(self.master, height=20, font=("Consolas", 10))
        self.preview_text.pack(fill="both", expand=True, padx=20, pady=5)

        self.status_label = tk.Label(self.master, text="Ready for operation...", fg="gray", anchor="w")
        self.status_label.pack(fill="x", padx=20, pady=5)

    def log(self, message: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prefix = f"[{timestamp}] {level}: "
        self.preview_text.insert(tk.END, prefix + message + "\n")
        self.preview_text.see(tk.END)
        self.master.update_idletasks()

    def export_log(self):
        if not self.source_path:
            messagebox.showwarning("Warning", "Please load a folder first.")
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = self.source_path / f"audit_log_{ts}.txt"
        try:
            with open(log_file, "w", encoding="utf-8") as f:
                f.write(self.preview_text.get(1.0, tk.END))
            messagebox.showinfo("Success", f"Audit log exported to:\n{log_file}")
        except Exception as e:
            messagebox.showerror("Export Failed", str(e))

    def on_drop(self, event):
        path = event.data.strip('{}')
        if os.path.isdir(path):
            self.load_folder(path)

    def browse_folder(self):
        folder = filedialog.askdirectory(title="Select Source Folder")
        if folder:
            self.load_folder(folder)

    def load_folder(self, folder_path: str):
        self.source_path = Path(folder_path).resolve()
        self.source_var.set(str(self.source_path))
        self.grouper = FileGrouper(self.source_path)
        self.current_log_path = self.source_path / "move_log.json"

        self.preview_text.delete(1.0, tk.END)
        self.reset_progress()

        undo_state = "normal" if self.current_log_path.exists() else "disabled"
        self.undo_btn.config(state=undo_state)

        self.log(f"Folder loaded: {self.source_path.name}", "INFO")
        self.status_label.config(text=f"Loaded: {self.source_path.name}", fg="green")

    def reset_progress(self):
        self.progress_bar['value'] = 0
        self.progress_label.config(text="Progress: Ready")

    def update_progress(self, current: int, total: int, filename: str = ""):
        if total > 0:
            percent = int((current / total) * 100)
            self.progress_bar['value'] = percent
            text = f"Progress: {current}/{total} ({percent}%)"
            if filename:
                text += f" - {filename}"
            self.progress_label.config(text=text)
            self.master.update_idletasks()

    def get_custom_names(self) -> List[str]:
        text = self.custom_names_text.get(1.0, tk.END).strip()
        return [line.strip() for line in text.splitlines() if line.strip()]

    def preview_groups(self):
        if not self.source_path or not self.grouper:
            messagebox.showerror("Error", "Please select a folder first!")
            return

        suffix = self.suffix_var.get().strip()
        use_numbering = self.numbering_var.get()
        custom_names = self.get_custom_names()

        if not suffix and not use_numbering:
            messagebox.showerror("Error", "Please enter a Default Suffix or enable Numbered Custom Mode!")
            return

        self.preview_text.delete(1.0, tk.END)
        self.log("=== PREVIEW START ===", "INFO")

        groups = self.grouper.preview_groups(self.filter_var.get(), suffix, use_numbering, custom_names)

        total_files = sum(len(files) for files in groups.values())
        self.log(f"Found {len(groups)} groups with {total_files} files", "INFO")

        for folder_name, files in sorted(groups.items()):
            self.log(f"\n→ Final Folder: {folder_name}  ({len(files)} files)", "INFO")
            for f in files[:6]:
                self.log(f"   └─ {f.name}", "INFO")
            if len(files) > 6:
                self.log(f"   ... and {len(files) - 6} more", "INFO")

        self.log("=== PREVIEW COMPLETE ===", "INFO")

        self.execute_btn.config(state="normal" if total_files > 0 else "disabled")

    def execute_move(self):
        if not self.source_path or not self.grouper:
            return
        if not messagebox.askyesno("Confirm", "Start moving files?"):
            return

        self.log("=== MOVE OPERATION START ===", "INFO")
        self.reset_progress()

        suffix = self.suffix_var.get().strip()
        use_numbering = self.numbering_var.get()
        custom_names = self.get_custom_names()

        moved, skipped = self.grouper.execute_move(self.filter_var.get(), suffix, use_numbering, custom_names)

        self.log(f"SUMMARY: Successfully moved {moved} files | Skipped {skipped} conflicts", "SUCCESS")
        self.log("=== MOVE OPERATION COMPLETE ===", "INFO")

        self.status_label.config(text=f"Move completed ({moved} files)", fg="green")
        self.undo_btn.config(state="normal")
        self.reset_progress()
        messagebox.showinfo("Success",
                            f"{moved} files grouped successfully.\n{skipped} files skipped due to conflicts.")

    def undo_last_move(self):
        if not self.grouper:
            return
        if not messagebox.askyesno("Confirm", "Execute Undo?"):
            return

        self.log("=== UNDO OPERATION START ===", "INFO")

        undone, remaining = self.grouper.undo_last_move(self.filter_var.get())

        self.log(f"SUMMARY: Restored {undone} files | {remaining} records remaining", "SUCCESS")
        self.log("=== UNDO OPERATION COMPLETE ===", "INFO")

        self.status_label.config(text=f"Undo completed ({undone} files)", fg="green")
        self.undo_btn.config(state="normal" if remaining > 0 else "disabled")
        self.reset_progress()
        messagebox.showinfo("Undo Success",
                            f"Successfully restored {undone} files.\nAll empty group folders have been automatically deleted.")


if __name__ == "__main__":
    root = TkinterDnD.Tk()
    app = FileGrouperApp(root)
    root.mainloop()