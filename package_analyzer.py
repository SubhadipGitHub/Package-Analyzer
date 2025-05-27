import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog, simpledialog,Toplevel, Text, Scrollbar, BOTH, RIGHT, Y
import threading
from threading import Thread
import json
import re
import csv
from collections import defaultdict
from PIL import Image, ImageTk
import os
import oracledb
import getpass
import time
import textwrap

DEBUG = True  # Set False to disable debug logs
username = getpass.getuser()
fetch_rows = 50
cancel_flag = False
current_connection = None  # Tracks the active DB connection


def debug_log(msg):
    if DEBUG:
        print("[DEBUG]", msg)

# ---------------- Constants and Globals ----------------
OPERATION_PATTERNS = {
    "SELECT": re.compile(r"\bSELECT\b.*?\bFROM\b\s+(?:\w+\.)?([a-zA-Z0-9_]+)", re.IGNORECASE | re.DOTALL),
    "INSERT": re.compile(r"\bINSERT\s+INTO\s+(?:\w+\.)?([a-zA-Z0-9_]+)", re.IGNORECASE),
    "UPDATE": re.compile(r"\bUPDATE\s+(?:\w+\.)?([a-zA-Z0-9_]+)\s+SET\b", re.IGNORECASE),
    "DELETE": re.compile(r"\bDELETE\s+FROM\s+(?:\w+\.)?([a-zA-Z0-9_]+)", re.IGNORECASE),
}

DB_USER = DB_PASS = DSN = None

# Get the directory where the current script resides
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------- Configuration I/O ----------------
def load_config():
    try:
        with open("config.json", "r") as f:
            cfg = json.load(f)

            # Clear existing text first
            username_entry.delete(0, tk.END)
            password_entry.delete(0, tk.END)
            dsn_entry.delete(0, tk.END)

            # Insert loaded values
            username_entry.insert(0, cfg.get("db_user", ""))
            password_entry.insert(0, cfg.get("db_password", ""))
            dsn_entry.insert(0, cfg.get("dsn", ""))

            return cfg.get("db_user"), cfg.get("db_password"), cfg.get("dsn")
    except Exception as e:
        debug_log(f"[ERROR] Failed to load config: {e}")
        return "", "", ""

def save_config():
    user = username_entry.get()
    password = password_entry.get()
    dsn = dsn_entry.get()
    with open("config.json", "w") as f:
        json.dump({
            "db_user": user,
            "db_password": password,
            "dsn": dsn
        }, f, indent=4)

# ---------------- Database Operations ----------------
def connect():
    global DB_USER, DB_PASS, DSN, current_connection
    DB_USER, DB_PASS, DSN = load_config()
    try:
        conn = oracledb.connect(user=DB_USER, password=DB_PASS, dsn=DSN)
        current_connection = conn  # Track active connection

        # Query for DB name (service_name)
        cursor = conn.cursor()
        cursor.execute("SELECT SYS_CONTEXT('USERENV','SERVICE_NAME') FROM dual")
        db_name = cursor.fetchone()[0]

        return conn, (DB_USER, db_name)
    except Exception as e:
        messagebox.showerror("Connection Failed", str(e))
        return None, (None, None)


def connect_worker():
    def stop_loader():
        progress_bar1.stop()
        progress_bar1.pack_forget()

    try:
        save_config()
        conn, (username, dbname) = connect()
        if conn:
            def update_ui():
                footer_label.config(text=f"Connected as {username} @ {dbname}", foreground="green")
                for i in range(1, 5):
                    notebook.tab(i, state='normal')
                notebook.select(1)
                
                # Disable inputs
                username_entry.config(state="disabled")
                password_entry.config(state="disabled")
                dsn_entry.config(state="disabled")
                connect_btn.config(state="disabled")
                disconnect_btn.config(state="normal")  # Enable Disconnect button
            app.after(0, update_ui)
    except Exception as e:
        err_msg = str(e)
        app.after(0, lambda: messagebox.showerror("Error", err_msg))
    finally:
        app.after(0, stop_loader)

def cancel_operation():
    global cancel_flag
    cancel_flag = True


def connect_callback():
    def start_loader():
        progress_bar1.pack(fill='x', padx=10, pady=(0, 10))
        progress_bar1.start()

    start_loader()
    run_in_thread(connect_worker)

def disconnect():
    global current_connection
    try:
        if current_connection:
            current_connection.close()
            current_connection = None

            # Re-enable inputs
            username_entry.config(state="normal")
            password_entry.config(state="normal")
            dsn_entry.config(state="normal")
            connect_btn.config(state="normal")
            disconnect_btn.config(state="disabled")

            footer_label.config(text="Disconnected", foreground="red")

            # Disable tabs again
            for i in range(1, 5):
                notebook.tab(i, state='disabled')
            notebook.select(0)  # Go back to connection tab

            messagebox.showinfo("Disconnected", "Database connection closed.")
    except Exception as e:
        messagebox.showerror("Error", f"Failed to disconnect: {str(e)}")


def fetch_query(query, params=None, on_progress=None, batch_size=1000, on_cancel=None):
    global cancel_flag
    cancel_flag = False
    results = []

    conn, _ = connect()
    if not conn:
        raise Exception("Database connection failed.")

    try:
        with conn.cursor() as cursor:
            cursor.execute(query, params or [])
            columns = [desc[0] for desc in cursor.description]

            while True:
                if cancel_flag:
                    if on_cancel:
                        on_cancel()
                    break
                rows = cursor.fetchmany(batch_size)
                if not rows:
                    break
                results.extend(rows)
                if on_progress:
                    on_progress(len(results))

            return columns, results
    finally:
        conn.close()

    
def execute_query(query, params=None):
    global cancel_flag
    cancel_flag = False

    conn, _ = connect()
    if not conn:
        raise Exception("Database connection failed.")

    try:
        with conn.cursor() as cursor:
            cursor.execute(query, params or [])
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def show_progress_dialog(title="Executing...", message="Please wait..."):
    progress_win = tk.Toplevel()
    progress_win.title(title)
    progress_win.geometry("300x100")
    progress_win.grab_set()
    progress_win.transient()
    progress_win.resizable(False, False)

    label = ttk.Label(progress_win, text=message)
    label.pack(pady=10)

    cancel_btn = ttk.Button(progress_win, text="Cancel", command=cancel_operation)
    cancel_btn.pack()

    return progress_win

def run_fetch_in_background(query):
    progress_win = show_progress_dialog("Fetching...", "Running query...")

    def worker():
        try:
            cols, rows = fetch_query(
                query,
                on_progress=lambda count: progress_win.title(f"Fetched {count} rows..."),
                on_cancel=lambda: messagebox.showinfo("Cancelled", "Query cancelled.")
            )
            # Update UI with result (in main thread)
            def update_ui():
                progress_win.destroy()
                display_query_results(cols, rows)
            app.after(0, update_ui)

        except Exception as e:
            progress_win.destroy()
            messagebox.showerror("Error", f"Query failed:\n{str(e)}")

    Thread(target=worker, daemon=True).start()

def display_query_results(columns, rows):
    result_tree.delete(*result_tree.get_children())
    result_tree["columns"] = columns
    for col in columns:
        result_tree.heading(col, text=col)
    for row in rows:
        result_tree.insert("", tk.END, values=row)
    result_label.config(text=f"✅ Query Result – {len(rows)} rows")

def get_schema_objects(schema, obj_type):
    return [row[0] for row in fetch_query(
        "SELECT object_name FROM all_objects WHERE owner = UPPER(:1) AND object_type = :2 ORDER BY object_name",
        [schema, obj_type]
    )[1]]

def get_all_schemas():
    try:
        return [row[0] for row in fetch_query("SELECT username FROM all_users ORDER BY username")[1]]
    except:
        return []

def get_tables(schema):
    return [row[0] for row in fetch_query(
        "SELECT table_name FROM all_tables WHERE owner = UPPER(:1) and TABLE_NAME != 'MY_SQL_SHEETS' ORDER BY table_name", [schema]
    )[1]]

def get_sequences(schema):
    return [row[0] for row in fetch_query(
        "SELECT sequence_name FROM all_sequences WHERE sequence_owner = UPPER(:1) ORDER BY sequence_name", [schema]
    )[1]]

def get_package_source(schema, name):
    return fetch_query("""
        SELECT line, text FROM all_source 
        WHERE owner = UPPER(:1) AND name = UPPER(:2) AND type IN ('PACKAGE BODY', 'PROCEDURE', 'FUNCTION')
        ORDER BY line
    """, [schema, name])[1]

# ---------------- Text Analysis Helpers ----------------
def analyze_table():
    output = []
    schema = schema_entry_table.get().strip()
    table_name = table_entry.get().strip()

    debug_log(f"[INPUT] Schema: {schema}")
    debug_log(f"[INPUT] Table: {table_name}")

    if not schema or not table_name:
        messagebox.showwarning("Input Error", "Please enter both schema and table name.")
        return

    try:
        # --- Columns ---
        debug_log("[STEP] Fetching columns")
        cols = fetch_query("""
            SELECT column_name, data_type, data_length 
            FROM all_tab_columns 
            WHERE table_name = UPPER(:1) AND owner = UPPER(:2) 
            ORDER BY column_id
        """, [table_name, schema])[1]
        debug_log(f"[RESULT] Columns found: {len(cols)}")
        output.append("Columns:")
        output.extend(f"  - {c[0]} ({c[1]} [{c[2]}])" for c in cols)

        # --- Constraints ---
        debug_log("[STEP] Fetching constraints")
        cons = fetch_query("""
            SELECT ac.constraint_name, ac.constraint_type, acc.column_name
            FROM all_constraints ac
            JOIN all_cons_columns acc ON ac.constraint_name = acc.constraint_name AND ac.owner = acc.owner
            WHERE ac.table_name = UPPER(:1) AND ac.owner = UPPER(:2)
            ORDER BY ac.constraint_name, acc.position
        """, [table_name, schema])[1]
        debug_log(f"[RESULT] Constraints found: {len(cons)}")
        output.append("\nConstraints:")
        output.extend(f"  - {c[0]} ({c[1]}) [{c[2]}]" for c in cons)

        # --- Indexes ---
        debug_log("[STEP] Fetching indexes")
        idxs = fetch_query("""
            SELECT ai.index_name, ai.uniqueness, aic.column_name
            FROM all_indexes ai
            JOIN all_ind_columns aic ON ai.index_name = aic.index_name AND ai.table_owner = aic.table_owner
            WHERE ai.table_name = UPPER(:1) AND ai.owner = UPPER(:2)
            ORDER BY ai.index_name, aic.column_position
        """, [table_name, schema])[1]
        debug_log(f"[RESULT] Indexes found: {len(idxs)}")
        output.append("\nIndexes:")
        output.extend(f"  - {i[0]} ({i[1]}) [{i[2]}]" for i in idxs)

        # --- Sequences Used ---
        debug_log("[STEP] Checking sequences used")
        output.append("\nSequences Used:")
        used_sequences = set()
        trigger_seq_map = {}
        col_seq_map = {}

        # --- From triggers ---
        debug_log("[STEP] Analyzing triggers for sequences")
        triggers = fetch_query("""
            SELECT trigger_name, trigger_body
            FROM all_triggers
            WHERE table_owner = UPPER(:1)
            AND table_name = UPPER(:2)
        """, [schema, table_name])[1]
        debug_log(f"[RESULT] Triggers found: {len(triggers)}")
        for trigger_name, trigger_body in triggers:
            if trigger_body:
                try:
                    body_str = str(trigger_body)
                    matches = re.findall(r"(\w+)\.(NEXTVAL|CURRVAL)", body_str.upper())
                    debug_log(f"[TRIGGER] {trigger_name} uses sequences: {matches}")
                    for seq_name, _ in matches:
                        used_sequences.add(seq_name)
                        trigger_seq_map.setdefault(seq_name, []).append(trigger_name)
                except Exception as e:
                    debug_log(f"[ERROR] Failed to read trigger {trigger_name}: {e}")

        # --- From default column values ---
        debug_log("[STEP] Analyzing default column values for sequences")
        defaults = fetch_query("""
            SELECT column_name, data_default
            FROM all_tab_columns 
            WHERE table_name = UPPER(:1)
            AND owner = UPPER(:2)
        """, [table_name, schema])[1]
        for col, default in defaults:
            if default:
                matches = re.findall(r"(\w+)\.NEXTVAL", default, re.IGNORECASE)
                if matches:
                    debug_log(f"[COLUMN] {col} default uses sequences: {matches}")
                for seq in matches:
                    seq_name = seq.upper()
                    used_sequences.add(seq_name)
                    col_seq_map[seq_name] = col

        if not used_sequences:
            debug_log("[RESULT] No sequences found")
            output.append("  - No sequences detected.")
        else:
            for seq in sorted(used_sequences):
                debug_log(f"[STEP] Fetching info for sequence: {seq}")
                seq_info = fetch_query("""
                    SELECT sequence_name, increment_by, last_number
                    FROM all_sequences
                    WHERE sequence_owner = UPPER(:1)
                      AND sequence_name = UPPER(:2)
                """, [schema, seq])[1]
                if seq_info:
                    sname, incr, last = seq_info[0]
                    col = col_seq_map.get(seq, "Unknown")
                    output.append(f"  - {sname} -> Column: {col}, Current Value: {last}, Next Value: {last + incr}, Increment: {incr}")
                    debug_log(f"[SEQUENCE] {sname} -> Current: {last}, Next: {last + incr}, Increment: {incr}")
                else:
                    debug_log(f"[WARN] Sequence {seq} not found in all_sequences")
                    output.append(f"  - {seq} -> Not found in all_sequences")

        # --- Usage in packages ---
        debug_log("[STEP] Analyzing usage in packages")
        usage = analyze_table_usage(schema, table_name)
        debug_log(f"[RESULT] Usage found in {len(usage)} entries")
        output.append("\nUsage in Packages:")
        pkgs = set()
        for (tbl, op, pkg), info in sorted(usage.items()):
            pkgs.add(pkg)
            output.append(f"  - Package: {pkg}, Operation: {op}, Lines: {', '.join(map(str, info['lines']))}")
        output.append(f"\nTotal packages using {table_name}: {len(pkgs)}")
        
        # --- Record count ---
        count_query = f"SELECT COUNT(*) FROM {schema}.{table_name}"
        debug_log(f"[STEP] Executing count query: {count_query}")
        count = fetch_query(count_query)[1][0][0]
        debug_log(f"[RESULT] Record count: {count}")
        output.append(f"\nTotal Records: {count}")

    except Exception as e:
        debug_log(f"[ERROR] Exception during analysis: {e}")
        output.append(f"\nError retrieving table details: {str(e)}")

    # --- Final debug log output ---
    debug_log("[STEP] Final output lines:")
    for line in output:
        debug_log(line)

    return output

def analyze_table_usage(schema, table_name):
    results = defaultdict(lambda: {"count": 0, "lines": [], "files": set()})
    debug_log(f"Analyzing usage of table {schema}.{table_name} in packages")
    
    for pkg in get_schema_objects(schema, 'PACKAGE'):
        debug_log(f"Checking package: {pkg}")
        src_lines = get_package_source(schema, pkg)
        if not src_lines:
            debug_log(f"No source found for package {pkg}")
            continue
        
        for line_number, line_text in src_lines:
            clean_line = re.sub(r"--.*", "", line_text).strip()
            for op, pattern in OPERATION_PATTERNS.items():
                for match in pattern.finditer(clean_line):
                    matched_table = match.group(1).split('.')[-1].upper()
                    if matched_table == table_name.upper():
                        key = (matched_table, op, pkg)
                        results[key]["count"] += 1
                        results[key]["lines"].append(line_number)
                        results[key]["files"].add(pkg)
                        debug_log(f"Match found in {pkg}: line {line_number}, op {op}")
    
    debug_log(f"Total matches found: {sum(len(v['lines']) for v in results.values())}")
    return results

def analyze_table_worker():
    def stop_loader():
        progress_bar1.stop()
        progress_bar1.pack_forget()

    try:
        output = analyze_table()
        def update_ui():
            table_output.config(state=tk.NORMAL)      # Enable editing temporarily
            table_output.delete('1.0', tk.END)
            if output:
                table_output.insert(tk.END, "\n".join(output))
            table_output.config(state=tk.DISABLED)    # Disable editing again
        app.after(0, update_ui)
    except Exception as e:
        app.after(0, lambda: messagebox.showerror("Error", str(e)))
    finally:
        app.after(0, stop_loader)

def analyze_table_callback():
    def start_loader():
        progress_bar1.pack(fill='x', padx=10, pady=(0, 10))
        progress_bar1.start()

    start_loader()
    run_in_thread(analyze_table_worker)

def list_packages():
    schema = schema_entry_pkg_list.get().strip()
    debug_log(f"[INPUT] Schema for listing packages: '{schema}'")

    if not schema:
        messagebox.showwarning("Input Error", "Please enter schema name.")
        return []

    try:
        query = """
            SELECT object_name, status, created 
            FROM all_objects 
            WHERE object_type = 'PACKAGE' 
            AND owner = UPPER(:1)
            ORDER BY object_name
        """
        rows = fetch_query(query, [schema])[1]
        packages = [row[0] for row in rows]
        debug_log(f"[RESULT] Packages found: {rows}")
        return rows

    except Exception as e:
        debug_log(f"[ERROR] Failed to list packages: {e}")
        messagebox.showerror("Database Error", f"Failed to list packages: {e}")
        return []
    
def list_packages_worker():
    def stop_loader():
        progress_bar2.stop()
        progress_bar2.pack_forget()

    try:
        output = list_packages()
        def update_ui():
            package_tree.delete(*package_tree.get_children())  # Clear old entries
            if output:
                for name, status, created in output:
                    package_tree.insert("", "end", values=(name, status, created.strftime("%Y-%m-%d %H:%M:%S")))
        app.after(0, update_ui)
    except Exception as e:
        app.after(0, lambda: messagebox.showerror("Error", str(e)))
    finally:
        app.after(0, stop_loader)

def list_packages_callback():
    def start_loader():
        progress_bar2.pack(fill='x', padx=10, pady=(0, 10))
        progress_bar2.start()

    start_loader()
    run_in_thread(list_packages_worker)

def run_in_thread(func, *args):
    """Run a function in a thread, used to keep UI responsive."""
    threading.Thread(target=func, args=args, daemon=True).start()

def extract_package_content():
    schema = schema_entry_package.get()
    pkg = package_entry.get()
    if not schema or not pkg:
        messagebox.showwarning("Missing Input", "Please enter both schema and package name.")
        return
    try:
        # Fetch source with line numbers
        query = """
            SELECT line, text FROM all_source 
            WHERE owner = UPPER(:1) AND name = UPPER(:2) AND type IN ('PACKAGE BODY')
            ORDER BY TYPE,line
        """
        rows = fetch_query(query, [schema, pkg])[1]
        if not rows:
            raise Exception("No source found for package.")
        return rows
    except Exception as e:
        messagebox.showerror("Error", str(e))

def extract_package_content_worker():
    def stop_loader():
        progress_bar3.stop()
        progress_bar3.pack_forget()

    try:
        output = extract_package_content()
        def update_ui():
            pkg_text.config(state=tk.NORMAL)      # Enable editing temporarily
            pkg_text.delete("1.0", tk.END)
            if output:              
                pkg_text.tag_configure("highlight", background="#ffffcc")
                # Insert with line numbers and highlight procedures/functions
                for line_num, text in output:
                    numbered_line = f"{line_num:>4}: {text.rstrip()}\n"
                    pkg_text.insert(tk.END, numbered_line)
                    if re.search(r"\b(PROCEDURE|FUNCTION)\b", text, re.IGNORECASE):
                        line_start = f"{line_num}.0"
                        line_end = f"{line_num}.end"
                        pkg_text.tag_add("highlight", line_start, line_end)
            pkg_text.config(state=tk.DISABLED)    # Disable editing again
        app.after(0, update_ui)
    except Exception as e:
        app.after(0, lambda: messagebox.showerror("Error", str(e)))
    finally:
        app.after(0, stop_loader)

def extract_package_content_callback():
    def start_loader():
        progress_bar3.pack(fill='x', padx=10, pady=(0, 10))
        progress_bar3.start()

    start_loader()
    run_in_thread(extract_package_content_worker)

def extract_table_operations(lines):
    operations = defaultdict(list)

    for line_num, line in lines:
        for op, pattern in OPERATION_PATTERNS.items():
            for match in pattern.finditer(line):
                table_name = match.group(1)
                operations[table_name].append((op, line_num, line.strip()))

    return operations

def detect_dynamic_sql_blocks(source_lines):
    dyn_blocks = []
    block = []
    capturing = False

    for line_num, line in source_lines:
        if not capturing and re.search(r":=\s*('|q'|\")", line):
            capturing = True
            block = [(line_num, line)]
        elif capturing:
            block.append((line_num, line))
            if re.search(r"('|q'|\");", line):
                capturing = False
                dyn_blocks.append(block)
                block = []

    return dyn_blocks

def extract_tables_from_block(block):
    sql = " ".join(line for _, line in block)
    tables = []
    for op, pattern in OPERATION_PATTERNS.items():
        for match in pattern.finditer(sql):
            tables.append((op, match.group(1)))
    return tables

def process_dynamic_sql(source_lines):
    tables = defaultdict(list)
    blocks = detect_dynamic_sql_blocks(source_lines)
    for block in blocks:
        line_start = block[0][0]
        extracted = extract_tables_from_block(block)
        for op, table in extracted:
            tables[table].append((op, line_start, " ".join(line.strip() for _, line in block)))
    return tables

def merge_operations(static_ops, dynamic_ops):
    for table, ops in dynamic_ops.items():
        static_ops[table].extend(ops)
    return static_ops

def highlight_operations(text_widget, operations):
    text_widget.tag_remove("highlight", "1.0", tk.END)
    text_widget.tag_config("highlight", background="yellow", foreground="black")

    for ops in operations.values():
        for _, line_num, _ in ops:
            text_widget.tag_add("highlight", f"{line_num}.0", f"{line_num}.end")

# ------------------- Main GUI Setup -------------------
app = tk.Tk()
app.title("Oracle ATP Analyzer")
# Open in maximized state (Windows only)
app.state('zoomed')

# Set theme
style = ttk.Style(app)
style.theme_use('default')

# ----------------- Helper GUI functions --------------------
def load_icon(path, size=(16, 16)):
    try:
        # Construct the relative path to the icons folder
        icon_path = os.path.join(BASE_DIR, "icons", path)
        print(f"Trying to load image from: {icon_path}")
        img = Image.open(icon_path).resize(size, Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(img)
    except Exception as e:
        print(f"Exception while loading image '{path}': {e}")
        return None

# Modern tab style
style.configure("TNotebook", tabposition='n')
style.configure("TNotebook.Tab", padding=[10, 5], font=('Segoe UI', 10))
style.configure("TButton", padding=6, relief="flat", background="#007ACC", foreground="white")
style.map("TButton", background=[('active', '#005F9E')])

notebook = ttk.Notebook(app)
notebook.pack(fill='both', expand=True)

# ------------------- Load Images -------------------
logo_img = load_icon("logo.jpg", size=(100, 100))
conn_icon = load_icon("db_icon.png")
table_icon = load_icon("sql_analyzer.png")
pkg_list_icon = load_icon("sql_analyzer.png")
pkg_extract_icon = load_icon("sql_analyzer.png")
sql_dev_icon = load_icon("sql_analyzer.png")

# ------------------- Tabs -------------------
tab_conn = ttk.Frame(notebook)
tab_table = ttk.Frame(notebook)
tab_pkg_list = ttk.Frame(notebook)
tab_pkg_extract = ttk.Frame(notebook)
tab_sql_editor = ttk.Frame(notebook)

if conn_icon:
    notebook.add(tab_conn, text=" Connection", image=conn_icon, compound="left")
else:
    notebook.add(tab_conn, text=" Connection")

if table_icon:
    notebook.add(tab_table, text=" Analyze Table", image=table_icon, compound="left")
else:
    notebook.add(tab_table, text=" Analyze Table")

if pkg_list_icon:
    notebook.add(tab_pkg_list, text=" Package List", image=pkg_list_icon, compound="left")
else:
    notebook.add(tab_pkg_list, text=" Package List")

if pkg_extract_icon:
    notebook.add(tab_pkg_extract, text=" Extract Content", image=pkg_extract_icon, compound="left")
else:
    notebook.add(tab_pkg_extract, text=" Extract Content")

if sql_dev_icon:
    notebook.add(tab_sql_editor, text=" SQL Editor", image=sql_dev_icon, compound="left")
else:
    notebook.add(tab_sql_editor, text=" SQL Editor")

# Disable tabs initially
notebook.tab(1, state="disabled")
notebook.tab(2, state="disabled")
notebook.tab(3, state="disabled")

# ------------------- Tab 1: Connection UI -------------------
center_frame = ttk.Frame(tab_conn, padding=30)
center_frame.place(relx=0.5, rely=0.4, anchor='center')

if logo_img:
    logo_label = tk.Label(center_frame, image=logo_img)
    logo_label.grid(row=0, column=0, columnspan=2, pady=(0, 20))

ttk.Label(center_frame, text="Username:").grid(row=1, column=0, sticky="e")
username_entry = ttk.Entry(center_frame, width=30)
username_entry.grid(row=1, column=1, padx=10, pady=5)

ttk.Label(center_frame, text="Password:").grid(row=2, column=0, sticky="e")
password_entry = ttk.Entry(center_frame, show="*", width=30)
password_entry.grid(row=2, column=1, padx=10, pady=5)

ttk.Label(center_frame, text="DSN:").grid(row=3, column=0, sticky="e")
dsn_entry = ttk.Entry(center_frame, width=30)
dsn_entry.grid(row=3, column=1, padx=10, pady=5)

connect_btn = ttk.Button(center_frame, text="Connect", command=connect_callback)
connect_btn.grid(row=4, column=1, pady=10, padx=10, sticky="e")

disconnect_btn = ttk.Button(center_frame, text="Disconnect", command=disconnect)
disconnect_btn.grid(row=5, column=1, pady=(0, 10), padx=10, sticky="e")
disconnect_btn.config(state="disabled")  # Initially disabled

# Get the connection details saved
load_config()

# ---------------- Tab 2: Analyze Table ----------------

tk.Label(tab_table, text="Enter Schema Name:").pack(pady=(5,0))
schema_entry_table = ttk.Combobox(tab_table, width=30)
schema_entry_table['values'] = get_all_schemas()  # We'll define this function below
schema_entry_table.pack(pady=(0,5))

tk.Label(tab_table, text="Enter Table Name:").pack(pady=5)
table_entry = ttk.Combobox(tab_table, width=40)
def update_table_list(*args):
    schema = schema_entry_table.get()
    if schema:
        table_entry['values'] = get_tables(schema)
schema_entry_table.bind("<<ComboboxSelected>>", update_table_list)
table_entry.pack()

analyze_btn = tk.Button(tab_table, text="Analyze Table", command=analyze_table_callback)
analyze_btn.pack(pady=5)

table_output = scrolledtext.ScrolledText(tab_table, wrap=tk.WORD, height=25)
table_output.pack(fill='both', expand=True, padx=10, pady=5)
table_output.config(state=tk.DISABLED)    # Disable editing setup

# ---------------- Tab 3: Package List ----------------

tk.Label(tab_pkg_list, text="Enter Schema Name:").pack(pady=(5,0))
schema_entry_pkg_list = ttk.Combobox(tab_pkg_list, width=30)
schema_entry_pkg_list['values'] = get_all_schemas()
schema_entry_pkg_list.pack(pady=(0,5))

refresh_btn = tk.Button(tab_pkg_list, text="Refresh Package List", command=list_packages_callback)
refresh_btn.pack(pady=5)

package_tree = ttk.Treeview(tab_pkg_list, columns=("Name", "Status", "Created"), show="headings", height=25)
package_tree.heading("Name", text="Package Name")
package_tree.heading("Status", text="Status")
package_tree.heading("Created", text="Created Date")

package_tree.column("Name", width=200)
package_tree.column("Status", width=100, anchor="center")
package_tree.column("Created", width=150)

package_tree.pack(fill="both", expand=True, padx=10, pady=5)

# ---------------- Tab 4: Extract Package Content ----------------

tk.Label(tab_pkg_extract, text="Enter Schema Name:").pack(pady=(5,0))
schema_entry_package = ttk.Combobox(tab_pkg_extract, width=30)
schema_entry_package['values'] = get_all_schemas()
schema_entry_package.pack(pady=(0,5))

tk.Label(tab_pkg_extract, text="Enter Package Name:").pack(pady=5)
package_entry = ttk.Combobox(tab_pkg_extract, width=40)
def update_package_list(*args):
    schema = schema_entry_package.get()
    if schema:
        package_entry['values'] = get_schema_objects(schema, 'PACKAGE')
schema_entry_package.bind("<<ComboboxSelected>>", update_package_list)
package_entry.pack()

analyze_pkg_btn = tk.Button(tab_pkg_extract, text="Extract Package Content", command=extract_package_content_callback)
analyze_pkg_btn.pack(pady=5)

pkg_text = scrolledtext.ScrolledText(tab_pkg_extract, wrap=tk.WORD)
pkg_text.pack(fill='both', expand=True, padx=10, pady=5)
pkg_text.config(state=tk.DISABLED)    # Disable editing setup

# ---------------- Progress Bar -----------------

style = ttk.Style()
style.theme_use("default")  # Better support for custom styles

style.configure("custom.Horizontal.TProgressbar",
                troughcolor="#E0E0E0",
                bordercolor="#D0D0D0",
                background="#0078D7",  # Modern blue
                lightcolor="#0078D7",
                darkcolor="#005A9E",
                thickness=10)

progress_bar1 = ttk.Progressbar(tab_table, style="custom.Horizontal.TProgressbar", mode='indeterminate')
progress_bar1.pack(fill='x', padx=50, pady=(0, 50))
progress_bar1.stop()  # make sure it's not running at start
progress_bar1.pack_forget()  # hide initially

progress_bar2 = ttk.Progressbar(tab_pkg_list, style="custom.Horizontal.TProgressbar", mode='indeterminate')
progress_bar2.pack(fill='x', padx=50, pady=(0, 50))
progress_bar2.stop()  # make sure it's not running at start
progress_bar2.pack_forget()  # hide initially

progress_bar3 = ttk.Progressbar(tab_pkg_extract, style="custom.Horizontal.TProgressbar", mode='indeterminate')
progress_bar3.pack(fill='x', padx=50, pady=(0, 50))
progress_bar3.stop()  # make sure it's not running at start
progress_bar3.pack_forget()  # hide initially

# ---------------- Footer Status ----------------
footer_frame = tk.Frame(app)
footer_frame.pack(fill='x', side='bottom')

footer_label = tk.Label(footer_frame, text="Not connected", anchor='w', fg="red")
footer_label.pack(fill='x', padx=5, pady=2)

# ---------------- Tab 5: SQL Editor ----------------

sql_keywords = [
    "SELECT", "FROM", "WHERE", "INSERT", "INTO", "VALUES", "UPDATE", "SET", "DELETE",
    "CREATE", "TABLE", "ALTER", "DROP", "VIEW", "INDEX", "JOIN", "LEFT", "RIGHT",
    "FULL", "OUTER", "INNER", "GROUP", "ORDER", "BY", "HAVING", "AS", "DISTINCT",
    "AND", "OR", "NOT", "IN", "IS", "NULL", "LIKE", "BETWEEN", "CASE", "WHEN", "THEN", "END",
    "DESC","ORDER BY","GROUP BY"
]

sql_sheets = {}
all_sql_rows = []  # List of (id, name, created_by)
current_sql_id = tk.IntVar(value=0)
sql_filter_var = tk.StringVar()

import re

def apply_syntax_highlighting(event=None):
    content = editor.get("1.0", tk.END)

    # Clear previous tags
    editor.tag_remove("keyword", "1.0", tk.END)
    editor.tag_remove("comment", "1.0", tk.END)

    # Highlight SQL keywords
    for word in sql_keywords:
        start = "1.0"
        while True:
            start = editor.search(rf"\y{word}\y", start, tk.END, regexp=True, nocase=True)
            if not start:
                break
            end = f"{start}+{len(word)}c"
            editor.tag_add("keyword", start, end)
            start = end
    editor.tag_configure("keyword", foreground="blue", font=("Courier New", 10, "bold"))

    # Highlight comments
    # -- single-line comments
    for match in re.finditer(r'--.*', content):
        start_index = f"1.0 + {match.start()} chars"
        end_index = f"1.0 + {match.end()} chars"
        editor.tag_add("comment", start_index, end_index)

    # /* multi-line comments */
    for match in re.finditer(r'/\*.*?\*/', content, re.DOTALL):
        start_index = f"1.0 + {match.start()} chars"
        end_index = f"1.0 + {match.end()} chars"
        editor.tag_add("comment", start_index, end_index)

    editor.tag_configure("comment", foreground="green", font=("Courier New", 10, "italic"))


def load_sql_sheet_names():
    return fetch_query("SELECT id, name FROM MY_SQL_SHEETS ORDER BY created_on DESC")

def load_sql_content(sql_id):
    conn, _ = connect()
    if not conn:
        raise Exception("Database connection failed.")
    
    with conn.cursor() as cursor:
        cursor.execute("SELECT content FROM MY_SQL_SHEETS WHERE id = :1", [sql_id])
        row = cursor.fetchone()
        if row:
            clob = row[0]
            content = clob.read() if hasattr(clob, 'read') else clob
            return content
    return ""

def save_new_sql(name, content, created_by=getpass.getuser()):
    try:
        conn, _ = connect()
        if not conn:
            messagebox.showerror("Database Error", "Database connection failed.")
            return False

        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM MY_SQL_SHEETS WHERE LOWER(name) = LOWER(:1)", [name])
                count = cursor.fetchone()[0]
                if count > 0:
                    messagebox.showerror("Duplicate Name", f"A SQL sheet with the name '{name}' already exists.")
                    return False

                query = "INSERT INTO MY_SQL_SHEETS (name, content, created_by) VALUES (:1, :2, :3)"
                cursor.execute(query, [name, content, created_by])
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            messagebox.showerror("Database Error", f"An error occurred while saving the SQL sheet:\n{str(e)}")
            return False
        finally:
            conn.close()
    except Exception as e:
        messagebox.showerror("Unexpected Error", f"An unexpected error occurred:\n{str(e)}")
        return False

def update_sql_sheet(sql_id, content):
    execute_query("UPDATE MY_SQL_SHEETS SET content = :1 WHERE id = :2", [content, sql_id])

def delete_sql_sheet(sql_id):
    execute_query("DELETE FROM MY_SQL_SHEETS WHERE id = :1", [sql_id])

def on_scroll(*args):
    editor.yview(*args)
    line_numbers.yview(*args)

def on_key_or_mouse(event=None):
    update_line_numbers()

def run_sql_query(query):
    debug_log("Establishing connection for SQL execution...")
    conn, _ = connect()
    if not conn:
        debug_log("Connection failed.")
        return [], [["Connection failed."]]

    try:
        with conn.cursor() as cursor:
            debug_log(f"Executing query:\n{query}")
            cursor.execute(query)
            debug_log(f"Cursor description: {cursor.description}")

            if cursor.description:
                try:
                    cols = [desc[0] for desc in cursor.description]
                except Exception as e:
                    debug_log(f"Error reading cursor.description: {e}")
                    cols = []
                rows = cursor.fetchmany(fetch_rows)
                debug_log(f"Query returned {len(rows)} rows with columns: {cols}")
                return cols, rows
            else:
                conn.commit()
                debug_log("Non-SELECT query executed and committed.")
                return [], [["Executed successfully."]]

    except Exception as e:
        debug_log(f"SQL Execution Error: {e}")
        return [], [[f"Error: {e}"]]

def refresh_sql_list():
    global all_sql_rows
    debug_log("Refreshing SQL sheet list...")
    for row in sql_tree.get_children():
        sql_tree.delete(row)
    sql_sheets.clear()

    all_sql_rows = fetch_query("SELECT id, name, created_by FROM MY_SQL_SHEETS ORDER BY created_on DESC")[1]  # getting only results
    debug_log(f"Sheets found: {all_sql_rows}")
    
    for sid, name, creator in all_sql_rows:
        sql_tree.insert("", tk.END, values=(sid, name, creator))
        sql_sheets[sid] = name

def filter_sql_tree(*args):
    search = sql_filter_var.get().lower()
    
    # Clear existing tree entries
    for row in sql_tree.get_children():
        sql_tree.delete(row)

    # Re-populate with filtered results
    for sid, name, creator in all_sql_rows:
        if search in name.lower() or search in creator.lower():
            sql_tree.insert("", tk.END, values=(sid, name, creator))
            sql_sheets[sid] = name



def on_sql_select(event=None):
    selected = sql_tree.selection()
    if selected:
        item = sql_tree.item(selected[0])
        sql_id, name, _ = item["values"]
        debug_log(f"Selected: {name} (ID: {sql_id})")

        current_sql_id.set(sql_id)
        sql_name_entry.delete(0, tk.END)
        sql_name_entry.insert(0, name)

        content = load_sql_content(sql_id)
        if content:
            debug_log(f"Loaded content: {str(content)[:100]}...")
            unsaved_label.config(text="")
        else:
            debug_log("WARNING: No content loaded or it's empty/null.")

        editor.delete("1.0", tk.END)
        editor.insert(tk.END, content if content else "")
        apply_syntax_highlighting()

def select_sql_block_in_editor(start_char_idx, end_char_idx):
    editor.tag_remove(tk.SEL, "1.0", tk.END)

    content = editor.get("1.0", tk.END)

    # Calculate start line/column
    start_line = content.count('\n', 0, start_char_idx)
    last_newline_before_start = content.rfind('\n', 0, start_char_idx)
    start_col = start_char_idx if last_newline_before_start == -1 else start_char_idx - (last_newline_before_start + 1)

    # Calculate end line/column
    end_line = content.count('\n', 0, end_char_idx)
    last_newline_before_end = content.rfind('\n', 0, end_char_idx)
    end_col = end_char_idx if last_newline_before_end == -1 else end_char_idx - (last_newline_before_end + 1)

    start_index = f"{start_line + 1}.{start_col}"
    end_index = f"{end_line + 1}.{end_col}"

    editor.tag_add(tk.SEL, start_index, end_index)
    editor.mark_set(tk.INSERT, end_index)
    editor.see(start_index)
    editor.focus_set()

def run_sql():
    query, start_pos, end_pos = extract_sql_from_cursor()

    if not query.strip():
        messagebox.showwarning("Empty Query", "No SQL found to run from cursor position.")
        return

    select_sql_block_in_editor(start_pos, end_pos)

    debug_log(f"Running trimmed query: {query}")
    result_label.config(text="⏳ Executing query...")
    tab_sql_editor.update_idletasks()  # Refresh the label immediately

    # Clear previous result
    result_tree.delete(*result_tree.get_children())
    result_tree["columns"] = []
    result_tree["show"] = "headings"
    result_label.config(text="Query Result")

    # Remove previous error highlights
    editor.tag_remove("error", "1.0", tk.END)

    # Show progress
    result_label.config(text="⏳ Executing query...")

    # Run query
    start_time = time.time()
    cols, data = run_sql_query(query.rstrip(';').strip()) # Strip ; while running query in DB
    end_time = time.time()
    elapsed = end_time - start_time

    row_count = len(data)

    # Check for errors
    if len(cols) == 0 and data and data[0][0].startswith("Error:"):
        error_msg = data[0][0]
        result_label.config(text="❌ Error occurred")
        highlight_error_block(query)  # Pass the query to highlight here
        show_error_popup(error_msg)
        return

    # Display results
    if cols:
        result_tree["columns"] = cols
        for col in cols:
            result_tree.heading(col, text=col)
        for row in data:
            result_tree.insert("", tk.END, values=row)
        if row_count == 50:
            result_label.config(text=f"✅ Showing first 50 rows – {elapsed:.2f}s (limited)")
        else:
            result_label.config(text=f"✅ Query Result – {row_count} rows in {elapsed:.2f}s")

    else:
        # For DML statements like INSERT/UPDATE
        status = data[0][0] if data else "Query completed"
        result_tree["columns"] = ["Status"]
        result_tree.heading("Status", text="Status")
        result_tree.insert("", tk.END, values=[status])
        result_label.config(text=f"✅ {status} – completed in {elapsed:.2f}s")


def highlight_error_block(error_query):
    content = editor.get("1.0", tk.END)
    start_index = content.find(error_query)

    if start_index == -1:
        # Query not found, no highlight
        return

    before_text = content[:start_index]
    start_line = before_text.count('\n') + 1
    if '\n' in before_text:
        start_char = len(before_text.split('\n')[-1])
    else:
        start_char = start_index

    end_index = start_index + len(error_query)
    before_end_text = content[:end_index]
    end_line = before_end_text.count('\n') + 1
    if '\n' in before_end_text:
        end_char = len(before_end_text.split('\n')[-1])
    else:
        end_char = end_index

    start_pos = f"{start_line}.{start_char}"
    end_pos = f"{end_line}.{end_char}"

    editor.tag_add("error", start_pos, end_pos)
    editor.tag_config("error", background="#FFDDDD", foreground="black")



def show_error_popup(error_message):
    popup = Toplevel()
    popup.title("SQL Error")
    popup.geometry("600x300")

    txt = Text(popup, wrap="word", foreground="red", font=("Courier New", 10))
    txt.pack(fill=BOTH, expand=True, side="left")

    scroll = Scrollbar(popup, command=txt.yview)
    scroll.pack(fill=Y, side=RIGHT)
    txt.config(yscrollcommand=scroll.set)

    wrapped_text = textwrap.fill(error_message, width=100)
    txt.insert("1.0", wrapped_text)
    txt.config(state="disabled")

def export_csv_full():
    query = extract_sql_from_cursor()
    if not query.strip().lower().startswith("select"):
        messagebox.showinfo("Not Supported", "Only SELECT queries can be exported to CSV.")
        return

    file_path = filedialog.asksaveasfilename(
        defaultextension=".csv",
        filetypes=[("CSV Files", "*.csv")],
        title="Export Full Query Result As"
    )
    if not file_path:
        return

    try:
        conn, _ = connect()
        if not conn:
            raise Exception("Failed to connect to database")

        with conn.cursor() as cursor:
            cursor.execute(query)

            columns = [desc[0] for desc in cursor.description]

            with open(file_path, mode="w", newline="", encoding="utf-8") as csv_file:
                writer = csv.writer(csv_file)
                writer.writerow(columns)  # header

                batch_size = 1000
                while True:
                    rows = cursor.fetchmany(batch_size)
                    if not rows:
                        break
                    writer.writerows(rows)

        messagebox.showinfo("Export Complete", f"Query result exported to:\n{file_path}")
    except Exception as e:
        messagebox.showerror("Export Failed", f"Error:\n{str(e)}")
    finally:
        if conn:
            conn.close()



def highlight_error_line(query):
    # Highlight the full block in red
    editor.tag_add("error", "1.0", tk.END)
    editor.tag_config("error", background="#FFDDDD", foreground="black")

def extract_sql_from_cursor():
    full_text = editor.get("1.0", tk.END)
    cursor_index = editor.index(tk.INSERT)
    cursor_line, cursor_col = map(int, cursor_index.split('.'))

    # Flat char position
    lines = full_text.splitlines()
    char_pos = sum(len(line) + 1 for line in lines[:cursor_line - 1]) + cursor_col

    # Slice before/after cursor
    before = full_text[:char_pos]
    after = full_text[char_pos:]

    # Scan backwards for SQL keyword start (we include common DML/DDL keywords)
    sql_keywords = r'\b(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|WITH|MERGE|BEGIN)\b'
    keyword_matches = list(re.finditer(sql_keywords, before, flags=re.IGNORECASE))

    if not keyword_matches:
        return "", -1, -1

    last_keyword = keyword_matches[-1]
    start_pos = last_keyword.start()

    # Find next semicolon (to mark statement end)
    next_semicolon = after.find(';')
    end_pos = char_pos + next_semicolon if next_semicolon != -1 else len(full_text)

    raw_sql = full_text[start_pos:end_pos]
    cleaned = remove_comments(raw_sql).strip()

    return cleaned, start_pos, start_pos + len(cleaned)


def remove_comments(sql):
    sql = re.sub(r'--.*?$', '', sql, flags=re.MULTILINE)   # Single-line comments
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)   # Multi-line comments
    return sql

def save_sql():
    name = sql_name_entry.get().strip()
    content = editor.get("1.0", tk.END)
    debug_log(f"Saving SQL: {name}")
    if name:
        success = save_new_sql(name, content)
        if success:
            refresh_sql_list()
            unsaved_label.config(text="")
            messagebox.showinfo("Saved", "SQL sheet saved.")


def update_sql():
    sql_id = current_sql_id.get()
    content = editor.get("1.0", tk.END).strip()
    if sql_id:
        update_sql_sheet(sql_id, content)
        refresh_sql_list()
        unsaved_label.config(text="")
        messagebox.showinfo("Updated", "SQL sheet updated.")


def delete_sql():
    sql_id = current_sql_id.get()
    if sql_id and messagebox.askyesno("Confirm", "Delete this SQL sheet?"):
        delete_sql_sheet(sql_id)
        refresh_sql_list()
        editor.delete("1.0", tk.END)
        sql_name_entry.delete(0, tk.END)
        current_sql_id.set(0)

def import_sql_file():
    file = filedialog.askopenfilename(filetypes=[("SQL Files", "*.sql")])
    if file:
        debug_log(f"Importing file: {file}")
        with open(file, 'r', encoding='utf-8') as f:
            editor.delete("1.0", tk.END)
            editor.insert(tk.END, f.read())
            apply_syntax_highlighting()


def export_sql_file():
    file = filedialog.asksaveasfilename(defaultextension=".sql", filetypes=[("SQL Files", "*.sql")])
    if file:
        debug_log(f"Exporting to file: {file}")
        with open(file, 'w', encoding='utf-8') as f:
            f.write(editor.get("1.0", tk.END))
        messagebox.showinfo("Exported", "File saved.")


# --- Layout ---
ttk.Label(tab_sql_editor, text="Search Sheets:").pack(anchor="w", padx=10, pady=(10, 0))
search_entry = ttk.Entry(tab_sql_editor, textvariable=sql_filter_var)
search_entry.pack(fill="x", padx=10)

sql_filter_var.trace_add("write", filter_sql_tree)

# ---- SQL Sheet List Section ----
ttk.Label(tab_sql_editor, text="🗂️ Below is the list of your saved SQL sheets. Select one to load, or use the search above to filter by name or creator.",
          foreground="gray").pack(anchor="w", padx=10, pady=(2, 0))

sql_section_label = ttk.Label(tab_sql_editor, text="Saved SQL Sheets", font=("Segoe UI", 10, "bold"))
sql_section_label.pack(anchor="w", padx=10, pady=(5, 0))

sql_tree_frame = ttk.Frame(tab_sql_editor)
sql_tree_frame.pack(fill="x", padx=10, pady=5)

sql_tree_scrollbar = ttk.Scrollbar(sql_tree_frame, orient="vertical")
sql_tree = ttk.Treeview(
    sql_tree_frame,
    columns=("ID", "Name", "Created By"),
    show="headings",
    yscrollcommand=sql_tree_scrollbar.set,
    height=5
)
sql_tree_scrollbar.config(command=sql_tree.yview)
sql_tree_scrollbar.pack(side="right", fill="y")
sql_tree.pack(side="left", fill="both", expand=True)

sql_tree.heading("ID", text="ID")
sql_tree.heading("Name", text="SQL Sheet Name")
sql_tree.heading("Created By", text="Created By")
sql_tree.column("ID", width=40, anchor="center")
sql_tree.column("Name", width=250, anchor="w")
sql_tree.column("Created By", width=150, anchor="w")

sql_tree.bind("<<TreeviewSelect>>", on_sql_select)

# ---- SQL Editor Section ----
ttk.Label(tab_sql_editor, text="💡 Give a name to your SQL query below to save or update it later.",
          foreground="gray").pack(anchor="w", padx=10, pady=(10, 0))

sql_name_label = ttk.Label(tab_sql_editor, text="SQL Name", font=("Segoe UI", 10, "bold"))
sql_name_label.pack(anchor="w", padx=10, pady=(0, 0))
sql_name_entry = ttk.Entry(tab_sql_editor)
sql_name_entry.pack(fill="x", padx=10, pady=(0, 5))
sql_name_entry.insert(0, "e.g., Active Users Query")

ttk.Label(tab_sql_editor, text="✏️ Write or edit your SQL queries below. The query at the cursor position will be executed.",
          foreground="gray").pack(anchor="w", padx=10, pady=(0, 2))

editor_label = ttk.Label(tab_sql_editor, text="SQL Editor", font=("Segoe UI", 10, "bold"))
editor_label.pack(anchor="w", padx=10)

# --- Line Numbered SQL Editor ---
editor_frame = ttk.Frame(tab_sql_editor)
editor_frame.pack(fill="both", expand=True, padx=10, pady=(0, 5))

# Vertical scrollbar
scrollbar = ttk.Scrollbar(editor_frame, orient="vertical")

# Line numbers (left)
line_numbers = tk.Text(editor_frame, width=4, padx=4, takefocus=0, border=0,
                       background='#F0F0F0', state='disabled', wrap='none')
line_numbers.pack(side="left", fill="y")

# SQL editor (right)
editor = tk.Text(editor_frame, wrap=tk.WORD, font=("Courier New", 10), undo=True,
                 yscrollcommand=scrollbar.set)
editor.pack(side="left", fill="both", expand=True)

# Scrollbar on the far right
scrollbar.config(command=lambda *args: (editor.yview(*args), line_numbers.yview(*args)))
scrollbar.pack(side="right", fill="y")

# Keep line numbers in sync
def update_line_numbers(event=None):
    lines = editor.get("1.0", "end-1c").split("\n")
    nums = "\n".join(f"{i+1}" for i in range(len(lines)))
    line_numbers.config(state="normal")
    line_numbers.delete("1.0", tk.END)
    line_numbers.insert("1.0", nums)
    line_numbers.config(state="disabled")
    line_numbers.yview_moveto(editor.yview()[0])

editor.bind("<KeyRelease>", lambda e: (apply_syntax_highlighting(), update_line_numbers()))
editor.bind("<MouseWheel>", update_line_numbers)
editor.bind("<ButtonRelease>", update_line_numbers)

# ---- SQL Help Panel ----
help_paned = ttk.PanedWindow(tab_sql_editor, orient=tk.VERTICAL)
help_paned.pack(fill="both", expand=False, padx=10, pady=(0, 10))

help_frame = ttk.Labelframe(help_paned, text="📘 SQL Help", padding=10)
help_text = tk.Text(help_frame, wrap="word", height=10)
help_scroll = ttk.Scrollbar(help_frame, command=help_text.yview)
help_text.config(yscrollcommand=help_scroll.set)
help_text.pack(side="left", fill="both", expand=True)
help_scroll.pack(side="right", fill="y")

# Add initial help text
help_intro_text = (
    "Welcome to SQL Help!\n\n"
    "Type a SQL keyword (e.g., SELECT, JOIN, INSERT) in the search box\n"
    "and press Enter to view syntax help or access quick learning links.\n"
    "If the topic is not available, an external link will be suggested.\n"
    "\nHappy querying!"
)
help_text.insert("1.0", help_intro_text)

help_frame.pack(fill="both", expand=True)
help_paned.add(help_frame)
help_paned.forget(help_frame)  # Initially hide the help panel


help_topics = {
    "SELECT": "The SELECT statement is used to fetch data from a database.\n\nExample:\nSELECT * FROM users;",
    "JOIN": "JOIN is used to combine rows from two or more tables.\n\nExample:\nSELECT * FROM users u JOIN orders o ON u.id = o.user_id;",
    "INSERT": "INSERT is used to add new rows.\n\nExample:\nINSERT INTO users(name, email) VALUES ('Alice', 'alice@example.com');",
    "UPDATE": "UPDATE modifies existing rows.\n\nExample:\nUPDATE users SET name = 'Bob' WHERE id = 1;",
    "DELETE": "DELETE removes rows.\n\nExample:\nDELETE FROM users WHERE id = 1;",
    "CREATE": "CREATE defines new objects like tables, views, etc.\n\nExample:\nCREATE TABLE students (id NUMBER, name VARCHAR2(50));"
}

def search_help(event=None):
    term = help_search_var.get().strip().upper()
    text = help_topics.get(term, f"No help found for '{term}'.\nYou can check: https://www.w3schools.com/sql/sql_{term.lower()}.asp")
    help_text.delete("1.0", tk.END)
    help_text.insert(tk.END, text)

help_search_var = tk.StringVar()
help_search_entry = ttk.Entry(help_frame, textvariable=help_search_var)
help_search_entry.pack(fill="x", side="top", pady=(0, 5))
help_search_entry.bind("<Return>", search_help)

help_visible = tk.BooleanVar(value=False)

def toggle_help():
    if help_visible.get():
        help_frame.pack_forget()
        help_visible.set(False)
        toggle_btn.config(text="Show Help")
    else:
        help_frame.pack(fill="both", expand=True)
        help_visible.set(True)
        toggle_btn.config(text="Hide Help")

toggle_btn = ttk.Button(tab_sql_editor, text="Show Help", command=toggle_help)
toggle_btn.pack(pady=(0, 5), padx=10, anchor="e")


# Track unsaved changes
unsaved_label = ttk.Label(tab_sql_editor, text="", foreground="red")
unsaved_label.pack(anchor="w", padx=12)

def mark_unsaved(event=None):
    unsaved_label.config(text="Unsaved changes")
editor.bind("<Key>", mark_unsaved)

# ---- Buttons (Run/Save/Update/Delete) ----
btn_frame = ttk.Frame(tab_sql_editor)
btn_frame.pack(fill="x", padx=10, pady=5)

# Button containers
left_btn_frame = ttk.Frame(btn_frame)
sep_frame = ttk.Frame(btn_frame)
right_btn_frame = ttk.Frame(btn_frame)

left_btn_frame.pack(side="left")
ttk.Separator(sep_frame, orient="vertical").pack(fill="y", pady=5)
sep_frame.pack(side="left", padx=10)
right_btn_frame.pack(side="right")

# Left-aligned buttons (SQL operations)
ttk.Button(left_btn_frame, text="Run SQL", command=run_sql).pack(side="left", padx=5)
ttk.Button(left_btn_frame, text="Export Full CSV", command=export_csv_full).pack(side="left", padx=5)
ttk.Button(left_btn_frame, text="Save New", command=lambda: (save_sql(), unsaved_label.config(text=""))).pack(side="left", padx=5)
ttk.Button(left_btn_frame, text="Update", command=lambda: (update_sql(), unsaved_label.config(text=""))).pack(side="left", padx=5)
ttk.Button(left_btn_frame, text="Delete", command=delete_sql).pack(side="left", padx=5)

import_icon = load_icon("import-icon.jpg")
export_icon = load_icon("icon-export.jpg")

ttk.Button(right_btn_frame, text="Import SQL", image=import_icon, compound="left", command=import_sql_file).pack(side="left", padx=5)
ttk.Button(right_btn_frame, text="Export SQL", image=export_icon, compound="left", command=export_sql_file).pack(side="left", padx=5)

# ---- Query Result Section ----
result_label = ttk.Label(tab_sql_editor, text="Query Result", font=("Segoe UI", 10, "bold"))
result_label.pack(anchor="w", padx=10, pady=(10, 0))

result_frame = ttk.Frame(tab_sql_editor)
result_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

# Scrollbar
result_scrollbar = ttk.Scrollbar(result_frame, orient="vertical")

# Treeview with scrollbar
result_tree = ttk.Treeview(
    result_frame,
    yscrollcommand=result_scrollbar.set,
    selectmode="browse"
)
# Context Menu
result_menu = tk.Menu(result_tree, tearoff=0)
result_menu.add_command(label="Copy Cell", command=lambda: copy_selected_cell(result_tree))
result_menu.add_command(label="Copy Row", command=lambda: copy_selected_row(result_tree))
result_menu.add_command(label="Copy All", command=lambda: copy_all_rows(result_tree))
result_menu.add_separator()
result_menu.add_command(label="Copy Column Name", command=lambda: copy_column_name(result_tree))
result_scrollbar.config(command=result_tree.yview)

# -----------------------------------------Helper functions---------------------------------------------

# Right-click menu
def show_result_context_menu(event):
    row_id = result_tree.identify_row(event.y)
    if row_id:
        result_tree.selection_set(row_id)
        result_menu.post(event.x_root, event.y_root)

result_tree.bind("<Button-3>", show_result_context_menu)

# Ctrl+C binding
def copy_on_ctrl_c(event):
    copy_selected_row(result_tree)

result_tree.bind("<Control-c>", copy_on_ctrl_c)

# Right-click menu
def show_result_context_menu(event):
    row_id = result_tree.identify_row(event.y)
    if row_id:
        result_tree.selection_set(row_id)
        result_menu.post(event.x_root, event.y_root)

result_tree.bind("<Button-3>", show_result_context_menu)

# Ctrl+C binding
def copy_on_ctrl_c(event):
    copy_selected_row(result_tree)

result_tree.bind("<Control-c>", copy_on_ctrl_c)

def copy_selected_cell(tree):
    selected = tree.selection()
    if not selected:
        return
    item = selected[0]
    col = tree.identify_column(tree.winfo_pointerx() - tree.winfo_rootx())
    col_index = int(col.replace("#", "")) - 1
    value = tree.item(item)['values'][col_index]
    app.clipboard_clear()
    app.clipboard_append(str(value))

def copy_selected_row(tree):
    selected = tree.selection()
    if not selected:
        return
    item = selected[0]
    values = tree.item(item)["values"]
    line = "\t".join(str(v) for v in values)
    app.clipboard_clear()
    app.clipboard_append(line)

def copy_all_rows(tree):
    all_rows = []
    for item in tree.get_children():
        row = tree.item(item)["values"]
        all_rows.append("\t".join(str(v) for v in row))
    result = "\n".join(all_rows)
    app.clipboard_clear()
    app.clipboard_append(result)

# Store clicked column index
clicked_column_index = tk.IntVar(value=0)

def show_result_context_menu(event):
    row_id = result_tree.identify_row(event.y)
    col_id = result_tree.identify_column(event.x)  # like '#1', '#2'
    if row_id:
        result_tree.selection_set(row_id)
    if col_id:
        clicked_column_index.set(int(col_id.replace("#", "")) - 1)
    result_menu.post(event.x_root, event.y_root)

result_tree.bind("<Button-3>", show_result_context_menu)

def copy_column_name(tree):
    index = clicked_column_index.get()
    columns = tree["columns"]
    if 0 <= index < len(columns):
        col_name = columns[index]
        app.clipboard_clear()
        app.clipboard_append(col_name)

# Pack Treeview and Scrollbar
result_tree.pack(side="left", fill="both", expand=True)
result_scrollbar.pack(side="right", fill="y")

# Horizontal Scrollbar
result_scrollbar_x = ttk.Scrollbar(result_frame, orient="horizontal", command=result_tree.xview)
result_tree.config(xscrollcommand=result_scrollbar_x.set)

result_scrollbar_x.pack(side="bottom", fill="x")

refresh_sql_list()

# ---------------- Start GUI ----------------
notebook.tab(1, state="disabled")
notebook.tab(2, state="disabled")
notebook.tab(3, state="disabled")
notebook.tab(4, state="disabled")

app.mainloop()
