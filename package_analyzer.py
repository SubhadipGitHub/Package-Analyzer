import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import threading
import json
import re
import csv
from collections import defaultdict
import oracledb

# Load config.json
with open("config.json", "r") as f:
    config = json.load(f)
    DB_USER = config["db_user"]
    DB_PASS = config["db_password"]
    DSN = config["dsn"]

OPERATION_PATTERNS = {
    "SELECT": re.compile(r"\bSELECT\b.*?(?:\bINTO\b.*?\b)?FROM\b\s+([a-zA-Z0-9_]+)", re.IGNORECASE | re.DOTALL),
    "INSERT": re.compile(r"\bINSERT\s+INTO\s+([a-zA-Z0-9_]+)", re.IGNORECASE | re.DOTALL),
    "UPDATE": re.compile(r"\bUPDATE\s+([a-zA-Z0-9_]+)\s+SET\b", re.IGNORECASE | re.DOTALL),
    "DELETE": re.compile(r"\bDELETE\s+FROM\s+([a-zA-Z0-9_]+)", re.IGNORECASE | re.DOTALL),
}

def connect():
    return oracledb.connect(user=DB_USER, password=DB_PASS, dsn=DSN)

def list_tables():
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT table_name FROM all_tables WHERE owner = UPPER(:1)", [DB_USER])
        return [row[0] for row in cursor.fetchall()]

def list_packages():
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT object_name FROM all_objects WHERE object_type = 'PACKAGE' AND owner = UPPER(:1)", [DB_USER])
        return [row[0] for row in cursor.fetchall()]

def list_sequences():
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT sequence_name FROM all_sequences WHERE sequence_owner = UPPER(:1)", [DB_USER])
        return [row[0] for row in cursor.fetchall()]

def get_package_source(package_name):
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT text FROM all_source 
            WHERE owner = UPPER(:1) AND name = UPPER(:2) AND type = 'PACKAGE BODY'
            ORDER BY line
        """, [DB_USER, package_name])
        return "\n".join(row[0] for row in cursor.fetchall())

def analyze_sql_source(source, filename="DB"):
    results = defaultdict(lambda: {"count": 0, "lines": [], "files": set()})
    statements = re.split(r";\s*(?=\n|\Z)", source)
    position = 0
    for statement in statements:
        stripped_stmt = statement.strip()
        if not stripped_stmt:
            position += statement.count("\n") + 1
            continue
        for op, pattern in OPERATION_PATTERNS.items():
            match = pattern.search(stripped_stmt)
            if match:
                table = match.group(1).upper()
                start_line = position + 1
                key = (table, op.upper(), filename)
                results[key]["count"] += 1
                results[key]["lines"].append(start_line)
                results[key]["files"].add(filename)
        position += statement.count("\n") + 1
    return results

def analyze_table_details(table_name):
    output = []
    with connect() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT column_name, data_type FROM all_tab_columns WHERE table_name = UPPER(:1) AND owner = UPPER(:2)", [table_name, DB_USER])
        output.append("Columns:")
        for row in cursor.fetchall():
            output.append(f"  - {row[0]} ({row[1]})")

        cursor.execute("""
            SELECT constraint_name, constraint_type 
            FROM all_constraints 
            WHERE table_name = UPPER(:1) AND owner = UPPER(:2)
        """, [table_name, DB_USER])
        output.append("\nConstraints:")
        for row in cursor.fetchall():
            output.append(f"  - {row[0]} ({row[1]})")

        cursor.execute("""
            SELECT index_name, uniqueness FROM all_indexes 
            WHERE table_name = UPPER(:1) AND owner = UPPER(:2)
        """, [table_name, DB_USER])
        output.append("\nIndexes:")
        for row in cursor.fetchall():
            output.append(f"  - {row[0]} ({row[1]})")

        cursor.execute(f"SELECT COUNT(*) FROM {DB_USER}.{table_name}")
        count = cursor.fetchone()[0]
        output.append(f"\nTotal Records: {count}")
    return output

def analyze_table_usage(table_name):
    results = defaultdict(lambda: {"count": 0, "lines": [], "files": set()})
    for pkg in list_packages():
        src = get_package_source(pkg)
        if not src:
            continue
        analysis = analyze_sql_source(src, filename=pkg)
        for (tbl, op, fname), info in analysis.items():
            if tbl == table_name.upper():
                key = (tbl, op, fname)
                results[key]["count"] += info["count"]
                results[key]["lines"].extend(info["lines"])
                results[key]["files"].update(info["files"])
    return results

def export_to_csv(data, filename="output.csv"):
    with open(filename, 'w', newline='') as f:
        writer = csv.writer(f)
        for line in data:
            writer.writerow([line])

def run_analysis(table_name, output_widget):
    output = analyze_table_details(table_name)
    usage = analyze_table_usage(table_name)
    output.append("\nUsage in Packages:")
    seen_pkgs = set()
    for (tbl, op, fname), info in sorted(usage.items()):
        seen_pkgs.add(fname)
        output.append(f"  - Package: {fname}, Operation: {op}, Lines: {', '.join(map(str, info['lines']))}")
    output.append(f"\nTotal packages using {table_name}: {len(seen_pkgs)}")
    update_output(output_widget, output)

# GUI
app = tk.Tk()
app.title("Oracle ATP Analyzer")
app.geometry("900x650")

notebook = ttk.Notebook(app)
notebook.pack(expand=True, fill='both')

# ---- Table Tab ----
table_frame = ttk.Frame(notebook)
notebook.add(table_frame, text='Analyze Table')

frame = ttk.Frame(table_frame)
frame.pack(padx=10, pady=10, fill="x")

label = ttk.Label(frame, text="Select Table:")
label.pack(side="left")

table_var = tk.StringVar()
table_dropdown = ttk.Combobox(frame, textvariable=table_var, width=50)
table_dropdown.pack(side="left", padx=10)

def populate_tables():
    try:
        table_dropdown["values"] = list_tables()
    except Exception as e:
        messagebox.showerror("Error", str(e))

populate_tables()

output_text = scrolledtext.ScrolledText(table_frame, wrap=tk.WORD, height=30)
output_text.pack(fill="both", expand=True)
output_text.config(state='disabled')

def update_output(text_widget, lines):
    text_widget.config(state='normal')
    text_widget.delete(1.0, tk.END)
    for line in lines:
        text_widget.insert(tk.END, line + "\n")
    text_widget.config(state='disabled')
    return lines

def on_analyze():
    table_name = table_var.get()
    if not table_name:
        messagebox.showwarning("Missing Input", "Please select a table.")
        return
    threading.Thread(target=run_analysis, args=(table_name, output_text), daemon=True).start()

analyze_button = ttk.Button(frame, text="Analyze Table", command=on_analyze)
analyze_button.pack(side="left", padx=10)

def export_output():
    file = filedialog.asksaveasfilename(defaultextension=".csv")
    if file:
        content = output_text.get("1.0", tk.END).strip().splitlines()
        export_to_csv(content, file)

export_button = ttk.Button(frame, text="Export to CSV", command=export_output)
export_button.pack(side="left", padx=10)

# ---- Package Tab ----
package_frame = ttk.Frame(notebook)
notebook.add(package_frame, text='Package List')

pkg_text = scrolledtext.ScrolledText(package_frame, wrap=tk.WORD, height=30)
pkg_text.pack(fill="both", expand=True)
pkg_text.config(state='normal')

try:
    pkg_text.insert(tk.END, "Packages:\n" + "\n".join(list_packages()) + "\n")
    pkg_text.insert(tk.END, "\nSequences:\n" + "\n".join(list_sequences()) + "\n")
    pkg_text.insert(tk.END, "\nTables:\n" + "\n".join(list_tables()) + "\n")
except Exception as e:
    pkg_text.insert(tk.END, f"Error: {str(e)}")

pkg_text.config(state='disabled')

app.mainloop()
