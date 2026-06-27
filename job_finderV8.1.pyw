"""
Job Folder Finder
------------------
A lightweight tool for quickly jumping to job folders and common
GPS/CAD locations across your J:, V:, and Y: drives.
"""

import os
import json
import re
import threading
import datetime
import glob
import subprocess
from urllib.parse import quote
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# ============================== CONFIG ===================================

KNOWN_CONTAINERS = {
    "J:": "J:\\",
    "V:": "V:\\",
    "Y:": r"Y:\JOB FOLDERS",
}

DRIVES = ["J:", "V:", "Y:"]
CONTAINER_KEYWORDS = ["job", "project"]
MAX_SEARCH_DEPTH = 5

GPS_ASBUILT_PATH = [
    ["scan", "cad"],          
    ["gps", "as-built"],      
]

QUICK_LINKS = [
    {
        "label": "Trimble Raw Data Root",
        "path": r"J:\3_GPS, MODELS, SOFTWARE\00 GPS RAW DATA",
        "year_picker": True,
        "default_filter": '"*.vce"',
    },
    {
        "label": "Jobs - (J:)",
        "path": r"J:",
    },
    {
        "label": "Jobs - (V:)",
        "path": r"V:",
    },
    {
        "label": "Jobs - (Y:)",
        "path": r"Y:\JOB FOLDERS",
    },
]

DWG_FILTER_QUERY = "*.dwg"
RECENTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".job_finder_recents.json")
MAX_RECENTS = 25

# ===========================================================================


def fill_year(path_template):
    return path_template.replace("{year}", str(datetime.datetime.now().year))


def resolve_existing_dir(path):
    p = path
    while p and not os.path.isdir(p):
        parent = os.path.dirname(p.rstrip("\\/"))
        if parent == p:
            return None
        p = parent
    return p if p and os.path.isdir(p) else None


def open_folder(path, filter_query=None):
    existing = resolve_existing_dir(path)
    if not existing:
        return None
    if filter_query:
        encoded_path = quote(existing, safe=":\\")
        encoded_query = quote(filter_query, safe="*.")
        uri = f"search-ms:query={encoded_query}&crumb=location:{encoded_path}"
        os.startfile(uri)
    else:
        os.startfile(existing)
    return existing


def search_known_containers(job_code, cancel_event=None):
    matches = []
    for container in KNOWN_CONTAINERS.values():
        if cancel_event and cancel_event.is_set():
            return matches
        if not os.path.isdir(container):
            continue
        try:
            year_entries = os.listdir(container)
        except (PermissionError, FileNotFoundError, OSError):
            continue
        for year_entry in year_entries:
            if cancel_event and cancel_event.is_set():
                return matches
            year_path = os.path.join(container, year_entry)
            if not os.path.isdir(year_path):
                continue
            if not any(kw in year_entry.lower() for kw in CONTAINER_KEYWORDS):
                continue
            try:
                job_entries = os.listdir(year_path)
            except (PermissionError, FileNotFoundError, OSError):
                continue
            for job_entry in job_entries:
                if job_code in job_entry.lower():
                    full = os.path.join(year_path, job_entry)
                    if os.path.isdir(full) and full not in matches:
                        matches.append(full)
    return matches


def find_job_folders(job_code, status_callback=None, cancel_event=None):
    job_code = job_code.strip().lower()
    if not job_code:
        return []

    matches = search_known_containers(job_code, cancel_event=cancel_event)
    if matches or (cancel_event and cancel_event.is_set()):
        return matches

    if status_callback:
        status_callback("Not found in known folders - running a deeper search...")

    for drive in DRIVES:
        if cancel_event and cancel_event.is_set():
            break
        root = drive + "\\"
        if not os.path.isdir(root):
            continue
        _search_recursive(root, job_code, MAX_SEARCH_DEPTH, matches, cancel_event)
    return matches


def _search_recursive(path, job_code, depth_remaining, matches, cancel_event=None):
    if cancel_event and cancel_event.is_set():
        return
    try:
        entries = os.listdir(path)
    except (PermissionError, FileNotFoundError, OSError):
        return

    for entry in entries:
        if cancel_event and cancel_event.is_set():
            return
        full = os.path.join(path, entry)
        if not os.path.isdir(full):
            continue
        name_lower = entry.lower()
        if job_code in name_lower:
            if full not in matches:
                matches.append(full)
            continue  
        if depth_remaining > 1 and any(kw in name_lower for kw in CONTAINER_KEYWORDS):
            _search_recursive(full, job_code, depth_remaining - 1, matches, cancel_event)


def _folder_matches_keywords(name, keywords):
    name_lower = name.lower()
    return all(kw in name_lower for kw in keywords)


def find_nested_folder(base_path, level_keywords):
    current = base_path
    for keywords in level_keywords:
        try:
            entries = os.listdir(current)
        except (PermissionError, FileNotFoundError, OSError):
            return None
        next_path = None
        for entry in entries:
            full = os.path.join(current, entry)
            if os.path.isdir(full) and _folder_matches_keywords(entry, keywords):
                next_path = full
                break
        if next_path is None:
            return None
        current = next_path
    return current


def load_recents():
    if os.path.exists(RECENTS_FILE):
        try:
            with open(RECENTS_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def save_recent(job_code, path):
    recents = load_recents()
    recents = [r for r in recents if r["path"] != path]
    recents.insert(0, {"code": job_code, "path": path})
    recents = recents[:MAX_RECENTS]
    try:
        with open(RECENTS_FILE, "w") as f:
            json.dump(recents, f, indent=2)
    except OSError:
        pass


class JobFinderApp:
    def __init__(self, root):
        self.root = root
        root.title("Job Finder")
        root.geometry("460x680")  
        root.minsize(400, 480)

        pad = {"padx": 8, "pady": 4}

        # --- Search bar ---
        search_frame = ttk.Frame(root)
        search_frame.pack(fill="x", **pad)

        ttk.Label(search_frame, text="Job code:").pack(side="left")
        self.search_var = tk.StringVar()
        entry = ttk.Entry(search_frame, textvariable=self.search_var)
        entry.pack(side="left", fill="x", expand=True, padx=(6, 6))
        entry.bind("<Return>", lambda e: self.do_search())
        entry.focus()

        self.search_button = ttk.Button(search_frame, text="Search", command=self.do_search)
        self.search_button.pack(side="left")

        self.cancel_button = ttk.Button(search_frame, text="Cancel", command=self.cancel_search, state="disabled")
        self.cancel_button.pack(side="left", padx=(4, 0))

        # --- Options Panel ---
        options_frame = ttk.Frame(root)
        options_frame.pack(fill="x", padx=8, pady=2)

        self.jump_to_asbuilt = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            options_frame,
            text="Jump straight to GPS Final As-Builts folder when found",
            variable=self.jump_to_asbuilt,
        ).pack(anchor="w")

        self.always_on_top = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            options_frame,
            text="Keep this window on top of others",
            variable=self.always_on_top,
            command=lambda: self.root.attributes("-topmost", self.always_on_top.get()),
        ).pack(anchor="w")

        self.filter_dwg = tk.BooleanVar(value=True)  
        ttk.Checkbutton(
            options_frame,
            text="Filter Explorer to .dwg files only when opening",
            variable=self.filter_dwg,
        ).pack(anchor="w")

        # --- Status label ---
        self.status_var = tk.StringVar(value="Type a job code, or load survey files.")
        ttk.Label(root, textvariable=self.status_var, foreground="#555").pack(fill="x", padx=8, pady=4)

        # --- DRAGGABLE SPLIT AREA ---
        self.pane = ttk.PanedWindow(root, orient="vertical")
        self.pane.pack(fill="both", expand=True, padx=8, pady=4)

        # Top Pane Widget Frame (Survey Section)
        survey_container = ttk.Frame(self.pane)
        
        survey_header = ttk.Frame(survey_container)
        survey_header.pack(fill="x", pady=(0, 2))
        ttk.Label(survey_header, text="Survey Files (.job)", font=("", 9, "bold")).pack(side="left")
        
        ttk.Button(survey_header, text="🔄 Load Desktop", width=14, command=self.load_survey_from_desktop).pack(side="right", padx=(2, 0))
        ttk.Button(survey_header, text="➕ Add Files...", width=12, command=self.browse_survey_files).pack(side="right")

        survey_list_frame = ttk.Frame(survey_container)
        survey_list_frame.pack(fill="both", expand=True)
        
        self.survey_listbox = tk.Listbox(survey_list_frame, exportselection=False)
        self.survey_listbox.pack(side="left", fill="both", expand=True)
        self.survey_listbox.bind("<Double-Button-1>", lambda e: self.open_paired_dwg())
        self.survey_listbox.bind("<Return>", lambda e: self.open_paired_dwg())
        
        survey_scroll = ttk.Scrollbar(survey_list_frame, orient="vertical", command=self.survey_listbox.yview)
        survey_scroll.pack(side="right", fill="y")
        self.survey_listbox.config(yscrollcommand=survey_scroll.set)

        # Bottom Pane Widget Frame (Search Results Section)
        results_container = ttk.Frame(self.pane)
        
        ttk.Label(results_container, text="Search Results", font=("", 9, "bold")).pack(anchor="w", pady=(2, 2))
        
        list_frame = ttk.Frame(results_container)
        list_frame.pack(fill="both", expand=True)

        self.listbox = tk.Listbox(list_frame, exportselection=False)
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<Double-Button-1>", lambda e: self.open_selected())
        self.listbox.bind("<Return>", lambda e: self.open_selected())

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=scrollbar.set)

        # Add both sections into the draggable interface pane
        self.pane.add(survey_container, weight=1)
        self.pane.add(results_container, weight=1)

        self._survey_job_mappings = {}  
        self._current_paths = []  

        # --- Quick access ---
        ttk.Separator(root).pack(fill="x", padx=8, pady=(4, 4))
        ttk.Label(root, text="Quick access", font=("", 9, "bold")).pack(anchor="w", padx=8)

        quick_frame = ttk.Frame(root)
        quick_frame.pack(fill="x", padx=8, pady=(2, 8))
        self._quick_link_filter_vars = {}
        for link in QUICK_LINKS:
            if link.get("year_picker"):
                row = ttk.Frame(quick_frame)
                row.pack(fill="x", pady=2)
                var = tk.BooleanVar(value=True)  
                self._quick_link_filter_vars[link["label"]] = var
                btn = ttk.Button(
                    row,
                    text=link["label"],
                    command=lambda l=link, v=var: self.open_year_picker(l, v),
                )
                btn.pack(side="left", fill="x", expand=True)
                ext = link.get("default_filter", "")
                ttk.Checkbutton(row, text=f"Filter {ext}", variable=var).pack(side="left", padx=(4, 0))
            else:
                btn = ttk.Button(
                    quick_frame,
                    text=link["label"],
                    command=lambda l=link: self.open_quick_link(l),
                )
                btn.pack(fill="x", pady=2)

    def current_filter(self):
        return DWG_FILTER_QUERY if self.filter_dwg.get() else None

    def resolve_target_path(self, job_path):
        if self.jump_to_asbuilt.get():
            nested = find_nested_folder(job_path, GPS_ASBUILT_PATH)
            if nested:
                return nested
        return job_path

    # --- Strict Code Parsing & Survey Management ---
    def extract_job_code(self, filename):
        match = re.search(r"\d{2}-\d{3}", filename)
        return match.group(0) if match else None

    def load_survey_from_desktop(self):
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        target_dir = None
        
        for folder_name in [".job", "job", ".JOB", "JOB"]:
            potential_path = os.path.join(desktop, folder_name)
            if os.path.isdir(potential_path):
                target_dir = potential_path
                break
                
        if not target_dir:
            messagebox.showinfo("Job Finder", "Could not find a '.job' or 'job' folder on your Desktop.\nUse 'Add Files...' instead.")
            return
            
        job_files = glob.glob(os.path.join(target_dir, "*.job")) + glob.glob(os.path.join(target_dir, "*.JOB"))
        if not job_files:
            self.status_var.set(f"No .job files found inside Desktop/{os.path.basename(target_dir)}.")
            return
            
        self.process_survey_files(job_files)

    def browse_survey_files(self):
        files = filedialog.askopenfilenames(
            title="Select Survey Files",
            filetypes=[("Trimble Job Files", "*.job;*.JOB"), ("All Files", "*.*")]
        )
        if files:
            self.process_survey_files(files)

    def process_survey_files(self, file_paths):
        self.survey_listbox.delete(0, "end")
        self._survey_job_mappings.clear()
        self.status_var.set("Scanning networks for stripped job codes...")
        
        seen_basenames = set()

        def survey_worker():
            for path in file_paths:
                basename = os.path.basename(path)
                
                if basename in seen_basenames:
                    continue
                seen_basenames.add(basename)

                job_code = self.extract_job_code(basename)
                if not job_code:
                    display_text = f"⚠ {basename}  ➔  [No valid XX-XXX code found]"
                    self.root.after(0, lambda text=display_text: self.survey_listbox.insert("end", text))
                    continue

                matches = find_job_folders(job_code)
                if matches:
                    self._survey_job_mappings[basename] = matches[0]
                    display_text = f"✅ {basename}  ➔  [Found: {os.path.basename(matches[0])}]"
                else:
                    display_text = f"❌ {basename}  ➔  [No folder found for {job_code}]"
                
                self.root.after(0, lambda text=display_text: self.survey_listbox.insert("end", text))
            
            self.root.after(0, lambda: self.status_var.set("All survey files processed."))

        threading.Thread(target=survey_worker, daemon=True).start()

    def open_paired_dwg(self):
        sel = self.survey_listbox.curselection()
        if not sel:
            return
        
        list_item = self.survey_listbox.get(sel[0])
        if "➔" in list_item:
            basename = list_item.split("➔")[0].replace("✅", "").replace("❌", "").replace("⚠", "").strip()
        else:
            return

        job_path = self._survey_job_mappings.get(basename)
        if not job_path:
            messagebox.showerror("Job Finder", f"Could not map a valid folder for: {basename}")
            return
            
        try:
            target = self.resolve_target_path(job_path)
            opened = open_folder(target, filter_query=DWG_FILTER_QUERY)
            if opened:
                self.status_var.set(f"Opened target drawing folder for {basename}")
        except Exception as e:
            messagebox.showerror("Job Finder", f"Error trying to open folder:\n{e}")

    # --- Search Engine Mechanics ---
    def do_search(self):
        code = self.search_var.get().strip()
        if not code:
            self.status_var.set("Enter a job code first.")
            return

        self.status_var.set("Searching... (this can take a bit on slow network drives)")
        self.search_button.config(state="disabled")
        self.cancel_button.config(state="normal")
        self.root.update_idletasks()

        self.cancel_event = threading.Event()
        cancel_event = self.cancel_event

        def worker():
            try:
                def status_update(msg):
                    self.root.after(0, lambda: self.status_var.set(msg))
                matches = find_job_folders(code, status_callback=status_update, cancel_event=cancel_event)
                error = None
            except Exception as e:
                matches = []
                error = str(e)
            self.root.after(0, lambda: self._on_search_done(code, matches, error, cancel_event.is_set()))

        threading.Thread(target=worker, daemon=True).start()

    def cancel_search(self):
        if hasattr(self, "cancel_event"):
            self.cancel_event.set()
        self.status_var.set("Cancelling...")
        self.cancel_button.config(state="disabled")

    def _on_search_done(self, code, matches, error, was_cancelled):
        self.search_button.config(state="normal")
        self.cancel_button.config(state="disabled")

        if error:
            self.status_var.set(f"Search hit an error: {error}")
            return

        for m in reversed(matches):
            if m not in self._current_paths:
                self.listbox.insert(0, m)
                self._current_paths.insert(0, m)

        if was_cancelled:
            self.status_var.set(f"Search cancelled. Matches shown.")
            return

        if not matches:
            self.status_var.set(f"No matches found for '{code}'.")
            return

        if len(matches) == 1:
            self.status_var.set("1 match found - opening...")
            try:
                target = self.resolve_target_path(matches[0])
                opened = open_folder(target, filter_query=self.current_filter())
            except Exception as e:
                messagebox.showerror("Job Finder", f"Found a match but couldn't open it:\n{e}")
                return
            if opened:
                save_recent(code, matches[0])
                if target != opened:
                    self.status_var.set(f"As-Builts folder not found, opened: {opened}")
                else:
                    self.status_var.set(f"Opened: {opened}")
            else:
                self.status_var.set("Found a match but couldn't open it.")
            return

        self.status_var.set(f"{len(matches)} matches added to the list below.")

    def open_selected(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        path = self._current_paths[sel[0]]
        try:
            target = self.resolve_target_path(path)
            opened = open_folder(target, filter_query=self.current_filter())
        except Exception as e:
            messagebox.showerror("Job Finder", f"Couldn't open:\n{path}\n\nError: {e}")
            return
        if opened:
            save_recent(self.search_var.get().strip(), path)
        else:
            messagebox.showerror("Job Finder", f"Couldn't open:\n{path}")

    def open_year_picker(self, link, filter_var):
        base = fill_year(link["path"])
        if not os.path.isdir(base):
            messagebox.showerror("Job Finder", f"Couldn't find:\n{base}")
            return
        try:
            entries = sorted(os.listdir(base))
        except (PermissionError, FileNotFoundError, OSError) as e:
            messagebox.showerror("Job Finder", f"Couldn't read:\n{base}\n\nError: {e}")
            return

        year_folders = [
            e for e in entries
            if os.path.isdir(os.path.join(base, e)) and any(kw in e.lower() for kw in CONTAINER_KEYWORDS)
        ]
        if not year_folders:
            messagebox.showinfo("Job Finder", f"No year folders found in:\n{base}")
            return

        menu = tk.Menu(self.root, tearoff=0)
        for folder_name in year_folders:
            full = os.path.join(base, folder_name)
            menu.add_command(
                label=folder_name,
                command=lambda f=full: self._open_year_choice(f, link, filter_var),
            )
        x = self.root.winfo_pointerx()
        y = self.root.winfo_pointery()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _open_year_choice(self, path, link, filter_var):
        filter_query = link.get("default_filter") if filter_var.get() else None
        try:
            opened = open_folder(path, filter_query=filter_query)
        except Exception as e:
            messagebox.showerror("Job Finder", f"Couldn't open:\n{path}\n\nError: {e}")
            return
        if opened:
            suffix = f" (filtered to {filter_query})" if filter_query else ""
            self.status_var.set(f"Opened: {opened}{suffix}")
        else:
            messagebox.showerror("Job Finder", f"Couldn't open:\n{path}")

    def open_quick_link(self, link):
        path = fill_year(link["path"])
        existing = resolve_existing_dir(path)
        if not existing:
            messagebox.showerror("Job Finder", f"Couldn't find or open:\n{path}")
            return
            
        try:
            # Spawning a brand-new Explorer instance clears active background search-ms query bottlenecks
            subprocess.Popen(f'explorer.exe "{existing}"', shell=True)
            if existing != path:
                self.status_var.set(f"Exact folder not found, opened clean instance: {existing}")
            else:
                self.status_var.set(f"Opened clean instance: {existing}")
        except Exception as e:
            messagebox.showerror("Job Finder", f"Subprocess launch failed:\n{e}")


if __name__ == "__main__":
    root = tk.Tk()

    def show_error(exc_type, exc_value, exc_tb):
        messagebox.showerror("Job Finder - unexpected error", f"{exc_type.__name__}: {exc_value}")

    root.report_callback_exception = show_error

    app = JobFinderApp(root)
    root.mainloop()
