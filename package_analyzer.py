import os
import re
import csv
from collections import defaultdict

# Improved regex patterns supporting multiline statements
OPERATION_PATTERNS = {
    "SELECT": re.compile(r"\bSELECT\b.*?(?:\bINTO\b.*?\b)?FROM\b\s+([a-zA-Z0-9_]+)", re.IGNORECASE | re.DOTALL),
    "INSERT": re.compile(r"\bINSERT\s+INTO\s+([a-zA-Z0-9_]+)", re.IGNORECASE | re.DOTALL),
    "UPDATE": re.compile(r"\bUPDATE\s+([a-zA-Z0-9_]+)\s+SET\b", re.IGNORECASE | re.DOTALL),
    "DELETE": re.compile(r"\bDELETE\s+FROM\s+([a-zA-Z0-9_]+)", re.IGNORECASE | re.DOTALL),
}

# Folder containing .sql files
current_dir = os.path.dirname(__file__)
package_folder = os.path.join(current_dir, "packages")
output_file = os.path.join(current_dir, "plsql_table_analysis.csv")

# Data structure to store the results
results = defaultdict(lambda: {"count": 0, "lines": [], "files": set()})

# List of all .sql files
sql_files = [f for f in os.listdir(package_folder) if f.lower().endswith(".sql")]

if not sql_files:
    print("No .sql files found in the 'packages' folder.")
else:
    for filename in sql_files:
        filepath = os.path.join(package_folder, filename)
        with open(filepath, 'r', encoding='utf-8') as file:
            content = file.read()

        # Break content into statements using semicolon
        statements = re.split(r";\s*(?=\n|\Z)", content)
        base_lines = content.splitlines()

        # Track processed lines to find line numbers
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

    # Write to CSV
    with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Table Name", "Operation", "Count", "Line Numbers", "File Name"])
        for (table, operation, filename), info in sorted(results.items()):
            writer.writerow([
                table,
                operation,
                info["count"],
                ", ".join(map(str, sorted(info["lines"]))),
                filename
            ])

    print(f"Analysis complete. Results saved to: {output_file}")
