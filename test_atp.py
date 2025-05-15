import oracledb
import json

# Ensure Thin mode is used
print("Using Thin mode:", oracledb.is_thin_mode())  # Should print: True

# Replace these with your values
# Load config.json
with open("config.json", "r") as f:
    config = json.load(f)
    DB_USER = config["db_user"]
    DB_PASS = config["db_password"]
    DSN = config["dsn"]
    
try:
    conn = oracledb.connect(user=DB_USER, password=DB_PASS, dsn=DSN)
    print("✅ Successfully connected using Thin mode!")
    
    cursor = conn.cursor()
    cursor.execute("SELECT SYSDATE FROM dual")
    result = cursor.fetchone()
    print("Query result:", result)
    
    conn.close()

except oracledb.Error as e:
    print("❌ Connection failed:", e)
