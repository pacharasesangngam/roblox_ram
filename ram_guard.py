"""
Roblox RAM Guard
================
โปรแกรมจัดการ RAM ของ Roblox อัตโนมัติ
- Auto Kill: ฆ่า process ที่ commit memory เกินกำหนด
- RAM Trim: ลด working set ของ process Roblox ที่ใช้ RAM เยอะ
- Low RAM Protection: หยุดการเปิด Roblox ใหม่เมื่อ RAM เหลือน้อย
- Process Limiter: จำกัด CPU Core / RAM / Priority ของ Roblox
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

# ---------- Windows API ----------
try:
    import win32api
    import win32process
    import win32con
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_INFORMATION = 0x0400

PRIORITY_MAP = {
    "Idle":         psutil.IDLE_PRIORITY_CLASS,
    "Below Normal": psutil.BELOW_NORMAL_PRIORITY_CLASS,
    "Normal":       psutil.NORMAL_PRIORITY_CLASS,
    "Above Normal": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
    "High":         psutil.HIGH_PRIORITY_CLASS,
}
PRIORITY_OPTIONS = list(PRIORITY_MAP.keys())


def trim_process_memory(pid: int) -> bool:
    try:
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        handle = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION, False, pid)
        if not handle:
            return False
        result = psapi.EmptyWorkingSet(handle)
        kernel32.CloseHandle(handle)
        return bool(result)
    except Exception:
        return False


def set_process_affinity(pid: int, core_count: int) -> bool:
    try:
        total = psutil.cpu_count(logical=True)
        core_count = max(1, min(core_count, total))
        psutil.Process(pid).cpu_affinity(list(range(core_count)))
        return True
    except Exception:
        return False


def set_process_priority(pid: int, priority_name: str) -> bool:
    try:
        psutil.Process(pid).nice(PRIORITY_MAP.get(priority_name, psutil.NORMAL_PRIORITY_CLASS))
        return True
    except Exception:
        return False


def set_process_ram_limit(pid: int, ram_mb: int) -> bool:
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1F0FFF, False, pid)
        if not handle:
            return False
        result = kernel32.SetProcessWorkingSetSize(
            handle,
            ctypes.c_size_t(1 * 1024 * 1024),
            ctypes.c_size_t(ram_mb * 1024 * 1024),
        )
        kernel32.CloseHandle(handle)
        return bool(result)
    except Exception:
        return False


# ---------- Settings ----------
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ram_guard_settings.json")

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
    "limiter_enabled": True,
    "max_cores": 1,
    "max_ram_mb": 1024,
    "auto_kill_overloaded": True,
    "priority": "Idle",
}


def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                merged = DEFAULT_SETTINGS.copy()
                merged.update(json.load(f))
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


class RobloxRAMGuard(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Roblox RAM Guard")
        self.geometry("780x900")
        self.minsize(700, 780)
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

        threading.Thread(target=self._monitor_loop, daemon=True).start()
        self.after(1000, self._tick_ui)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── helpers ──────────────────────────────────────────────────────────
    def _section_frame(self, parent):
        f = ctk.CTkFrame(parent, fg_color=PANEL_BG, corner_radius=8)
        f.pack(fill="x", padx=12, pady=8)
        return f

    def _toggle_button(self, parent, command):
        return ctk.CTkButton(parent, text="ON", width=70, height=28,
                             fg_color=ACCENT_GREEN, hover_color=ACCENT_GREEN_HOVER,
                             text_color="#000", font=ctk.CTkFont(size=12, weight="bold"),
                             corner_radius=6, command=command)

    def _set_toggle_state(self, btn, on):
        if on:
            btn.configure(text="ON", fg_color=ACCENT_GREEN, hover_color=ACCENT_GREEN_HOVER)
        else:
            btn.configure(text="OFF", fg_color="#444", hover_color="#555")

    def _spinbox(self, parent, label, width=180):
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkLabel(wrap, text=label, font=ctk.CTkFont(size=11), text_color=TEXT_DIM).pack(anchor="w", pady=(0, 2))
        entry = ctk.CTkEntry(wrap, width=width, height=32, fg_color="#1A1A1A", border_color="#333")
        entry.pack(fill="x")
        return wrap, entry

    # ── build UI ─────────────────────────────────────────────────────────
    def _build_ui(self):
        c = ctk.CTkScrollableFrame(self, fg_color=DARK_BG, corner_radius=0)
        c.pack(fill="both", expand=True)
        self._build_auto_kill_panel(c)
        self._build_ram_trim_panel(c)
        self._build_low_ram_panel(c)
        self._build_limiter_panel(c)
        self._build_processes_panel(c)
        self._build_log_panel(c)

    def _build_auto_kill_panel(self, p):
        f = self._section_frame(p)
        h = ctk.CTkFrame(f, fg_color="transparent")
        h.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(h, text="Auto Kill", font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        self.btn_auto_kill = self._toggle_button(h, self._toggle_auto_kill)
        self.btn_auto_kill.pack(side="right")
        ctk.CTkLabel(f, text="Kill Roblox processes that exceed commit memory limits.",
                     font=ctk.CTkFont(size=11), text_color=TEXT_DIM).pack(anchor="w", padx=14)
        b = ctk.CTkFrame(f, fg_color="transparent")
        b.pack(fill="x", padx=14, pady=(8, 14))
        w1, self.entry_commit_size = self._spinbox(b, "Commit Size (GB)")
        w1.pack(side="left", fill="x", expand=True, padx=(0, 8))
        w2, self.entry_check_interval = self._spinbox(b, "Check Interval (sec)")
        w2.pack(side="left", fill="x", expand=True)

    def _build_ram_trim_panel(self, p):
        f = self._section_frame(p)
        h = ctk.CTkFrame(f, fg_color="transparent")
        h.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(h, text="RAM Trim", font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        self.btn_ram_trim = self._toggle_button(h, self._toggle_ram_trim)
        self.btn_ram_trim.pack(side="right")
        ctk.CTkLabel(f, text="Trim working set when a Roblox window uses too much RAM.",
                     font=ctk.CTkFont(size=11), text_color=TEXT_DIM).pack(anchor="w", padx=14)
        b = ctk.CTkFrame(f, fg_color="transparent")
        b.pack(fill="x", padx=14, pady=(8, 14))
        w1, self.entry_trim_above = self._spinbox(b, "Trim Above RAM (MB)")
        w1.pack(side="left", fill="x", expand=True, padx=(0, 8))
        w2, self.entry_process_names = self._spinbox(b, "Process Names", width=320)
        w2.pack(side="left", fill="x", expand=True)

    def _build_low_ram_panel(self, p):
        f = self._section_frame(p)
        h = ctk.CTkFrame(f, fg_color="transparent")
        h.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(h, text="Low RAM Protection", font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        self.btn_low_ram = self._toggle_button(h, self._toggle_low_ram)
        self.btn_low_ram.pack(side="right")
        ctk.CTkLabel(f, text="Pause new launches when free RAM is low. Critical mode pauses all Roblox until RAM recovers.",
                     font=ctk.CTkFont(size=11), text_color=TEXT_DIM).pack(anchor="w", padx=14)
        b = ctk.CTkFrame(f, fg_color="transparent")
        b.pack(fill="x", padx=14, pady=(8, 14))
        w1, self.entry_pause_below = self._spinbox(b, "Pause Below (GB)", width=140)
        w1.pack(side="left", fill="x", expand=True, padx=(0, 8))
        w2, self.entry_resume_at = self._spinbox(b, "Resume At (GB)", width=140)
        w2.pack(side="left", fill="x", expand=True, padx=(0, 8))
        w3, self.entry_critical_below = self._spinbox(b, "Critical Below (GB)", width=140)
        w3.pack(side="left", fill="x", expand=True)

    def _build_limiter_panel(self, p):
        f = self._section_frame(p)
        h = ctk.CTkFrame(f, fg_color="transparent")
        h.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(h, text="Process Limiter (unstable)", font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        self.btn_limiter = self._toggle_button(h, self._toggle_limiter)
        self.btn_limiter.pack(side="right")
        ctk.CTkLabel(f, text="จำกัด CPU Core, RAM และ Priority ของ Roblox แต่ละ process อัตโนมัติ",
                     font=ctk.CTkFont(size=11), text_color=TEXT_DIM).pack(anchor="w", padx=14)

        b = ctk.CTkFrame(f, fg_color="transparent")
        b.pack(fill="x", padx=14, pady=(8, 6))

        w1, self.entry_max_cores = self._spinbox(b, f"Max Core (unstable)  [1–{psutil.cpu_count(logical=True)}]", width=130)
        w1.pack(side="left", fill="x", expand=True, padx=(0, 8))

        w2, self.entry_max_ram = self._spinbox(b, "Max RAM (unstable)  MB", width=130)
        w2.pack(side="left", fill="x", expand=True, padx=(0, 8))

        prio_wrap = ctk.CTkFrame(b, fg_color="transparent")
        ctk.CTkLabel(prio_wrap, text="Priority", font=ctk.CTkFont(size=11), text_color=TEXT_DIM).pack(anchor="w", pady=(0, 2))
        self.combo_priority = ctk.CTkComboBox(prio_wrap, values=PRIORITY_OPTIONS, width=140, height=32,
                                              fg_color="#1A1A1A", border_color="#333", button_color="#333")
        self.combo_priority.pack(fill="x")
        prio_wrap.pack(side="left", fill="x", expand=True)

        chk_wrap = ctk.CTkFrame(f, fg_color="transparent")
        chk_wrap.pack(fill="x", padx=14, pady=(4, 14))
        self.chk_auto_kill_overloaded = ctk.CTkCheckBox(
            chk_wrap,
            text="Auto kill when overloaded (almost full committed)",
            font=ctk.CTkFont(size=11), text_color="#DDDDDD",
            fg_color=ACCENT_GREEN, hover_color=ACCENT_GREEN_HOVER,
            command=self._toggle_auto_kill_overloaded,
        )
        self.chk_auto_kill_overloaded.pack(anchor="w")

    def _build_processes_panel(self, p):
        f = self._section_frame(p)
        h = ctk.CTkFrame(f, fg_color="transparent")
        h.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(h, text="Processes", font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        bb = ctk.CTkFrame(h, fg_color="transparent")
        bb.pack(side="right")
        self.btn_pause = ctk.CTkButton(bb, text="Pause Monitor", width=110, height=28,
                                       fg_color="#3A3A3A", hover_color="#4A4A4A", command=self._toggle_pause)
        self.btn_pause.pack(side="left", padx=4)
        ctk.CTkButton(bb, text="Save Settings", width=110, height=28,
                      fg_color="#3A3A3A", hover_color="#4A4A4A",
                      command=self._save_settings_clicked).pack(side="left", padx=4)
        ctk.CTkButton(bb, text="Trim All Now", width=100, height=28,
                      fg_color="#3A3A3A", hover_color="#4A4A4A",
                      command=self._trim_all_now).pack(side="left", padx=4)
        ctk.CTkButton(bb, text="Apply Limits Now", width=125, height=28,
                      fg_color=ACCENT_GREEN, hover_color=ACCENT_GREEN_HOVER,
                      text_color="#000", command=self._apply_limits_now).pack(side="left", padx=4)

        self.lbl_status_line = ctk.CTkLabel(f, text="Free RAM: -- | Next check in -- sec",
                                            font=ctk.CTkFont(size=11), text_color=TEXT_DIM)
        self.lbl_status_line.pack(anchor="w", padx=14)

        tree_wrap = ctk.CTkFrame(f, fg_color="#1A1A1A", corner_radius=4)
        tree_wrap.pack(fill="x", padx=14, pady=(8, 14))

        style = ttk.Style()
        style.theme_use("default")
        style.configure("RAMGuard.Treeview", background="#1A1A1A", foreground="white",
                        fieldbackground="#1A1A1A", borderwidth=0, rowheight=24)
        style.configure("RAMGuard.Treeview.Heading", background="#2A2A2A", foreground="white",
                        borderwidth=0, font=("Segoe UI", 10, "bold"))
        style.map("RAMGuard.Treeview", background=[("selected", ACCENT_GREEN)], foreground=[("selected", "black")])

        columns = ("pid", "window", "commit", "ram", "status")
        self.tree = ttk.Treeview(tree_wrap, columns=columns, show="headings", height=5, style="RAMGuard.Treeview")
        for col, txt, w in [("pid","PID",70),("window","Window",200),("commit","Commit GB",90),("ram","RAM MB",90),("status","Status",180)]:
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=w, anchor="w")
        self.tree.pack(side="left", fill="x", expand=True, padx=2, pady=2)
        sb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)

    def _build_log_panel(self, p):
        f = self._section_frame(p)
        ctk.CTkLabel(f, text="Log", font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", padx=14, pady=(12, 4))
        self.log_text = ctk.CTkTextbox(f, height=130, fg_color="#1A1A1A", text_color="#DDDDDD",
                                       font=ctk.CTkFont(family="Consolas", size=11), corner_radius=4)
        self.log_text.pack(fill="x", padx=14, pady=(4, 14))
        self.log_text.configure(state="disabled")

    # ── settings ─────────────────────────────────────────────────────────
    def _apply_settings_to_ui(self):
        s = self.settings
        self._set_toggle_state(self.btn_auto_kill, s["auto_kill_enabled"])
        self._set_toggle_state(self.btn_ram_trim, s["ram_trim_enabled"])
        self._set_toggle_state(self.btn_low_ram, s["low_ram_enabled"])
        self._set_toggle_state(self.btn_limiter, s["limiter_enabled"])
        self.entry_commit_size.insert(0, str(s["commit_size_gb"]))
        self.entry_check_interval.insert(0, str(s["check_interval_sec"]))
        self.entry_trim_above.insert(0, str(s["trim_above_mb"]))
        self.entry_process_names.insert(0, s["process_names"])
        self.entry_pause_below.insert(0, str(s["pause_below_gb"]))
        self.entry_resume_at.insert(0, str(s["resume_at_gb"]))
        self.entry_critical_below.insert(0, str(s["critical_below_gb"]))
        self.entry_max_cores.insert(0, str(s["max_cores"]))
        self.entry_max_ram.insert(0, str(s["max_ram_mb"]))
        self.combo_priority.set(s["priority"])
        if s["auto_kill_overloaded"]:
            self.chk_auto_kill_overloaded.select()
        else:
            self.chk_auto_kill_overloaded.deselect()

    def _read_settings_from_ui(self):
        try:
            with self._settings_lock:
                self.settings["commit_size_gb"] = float(self.entry_commit_size.get() or 0)
                self.settings["check_interval_sec"] = max(1, int(float(self.entry_check_interval.get() or 15)))
                self.settings["trim_above_mb"] = float(self.entry_trim_above.get() or 0)
                self.settings["process_names"] = self.entry_process_names.get().strip() or "RobloxPlayerBeta"
                self.settings["pause_below_gb"] = float(self.entry_pause_below.get() or 0)
                self.settings["resume_at_gb"] = float(self.entry_resume_at.get() or 0)
                self.settings["critical_below_gb"] = float(self.entry_critical_below.get() or 0)
                self.settings["max_cores"] = max(1, int(float(self.entry_max_cores.get() or 1)))
                self.settings["max_ram_mb"] = max(128, int(float(self.entry_max_ram.get() or 1024)))
                self.settings["priority"] = self.combo_priority.get()
        except ValueError:
            self.log("⚠ Invalid number in settings; ignored.")

    def _get_settings_snapshot(self):
        with self._settings_lock:
            return self.settings.copy()

    # ── toggles ──────────────────────────────────────────────────────────
    def _toggle_auto_kill(self):
        self.settings["auto_kill_enabled"] = not self.settings["auto_kill_enabled"]
        self._set_toggle_state(self.btn_auto_kill, self.settings["auto_kill_enabled"])

    def _toggle_ram_trim(self):
        self.settings["ram_trim_enabled"] = not self.settings["ram_trim_enabled"]
        self._set_toggle_state(self.btn_ram_trim, self.settings["ram_trim_enabled"])

    def _toggle_low_ram(self):
        self.settings["low_ram_enabled"] = not self.settings["low_ram_enabled"]
        self._set_toggle_state(self.btn_low_ram, self.settings["low_ram_enabled"])

    def _toggle_limiter(self):
        self.settings["limiter_enabled"] = not self.settings["limiter_enabled"]
        self._set_toggle_state(self.btn_limiter, self.settings["limiter_enabled"])

    def _toggle_auto_kill_overloaded(self):
        self.settings["auto_kill_overloaded"] = not self.settings.get("auto_kill_overloaded", True)

    def _toggle_pause(self):
        self.monitor_paused = not self.monitor_paused
        self.btn_pause.configure(text="Resume Monitor" if self.monitor_paused else "Pause Monitor")
        self.log("⏸ Monitor paused." if self.monitor_paused else "▶ Monitor resumed.")

    def _save_settings_clicked(self):
        self._read_settings_from_ui()
        self.log("💾 Settings saved." if save_settings(self.settings) else "⚠ Failed to save settings.")

    def _trim_all_now(self):
        self._read_settings_from_ui()
        names = [n.strip().lower() for n in self.settings["process_names"].split(",") if n.strip()]
        count = sum(
            1 for proc in psutil.process_iter(["pid", "name"])
            if any(n in (proc.info["name"] or "").lower() for n in names)
            and trim_process_memory(proc.info["pid"])
        )
        self.log(f"✂ Trimmed working set of {count} process(es).")

    def _apply_limits_now(self):
        self._read_settings_from_ui()
        s = self._get_settings_snapshot()
        if not s["limiter_enabled"]:
            self.log("⚠ Process Limiter is OFF.")
            return
        names = [n.strip().lower() for n in s["process_names"].split(",") if n.strip()]
        count = 0
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if any(n in (proc.info["name"] or "").lower() for n in names):
                    pid = proc.info["pid"]
                    set_process_affinity(pid, s["max_cores"])
                    set_process_ram_limit(pid, s["max_ram_mb"])
                    set_process_priority(pid, s["priority"])
                    count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        self.log(f"⚙ Applied to {count} process(es): {s['max_cores']} core(s), {s['max_ram_mb']} MB, {s['priority']}.")

    # ── logging ───────────────────────────────────────────────────────────
    def log(self, message: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{ts}  {message}\n")
        if int(self.log_text.index("end-1c").split(".")[0]) > 200:
            self.log_text.delete("1.0", "50.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ── monitor loop ──────────────────────────────────────────────────────
    def _monitor_loop(self):
        last_check = 0
        suspended_pids = set()
        while not self.stop_flag:
            s = self._get_settings_snapshot()
            now = time.time()
            self.next_check_in = max(0, int(s["check_interval_sec"] - (now - last_check)))
            if self.monitor_paused:
                time.sleep(1)
                last_check = now - s["check_interval_sec"]
                continue
            if now - last_check >= s["check_interval_sec"]:
                last_check = now
                try:
                    self._do_check_safe(s, suspended_pids)
                except Exception as e:
                    self.log(f"⚠ Monitor error: {e}")
            time.sleep(1)

    def _do_check_safe(self, s, suspended_pids):
        names = [n.strip().lower() for n in s["process_names"].split(",") if n.strip()]
        commit_limit = s["commit_size_gb"] * (1024 ** 3)
        trim_above = s["trim_above_mb"] * (1024 ** 2)

        vm = psutil.virtual_memory()
        free_gb = vm.available / (1024 ** 3)
        self.free_ram_gb = free_gb

        # Resume suspended processes
        if free_gb >= s["resume_at_gb"] and suspended_pids:
            for pid in list(suspended_pids):
                try:
                    psutil.Process(pid).resume()
                    self.log(f"▶ RAM recovered — resumed Roblox ({pid}).")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                suspended_pids.discard(pid)

        # Critical suspend
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

        processes = []
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if not any(n in (proc.info["name"] or "").lower() for n in names):
                    continue
                pid = proc.info["pid"]
                p = psutil.Process(pid)
                mem = p.memory_info()
                commit_bytes = getattr(mem, "private", mem.vms)
                ram_bytes = mem.rss
                status = "Watching"

                # Apply limiter
                if s["limiter_enabled"]:
                    set_process_affinity(pid, s["max_cores"])
                    set_process_ram_limit(pid, s["max_ram_mb"])
                    set_process_priority(pid, s["priority"])
                    status = f"Limited ({s['max_cores']}c / {s['max_ram_mb']}MB / {s['priority']})"

                # Auto kill overloaded
                if s.get("auto_kill_overloaded", True) and commit_bytes > commit_limit:
                    try:
                        p.kill()
                        status = "Killed (overloaded)"
                        suspended_pids.discard(pid)
                        self.log(f"💀 Killed Roblox ({pid}) — overloaded {commit_bytes/(1024**3):.2f} GB.")
                    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                        self.log(f"⚠ Cannot kill {pid}: {e}")

                # RAM Trim
                elif s["ram_trim_enabled"] and ram_bytes > trim_above:
                    if trim_process_memory(pid):
                        status = "Trimmed"
                        self.log(f"✂ Trimmed Roblox ({pid}) — {ram_bytes/(1024**2):.0f} MB.")

                processes.append({"pid": pid, "window": f"Roblox ({pid})",
                                  "commit_gb": commit_bytes/(1024**3), "ram_mb": ram_bytes/(1024**2),
                                  "status": status})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        self.processes_data = processes

    # ── UI tick ───────────────────────────────────────────────────────────
    def _tick_ui(self):
        free = self.free_ram_gb or psutil.virtual_memory().available / (1024 ** 3)
        self.lbl_status_line.configure(text=f"Free RAM: {free:.1f} GB    |    Next check in {self.next_check_in} sec")

        existing = {self.tree.item(i, "values")[0]: i for i in self.tree.get_children()}
        seen = set()
        for p in self.processes_data:
            pid_str = str(p["pid"])
            seen.add(pid_str)
            vals = (pid_str, p["window"], f"{p['commit_gb']:.2f}", f"{p['ram_mb']:.0f}", p["status"])
            if pid_str in existing:
                self.tree.item(existing[pid_str], values=vals)
            else:
                self.tree.insert("", "end", values=vals)
        for pid_str, iid in existing.items():
            if pid_str not in seen:
                self.tree.delete(iid)

        if not self.stop_flag:
            self.after(1000, self._tick_ui)

    def _on_close(self):
        self.stop_flag = True
        self._read_settings_from_ui()
        save_settings(self.settings)
        self.destroy()


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
