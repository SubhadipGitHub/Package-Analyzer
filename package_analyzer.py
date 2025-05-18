import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import threading
import json
import re
import csv
from collections import defaultdict
import oracledb

# Load config once
with open("config.json", "r") as f:
    config = json.load(f)
    DB_USER = config["db_user"]
    DB_PASS = config["db_password"]
    DSN = config["dsn"]

OPERATION_PATTERNS = {
    "SELECT": re.compile(r"\bSELECT\b.*?\bFROM\b\s+(?:\w+\.)?([a-zA-Z0-9_]+)", re.IGNORECASE | re.DOTALL),
    "INSERT": re.compile(r"\bINSERT\s+INTO\s+(?:\w+\.)?([a-zA-Z0-9_]+)", re.IGNORECASE),
    "UPDATE": re.compile(r"\bUPDATE\s+(?:\w+\.)?([a-zA-Z0-9_]+)\s+SET\b", re.IGNORECASE),
    "DELETE": re.compile(r"\bDELETE\s+FROM\s+(?:\w+\.)?([a-zA-Z0-9_]+)", re.IGNORECASE),
}

def connect():
    return oracledb.connect(user=DB_USER, password=DB_PASS, dsn=DSN)

def fetch_query(query, params=None):
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params or [])
        return cursor.fetchall()

def get_schema_objects(schema, obj_type):
    query = "SELECT object_name FROM all_objects WHERE owner = UPPER(:1) AND object_type = :2 ORDER BY object_name"
    return [row[0] for row in fetch_query(query, [schema, obj_type])]

def get_tables(schema):
    return [row[0] for row in fetch_query("SELECT table_name FROM all_tables WHERE owner = UPPER(:1) ORDER BY table_name", [schema])]

def get_sequences(schema):
    return [row[0] for row in fetch_query("SELECT sequence_name FROM all_sequences WHERE sequence_owner = UPPER(:1) ORDER BY sequence_name", [schema])]

def get_package_source(schema, package_name):
    query = """
        SELECT line, text FROM all_source 
        WHERE owner = UPPER(:1) AND name = UPPER(:2) AND type IN ('PACKAGE BODY', 'PROCEDURE', 'FUNCTION')
        ORDER BY line
    """
    rows = fetch_query(query, [schema, package_name])
    return rows  # list of (line_number, text)

def preprocess_source_lines(src_lines):
    """
    Combines multiline PL/SQL statements and tracks their starting line numbers.
    Removes inline comments. Returns list of (start_line, full_statement).
    """
    combined = []
    statement = ""
    start_line = None

    for line_number, line in src_lines:
        # Remove comments
        line_clean = re.sub(r"--.*", "", line).strip()
        if not line_clean:
            continue

        if start_line is None:
            start_line = line_number

        statement += line_clean + " "

        # Consider the statement ends at a semicolon
        if ";" in line:
            combined.append((start_line, statement.strip()))
            statement = ""
            start_line = None

    if statement:
        combined.append((start_line or 1, statement.strip()))

    return combined

def analyze_table_usage(schema, table_name):
    results = defaultdict(lambda: {"count": 0, "lines": [], "files": set()})

    for pkg in get_schema_objects(schema, 'PACKAGE'):
        src_lines = get_package_source(schema, pkg)  # [(line_number, text), ...]

        if not src_lines:
            continue

        for line_number, line_text in src_lines:
            clean_line = re.sub(r"--.*", "", line_text).strip()  # Strip comments

            for op, pattern in OPERATION_PATTERNS.items():
                for match in pattern.finditer(clean_line):
                    matched_table = match.group(1).split('.')[-1].upper()
                    if matched_table == table_name.upper():
                        key = (matched_table, op, pkg)
                        results[key]["count"] += 1
                        results[key]["lines"].append(line_number)
                        results[key]["files"].add(pkg)

    return results


def analyze_table_details(schema, table_name):
    output = []
    col_query = """
        SELECT column_name, data_type,data_length FROM all_tab_columns WHERE table_name = UPPER(:1) AND owner = UPPER(:2) ORDER BY column_id
    """
    cons_query = """
        SELECT 
    ac.constraint_name,
    ac.constraint_type,
    acc.column_name
FROM 
    all_constraints ac
JOIN 
    all_cons_columns acc 
    ON ac.constraint_name = acc.constraint_name 
    AND ac.owner = acc.owner
WHERE 
    ac.table_name = UPPER(:1)
    AND ac.owner = UPPER(:2)
ORDER BY 
    ac.constraint_name, acc.position
    """
    idx_query = """
        SELECT 
    ai.index_name,
    ai.uniqueness,
    aic.column_name
FROM 
    all_indexes ai
JOIN 
    all_ind_columns aic 
    ON ai.index_name = aic.index_name 
    AND ai.table_owner = aic.table_owner
WHERE 
    ai.table_name = UPPER(:1)
    AND ai.owner = UPPER(:2)
ORDER BY 
    ai.index_name, aic.column_position
    """
    try:
        cols = fetch_query(col_query, [table_name, schema])
        output.append("Columns:")
        output.extend(f"  - {c[0]} ({c[1]} [{c[2]}])" for c in cols)

        cons = fetch_query(cons_query, [table_name, schema])
        output.append("\nConstraints:")
        output.extend(f"  - {c[0]} ({c[1]}) [{c[2]}]" for c in cons)

        idxs = fetch_query(idx_query, [table_name, schema])
        output.append("\nIndexes:")
        output.extend(f"  - {i[0]} ({i[1]}) [{i[2]}]" for i in idxs)

        count_query = f"SELECT COUNT(*) FROM {schema}.{table_name}"
        count = fetch_query(count_query)[0][0]
        output.append(f"\nTotal Records: {count}")
    except Exception as e:
        output.append(f"\nError retrieving table details: {str(e)}")
    return output


def export_to_csv(data, filename="output.csv"):
    with open(filename, 'w', newline='') as f:
        writer = csv.writer(f)
        for line in data:
            writer.writerow([line])

def update_output(text_widget, lines):
    text_widget.config(state='normal')
    text_widget.delete(1.0, tk.END)
    text_widget.insert(tk.END, "\n".join(lines) + "\n")
    text_widget.config(state='disabled')

def run_analysis(schema, table_name, output_widget):
    def start_loader():
        progress_bar.pack(fill='x', padx=10, pady=(0, 10))
        progress_bar.start()

    def stop_loader():
        progress_bar.stop()
        progress_bar.pack_forget()

    app.after(0, start_loader)
    try:
        output = analyze_table_details(schema, table_name)
        usage = analyze_table_usage(schema, table_name)
        output.append("\nUsage in Packages:")
        pkgs = set()
        for (tbl, op, fname), info in sorted(usage.items()):
            pkgs.add(fname)
            output.append(f"  - Package: {fname}, Operation: {op}, Lines: {', '.join(map(str, info['lines']))}")
        output.append(f"\nTotal packages using {table_name}: {len(pkgs)}")
        app.after(0, lambda: update_output(output_widget, output))
    except Exception as e:
        app.after(0, lambda: messagebox.showerror("Error", str(e)))
    finally:
        app.after(0, stop_loader)


# GUI Setup
app = tk.Tk()
app.title("Oracle ATP Analyzer")
app.geometry("900x650")

notebook = ttk.Notebook(app)
notebook.pack(expand=True, fill='both')

# --- Helper for loading schemas ---
def load_schemas(dropdown):
    try:
        with connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT owner FROM all_objects ORDER BY owner")
            schemas = [row[0] for row in cursor.fetchall()]
            dropdown['values'] = schemas
    except Exception as e:
        messagebox.showerror("Error loading schemas", str(e))

# --- Analyze Table Tab ---
table_frame = ttk.Frame(notebook)
notebook.add(table_frame, text='Analyze Table')

top_frame = ttk.Frame(table_frame)
top_frame.pack(padx=10, pady=10, fill="x")

ttk.Label(top_frame, text="Select Schema:").pack(side="left")
schema_var_table = tk.StringVar()
schema_dropdown_table = ttk.Combobox(top_frame, textvariable=schema_var_table, width=20)
schema_dropdown_table.pack(side="left", padx=5)

ttk.Label(top_frame, text="Select Table:").pack(side="left")
table_var = tk.StringVar()
table_dropdown = ttk.Combobox(top_frame, textvariable=table_var, width=40)
table_dropdown.pack(side="left", padx=5)

load_schemas(schema_dropdown_table)

def on_schema_select_table(event=None):
    schema = schema_var_table.get()
    if schema:
        try:
            table_dropdown['values'] = get_tables(schema)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to fetch tables for schema {schema}:\n{e}")

schema_dropdown_table.bind("<<ComboboxSelected>>", on_schema_select_table)

progress_bar = ttk.Progressbar(table_frame, mode='indeterminate')
progress_bar.pack(fill='x', padx=10, pady=(0, 10))
progress_bar.stop()  # make sure it's not running at start
progress_bar.pack_forget()  # hide initially

output_text = scrolledtext.ScrolledText(table_frame, wrap=tk.WORD, height=30)
output_text.pack(fill="both", expand=True)
output_text.config(state='disabled')

def on_analyze_table():
    schema = schema_var_table.get()
    table_name = table_var.get()
    if not schema or not table_name:
        messagebox.showwarning("Missing Input", "Please select both schema and table.")
        return
    threading.Thread(target=run_analysis, args=(schema, table_name, output_text), daemon=True).start()

btn_frame = ttk.Frame(top_frame)
btn_frame.pack(side="left", padx=5)

ttk.Button(btn_frame, text="Analyze Table", command=on_analyze_table).pack(side="left", padx=5)

def export_output():
    file = filedialog.asksaveasfilename(defaultextension=".csv")
    if file:
        content = output_text.get("1.0", tk.END).strip().splitlines()
        export_to_csv(content, file)

ttk.Button(btn_frame, text="Export to CSV", command=export_output).pack(side="left", padx=5)

# --- Package List Tab ---
package_frame = ttk.Frame(notebook)
notebook.add(package_frame, text='Package List')

pkg_top_frame = ttk.Frame(package_frame)
pkg_top_frame.pack(pady=10, fill="x")

ttk.Label(pkg_top_frame, text="Select Schema:").pack(side="left")
schema_var_pkg = tk.StringVar()
schema_dropdown_pkg = ttk.Combobox(pkg_top_frame, textvariable=schema_var_pkg, width=30)
schema_dropdown_pkg.pack(side="left", padx=10)

load_schemas(schema_dropdown_pkg)

def refresh_package_info():
    schema = schema_var_pkg.get()
    if not schema:
        messagebox.showwarning("Missing Input", "Please select a schema.")
    else:
        try:
            pkgs = get_schema_objects(schema, "PACKAGE")
            tbls = get_tables(schema)
            seqs = get_sequences(schema)
            package_text.config(state='normal')          # Enable editing to update text
            package_text.delete(1.0, tk.END)              # Clear previous content
            
            package_text.insert(tk.END, f"Packages in schema '{schema}':\n")
            if pkgs:
                for p in pkgs:
                    package_text.insert(tk.END, f"  - {p}\n")
            else:
                package_text.insert(tk.END, "  (No packages found)\n")

            package_text.insert(tk.END, f"\nTables in schema '{schema}':\n")
            if tbls:
                for t in tbls:
                    package_text.insert(tk.END, f"  - {t}\n")
            else:
                package_text.insert(tk.END, "  (No tables found)\n")

            package_text.insert(tk.END, f"\nSequences in schema '{schema}':\n")
            if seqs:
                for s in seqs:
                    package_text.insert(tk.END, f"  - {s}\n")
            else:
                package_text.insert(tk.END, "  (No sequences found)\n")

            package_text.config(state='disabled')         # Disable editing again after updating
        except Exception as e:
            messagebox.showerror("Error", f"Failed to fetch packages/sequences:\n{str(e)}")
    

# Button to refresh package and sequence info for the selected schema
ttk.Button(pkg_top_frame, text="Refresh", command=refresh_package_info).pack(side="left", padx=5)

# ScrolledText widget to display package and sequence lists
package_text = scrolledtext.ScrolledText(package_frame, wrap=tk.WORD)
package_text.pack(fill="both", expand=True)
package_text.config(state='disabled')  # Initially read-only

# --- Extract Package Content Tab ---
pkg_extract_frame = ttk.Frame(notebook)
notebook.add(pkg_extract_frame, text="Extract Package")

pkg_form_frame = ttk.Frame(pkg_extract_frame)
pkg_form_frame.pack(padx=10, pady=10, fill="x")

schema_input_var = tk.StringVar()
package_input_var = tk.StringVar()

ttk.Label(pkg_form_frame, text="Schema:").pack(side="left")
ttk.Entry(pkg_form_frame, textvariable=schema_input_var, width=30).pack(side="left", padx=5)

ttk.Label(pkg_form_frame, text="Package:").pack(side="left")
ttk.Entry(pkg_form_frame, textvariable=package_input_var, width=30).pack(side="left", padx=5)

pkg_output_text = scrolledtext.ScrolledText(pkg_extract_frame, wrap=tk.WORD, height=30)
pkg_output_text.pack(fill="both", expand=True)
pkg_output_text.config(state="disabled")

def extract_package_content():
    schema = schema_input_var.get()
    pkg = package_input_var.get()
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

        pkg_output_text.config(state="normal")
        pkg_output_text.delete("1.0", tk.END)

        pkg_output_text.tag_configure("highlight", background="#ffffcc")

        # Insert with line numbers and highlight procedures/functions
        for line_num, text in rows:
            numbered_line = f"{line_num:>4}: {text.rstrip()}\n"
            pkg_output_text.insert(tk.END, numbered_line)
            if re.search(r"\b(PROCEDURE|FUNCTION)\b", text, re.IGNORECASE):
                line_start = f"{line_num}.0"
                line_end = f"{line_num}.end"
                pkg_output_text.tag_add("highlight", line_start, line_end)

        pkg_output_text.config(state="disabled")

    except Exception as e:
        messagebox.showerror("Error", str(e))

btn_extract = ttk.Button(pkg_form_frame, text="Extract", command=extract_package_content)
def jump_to_line():
    line_num = tk.simpledialog.askinteger("Jump to Line", "Enter line number:")
    if line_num:
        index = f"{line_num}.0"
        pkg_output_text.see(index)
        pkg_output_text.tag_remove("jump", "1.0", tk.END)
        pkg_output_text.tag_configure("jump", background="#cceeff")
        pkg_output_text.tag_add("jump", index, f"{line_num}.end")

ttk.Button(pkg_form_frame, text="Jump to Line", command=jump_to_line).pack(side="left", padx=5)
btn_extract.pack(side="left", padx=5)

app.mainloop()

