"""
Roblox RAM Guard
================
โปรแกรมจัดการ RAM ของ Roblox อัตโนมัติ
- Auto Kill: ฆ่า process ที่ commit memory เกินกำหนด
- RAM Trim: ลด working set ของ process Roblox ที่ใช้ RAM เยอะ
- Low RAM Protection: หยุดการเปิด Roblox ใหม่เมื่อ RAM เหลือน้อย
- Process List: แสดง Roblox process ทั้งหมด พร้อม commit/RAM
- Log: บันทึกเหตุการณ์ที่เกิดขึ้น

Requirements:
    pip install customtkinter psutil pywin32

Run as Administrator แนะนำ (เพื่อให้ kill/trim ทำงานครบ)
"""

import customtkinter as ctk
import psutil
import threading
import time
import json
import os
import ctypes
from datetime import datetime
from tkinter import ttk

# ---------- Windows API สำหรับ trim working set ----------
try:
    import win32api
    import win32process
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_INFORMATION = 0x0400


def trim_process_memory(pid: int) -> bool:
    try:
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        handle = kernel32.OpenProcess(
            PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION, False, pid
        )
        if not handle:
            return False
        result = psapi.EmptyWorkingSet(handle)
        kernel32.CloseHandle(handle)
        return bool(result)
    except Exception:
        return False


# ---------- Settings ----------
SETTINGS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "ram_guard_settings.json"
)

DEFAULT_SETTINGS = {
    "auto_kill_enabled": True,
    "commit_size_gb": 3.8,
    "check_interval_sec": 15,
    "ram_trim_enabled": True,
    "trim_above_mb": 2000,
    "process_names": "RobloxPlayerBeta",
    "low_ram_enabled": True,
    "pause_below_gb": 4.0,
    "resume_at_gb": 8.0,
    "critical_below_gb": 2.0,
}


def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                merged = DEFAULT_SETTINGS.copy()
                merged.update(data)
                return merged
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        return True
    except Exception:
        return False


# ---------- Theme ----------
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

ACCENT_GREEN = "#3DDC97"
ACCENT_GREEN_HOVER = "#2BC082"
DARK_BG = "#1E1E1E"
PANEL_BG = "#2A2A2A"
TEXT_DIM = "#9A9A9A"


# ---------- Main App ----------
class RobloxRAMGuard(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Roblox RAM Guard")
        self.geometry("780x720")
        self.minsize(700, 650)
        self.configure(fg_color=DARK_BG)

        self.settings = load_settings()
        self.monitor_paused = False
        self.stop_flag = False
        self.next_check_in = 0
        self.processes_data = []
        self.free_ram_gb = 0.0
        self._settings_lock = threading.Lock()

        self._build_ui()
        self._apply_settings_to_ui()

        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

        self.after(1000, self._tick_ui)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- UI ----------------
    def _build_ui(self):
        container = ctk.CTkScrollableFrame(self, fg_color=DARK_BG, corner_radius=0)
        container.pack(fill="both", expand=True, padx=0, pady=0)

        self._build_auto_kill_panel(container)
        self._build_ram_trim_panel(container)
        self._build_low_ram_panel(container)
        self._build_processes_panel(container)
        self._build_log_panel(container)

    def _section_frame(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=PANEL_BG, corner_radius=8)
        frame.pack(fill="x", padx=12, pady=8)
        return frame

    def _toggle_button(self, parent, command):
        btn = ctk.CTkButton(
            parent,
            text="ON",
            width=70,
            height=28,
            fg_color=ACCENT_GREEN,
            hover_color=ACCENT_GREEN_HOVER,
            text_color="#000000",
            font=ctk.CTkFont(size=12, weight="bold"),
            corner_radius=6,
            command=command,
        )
        return btn

    def _set_toggle_state(self, btn: ctk.CTkButton, on: bool):
        if on:
            btn.configure(text="ON", fg_color=ACCENT_GREEN, hover_color=ACCENT_GREEN_HOVER)
        else:
            btn.configure(text="OFF", fg_color="#444444", hover_color="#555555")

    def _spinbox(self, parent, label, width=180):
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        lbl = ctk.CTkLabel(wrap, text=label, font=ctk.CTkFont(size=11), text_color=TEXT_DIM)
        lbl.pack(anchor="w", pady=(0, 2))
        entry = ctk.CTkEntry(wrap, width=width, height=32, fg_color="#1A1A1A", border_color="#333")
        entry.pack(fill="x")
        return wrap, entry

    def _build_auto_kill_panel(self, parent):
        frame = self._section_frame(parent)
        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(header, text="Auto Kill", font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        self.btn_auto_kill = self._toggle_button(header, self._toggle_auto_kill)
        self.btn_auto_kill.pack(side="right")
        ctk.CTkLabel(
            frame,
            text="Kill Roblox processes that exceed commit memory limits.",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_DIM,
        ).pack(anchor="w", padx=14)
        body = ctk.CTkFrame(frame, fg_color="transparent")
        body.pack(fill="x", padx=14, pady=(8, 14))
        w1, self.entry_commit_size = self._spinbox(body, "Commit Size (GB)")
        w1.pack(side="left", fill="x", expand=True, padx=(0, 8))
        w2, self.entry_check_interval = self._spinbox(body, "Check Interval (sec)")
        w2.pack(side="left", fill="x", expand=True)

    def _build_ram_trim_panel(self, parent):
        frame = self._section_frame(parent)
        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(header, text="RAM Trim", font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        self.btn_ram_trim = self._toggle_button(header, self._toggle_ram_trim)
        self.btn_ram_trim.pack(side="right")
        ctk.CTkLabel(
            frame,
            text="Trim working set when a Roblox window uses too much RAM.",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_DIM,
        ).pack(anchor="w", padx=14)
        body = ctk.CTkFrame(frame, fg_color="transparent")
        body.pack(fill="x", padx=14, pady=(8, 14))
        w1, self.entry_trim_above = self._spinbox(body, "Trim Above RAM (MB)")
        w1.pack(side="left", fill="x", expand=True, padx=(0, 8))
        w2, self.entry_process_names = self._spinbox(body, "Process Names", width=320)
        w2.pack(side="left", fill="x", expand=True)

    def _build_low_ram_panel(self, parent):
        frame = self._section_frame(parent)
        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(header, text="Low RAM Protection", font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        self.btn_low_ram = self._toggle_button(header, self._toggle_low_ram)
        self.btn_low_ram.pack(side="right")
        ctk.CTkLabel(
            frame,
            text="Pause new launches when free RAM is low. Critical mode pauses all Roblox until RAM recovers.",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_DIM,
        ).pack(anchor="w", padx=14)
        body = ctk.CTkFrame(frame, fg_color="transparent")
        body.pack(fill="x", padx=14, pady=(8, 14))
        w1, self.entry_pause_below = self._spinbox(body, "Pause Below (GB)", width=140)
        w1.pack(side="left", fill="x", expand=True, padx=(0, 8))
        w2, self.entry_resume_at = self._spinbox(body, "Resume At (GB)", width=140)
        w2.pack(side="left", fill="x", expand=True, padx=(0, 8))
        w3, self.entry_critical_below = self._spinbox(body, "Critical Below (GB)", width=140)
        w3.pack(side="left", fill="x", expand=True)

    def _build_processes_panel(self, parent):
        frame = self._section_frame(parent)
        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(header, text="Processes", font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        btn_box = ctk.CTkFrame(header, fg_color="transparent")
        btn_box.pack(side="right")
        self.btn_pause = ctk.CTkButton(
            btn_box, text="Pause Monitor", width=110, height=28,
            fg_color="#3A3A3A", hover_color="#4A4A4A",
            command=self._toggle_pause,
        )
        self.btn_pause.pack(side="left", padx=4)
        ctk.CTkButton(
            btn_box, text="Save Settings", width=110, height=28,
            fg_color="#3A3A3A", hover_color="#4A4A4A",
            command=self._save_settings_clicked,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            btn_box, text="Trim All Now", width=110, height=28,
            fg_color="#3A3A3A", hover_color="#4A4A4A",
            command=self._trim_all_now,
        ).pack(side="left", padx=4)

        self.lbl_status_line = ctk.CTkLabel(
            frame, text="Free RAM: -- | Next check in -- sec",
            font=ctk.CTkFont(size=11), text_color=TEXT_DIM,
        )
        self.lbl_status_line.pack(anchor="w", padx=14)

        tree_wrap = ctk.CTkFrame(frame, fg_color="#1A1A1A", corner_radius=4)
        tree_wrap.pack(fill="x", padx=14, pady=(8, 14))

        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "RAMGuard.Treeview",
            background="#1A1A1A", foreground="white",
            fieldbackground="#1A1A1A", borderwidth=0, rowheight=24,
        )
        style.configure(
            "RAMGuard.Treeview.Heading",
            background="#2A2A2A", foreground="white", borderwidth=0,
            font=("Segoe UI", 10, "bold"),
        )
        style.map("RAMGuard.Treeview",
                  background=[("selected", ACCENT_GREEN)],
                  foreground=[("selected", "black")])

        columns = ("pid", "window", "commit", "ram", "status")
        self.tree = ttk.Treeview(
            tree_wrap, columns=columns, show="headings",
            height=5, style="RAMGuard.Treeview",
        )
        self.tree.heading("pid", text="PID")
        self.tree.heading("window", text="Window")
        self.tree.heading("commit", text="Commit GB")
        self.tree.heading("ram", text="RAM MB")
        self.tree.heading("status", text="Status")
        self.tree.column("pid", width=70, anchor="w")
        self.tree.column("window", width=240, anchor="w")
        self.tree.column("commit", width=90, anchor="w")
        self.tree.column("ram", width=90, anchor="w")
        self.tree.column("status", width=120, anchor="w")
        self.tree.pack(side="left", fill="x", expand=True, padx=2, pady=2)

        sb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)

    def _build_log_panel(self, parent):
        frame = self._section_frame(parent)
        ctk.CTkLabel(
            frame, text="Log", font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", padx=14, pady=(12, 4))
        self.log_text = ctk.CTkTextbox(
            frame, height=130, fg_color="#1A1A1A",
            text_color="#DDDDDD", font=ctk.CTkFont(family="Consolas", size=11),
            corner_radius=4,
        )
        self.log_text.pack(fill="x", padx=14, pady=(4, 14))
        self.log_text.configure(state="disabled")

    # ---------------- Settings sync ----------------
    def _apply_settings_to_ui(self):
        s = self.settings
        self._set_toggle_state(self.btn_auto_kill, s["auto_kill_enabled"])
        self._set_toggle_state(self.btn_ram_trim, s["ram_trim_enabled"])
        self._set_toggle_state(self.btn_low_ram, s["low_ram_enabled"])
        self.entry_commit_size.insert(0, str(s["commit_size_gb"]))
        self.entry_check_interval.insert(0, str(s["check_interval_sec"]))
        self.entry_trim_above.insert(0, str(s["trim_above_mb"]))
        self.entry_process_names.insert(0, s["process_names"])
        self.entry_pause_below.insert(0, str(s["pause_below_gb"]))
        self.entry_resume_at.insert(0, str(s["resume_at_gb"]))
        self.entry_critical_below.insert(0, str(s["critical_below_gb"]))

    def _read_settings_from_ui(self):
        """เรียกได้เฉพาะใน main thread เท่านั้น"""
        try:
            with self._settings_lock:
                self.settings["commit_size_gb"] = float(self.entry_commit_size.get() or 0)
                self.settings["check_interval_sec"] = max(1, int(float(self.entry_check_interval.get() or 15)))
                self.settings["trim_above_mb"] = float(self.entry_trim_above.get() or 0)
                self.settings["process_names"] = self.entry_process_names.get().strip() or "RobloxPlayerBeta"
                self.settings["pause_below_gb"] = float(self.entry_pause_below.get() or 0)
                self.settings["resume_at_gb"] = float(self.entry_resume_at.get() or 0)
                self.settings["critical_below_gb"] = float(self.entry_critical_below.get() or 0)
        except ValueError:
            self.log("⚠ Invalid number in settings; ignored.")

    def _get_settings_snapshot(self):
        """ดึง settings copy ปลอดภัยจาก background thread"""
        with self._settings_lock:
            return self.settings.copy()

    # ---------------- Toggle handlers ----------------
    def _toggle_auto_kill(self):
        self.settings["auto_kill_enabled"] = not self.settings["auto_kill_enabled"]
        self._set_toggle_state(self.btn_auto_kill, self.settings["auto_kill_enabled"])

    def _toggle_ram_trim(self):
        self.settings["ram_trim_enabled"] = not self.settings["ram_trim_enabled"]
        self._set_toggle_state(self.btn_ram_trim, self.settings["ram_trim_enabled"])

    def _toggle_low_ram(self):
        self.settings["low_ram_enabled"] = not self.settings["low_ram_enabled"]
        self._set_toggle_state(self.btn_low_ram, self.settings["low_ram_enabled"])

    def _toggle_pause(self):
        self.monitor_paused = not self.monitor_paused
        self.btn_pause.configure(text="Resume Monitor" if self.monitor_paused else "Pause Monitor")
        self.log("⏸ Monitor paused." if self.monitor_paused else "▶ Monitor resumed.")

    def _save_settings_clicked(self):
        self._read_settings_from_ui()  # ปลอดภัย เพราะอยู่ใน main thread
        if save_settings(self.settings):
            self.log("💾 Settings saved.")
        else:
            self.log("⚠ Failed to save settings.")

    def _trim_all_now(self):
        self._read_settings_from_ui()
        names = [n.strip().lower() for n in self.settings["process_names"].split(",") if n.strip()]
        count = 0
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                pname = (proc.info["name"] or "").lower()
                if any(n in pname for n in names):
                    if trim_process_memory(proc.info["pid"]):
                        count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        self.log(f"✂ Trimmed working set of {count} process(es).")

    # ---------------- Logging ----------------
    def log(self, message: str):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"{ts}  {message}\n"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line)
        if int(self.log_text.index("end-1c").split(".")[0]) > 200:
            self.log_text.delete("1.0", "50.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ---------------- Monitor loop ----------------
    def _monitor_loop(self):
        last_check = 0
        suspended_pids = set()

        while not self.stop_flag:
            # ดึง snapshot ปลอดภัย ไม่อ่าน widget ตรง
            s = self._get_settings_snapshot()
            interval = s["check_interval_sec"]
            now = time.time()
            self.next_check_in = max(0, int(interval - (now - last_check)))

            if self.monitor_paused:
                time.sleep(1)
                last_check = now - interval
                continue

            if now - last_check >= interval:
                last_check = now
                try:
                    self._do_check_safe(s, suspended_pids)
                except Exception as e:
                    self.log(f"⚠ Monitor error: {e}")

            time.sleep(1)

    def _do_check_safe(self, s, suspended_pids):
        names = [n.strip().lower() for n in s["process_names"].split(",") if n.strip()]
        commit_limit_bytes = s["commit_size_gb"] * (1024 ** 3)
        trim_above_bytes = s["trim_above_mb"] * (1024 ** 2)

        vm = psutil.virtual_memory()
        free_gb = vm.available / (1024 ** 3)
        self.free_ram_gb = free_gb

        # Resume process ที่ suspend ไว้ ถ้า RAM กลับมาแล้ว
        if free_gb >= s["resume_at_gb"] and suspended_pids:
            for pid in list(suspended_pids):
                try:
                    psutil.Process(pid).resume()
                    self.log(f"▶ RAM recovered — resumed Roblox ({pid}).")
                    suspended_pids.discard(pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    suspended_pids.discard(pid)

        # Critical RAM: suspend Roblox ทั้งหมด
        if s["low_ram_enabled"] and free_gb < s["critical_below_gb"]:
            for proc in psutil.process_iter(["pid", "name"]):
                try:
                    if any(n in (proc.info["name"] or "").lower() for n in names):
                        pid = proc.info["pid"]
                        if pid not in suspended_pids:
                            psutil.Process(pid).suspend()
                            suspended_pids.add(pid)
                            self.log(f"⛔ Critical RAM — suspended Roblox ({pid}).")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

        # ตรวจสอบแต่ละ process
        processes = []
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if not any(n in (proc.info["name"] or "").lower() for n in names):
                    continue
                pid = proc.info["pid"]
                p = psutil.Process(pid)
                mem = p.memory_info()
                try:
                    commit_bytes = mem.private
                except AttributeError:
                    commit_bytes = mem.vms
                ram_bytes = mem.rss
                status = "Watching"

                # Auto Kill
                if s["auto_kill_enabled"] and commit_bytes > commit_limit_bytes:
                    try:
                        p.kill()
                        status = "Killed"
                        suspended_pids.discard(pid)
                        self.log(f"💀 Killed Roblox ({pid}) — commit {commit_bytes/(1024**3):.2f} GB.")
                    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                        status = "Kill failed"
                        self.log(f"⚠ Cannot kill {pid}: {e}")

                # RAM Trim
                elif s["ram_trim_enabled"] and ram_bytes > trim_above_bytes:
                    if trim_process_memory(pid):
                        status = "Trimmed"
                        self.log(f"✂ Trimmed Roblox ({pid}) — {ram_bytes/(1024**2):.0f} MB.")

                processes.append({
                    "pid": pid,
                    "window": f"Roblox ({pid})",
                    "commit_gb": commit_bytes / (1024 ** 3),
                    "ram_mb": ram_bytes / (1024 ** 2),
                    "status": status,
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        self.processes_data = processes

    # ---------------- UI tick ----------------
    def _tick_ui(self):
        free = self.free_ram_gb or psutil.virtual_memory().available / (1024 ** 3)
        self.lbl_status_line.configure(
            text=f"Free RAM: {free:.1f} GB    |    Next check in {self.next_check_in} sec"
        )

        existing = {self.tree.item(i, "values")[0]: i for i in self.tree.get_children()}
        seen_pids = set()
        for p in self.processes_data:
            pid_str = str(p["pid"])
            seen_pids.add(pid_str)
            values = (
                pid_str, p["window"],
                f"{p['commit_gb']:.2f}",
                f"{p['ram_mb']:.0f}",
                p["status"],
            )
            if pid_str in existing:
                self.tree.item(existing[pid_str], values=values)
            else:
                self.tree.insert("", "end", values=values)

        for pid_str, iid in existing.items():
            if pid_str not in seen_pids:
                self.tree.delete(iid)

        if not self.stop_flag:
            self.after(1000, self._tick_ui)

    def _on_close(self):
        self.stop_flag = True
        self._read_settings_from_ui()
        save_settings(self.settings)
        self.destroy()


# ---------- Admin check ----------
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


if __name__ == "__main__":
    app = RobloxRAMGuard()
    if not is_admin():
        app.log("ℹ Tip: Run as Administrator for full kill/trim permissions.")
    if not HAS_WIN32:
        app.log("ℹ pywin32 not installed — using ctypes fallback (still works).")
    app.mainloop()
