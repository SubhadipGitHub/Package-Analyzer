
# 🧠 Oracle ATP Analyzer - SQL/PLSQL UI Tool

A **Python desktop GUI application** to connect securely to your **Oracle Autonomous Transaction Processing (ATP)** database and visually analyze **Tables, Packages, and Sequences** in your schema.

Built using `Tkinter`, `python-oracledb` (Thin mode), and `re` for SQL/PLSQL parsing.

---

## ✨ Features

✅ **TLS-based secure Oracle ATP connection**  
✅ Visual GUI built with Tkinter  
✅ Table Analysis:
- List columns, datatypes, constraints, indexes
- Show total record count  
- Track which packages use the table (with line numbers and operation types)  

✅ Package Analysis:
- List all stored packages and bodies  
- Analyze SQL/PLSQL operations across package source  

✅ Sequence Viewer  
✅ Export results to CSV  
✅ Multi-threaded UI for smooth analysis

---

## 🖼️ UI Preview

| Table Analyzer Tab | Package Browser Tab |
|--------------------|---------------------|
| ![Table UI](docs/table_ui.png) | ![Package UI](docs/package_ui.png) |

---

## 🔐 Requirements

- Python 3.8 – 3.12  
- Oracle Autonomous Database with TLS enabled  
- Oracle ATP credentials (username/password + TLS connection string)

---

## 📦 Dependencies

Install with:

```bash
pip install -r requirements.txt
````

**`requirements.txt`:**

```txt
oracledb>=1.4.0
```

If needed:

```bash
python -m pip install --upgrade pip
pip install oracledb
```

---

## 📁 Project Structure

```
oracle-atp-analyzer/
├── analyzer.py           # Main GUI application
├── config.json           # DB connection settings
├── requirements.txt
├── README.md             # This file
└── docs/
    ├── table_ui.png
    └── package_ui.png
```

---

## 🧩 config.json Format

```json
{
  "db_user": "MY_SCHEMA",
  "db_password": "mypassword",
  "dsn": "myadb.region.oraclecloud.com/your_db_high.adb.oraclecloud.com"
}
```

You can find your DSN from the **Oracle ATP Connection settings** under the "Database Connection" section → Copy the **"TLS" connect string**.

---

## 🚀 Running the App

```bash
python analyzer.py
```

> ✅ Ensure you are connected to the internet and port `1522` is open for ATP.

---

## 🔍 Example: Table Usage Output

```
Columns:
  - ID (NUMBER)
  - NAME (VARCHAR2)

Constraints:
  - PK_EMPLOYEE (P)

Indexes:
  - IDX_EMP_NAME (UNIQUE)

Total Records: 1200

Usage in Packages:
  - Package: HR_UTILS, Operation: SELECT, Lines: 23, 45
  - Package: PAYROLL_PROC, Operation: UPDATE, Lines: 12

Total packages using EMPLOYEE: 2
```

---

## 📤 Export to CSV

Click **"Export to CSV"** to save the analysis result.

---

## 🛠️ Troubleshooting

* **"connection was forcibly closed"** → Check firewall or VCN rules; port 1522 must be open
* **DPY-3016: cryptography import error** → Reinstall Python, make sure OpenSSL is correctly installed
* **TLS fails** → Confirm you’re using the correct DSN and credentials from Oracle ATP’s console

---

## 📚 Resources

* [Oracle python-oracledb Docs](https://python-oracledb.readthedocs.io/en/latest/)
* [Oracle ATP Docs](https://docs.oracle.com/en/cloud/paas/autonomous-database/index.html)

---

## 🧑‍💻 Author

Built by \[Your Name]
Licensed under MIT License.

---

## 💡 Contributions

Feel free to fork, improve or raise issues.
PRs are welcome!

```

---

Let me know if you'd like this README in a downloadable `.md` file or need help creating the `docs/` images or icons.
```
