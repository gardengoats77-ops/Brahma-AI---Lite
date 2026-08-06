"""
Database Query Plugin for REX
Query SQLite, PostgreSQL, and MySQL databases with natural language.
Translates natural language questions into SQL using Gemini,
executes queries safely, and returns formatted results.
"""

import json
import sqlite3
import os
from pathlib import Path
from typing import Optional
from datetime import datetime

import requests
from core.error_handler import log_error

BASE_DIR = Path(__file__).parent.parent
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
DB_CONFIG_PATH = BASE_DIR / "config" / "database_connections.json"


def _get_api_keys() -> dict:
    """Load API keys from config."""
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _get_gemini_key() -> str:
    """Get Gemini API key."""
    return _get_api_keys().get("gemini_api_key", "")


# ═══════════════════════════════════════════════════════════════════
# Database Connection Management
# ═══════════════════════════════════════════════════════════════════

def _load_db_connections() -> dict:
    """Load saved database connections."""
    try:
        if DB_CONFIG_PATH.exists():
            return json.loads(DB_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as _e:
        log_error(_e, context="actions.db_query", severity="debug")
    return {}


def _save_db_connections(connections: dict) -> None:
    """Save database connections."""
    DB_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_CONFIG_PATH.write_text(json.dumps(connections, indent=2, default=str), encoding="utf-8")


def _get_db_connection(name: str) -> dict:
    """Get a specific database connection config."""
    connections = _load_db_connections()
    return connections.get(name, {})


def _connect_sqlite(db_path: str) -> sqlite3.Connection:
    """Connect to a SQLite database."""
    expanded = os.path.expanduser(db_path)
    if not os.path.exists(expanded):
        # Create new database if it doesn't exist
        Path(expanded).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(expanded)
    conn.row_factory = sqlite3.Row
    return conn


def _connect_postgres(host: str, port: int, database: str, user: str, password: str):
    """Connect to a PostgreSQL database."""
    try:
        import psycopg2
    except ImportError:
        raise RuntimeError("psycopg2 not installed. Run: pip install psycopg2-binary")
    return psycopg2.connect(
        host=host, port=port, database=database,
        user=user, password=password
    )


def _connect_mysql(host: str, port: int, database: str, user: str, password: str):
    """Connect to a MySQL database."""
    try:
        import pymysql
    except ImportError:
        raise RuntimeError("pymysql not installed. Run: pip install pymysql")
    return pymysql.connect(
        host=host, port=port, database=database,
        user=user, password=password,
        cursorclass=pymysql.cursors.DictCursor
    )


def _get_connection(name: str):
    """Get a database connection from saved config."""
    config = _get_db_connection(name)
    if not config:
        raise ValueError(f"Database connection '{name}' not found. Use db_add_connection first.")

    db_type = config.get("type", "").lower()
    if db_type == "sqlite":
        return _connect_sqlite(config["path"])
    elif db_type == "postgres":
        return _connect_postgres(
            config.get("host", "localhost"),
            config.get("port", 5432),
            config["database"],
            config.get("user", "postgres"),
            config.get("password", "")
        )
    elif db_type == "mysql":
        return _connect_mysql(
            config.get("host", "localhost"),
            config.get("port", 3306),
            config["database"],
            config.get("user", "root"),
            config.get("password", "")
        )
    else:
        raise ValueError(f"Unsupported database type: {db_type}")


# ═══════════════════════════════════════════════════════════════════
# Natural Language to SQL Translation
# ═══════════════════════════════════════════════════════════════════

def _get_schema_text(name: str) -> str:
    """Get the schema of a database as text for the LLM."""
    conn = _get_connection(name)
    try:
        cursor = conn.cursor()
        config = _get_db_connection(name)
        db_type = config.get("type", "").lower()

        schema_parts = []

        if db_type == "sqlite":
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = [row[0] for row in cursor.fetchall()]
            for table in tables:
                cursor.execute(f"PRAGMA table_info({table})")
                columns = cursor.fetchall()
                col_defs = []
                for col in columns:
                    col_defs.append(f"  {col['name']} {col['type']}")
                schema_parts.append(f"TABLE {table} (\n" + ",\n".join(col_defs) + "\n)")

        elif db_type == "postgres":
            cursor.execute("""
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                ORDER BY table_name, ordinal_position
            """)
            rows = cursor.fetchall()
            current_table = None
            col_defs = []
            for row in rows:
                table = row[0] if isinstance(row, (list, tuple)) else row.get("table_name", "")
                col = row[1] if isinstance(row, (list, tuple)) else row.get("column_name", "")
                dtype = row[2] if isinstance(row, (list, tuple)) else row.get("data_type", "")
                if table != current_table:
                    if current_table and col_defs:
                        schema_parts.append(f"TABLE {current_table} (\n" + ",\n".join(col_defs) + "\n)")
                    current_table = table
                    col_defs = []
                col_defs.append(f"  {col} {dtype}")
            if current_table and col_defs:
                schema_parts.append(f"TABLE {current_table} (\n" + ",\n".join(col_defs) + "\n)")

        elif db_type == "mysql":
            cursor.execute("SHOW TABLES")
            tables = [row[0] if isinstance(row, (list, tuple)) else list(row.values())[0] for row in cursor.fetchall()]
            for table in tables:
                cursor.execute(f"DESCRIBE {table}")
                columns = cursor.fetchall()
                col_defs = []
                for col in columns:
                    col_name = col[0] if isinstance(col, (list, tuple)) else col.get("Field", "")
                    col_type = col[1] if isinstance(col, (list, tuple)) else col.get("Type", "")
                    col_defs.append(f"  {col_name} {col_type}")
                schema_parts.append(f"TABLE {table} (\n" + ",\n".join(col_defs) + "\n)")

        return "\n\n".join(schema_parts) if schema_parts else "No tables found."

    finally:
        conn.close()


def _nl_to_sql(question: str, schema: str, db_type: str = "sqlite") -> str:
    """Translate a natural language question to SQL using Gemini."""
    api_key = _get_gemini_key()
    if not api_key:
        raise RuntimeError("Gemini API key not configured")

    dialect_hint = ""
    if db_type == "mysql":
        dialect_hint = "\nUse MySQL syntax. Use backticks for identifiers if needed."
    elif db_type == "postgres":
        dialect_hint = "\nUse PostgreSQL syntax."
    else:
        dialect_hint = "\nUse SQLite syntax."

    prompt = f"""You are a SQL expert. Given the following database schema, convert the user's natural language question into a SQL query.

SCHEMA:
{schema}
{dialect_hint}

RULES:
- Output ONLY the SQL query, nothing else
- Use proper JOIN syntax when querying multiple tables
- Use aggregate functions (COUNT, SUM, AVG, etc.) when appropriate
- Limit results to 50 rows unless the user asks for all
- Use meaningful column aliases
- Do NOT use SELECT * unless the user explicitly asks for all columns
- Be safe: do NOT generate DROP, DELETE, TRUNCATE, ALTER, or UPDATE statements
- Only generate SELECT queries

USER QUESTION: {question}

SQL QUERY:"""

    from google import genai
    client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config={"temperature": 0.1},
    )

    sql = ""
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        if content:
            for part in getattr(content, "parts", []) or []:
                text = getattr(part, "text", None)
                if text:
                    sql += text

    sql = sql.strip()
    # Clean up common LLM artifacts
    if sql.startswith("```"):
        sql = sql.split("\n", 1)[1] if "\n" in sql else sql[3:]
    if sql.endswith("```"):
        sql = sql[:-3]
    sql = sql.strip()

    # Safety check: only allow SELECT
    if not _is_safe_sql(sql):
        raise ValueError(f"Safety check blocked this query: {sql}")

    return sql


# ═══════════════════════════════════════════════════════════════════
# Query Execution & Formatting
# ═══════════════════════════════════════════════════════════════════

def _is_safe_sql(sql: str) -> bool:
    """Check if a SQL query is safe (SELECT only, no multi-statement)."""
    stripped = sql.strip()
    upper = stripped.upper()
    # Block dangerous keywords at start
    dangerous = ("DROP", "DELETE", "TRUNCATE", "ALTER", "UPDATE", "INSERT", "CREATE", "GRANT", "REVOKE")
    if any(upper.startswith(kw) for kw in dangerous):
        return False
    # Block multi-statement (semicolons outside quotes)
    # Simple check: reject if semicolon exists (covers most injection attempts)
    if ";" in stripped:
        return False
    return True


def _execute_query(name: str, sql: str, max_rows: int = 50) -> str:
    """Execute a SQL query and return formatted results."""
    conn = _get_connection(name)
    try:
        cursor = conn.cursor()
        cursor.execute(sql)

        # Check if it's a SELECT query
        if not sql.strip().upper().startswith("SELECT"):
            conn.commit()
            return f"✅ Query executed successfully. {cursor.rowcount} rows affected."

        rows = cursor.fetchmany(max_rows)

        if not rows:
            return "📭 Query returned no results."

        # Get column names
        if hasattr(cursor, "description") and cursor.description:
            columns = [desc[0] for desc in cursor.description]
        else:
            columns = list(rows[0].keys()) if isinstance(rows[0], dict) else [f"col_{i}" for i in range(len(rows[0]))]

        # Format as table
        output = f"📊 Query Results ({len(rows)} rows)\n"
        output += "=" * 60 + "\n\n"

        # Calculate column widths
        col_widths = {}
        for col in columns:
            col_widths[col] = max(len(str(col)), 12)
        for row in rows:
            for i, col in enumerate(columns):
                if isinstance(row, dict):
                    val = str(row.get(col, ""))
                else:
                    # list, tuple, or sqlite3.Row: use index access
                    val = str(row[i])
                col_widths[col] = max(col_widths[col], min(len(val), 40))

        # Header
        header = " | ".join(str(col).ljust(col_widths[col]) for col in columns)
        output += header + "\n"
        output += "-" * len(header) + "\n"

        # Rows
        for row in rows:
            vals = []
            for i, col in enumerate(columns):
                if isinstance(row, (list, tuple)):
                    val = row[i]
                elif isinstance(row, dict):
                    val = row.get(col, "")
                else:
                    # sqlite3.Row: index access
                    val = row[i]
                val_str = str(val) if val is not None else "NULL"
                vals.append(val_str[:40].ljust(col_widths[col]))
            output += " | ".join(vals) + "\n"

        if cursor.rowcount > max_rows:
            output += f"\n⚠️ Showing {max_rows} of {cursor.rowcount} total rows"

        return output

    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════
# Tool Functions
# ═══════════════════════════════════════════════════════════════════

def db_query(name: str, question: str, sql: str = "", max_rows: int = 50) -> str:
    """Query a database with natural language or direct SQL."""
    if not name:
        return "❌ Please provide a database connection name."

    if sql:
        if not _is_safe_sql(sql):
            return "❌ Safety check: Only SELECT queries are allowed."
        return _execute_query(name, sql, max_rows)

    config = _get_db_connection(name)
    if not config:
        return f"❌ Database connection '{name}' not found. Use db_add_connection first."

    schema = _get_schema_text(name)
    sql = _nl_to_sql(question, schema, config.get("type", "sqlite"))

    output = f"🤖 Generated SQL:\n```sql\n{sql}\n```\n\n"
    result = _execute_query(name, sql, max_rows)
    return output + result


def db_add_connection(
    name: str, db_type: str,
    path: str = "", host: str = "localhost", port: int = 0,
    database: str = "", user: str = "", password: str = ""
) -> str:
    """Add a database connection."""
    connections = _load_db_connections()

    if db_type.lower() == "sqlite":
        connections[name] = {
            "type": "sqlite",
            "path": path or os.path.expanduser(f"~/{name}.db"),
            "added": datetime.now().isoformat()
        }
    elif db_type.lower() == "postgres":
        connections[name] = {
            "type": "postgres",
            "host": host,
            "port": port or 5432,
            "database": database,
            "user": user or "postgres",
            "password": password,
            "added": datetime.now().isoformat()
        }
    elif db_type.lower() == "mysql":
        connections[name] = {
            "type": "mysql",
            "host": host,
            "port": port or 3306,
            "database": database,
            "user": user or "root",
            "password": password,
            "added": datetime.now().isoformat()
        }
    else:
        return f"❌ Unsupported database type: {db_type}. Use sqlite, postgres, or mysql."

    _save_db_connections(connections)
    return f"✅ Database connection '{name}' ({db_type}) saved successfully."


def db_remove_connection(name: str) -> str:
    """Remove a database connection."""
    connections = _load_db_connections()
    if name not in connections:
        return f"❌ Database connection '{name}' not found."
    del connections[name]
    _save_db_connections(connections)
    return f"✅ Database connection '{name}' removed."


def db_list_connections() -> str:
    """List all saved database connections."""
    connections = _load_db_connections()
    if not connections:
        return "📭 No database connections configured. Use db_add_connection to add one."

    output = f"🗄️ Database Connections ({len(connections)})\n"
    output += "=" * 40 + "\n\n"

    for name, config in connections.items():
        db_type = config.get("type", "unknown")
        added = config.get("added", "Unknown")
        output += f"📦 {name}\n"
        output += f"   Type: {db_type.upper()}\n"
        if db_type == "sqlite":
            output += f"   Path: {config.get('path', 'N/A')}\n"
        else:
            output += f"   Host: {config.get('host', 'N/A')}:{config.get('port', 'N/A')}\n"
            output += f"   Database: {config.get('database', 'N/A')}\n"
            output += f"   User: {config.get('user', 'N/A')}\n"
        output += f"   Added: {added}\n\n"

    return output


def db_test_connection(name: str) -> str:
    """Test a database connection."""
    config = _get_db_connection(name)
    if not config:
        return f"❌ Database connection '{name}' not found."

    try:
        conn = _get_connection(name)
        cursor = conn.cursor()
        db_type = config.get("type", "").lower()

        if db_type == "sqlite":
            cursor.execute("SELECT sqlite_version()")
            version = cursor.fetchone()[0]
        elif db_type == "postgres":
            cursor.execute("SELECT version()")
            version = cursor.fetchone()[0]
        elif db_type == "mysql":
            cursor.execute("SELECT version()")
            version = cursor.fetchone()[0]
        else:
            version = "Unknown"

        conn.close()
        return f"✅ Connection to '{name}' successful!\n   Version: {version}"
    except Exception as e:
        return f"❌ Connection failed: {e}"


def db_get_schema(name: str) -> str:
    """Get the schema/structure of a database."""
    config = _get_db_connection(name)
    if not config:
        return f"❌ Database connection '{name}' not found."

    schema = _get_schema_text(name)
    return f"📋 Schema for '{name}' ({config.get('type', 'unknown').upper()}):\n\n{schema}"


def db_execute_sql(name: str, sql: str, max_rows: int = 50) -> str:
    """Execute a direct SQL query (SELECT only)."""
    if not _is_safe_sql(sql):
        return "❌ Safety check: Only SELECT queries are allowed via direct SQL."

    return _execute_query(name, sql, max_rows)


# ═══════════════════════════════════════════════════════════════════
# Tool Definitions for Registration
# ═══════════════════════════════════════════════════════════════════

DB_TOOLS = [
    {
        "name": "db_query",
        "description": (
            "Query a database using natural language or direct SQL. "
            "Supports SQLite, PostgreSQL, and MySQL. "
            "Provide either a 'question' for natural language or 'sql' for direct queries."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING", "description": "Database connection name (from db_add_connection)"},
                "question": {"type": "STRING", "description": "Natural language question about the data"},
                "sql": {"type": "STRING", "description": "Direct SQL query (optional, overrides question)"},
                "max_rows": {"type": "INTEGER", "description": "Max rows to return (default: 50)"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "db_add_connection",
        "description": (
            "Add a database connection. "
            "Supports SQLite (local file), PostgreSQL, and MySQL."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING", "description": "Connection name (e.g., 'mydb', 'production')"},
                "db_type": {"type": "STRING", "description": "sqlite | postgres | mysql"},
                "path": {"type": "STRING", "description": "SQLite: file path (e.g., ~/data/mydb.db)"},
                "host": {"type": "STRING", "description": "Postgres/MySQL: host (default: localhost)"},
                "port": {"type": "INTEGER", "description": "Postgres/MySQL: port (default: 5432/3306)"},
                "database": {"type": "STRING", "description": "Postgres/MySQL: database name"},
                "user": {"type": "STRING", "description": "Postgres/MySQL: username"},
                "password": {"type": "STRING", "description": "Postgres/MySQL: password"}
            },
            "required": ["name", "db_type"]
        }
    },
    {
        "name": "db_remove_connection",
        "description": "Remove a saved database connection.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING", "description": "Connection name to remove"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "db_list_connections",
        "description": "List all saved database connections.",
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "db_test_connection",
        "description": "Test a database connection and show the server version.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING", "description": "Connection name to test"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "db_get_schema",
        "description": (
            "Get the schema/structure of a database. "
            "Shows all tables, columns, and types."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING", "description": "Database connection name"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "db_execute_sql",
        "description": (
            "Execute a direct SQL query. SELECT only (no INSERT/UPDATE/DELETE). "
            "Use db_query with a question for natural language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "name": {"type": "STRING", "description": "Database connection name"},
                "sql": {"type": "STRING", "description": "SQL query to execute (SELECT only)"},
                "max_rows": {"type": "INTEGER", "description": "Max rows to return (default: 50)"}
            },
            "required": ["name", "sql"]
        }
    },
]


def handle_db_tool(tool_name: str, parameters: dict, speak=None) -> str:
    """Route database tool calls to appropriate functions."""
    try:
        if tool_name == "db_query":
            return db_query(
                name=parameters.get("name", ""),
                question=parameters.get("question", ""),
                sql=parameters.get("sql", ""),
                max_rows=parameters.get("max_rows", 50)
            )
        elif tool_name == "db_add_connection":
            return db_add_connection(
                name=parameters.get("name", ""),
                db_type=parameters.get("db_type", ""),
                path=parameters.get("path", ""),
                host=parameters.get("host", "localhost"),
                port=parameters.get("port", 0),
                database=parameters.get("database", ""),
                user=parameters.get("user", ""),
                password=parameters.get("password", "")
            )
        elif tool_name == "db_remove_connection":
            return db_remove_connection(
                name=parameters.get("name", "")
            )
        elif tool_name == "db_list_connections":
            return db_list_connections()
        elif tool_name == "db_test_connection":
            return db_test_connection(
                name=parameters.get("name", "")
            )
        elif tool_name == "db_get_schema":
            return db_get_schema(
                name=parameters.get("name", "")
            )
        elif tool_name == "db_execute_sql":
            return db_execute_sql(
                name=parameters.get("name", ""),
                sql=parameters.get("sql", ""),
                max_rows=parameters.get("max_rows", 50)
            )
        else:
            return f"❌ Unknown database tool: {tool_name}"
    except Exception as e:
        return f"❌ Database tool error: {e}"
