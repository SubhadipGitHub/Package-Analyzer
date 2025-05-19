import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog, simpledialog
import threading
import json
import re
import csv
from collections import defaultdict
import oracledb

DEBUG = True  # Set False to disable debug logs

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

# ---------------- Configuration I/O ----------------
def load_config():
    try:
        with open("config.json", "r") as f:
            cfg = json.load(f)
            return cfg.get("db_user"), cfg.get("db_password"), cfg.get("dsn")
    except:
        return "", "", ""

def save_config(user, password, dsn):
    with open("config.json", "w") as f:
        json.dump({
            "db_user": user,
            "db_password": password,
            "dsn": dsn
        }, f, indent=4)

# ---------------- Database Operations ----------------
def connect():
    DB_USER,DB_PASS,DSN = load_config()
    #print(DB_USER)
    #print(DB_PASS)
    #print(DSN)

    try:
        connectinfo = oracledb.connect(user=DB_USER, password=DB_PASS, dsn=DSN)
        # Update label
        footer_label.config(text=f"Connected as {DB_USER}",fg="green")

        # Enable other tabs
        notebook.tab(1, state="normal")  # Analyze Table
        notebook.tab(2, state="normal")  # Package List
        notebook.tab(3, state="normal")  # Extract Package
        notebook.hide(0)
        #messagebox.showinfo("Connection Successful", f"Connection {DB_USER} established successfully!")
        #print(connectinfo)
        return connectinfo
    except Exception as e:
        messagebox.showerror("Connection Failed", str(e))

def fetch_query(query, params=None):
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params or [])
        return cursor.fetchall()

def get_schema_objects(schema, obj_type):
    return [row[0] for row in fetch_query(
        "SELECT object_name FROM all_objects WHERE owner = UPPER(:1) AND object_type = :2 ORDER BY object_name",
        [schema, obj_type]
    )]

def get_tables(schema):
    return [row[0] for row in fetch_query(
        "SELECT table_name FROM all_tables WHERE owner = UPPER(:1) ORDER BY table_name", [schema]
    )]

def get_sequences(schema):
    return [row[0] for row in fetch_query(
        "SELECT sequence_name FROM all_sequences WHERE sequence_owner = UPPER(:1) ORDER BY sequence_name", [schema]
    )]

def get_package_source(schema, name):
    return fetch_query("""
        SELECT line, text FROM all_source 
        WHERE owner = UPPER(:1) AND name = UPPER(:2) AND type IN ('PACKAGE BODY', 'PROCEDURE', 'FUNCTION')
        ORDER BY line
    """, [schema, name])

# ---------------- Text Analysis Helpers ----------------
def analyze_table():
    output = []
    schema = schema_entry_table.get().strip()
    table_name = table_entry.get().strip()
    
    debug_log(f"Schema: {schema}")
    debug_log(f"Table: {table_name}")
    
    if not schema or not table_name:
        messagebox.showwarning("Input Error", "Please enter both schema and table name.")
        return
    
    try:
        # Columns
        cols = fetch_query("""
            SELECT column_name, data_type, data_length 
            FROM all_tab_columns 
            WHERE table_name = UPPER(:1) AND owner = UPPER(:2) 
            ORDER BY column_id
        """, [table_name, schema])
        debug_log(f"Columns found: {len(cols)}")
        output.append("Columns:")
        output.extend(f"  - {c[0]} ({c[1]} [{c[2]}])" for c in cols)
        
        # Constraints
        cons = fetch_query("""
            SELECT ac.constraint_name, ac.constraint_type, acc.column_name
            FROM all_constraints ac
            JOIN all_cons_columns acc ON ac.constraint_name = acc.constraint_name AND ac.owner = acc.owner
            WHERE ac.table_name = UPPER(:1) AND ac.owner = UPPER(:2)
            ORDER BY ac.constraint_name, acc.position
        """, [table_name, schema])
        debug_log(f"Constraints found: {len(cons)}")
        output.append("\nConstraints:")
        output.extend(f"  - {c[0]} ({c[1]}) [{c[2]}]" for c in cons)
        
        # Indexes
        idxs = fetch_query("""
            SELECT ai.index_name, ai.uniqueness, aic.column_name
            FROM all_indexes ai
            JOIN all_ind_columns aic ON ai.index_name = aic.index_name AND ai.table_owner = aic.table_owner
            WHERE ai.table_name = UPPER(:1) AND ai.owner = UPPER(:2)
            ORDER BY ai.index_name, aic.column_position
        """, [table_name, schema])
        debug_log(f"Indexes found: {len(idxs)}")
        output.append("\nIndexes:")
        output.extend(f"  - {i[0]} ({i[1]}) [{i[2]}]" for i in idxs)
        
        # Record count
        count_query = f"SELECT COUNT(*) FROM {schema}.{table_name}"
        debug_log(f"Executing count query: {count_query}")
        count = fetch_query(count_query)[0][0]
        debug_log(f"Record count: {count}")
        output.append(f"\nTotal Records: {count}")
        
        # Usage in packages
        usage = analyze_table_usage(schema, table_name)
        debug_log(f"Usage found in {len(usage)} entries")
        output.append("\nUsage in Packages:")
        pkgs = set()
        for (tbl, op, pkg), info in sorted(usage.items()):
            pkgs.add(pkg)
            output.append(f"  - Package: {pkg}, Operation: {op}, Lines: {', '.join(map(str, info['lines']))}")
        output.append(f"\nTotal packages using {table_name}: {len(pkgs)}")
        
    except Exception as e:
        debug_log(f"Exception: {e}")
        output.append(f"\nError retrieving table details: {str(e)}")
    
    # Print output for debug (optional)
    debug_log("Final output lines:")
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
            table_output.delete('1.0', tk.END)
            if output:
                table_output.insert(tk.END, "\n".join(output))
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
    print(f"Debug: schema entered for listing packages: '{schema}'")
    
    if not schema:
        messagebox.showwarning("Input Error", "Please enter schema name.")
        return []
    
    try:
        with connect() as conn:
            cursor = conn.cursor()
            # Use the schema variable, not DB_USER here
            cursor.execute(
                "SELECT object_name FROM all_objects WHERE object_type = 'PACKAGE' AND owner = UPPER(:1)",
                [schema]
            )
            packages = [row[0] for row in cursor.fetchall()]
            print(f"Debug: packages found: {packages}")
            return packages
    except Exception as e:
        print(f"Error in list_packages: {e}")
        messagebox.showerror("Database Error", f"Failed to list packages: {e}")
        return []
    
def list_packages_worker():
    def stop_loader():
        progress_bar2.stop()
        progress_bar2.pack_forget()

    try:
        output = list_packages()
        def update_ui():
            table_package_list_output.delete('1.0', tk.END)
            if output:
                table_package_list_output.insert(tk.END, "\n".join(output))
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
        rows = fetch_query(query, [schema, pkg])
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

# ---------------- GUI Setup ----------------
app = tk.Tk()
app.title("Oracle ATP PL/SQL Analyzer")
app.geometry("1100x700")

notebook = ttk.Notebook(app)
notebook.pack(fill='both', expand=True)

# ---------------- Tab 1: Connection Settings ----------------
tab_conn = ttk.Frame(notebook)
notebook.add(tab_conn, text='Connection Settings')

tk.Label(tab_conn, text="Username:").grid(row=0, column=0, sticky="w")
username_entry = tk.Entry(tab_conn, width=30)
username_entry.grid(row=0, column=1, padx=10, pady=5)

tk.Label(tab_conn, text="Password:").grid(row=1, column=0, sticky="w")
password_entry = tk.Entry(tab_conn, show="*", width=30)
password_entry.grid(row=1, column=1, padx=10, pady=5)

tk.Label(tab_conn, text="DSN (e.g., mydb_high):").grid(row=2, column=0, sticky="w")
dsn_entry = tk.Entry(tab_conn, width=30)
dsn_entry.grid(row=2, column=1, padx=10, pady=5)

connect_btn = tk.Button(tab_conn, text="Connect", command=connect)
connect_btn.grid(row=3, column=1, pady=10)

conn_status = tk.Label(tab_conn, text="Not Connected", fg="red")
conn_status.grid(row=4, column=1)

# ---------------- Tab 2: Analyze Table ----------------
tab_table = ttk.Frame(notebook)
notebook.add(tab_table, text='Analyze Table')

tk.Label(tab_table, text="Enter Schema Name:").pack(pady=(5,0))
schema_entry_table = tk.Entry(tab_table, width=30)
schema_entry_table.pack(pady=(0,5))

tk.Label(tab_table, text="Enter Table Name:").pack(pady=5)
table_entry = tk.Entry(tab_table, width=40)
table_entry.pack()

analyze_btn = tk.Button(tab_table, text="Analyze Table", command=analyze_table_callback)
analyze_btn.pack(pady=5)

table_output = scrolledtext.ScrolledText(tab_table, wrap=tk.WORD, height=25)
table_output.pack(fill='both', expand=True, padx=10, pady=5)

# ---------------- Tab 3: Package List ----------------
tab_pkg_list = ttk.Frame(notebook)
notebook.add(tab_pkg_list, text='Package List')

tk.Label(tab_pkg_list, text="Enter Schema Name:").pack(pady=(5,0))
schema_entry_pkg_list = tk.Entry(tab_pkg_list, width=30)
schema_entry_pkg_list.pack(pady=(0,5))

refresh_btn = tk.Button(tab_pkg_list, text="Refresh Package List", command=list_packages_callback)
refresh_btn.pack(pady=5)

table_package_list_output = scrolledtext.ScrolledText(tab_pkg_list, wrap=tk.WORD, height=25)
table_package_list_output.pack(fill='both', expand=True, padx=10, pady=5)

# ---------------- Tab 4: Extract Package Content ----------------
tab_pkg_extract = ttk.Frame(notebook)
notebook.add(tab_pkg_extract, text='Extract Package Content')

tk.Label(tab_pkg_extract, text="Enter Schema Name:").pack(pady=(5,0))
schema_entry_package = tk.Entry(tab_pkg_extract, width=30)
schema_entry_package.pack(pady=(0,5))

tk.Label(tab_pkg_extract, text="Enter Package Name:").pack(pady=5)
package_entry = tk.Entry(tab_pkg_extract, width=40)
package_entry.pack()

analyze_pkg_btn = tk.Button(tab_pkg_extract, text="Extract Package Content", command=extract_package_content_callback)
analyze_pkg_btn.pack(pady=5)

pkg_text = scrolledtext.ScrolledText(tab_pkg_extract, wrap=tk.WORD)
pkg_text.pack(fill='both', expand=True, padx=10, pady=5)

# ---------------- Progress Bar -----------------

progress_bar1 = ttk.Progressbar(tab_table, mode='indeterminate')
progress_bar1.pack(fill='x', padx=50, pady=(0, 50))
progress_bar1.stop()  # make sure it's not running at start
progress_bar1.pack_forget()  # hide initially

progress_bar2 = ttk.Progressbar(tab_pkg_list, mode='indeterminate')
progress_bar2.pack(fill='x', padx=50, pady=(0, 50))
progress_bar2.stop()  # make sure it's not running at start
progress_bar2.pack_forget()  # hide initially

progress_bar3 = ttk.Progressbar(tab_pkg_extract, mode='indeterminate')
progress_bar3.pack(fill='x', padx=50, pady=(0, 50))
progress_bar3.stop()  # make sure it's not running at start
progress_bar3.pack_forget()  # hide initially

# ---------------- Footer Status ----------------
footer_frame = tk.Frame(app)
footer_frame.pack(fill='x', side='bottom')

footer_label = tk.Label(footer_frame, text="Not connected", anchor='w', fg="blue")
footer_label.pack(fill='x', padx=5, pady=2)

# ---------------- Start GUI ----------------
app.mainloop()
