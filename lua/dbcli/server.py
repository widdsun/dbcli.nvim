#!/usr/bin/env python3
"""
dbcli completion and execution server: High-performance backend for Neovim.
Reuses pgcli and litecli (dbcli) context-aware completion and execution engines.
"""

import sys
import os
import glob
import json
import time
import logging
import traceback
import threading
from typing import Dict, Any, List, Optional, Tuple

# Automatically add Homebrew, pipx, and virtualenv site-packages to sys.path
def _setup_python_paths():
    candidates = [
        "/opt/homebrew/Cellar/pgcli/*/libexec/lib/python*/site-packages",
        "/opt/homebrew/Cellar/litecli/*/libexec/lib/python*/site-packages",
        "/usr/local/Cellar/pgcli/*/libexec/lib/python*/site-packages",
        "/usr/local/Cellar/litecli/*/libexec/lib/python*/site-packages",
        os.path.expanduser("~/.local/pipx/venvs/pgcli/lib/python*/site-packages"),
        os.path.expanduser("~/.local/pipx/venvs/litecli/lib/python*/site-packages"),
    ]
    for pattern in candidates:
        for p in glob.glob(pattern):
            if p not in sys.path and os.path.isdir(p):
                sys.path.insert(0, p)

_setup_python_paths()

try:
    from prompt_toolkit.document import Document
    from prompt_toolkit.completion import CompleteEvent
except ImportError:
    pass

try:
    import cli_helpers.tabular_output as t_out
except ImportError:
    t_out = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("dbcli_server")


def to_string(val: Any) -> str:
    """Convert prompt_toolkit FormattedText or other objects to plain string."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, (list, tuple)):
        parts = []
        for item in val:
            if isinstance(item, (list, tuple)) and len(item) > 1:
                parts.append(str(item[1]))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(val)


def map_kind_and_detail(label: str, meta_str: str, completer: Any) -> Tuple[str, str]:
    """Map completion item to LSP CompletionItemKind string and detail."""
    m = meta_str.lower().strip()
    
    if m:
        if any(k in m for k in ["func", "function", "procedure", "routine"]):
            return "Function", meta_str
        if any(k in m for k in ["table", "relation"]):
            return "Class", meta_str
        if any(k in m for k in ["column", "field"]):
            return "Field", meta_str
        if any(k in m for k in ["view", "materialized view"]):
            return "Interface", meta_str
        if any(k in m for k in ["schema", "database", "catalog"]):
            return "Module", meta_str
        if any(k in m for k in ["type", "datatype", "enum", "domain"]):
            return "TypeParameter", meta_str
        if any(k in m for k in ["keyword", "statement"]):
            return "Keyword", meta_str
        if any(k in m for k in ["alias"]):
            return "Variable", meta_str
        if any(k in m for k in ["special", "command"]):
            return "Operator", meta_str
        return "Text", meta_str

    if completer:
        upper_label = label.upper()
        lower_label = label.lower()
        
        funcs = getattr(completer, "functions", set())
        if upper_label in funcs or lower_label in funcs or label in funcs:
            return "Function", "function"

        kws = getattr(completer, "keywords", set())
        if upper_label in kws or label in kws:
            return "Keyword", "keyword"

        dbmeta = getattr(completer, "dbmetadata", {})
        tables = dbmeta.get("tables", {})
        if isinstance(tables, dict):
            for sch, t_dict in tables.items():
                if isinstance(t_dict, dict) and (label in t_dict or lower_label in t_dict):
                    return "Class", f"table ({sch})"
                elif isinstance(t_dict, (list, set)) and (label in t_dict or lower_label in t_dict):
                    return "Class", f"table ({sch})"

        cols = dbmeta.get("columns", {})
        if isinstance(cols, dict):
            for t_name, c_list in cols.items():
                if isinstance(c_list, (list, set)) and (label in c_list or lower_label in c_list):
                    return "Field", f"column ({t_name})"

    return "Text", ""


FORMAT_ALIASES = {
    "markdown": "github",
}


def format_table(rows, headers, format_name: str = "psql") -> str:
    """Format tabular data into configurable table format."""
    if not rows and not headers:
        return ""
    if format_name:
        format_name = FORMAT_ALIASES.get(format_name.lower().strip(), format_name)
    if t_out:
        try:
            formatted = t_out.format_output(rows, headers, format_name=format_name)
            return "\n".join(formatted)
        except Exception as e:
            logger.warning(f"Formatting with {format_name} failed: {e}, falling back to psql")
            try:
                formatted = t_out.format_output(rows, headers, format_name="psql")
                return "\n".join(formatted)
            except Exception:
                pass
    
    # Fallback simple ASCII formatter
    headers = [str(h) for h in (headers or [])]
    str_rows = [[str(cell) for cell in row] for row in rows]
    col_widths = [len(h) for h in headers]
    for row in str_rows:
        for i, cell in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(cell))
            else:
                col_widths.append(len(cell))
    
    sep = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"
    header_line = "| " + " | ".join(h.ljust(w) for h, w in zip(headers, col_widths)) + " |"
    lines = [sep, header_line, sep]
    for row in str_rows:
        padded = [cell.ljust(w) for cell, w in zip(row, col_widths)]
        lines.append("| " + " | ".join(padded) + " |")
    lines.append(sep)
    return "\n".join(lines)


class DBEngine:
    def __init__(self, db_type: str, uri_or_path: str):
        self.db_type = db_type
        self.uri = uri_or_path
        self.completer = None
        self.executor = None
        self.lock = threading.Lock()
        self.is_ready = False
        self.last_error = None
        self._init_completer()

    def _init_completer(self):
        try:
            if self.db_type == "postgres":
                self._init_postgres()
            elif self.db_type == "sqlite":
                self._init_sqlite()
            else:
                self._init_generic()
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Failed to initialize engine for {self.uri}: {e}")
            self._init_generic()

    def _init_postgres(self):
        try:
            from pgcli.pgcompleter import PGCompleter
            from pgcli.pgexecute import PGExecute
            from pgcli.completion_refresher import CompletionRefresher
            
            conn_dsn = self.uri
            self.completer = PGCompleter(smart_completion=True)
            self.executor = PGExecute(dsn=conn_dsn)
            
            refresher = CompletionRefresher()
            def on_refreshed(new_completer):
                with self.lock:
                    self.completer = new_completer
                    self.is_ready = True
                    logger.info(f"Postgres metadata refreshed for {self.uri}")

            refresher.refresh(self.executor, None, [on_refreshed])
            self.is_ready = True
        except Exception as e:
            logger.warning(f"Postgres direct connect failed, using generic PG completer: {e}")
            from pgcli.pgcompleter import PGCompleter
            self.completer = PGCompleter(smart_completion=True)
            self.is_ready = True

    def _init_sqlite(self):
        try:
            import sqlite3
            from litecli.sqlexecute import SQLExecute
            from litecli.sqlcompleter import SQLCompleter

            db_path = self.uri
            if db_path.startswith("sqlite://"):
                db_path = db_path[len("sqlite://"):]
            elif db_path.startswith("sqlite:"):
                db_path = db_path[len("sqlite:"):]
            
            db_path = os.path.expanduser(db_path)
            self.executor = SQLExecute(db_path)
            self.completer = SQLCompleter()

            # Populate metadata
            self.completer.extend_database_names(self.executor.databases())
            self.completer.extend_schemata(self.executor.dbname)
            self.completer.set_dbname(self.executor.dbname)

            table_cols = list(self.executor.table_columns())
            self.completer.extend_relations(table_cols, kind="tables")
            self.completer.extend_columns(table_cols, kind="tables")
            
            try:
                self.completer.extend_functions(self.executor.functions())
            except Exception:
                pass

            self.is_ready = True
            logger.info(f"SQLite metadata initialized for {db_path}")
        except Exception as e:
            logger.warning(f"SQLite init failed, using generic SQLite completer: {e}")
            from litecli.sqlcompleter import SQLCompleter
            self.completer = SQLCompleter()
            self.is_ready = True

    def _init_generic(self):
        try:
            from litecli.sqlcompleter import SQLCompleter
            self.completer = SQLCompleter()
        except ImportError:
            try:
                from pgcli.pgcompleter import PGCompleter
                self.completer = PGCompleter(smart_completion=True)
            except ImportError:
                self.completer = None
        self.is_ready = True

    def refresh(self):
        self._init_completer()

    def get_completions(self, text: str, cursor_pos: int) -> List[Dict[str, Any]]:
        if not self.completer:
            return []
        
        doc = Document(text=text, cursor_position=cursor_pos)
        event = CompleteEvent(text_inserted=True)
        
        with self.lock:
            try:
                raw_completions = list(self.completer.get_completions(doc, event))
            except Exception as e:
                logger.error(f"Error during completion: {e}")
                return []

        results = []
        for c in raw_completions:
            raw_meta = to_string(getattr(c, "display_meta", "") or "")
            display = to_string(getattr(c, "display", "") or "")
            label = c.text
            kind, detail = map_kind_and_detail(label, raw_meta, self.completer)

            results.append({
                "label": label,
                "display": display or label,
                "detail": detail,
                "kind": kind,
                "insertText": label,
                "startPosition": getattr(c, "start_position", 0),
            })
        return results

    def execute(self, query: str, format_name: str = "psql") -> Dict[str, Any]:
        """Execute one or more SQL statements and return formatted results."""
        if not self.executor:
            self._init_completer()
            if not self.executor:
                return {
                    "success": False,
                    "error": f"No active database executor for {self.uri or '(unspecified database)'}",
                }

        t_start = time.time()
        output_blocks = []
        has_error = False
        error_msg = ""

        try:
            with self.lock:
                if self.db_type == "postgres":
                    for res in self.executor.run(query):
                        if len(res) >= 6:
                            title, rows, headers, status, q_text, success = res[:6]
                        else:
                            title, rows, headers, status = res[:4]
                            success = True

                        if not success:
                            has_error = True
                            error_msg = str(status or "Query execution failed")
                            output_blocks.append(f"[ERROR] {error_msg}")
                            continue

                        if rows is not None and headers is not None:
                            table_str = format_table(rows, headers, format_name=format_name)
                            output_blocks.append(table_str)
                        if status:
                            output_blocks.append(f"[{status}]")
                        output_blocks.append("")

                elif self.db_type == "sqlite":
                    for title, rows, headers, status in self.executor.run(query):
                        if rows is not None and headers is not None:
                            table_str = format_table(rows, headers, format_name=format_name)
                            output_blocks.append(table_str)
                        if status:
                            output_blocks.append(f"[{status}]")
                        output_blocks.append("")
                else:
                    return {"success": False, "error": f"Unknown database type: {self.db_type}"}

            elapsed_ms = (time.time() - t_start) * 1000
            formatted_output = "\n".join(output_blocks).strip()
            
            return {
                "success": not has_error,
                "output": formatted_output,
                "elapsed_ms": round(elapsed_ms, 2),
                "error": error_msg if has_error else None,
            }
        except Exception as e:
            elapsed_ms = (time.time() - t_start) * 1000
            return {
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc(),
                "elapsed_ms": round(elapsed_ms, 2),
            }


class DBService:
    def __init__(self):
        self.engines: Dict[str, DBEngine] = {}
        self.default_engine = DBEngine("generic", "")

    def detect_db_type(self, uri: str) -> str:
        u = uri.strip()
        if u.startswith("postgresql://") or u.startswith("postgres://"):
            return "postgres"
        if u.startswith("sqlite://") or u.startswith("sqlite:") or u.endswith(".db") or u.endswith(".sqlite") or u.endswith(".sqlite3") or u == ":memory:":
            return "sqlite"
        if os.path.isfile(os.path.expanduser(u)):
            return "sqlite"
        return "postgres" if "5432" in u or "port=" in u else "sqlite"

    def get_or_create_engine(self, uri: str) -> DBEngine:
        if not uri or uri == "":
            return self.default_engine
        
        if uri not in self.engines:
            db_type = self.detect_db_type(uri)
            self.engines[uri] = DBEngine(db_type, uri)
        return self.engines[uri]

    def handle_request(self, req: Dict[str, Any]) -> Dict[str, Any]:
        req_id = req.get("id")
        action = req.get("action", req.get("method"))
        
        if action == "ping":
            return {"id": req_id, "status": "ok", "pong": True}

        elif action == "connect":
            uri = req.get("uri") or req.get("url") or ""
            db_type = req.get("db_type") or self.detect_db_type(uri)
            engine = DBEngine(db_type, uri)
            self.engines[uri] = engine
            return {
                "id": req_id,
                "status": "ok",
                "db_type": db_type,
                "uri": uri,
                "ready": engine.is_ready,
                "error": engine.last_error,
            }

        elif action == "refresh":
            uri = req.get("uri") or req.get("url") or ""
            if uri in self.engines:
                self.engines[uri].refresh()
            return {"id": req_id, "status": "ok"}

        elif action == "complete":
            uri = req.get("db") or req.get("uri") or ""
            text = req.get("text", "")
            cursor_pos = req.get("cursor_pos", len(text))
            
            engine = self.get_or_create_engine(uri)
            items = engine.get_completions(text, cursor_pos)
            return {"id": req_id, "status": "ok", "items": items}

        elif action == "execute":
            uri = req.get("db") or req.get("uri") or ""
            query = req.get("query") or req.get("sql") or ""
            format_name = req.get("format") or "psql"
            
            engine = self.get_or_create_engine(uri)
            result = engine.execute(query, format_name=format_name)
            return {
                "id": req_id,
                "status": "ok" if result.get("success") else "error",
                "result": result,
            }

        elif action == "disconnect":
            uri = req.get("uri") or ""
            if uri in self.engines:
                del self.engines[uri]
            return {"id": req_id, "status": "ok"}

        elif action == "status":
            connections = [
                {"uri": k, "db_type": v.db_type, "ready": v.is_ready}
                for k, v in self.engines.items()
            ]
            return {"id": req_id, "status": "ok", "connections": connections}

        else:
            return {"id": req_id, "status": "error", "message": f"Unknown action: {action}"}


def main():
    service = DBService()
    logger.info("dbcli completion & execution server started")

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue

            try:
                req = json.loads(line)
                resp = service.handle_request(req)
            except Exception as e:
                resp = {
                    "id": req.get("id") if "req" in locals() and isinstance(req, dict) else None,
                    "status": "error",
                    "message": str(e),
                    "traceback": traceback.format_exc(),
                }
            
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
        except (KeyboardInterrupt, SystemExit):
            break
        except Exception as e:
            logger.error(f"Fatal error in main loop: {e}")
            break

if __name__ == "__main__":
    main()
