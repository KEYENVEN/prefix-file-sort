import os
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from tkinter.ttk import Progressbar, Combobox
from tkinterdnd2 import TkinterDnD, DND_FILES
from pathlib import Path
import shutil
import json
from datetime import datetime
import threading
from typing import List, Dict, Optional, Tuple, Set


class FileGrouper:
    """Core business logic - enhanced with robustness, multi-threading, and smart cleanup"""

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

    def preview_groups(self, filter_text: str, suffix: str, use_numbering: bool, custom_names: List[str],
                       case_sensitive: bool) -> Dict[str, List[Path]]:
        files = self.get_filtered_files(filter_text)
        temp_groups: Dict[str, Dict] = {}

        for item in files:
            prefix = item.name.split('-', 1)[0].strip()
            if not prefix:
                continue
            group_key = prefix if case_sensitive else prefix.lower()
            if group_key not in temp_groups:
                temp_groups[group_key] = {"display_prefix": prefix, "files": []}
            temp_groups[group_key]["files"].append(item)

        sorted_keys = sorted(temp_groups.keys())
        final_groups: Dict[str, List[Path]] = {}

        for i, key in enumerate(sorted_keys):
            display_prefix = temp_groups[key]["display_prefix"]

            if use_numbering:
                # Smart numeric prefix mapping (e.g., "05" -> targets index 5)
                try:
                    target_idx = int(display_prefix)
                except ValueError:
                    target_idx = i

                if custom_names and target_idx < len(custom_names):
                    folder_name = f"{target_idx:02d}{custom_names[target_idx]}"
                else:
                    folder_name = f"{target_idx:02d} - {display_prefix}{suffix}"
            else:
                folder_name = f"{display_prefix}{suffix}"

            # Prevent naming collision
            base_folder_name = folder_name
            counter = 1
            while folder_name in final_groups:
                folder_name = f"{base_folder_name}_{counter}"
                counter += 1

            final_groups[folder_name] = temp_groups[key]["files"]

        return final_groups

    def _get_unique_path(self, target_folder: Path, original_name: str) -> Path:
        """Helper to generate a unique filename like file (1).txt"""
        base, ext = os.path.splitext(original_name)
        counter = 1
        new_path = target_folder / original_name
        while new_path.exists():
            new_path = target_folder / f"{base} ({counter}){ext}"
            counter += 1
        return new_path

    def execute_move(self, filter_text: str, suffix: str, use_numbering: bool, custom_names: List[str],
                     case_sensitive: bool, conflict_policy: str,
                     progress_callback, log_callback) -> Tuple[int, int, int, int]:

        groups = self.preview_groups(filter_text, suffix, use_numbering, custom_names, case_sensitive)
        if not groups:
            return 0, 0, 0, 0

        existing_log = self._load_log()
        batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        moved_count = 0
        skipped_count = 0
        renamed_count = 0
        error_count = 0
        total = sum(len(files) for files in groups.values())
        current = 0

        for folder_name, file_list in groups.items():
            target_folder = self.source_path / folder_name
            target_folder.mkdir(exist_ok=True)

            for file_path in file_list:
                current += 1
                progress_callback(current, total, file_path.name)
                dst_path = target_folder / file_path.name

                # Conflict Handling Policy
                if dst_path.exists():
                    if conflict_policy == "Skip":
                        skipped_count += 1
                        log_callback(f"Skipped (Conflict): {file_path.name}", "WARN")
                        continue
                    elif conflict_policy == "Overwrite":
                        try:
                            os.remove(dst_path)
                            log_callback(f"Overwriting: {file_path.name}", "WARN")
                        except OSError as e:
                            error_count += 1
                            log_callback(f"Failed to overwrite {dst_path.name}: {e}", "ERROR")
                            continue
                    elif conflict_policy == "Auto-Rename":
                        dst_path = self._get_unique_path(target_folder, file_path.name)
                        renamed_count += 1
                        log_callback(f"Auto-Renamed to: {dst_path.name}", "INFO")

                try:
                    shutil.move(str(file_path), str(dst_path))
                    existing_log.append({
                        "batch_id": batch_id,
                        "original": str(file_path.resolve()),
                        "moved_to": str(dst_path.resolve()),
                        "group": folder_name,
                        "extension": file_path.suffix.lower(),
                        "timestamp": datetime.now().isoformat()
                    })
                    moved_count += 1
                except Exception as e:
                    error_count += 1
                    log_callback(f"Move error for {file_path.name}: {e}", "ERROR")

        self._save_log(existing_log)
        return moved_count, skipped_count, renamed_count, error_count

    def undo_last_move(self, progress_callback, log_callback) -> Tuple[int, int]:
        """Undo only the last batch of moves."""
        if not self.log_path.exists():
            return 0, 0

        log_data = self._load_log()
        if not log_data:
            return 0, 0

        last_batch_id = log_data[-1].get("batch_id")
        to_undo = []
        to_keep = []

        for item in log_data:
            if last_batch_id is None or item.get("batch_id") == last_batch_id:
                to_undo.append(item)
            else:
                to_keep.append(item)

        undone_count = 0
        total = len(to_undo)
        folders_to_cleanup: Set[Path] = set()

        for i, item in enumerate(reversed(to_undo)):
            progress_callback(i + 1, total, Path(item["moved_to"]).name)

            dst = Path(item["moved_to"])
            original = Path(item["original"])

            if dst.exists():
                try:
                    shutil.move(str(dst), str(original))
                    undone_count += 1
                    folders_to_cleanup.add(dst.parent)
                except Exception as e:
                    log_callback(f"Undo error for {dst.name}: {e}", "ERROR")
                    to_keep.append(item)
            else:
                log_callback(f"File missing for undo: {dst.name}", "WARN")

        self._save_log(to_keep)
        self._cleanup_all_empty_group_folders(folders_to_cleanup, log_callback)

        return undone_count, len(to_keep)

    def _cleanup_all_empty_group_folders(self, known_folders: Set[Path], log_callback):
        """Smart Cleanup: Ignores system files, forces deletion via rmtree, handles OS locks"""
        candidates: Set[Path] = set()
        if known_folders:
            candidates.update(known_folders)

        # Fallback scan for numbered folders
        for folder in self.source_path.iterdir():
            if not folder.is_dir(): continue
            name = folder.name
            if len(name) >= 2 and name[0].isdigit() and name[1].isdigit():
                candidates.add(folder)

        # OS hidden system files to ignore
        ignored_files = {'.ds_store', 'thumbs.db', 'desktop.ini'}

        for folder in candidates:
            if not folder.is_dir(): continue

            try:
                is_conceptually_empty = True

                for item in folder.iterdir():
                    if item.is_dir():
                        is_conceptually_empty = False
                        break
                    # Ignore system files and Office temporary files (~$) or Mac AppleDouble (._)
                    if item.name.lower() not in ignored_files and not item.name.startswith(
                            '~$') and not item.name.startswith('._'):
                        is_conceptually_empty = False
                        break

                if is_conceptually_empty:
                    # Retry loop to bypass temporary Windows Explorer file locks
                    for _ in range(3):
                        shutil.rmtree(folder, ignore_errors=True)
                        if not folder.exists():
                            if log_callback:
                                log_callback(f"Cleaned up empty folder: {folder.name}", "INFO")
                            break
                        time.sleep(0.1)  # Brief pause before retry
            except Exception:
                pass  # Fail silently if the OS simply refuses to yield the lock

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


class FileGrouperApp:
    def __init__(self, master):
        self.master = master

        self.source_var = tk.StringVar()
        self.suffix_var = tk.StringVar(value="series")
        self.filter_var = tk.StringVar(value="")
        self.numbering_var = tk.BooleanVar(value=False)
        self.case_sensitive_var = tk.BooleanVar(value=True)
        self.conflict_policy_var = tk.StringVar(value="Skip")

        self.source_path: Optional[Path] = None
        self.grouper: Optional[FileGrouper] = None

        self.master.title("File Grouper by Prefix - Enterprise Edition")
        self.master.geometry("1024x820")
        self.master.resizable(True, True)

        self.create_widgets()

    def create_widgets(self):
        title_label = tk.Label(self.master, text="File Grouper Enterprise Edition", font=("Arial", 18, "bold"))
        title_label.pack(pady=12)

        source_frame = tk.Frame(self.master)
        source_frame.pack(fill="x", padx=20, pady=5)
        tk.Label(source_frame, text="Source Folder:", font=("Arial", 10)).pack(anchor="w")
        self.folder_entry = tk.Entry(source_frame, textvariable=self.source_var, font=("Arial", 10))
        self.folder_entry.pack(fill="x", pady=2)

        options_frame = tk.Frame(self.master)
        options_frame.pack(fill="x", padx=20, pady=5)

        left_opt = tk.Frame(options_frame)
        left_opt.pack(side="left", fill="x", expand=True, padx=(0, 10))

        tk.Label(left_opt, text="Default Suffix:", font=("Arial", 10)).pack(anchor="w")
        tk.Entry(left_opt, textvariable=self.suffix_var, font=("Arial", 10)).pack(fill="x", pady=(0, 8))

        tk.Label(left_opt, text="File Filter (e.g. .jpg, .xlsx):", font=("Arial", 10)).pack(anchor="w")
        tk.Entry(left_opt, textvariable=self.filter_var, font=("Arial", 10)).pack(fill="x")

        right_opt = tk.LabelFrame(options_frame, text="Robustness Settings", font=("Arial", 9, "bold"), padx=10, pady=5)
        right_opt.pack(side="right", fill="both", expand=True)

        tk.Label(right_opt, text="Conflict Policy:", font=("Arial", 9)).grid(row=0, column=0, sticky="w", pady=2)
        conflict_cb = Combobox(right_opt, textvariable=self.conflict_policy_var,
                               values=["Skip", "Auto-Rename", "Overwrite"], state="readonly", width=15)
        conflict_cb.grid(row=0, column=1, sticky="w", padx=5, pady=2)

        tk.Checkbutton(right_opt, text="Prefix Case Sensitive", variable=self.case_sensitive_var,
                       font=("Arial", 9)).grid(row=1, column=0, columnspan=2, sticky="w", pady=2)

        numbered_frame = tk.Frame(self.master)
        numbered_frame.pack(fill="x", padx=20, pady=(10, 5))
        tk.Checkbutton(
            numbered_frame,
            text="Enable Numbered Custom Mode",
            variable=self.numbering_var,
            font=("Arial", 10, "bold")
        ).pack(anchor="w")

        custom_frame = tk.Frame(self.master)
        custom_frame.pack(fill="x", padx=20, pady=0)
        tk.Label(custom_frame,
                 text="Custom Folder Names (one per line):\nIf prefix is numeric (e.g., '05-'), it strictly uses the corresponding row.",
                 font=("Arial", 9)).pack(anchor="w")
        self.custom_names_text = scrolledtext.ScrolledText(custom_frame, height=4, font=("Consolas", 10))
        self.custom_names_text.pack(fill="x", pady=2)
        self.custom_names_text.insert(tk.END, "ACS\nDelta\nED\nOP\nBGM")

        self.master.drop_target_register(DND_FILES)
        self.master.dnd_bind('<<Drop>>', self.on_drop)

        btn_frame = tk.Frame(self.master)
        btn_frame.pack(pady=10)

        self.browse_btn = tk.Button(btn_frame, text="Browse...", command=self.browse_folder, width=12)
        self.browse_btn.pack(side="left", padx=5)
        self.preview_btn = tk.Button(btn_frame, text="Preview Groups", command=self.preview_groups, width=15,
                                     bg="#4CAF50", fg="white")
        self.preview_btn.pack(side="left", padx=5)
        self.execute_btn = tk.Button(btn_frame, text="Execute Move", command=self.execute_move, width=15, bg="#f44336",
                                     fg="white", state="disabled")
        self.execute_btn.pack(side="left", padx=5)
        self.undo_btn = tk.Button(btn_frame, text="Undo Last Batch", command=self.undo_last_move, width=15,
                                  bg="#FF9800", fg="white", state="disabled")
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

        log_label = tk.Label(self.master, text="Enterprise Audit Log:", font=("Arial", 10, "bold"))
        log_label.pack(anchor="w", padx=20, pady=(5, 0))
        self.preview_text = scrolledtext.ScrolledText(self.master, height=15, font=("Consolas", 10), bg="#1e1e1e",
                                                      fg="#d4d4d4")
        self.preview_text.pack(fill="both", expand=True, padx=20, pady=5)

        self.status_label = tk.Label(self.master, text="Ready for operation...", fg="gray", anchor="w")
        self.status_label.pack(fill="x", padx=20, pady=5)

    def log_safe(self, message: str, level: str = "INFO"):
        self.master.after(0, self.log, message, level)

    def update_progress_safe(self, current: int, total: int, filename: str = ""):
        self.master.after(0, self.update_progress, current, total, filename)

    def set_buttons_state(self, state="normal"):
        self.browse_btn.config(state=state)
        self.preview_btn.config(state=state)
        self.execute_btn.config(state=state if self.execute_btn['state'] != 'disabled' else 'disabled')
        self.undo_btn.config(state=state if self.undo_btn['state'] != 'disabled' else 'disabled')
        self.export_btn.config(state=state)

    def log(self, message: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prefix = f"[{timestamp}] {level}: "

        tag = level.lower()
        self.preview_text.tag_config("info", foreground="#d4d4d4")
        self.preview_text.tag_config("success", foreground="#4CAF50", font=("Consolas", 10, "bold"))
        self.preview_text.tag_config("warn", foreground="#FFEB3B")
        self.preview_text.tag_config("error", foreground="#f44336", font=("Consolas", 10, "bold"))

        self.preview_text.insert(tk.END, prefix + message + "\n", tag)
        self.preview_text.see(tk.END)

    def export_log(self):
        if not self.source_path: return
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
        if folder: self.load_folder(folder)

    def load_folder(self, folder_path: str):
        self.source_path = Path(folder_path).resolve()
        self.source_var.set(str(self.source_path))
        self.grouper = FileGrouper(self.source_path)

        self.preview_text.delete(1.0, tk.END)
        self.reset_progress()

        undo_state = "normal" if self.grouper.log_path.exists() else "disabled"
        self.undo_btn.config(state=undo_state)
        self.execute_btn.config(state="disabled")

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
                display_name = filename if len(filename) < 40 else filename[:37] + "..."
                text += f" - {display_name}"
            self.progress_label.config(text=text)

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
        case_sensitive = self.case_sensitive_var.get()

        if not suffix and not use_numbering:
            messagebox.showerror("Error", "Please enter a Default Suffix or enable Numbered Custom Mode!")
            return

        self.preview_text.delete(1.0, tk.END)
        self.log("=== PREVIEW START ===", "INFO")

        groups = self.grouper.preview_groups(self.filter_var.get(), suffix, use_numbering, custom_names, case_sensitive)

        total_files = sum(len(files) for files in groups.values())
        self.log(f"Found {len(groups)} groups with {total_files} files", "SUCCESS")

        for folder_name, files in sorted(groups.items()):
            self.log(f"\n→ Target Folder: {folder_name}  ({len(files)} files)", "INFO")
            for f in files[:6]:
                self.log(f"   └─ {f.name}", "INFO")
            if len(files) > 6:
                self.log(f"   ... and {len(files) - 6} more files", "WARN")

        self.log("=== PREVIEW COMPLETE ===", "INFO")
        self.execute_btn.config(state="normal" if total_files > 0 else "disabled")

    def execute_move(self):
        if not self.source_path or not self.grouper: return
        if not messagebox.askyesno("Confirm", "Confirm start moving files?"): return

        self.set_buttons_state("disabled")
        self.log("=== MOVE OPERATION START ===", "INFO")
        self.reset_progress()

        threading.Thread(target=self._execute_move_thread, daemon=True).start()

    def _execute_move_thread(self):
        filter_text = self.filter_var.get()
        suffix = self.suffix_var.get().strip()
        use_numbering = self.numbering_var.get()
        custom_names = self.get_custom_names()
        case_sensitive = self.case_sensitive_var.get()
        conflict_policy = self.conflict_policy_var.get()

        moved, skipped, renamed, errors = self.grouper.execute_move(
            filter_text, suffix, use_numbering, custom_names, case_sensitive, conflict_policy,
            progress_callback=self.update_progress_safe,
            log_callback=self.log_safe
        )

        self.master.after(0, self._on_execute_complete, moved, skipped, renamed, errors)

    def _on_execute_complete(self, moved, skipped, renamed, errors):
        self.log(f"SUMMARY: Moved {moved} | Auto-Renamed {renamed} | Skipped {skipped} | Errors {errors}", "SUCCESS")
        self.log("=== MOVE OPERATION COMPLETE ===", "INFO")

        self.status_label.config(text=f"Move completed (Moved {moved}, Renamed {renamed})", fg="green")
        self.undo_btn.config(state="normal")
        self.set_buttons_state("normal")
        self.reset_progress()

        msg = f"Successfully grouped {moved + renamed} files.\n"
        if skipped > 0: msg += f"Skipped {skipped} conflicts.\n"
        if errors > 0: msg += f"Errors on {errors} files (check log)."
        messagebox.showinfo("Operation Complete", msg)

    def undo_last_move(self):
        if not self.grouper: return
        if not messagebox.askyesno("Confirm", "Undo the LAST batch of moves?"): return

        self.set_buttons_state("disabled")
        self.log("=== UNDO OPERATION START ===", "INFO")
        self.reset_progress()

        threading.Thread(target=self._undo_thread, daemon=True).start()

    def _undo_thread(self):
        undone, remaining_logs = self.grouper.undo_last_move(
            progress_callback=self.update_progress_safe,
            log_callback=self.log_safe
        )
        self.master.after(0, self._on_undo_complete, undone, remaining_logs)

    def _on_undo_complete(self, undone, remaining_logs):
        self.log(f"SUMMARY: Restored {undone} files | {remaining_logs} records remaining", "SUCCESS")
        self.log("=== UNDO OPERATION COMPLETE ===", "INFO")

        self.status_label.config(text=f"Undo completed ({undone} files restored)", fg="green")

        self.set_buttons_state("normal")
        self.undo_btn.config(state="normal" if remaining_logs > 0 else "disabled")
        self.reset_progress()
        messagebox.showinfo("Undo Success",
                            f"Successfully restored {undone} files.\nCleaned up residual empty folders.")


if __name__ == "__main__":
    root = TkinterDnD.Tk()
    app = FileGrouperApp(root)
    root.mainloop()