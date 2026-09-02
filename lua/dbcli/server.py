#!/usr/bin/env python3
"""
dbcli completion and execution server: High-performance backend for Neovim.
Reuses pgcli and litecli (dbcli) context-aware completion and execution engines.
"""

import sys
import os
import re
import glob
import json
import time
import logging
import traceback
import threading
from urllib.parse import urlparse, unquote
from typing import Dict, Any, List, Optional, Tuple

# Automatically add Homebrew, pipx, and virtualenv site-packages to sys.path
def _setup_python_paths():
    candidates = [
        "/opt/homebrew/Cellar/pgcli/*/libexec/lib/python*/site-packages",
        "/opt/homebrew/Cellar/litecli/*/libexec/lib/python*/site-packages",
        "/opt/homebrew/Cellar/mycli/*/libexec/lib/python*/site-packages",
        "/usr/local/Cellar/pgcli/*/libexec/lib/python*/site-packages",
        "/usr/local/Cellar/litecli/*/libexec/lib/python*/site-packages",
        "/usr/local/Cellar/mycli/*/libexec/lib/python*/site-packages",
        os.path.expanduser("~/.local/pipx/venvs/pgcli/lib/python*/site-packages"),
        os.path.expanduser("~/.local/pipx/venvs/litecli/lib/python*/site-packages"),
        os.path.expanduser("~/.local/pipx/venvs/mycli/lib/python*/site-packages"),
    ]
    for pattern in candidates:
        for p in glob.glob(pattern):
            if p not in sys.path and os.path.isdir(p):
                sys.path.insert(0, p)

_setup_python_paths()

try:
    from prompt_toolkit.document import Document
    from prompt_toolkit.completion import CompleteEvent, Completion
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


def unquote_identifier(s: str) -> str:
    """Strip quotes/brackets from SQL identifier."""
    if not s:
        return ""
    if (s.startswith('"') and s.endswith('"')) or \
       (s.startswith('`') and s.endswith('`')) or \
       (s.startswith('[') and s.endswith(']')):
        return s[1:-1]
    return s


def parse_mysql_uri(uri: str) -> Dict[str, Any]:
    """Parse mysql:// or mariadb:// URI into core 5 connection parameters."""
    u = urlparse(uri)
    dbname = u.path.lstrip("/") if u.path else ""
    user = unquote(u.username) if u.username else None
    password = unquote(u.password) if u.password else None
    host = u.hostname or "localhost"
    port = u.port or 3306
    return {
        "database": dbname,
        "user": user,
        "password": password,
        "host": host,
        "port": int(port),
    }


COMMON_SQL_RESERVED = {
    "USER", "CURRENT_USER", "SESSION_USER", "SYSTEM_USER",
    "PASSWORD", "KEY", "INDEX", "DESC", "ASC", "TYPE", "STATUS", "ROLE", "VALUE", "VALUES",
}


def patch_completers():
    """Patch litecli and pgcli completers to support identifier quote triggers (\", `) and common SQL reserved words."""
    try:
        from litecli.sqlcompleter import SQLCompleter
        import litecli.sqlcompleter as sc

        orig_litecli_init = SQLCompleter.__init__
        def patched_litecli_init(self, *args, **kwargs):
            orig_litecli_init(self, *args, **kwargs)
            self.reserved_words.update(COMMON_SQL_RESERVED)
        SQLCompleter.__init__ = patched_litecli_init

        def litecli_escape_name(self, name: str) -> str:
            if isinstance(name, bytes):
                name = name.decode("utf-8", "replace")
            if name and ((not self.name_pattern.match(name)) or (name.upper() in self.reserved_words) or (name.upper() in self.functions)):
                return f'"{name}"'
            return name
        SQLCompleter.escape_name = litecli_escape_name

        def litecli_find_matches(
            text,
            collection,
            start_only=False,
            fuzzy=True,
            casing=None,
            punctuations="most_punctuations",
        ):
            last = sc.last_word(text, include=punctuations)
            orig_len = len(last)
            quote_char = None
            if last and last[0] in ('"', '`'):
                quote_char = last[0]
                clean_text = last[1:]
            else:
                clean_text = last

            match_text = clean_text.lower()
            completions = []

            if fuzzy:
                regex = ".*?".join(map(re.escape, match_text))
                pat = re.compile("(%s)" % regex)
                for item in sorted(collection):
                    raw_item = unquote_identifier(item)
                    r = pat.search(raw_item.lower())
                    if r:
                        completions.append((len(r.group()), r.start(), item, raw_item))
            else:
                match_end_limit = len(match_text) if start_only else None
                for item in sorted(collection):
                    raw_item = unquote_identifier(item)
                    match_point = raw_item.lower().find(match_text, 0, match_end_limit)
                    if match_point >= 0:
                        completions.append((len(match_text), match_point, item, raw_item))

            if casing == "auto":
                casing = "lower" if clean_text and clean_text[-1].islower() else "upper"

            def format_item(item, raw_item):
                if raw_item == '*':
                    return '*'
                if quote_char == '"':
                    return f'"{raw_item}"'
                elif quote_char == '`':
                    return f'`{raw_item}`'

                # Use double quotes for SQLite identifier escaping by default
                if item.startswith('`'):
                    return f'"{raw_item}"'
                if re.search(r'[^a-zA-Z0-9_]', raw_item) and not (item.startswith('`') or item.startswith('"')):
                    return f'"{raw_item}"'

                if casing == "upper":
                    return item.upper()
                elif casing == "lower":
                    return item.lower()
                return item

            for x, y, item, raw_item in sorted(completions):
                yield Completion(
                    format_item(item, raw_item),
                    -orig_len,
                    display=raw_item,
                )

        SQLCompleter.find_matches = staticmethod(litecli_find_matches)
    except Exception as e:
        logger.debug(f"Failed to patch litecli SQLCompleter: {e}")

    try:
        from pgcli.pgcompleter import PGCompleter, _Candidate, Match
        import pgcli.pgcompleter as pc

        orig_pg_init = PGCompleter.__init__
        def patched_pg_init(self, *args, **kwargs):
            orig_pg_init(self, *args, **kwargs)
            self.reserved_words.update(COMMON_SQL_RESERVED)
        PGCompleter.__init__ = patched_pg_init

        def pgcli_find_matches(self, text, collection, mode="fuzzy", meta=None):
            if not collection:
                return []
            prio_order = [
                "keyword", "function", "view", "table", "datatype", "database",
                "schema", "column", "table alias", "join", "name join", "fk join", "table format"
            ]
            type_priority = prio_order.index(meta) if meta in prio_order else -1

            last = pc.last_word(text, include="most_punctuations")
            orig_len = len(last)
            quote_char = None
            if last and last[0] == '"':
                quote_char = '"'
                clean_text = last[1:].lower()
            else:
                clean_text = last.lower()

            if mode == "fuzzy":
                fuzzy = True
                priority_func = self.prioritizer.name_count
            else:
                fuzzy = False
                priority_func = self.prioritizer.keyword_count

            if fuzzy:
                regex = ".*?".join(map(re.escape, clean_text))
                pat = re.compile("(%s)" % regex)

                def _match(item):
                    raw = unquote_identifier(item).lower()
                    if raw[: len(clean_text) + 1] in (clean_text, clean_text + " "):
                        return float("Infinity"), -1
                    r = pat.search(raw)
                    if r:
                        return -len(r.group()), -r.start()
            else:
                match_end_limit = len(clean_text)

                def _match(item):
                    raw = unquote_identifier(item).lower()
                    match_point = raw.find(clean_text, 0, match_end_limit)
                    if match_point >= 0:
                        return -float("Infinity"), -match_point

            def format_item(item, raw_item):
                if raw_item == '*':
                    return '*'
                if quote_char == '"':
                    return f'"{raw_item}"'

                if re.search(r'[^a-zA-Z0-9_]', raw_item) and not item.startswith('"'):
                    return f'"{raw_item}"'
                return item

            matches = []
            for cand in collection:
                if isinstance(cand, _Candidate):
                    item, prio, display_meta, synonyms, prio2, display = cand
                    if display_meta is None:
                        display_meta = meta
                    syn_matches = (_match(x) for x in synonyms)
                    syn_matches = [m for m in syn_matches if m]
                    sort_key = max(syn_matches) if syn_matches else None
                else:
                    item, display_meta, prio, prio2, display = cand, meta, 0, 0, cand
                    sort_key = _match(cand)

                if sort_key:
                    if display_meta and len(display_meta) > 50:
                        display_meta = display_meta[:47] + "..."
                    raw_item = unquote_identifier(item)
                    lexical_priority = (
                        tuple(0 if c in " _" else -ord(c) for c in raw_item.lower()) + (1,) + tuple(c for c in item)
                    )
                    formatted_item = format_item(self.case(item), raw_item)
                    priority = (
                        sort_key,
                        type_priority,
                        prio,
                        priority_func(item),
                        prio2,
                        lexical_priority,
                    )
                    matches.append(
                        Match(
                            completion=Completion(
                                text=formatted_item,
                                start_position=-orig_len,
                                display_meta=display_meta,
                                display=raw_item,
                            ),
                            priority=priority,
                        )
                    )
            return matches

        PGCompleter.find_matches = pgcli_find_matches
    except Exception as e:
        logger.debug(f"Failed to patch pgcli PGCompleter: {e}")

    try:
        from mycli.sqlcompleter import SQLCompleter
        import mycli.sqlcompleter as mc

        def mycli_find_matches(
            text,
            collection,
            start_only=False,
            fuzzy=True,
            casing=None,
            punctuations="most_punctuations",
        ):
            last = mc.last_word(text, include=punctuations)
            orig_len = len(last)
            quote_char = None
            if last and last[0] in ('"', '`'):
                quote_char = last[0]
                clean_text = last[1:]
            else:
                clean_text = last

            match_text = clean_text.lower()
            completions = []

            if fuzzy:
                regex = ".*?".join(map(re.escape, match_text))
                pat = re.compile("(%s)" % regex)
                for item in sorted(collection):
                    raw_item = unquote_identifier(item)
                    r = pat.search(raw_item.lower())
                    if r:
                        completions.append((len(r.group()), r.start(), item, raw_item))
            else:
                match_end_limit = len(match_text) if start_only else None
                for item in sorted(collection):
                    raw_item = unquote_identifier(item)
                    match_point = raw_item.lower().find(match_text, 0, match_end_limit)
                    if match_point >= 0:
                        completions.append((len(match_text), match_point, item, raw_item))

            if casing == "auto":
                casing = "lower" if clean_text and clean_text[-1].islower() else "upper"

            def format_item(item, raw_item):
                if raw_item == '*':
                    return '*'
                if quote_char == '"':
                    return f'"{raw_item}"'
                elif quote_char == '`':
                    return f'`{raw_item}`'

                if re.search(r'[^a-zA-Z0-9_]', raw_item) and not (item.startswith('`') or item.startswith('"')):
                    return f'`{raw_item}`'

                if casing == "upper":
                    return item.upper()
                elif casing == "lower":
                    return item.lower()
                return item

            for x, y, item, raw_item in sorted(completions):
                yield Completion(
                    format_item(item, raw_item),
                    -orig_len,
                    display=raw_item,
                )

        SQLCompleter.find_matches = staticmethod(mycli_find_matches)
    except Exception as e:
        logger.debug(f"Failed to patch mycli SQLCompleter: {e}")


patch_completers()



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
        raw_clean = unquote_identifier(label)
        raw_upper = raw_clean.upper()
        raw_lower = raw_clean.lower()

        dbmeta = getattr(completer, "dbmetadata", {})
        tables = dbmeta.get("tables", {})
        if isinstance(tables, dict):
            for sch, t_dict in tables.items():
                if isinstance(t_dict, dict):
                    for t_name, c_list in t_dict.items():
                        clean_t_name = unquote_identifier(t_name)
                        if isinstance(c_list, (list, set)):
                            for col in c_list:
                                clean_col = unquote_identifier(col)
                                if clean_col != '*' and (raw_clean == clean_col or raw_lower == clean_col.lower()):
                                    return "Field", f"column ({clean_t_name})"
                        if raw_clean == clean_t_name or raw_lower == clean_t_name.lower():
                            return "Class", f"table ({sch})"
                elif isinstance(t_dict, (list, set)):
                    for t_name in t_dict:
                        clean_t_name = unquote_identifier(t_name)
                        if raw_clean == clean_t_name or raw_lower == clean_t_name.lower():
                            return "Class", f"table ({sch})"

        cols = dbmeta.get("columns", {})
        if isinstance(cols, dict):
            for t_name, c_list in cols.items():
                clean_t_name = unquote_identifier(t_name)
                if isinstance(c_list, (list, set)):
                    for col in c_list:
                        clean_col = unquote_identifier(col)
                        if clean_col != '*' and (raw_clean == clean_col or raw_lower == clean_col.lower()):
                            return "Field", f"column ({clean_t_name})"

        funcs = getattr(completer, "functions", set())
        if raw_upper in funcs or raw_lower in funcs or raw_clean in funcs:
            return "Function", "function"

        kws = getattr(completer, "keywords", set())
        if raw_upper in kws or raw_clean in kws:
            return "Keyword", "keyword"

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
            elif self.db_type == "mysql":
                self._init_mysql()
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

    def _init_mysql(self):
        try:
            from mycli.sqlexecute import SQLExecute
            from mycli.sqlcompleter import SQLCompleter
            from mycli.completion_refresher import CompletionRefresher

            params = parse_mysql_uri(self.uri)
            self.completer = SQLCompleter(smart_completion=True)
            self.executor = SQLExecute(
                database=params["database"],
                user=params["user"],
                password=params["password"],
                host=params["host"],
                port=params["port"],
            )

            refresher = CompletionRefresher()
            def on_refreshed(new_completer):
                with self.lock:
                    self.completer = new_completer
                    self.is_ready = True
                    logger.info(f"MySQL metadata refreshed for {self.uri}")

            refresher.refresh(self.executor, None, [on_refreshed])
            self.is_ready = True
        except ImportError as e:
            self.last_error = f"mycli is not installed or importable. Please install via 'brew install mycli' or 'pip install mycli' ({e})"
            logger.warning(self.last_error)
            self._init_generic()
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Failed to initialize MySQL engine for {self.uri}: {e}")
            self._init_generic()

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
            start_pos = getattr(c, "start_position", 0)

            quote_char = None
            if start_pos < 0 and (cursor_pos + start_pos) >= 0:
                prefix = text[cursor_pos + start_pos:cursor_pos]
                if self.db_type == "postgres":
                    valid_quotes = ('"',)
                elif self.db_type == "mysql":
                    valid_quotes = ('"', '`')
                else:
                    valid_quotes = ('"', '`')
                if prefix and prefix[0] in valid_quotes:
                    quote_char = prefix[0]

            raw_item = unquote_identifier(display or label)

            results.append({
                "label": label,
                "display": display or raw_item,
                "detail": detail,
                "kind": kind,
                "insertText": label,
                "filterText": raw_item,
                "startPosition": start_pos,
                "quoteChar": quote_char,
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
                            if table_str:
                                output_blocks.append(table_str)
                                if status:
                                    output_blocks.append("")
                        if status:
                            output_blocks.append(f"[{status}]")
                        output_blocks.append("")

                elif self.db_type == "mysql":
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
                            if table_str:
                                output_blocks.append(table_str)
                                if status:
                                    output_blocks.append("")
                        if status:
                            output_blocks.append(f"[{status}]")
                        output_blocks.append("")

                elif self.db_type == "sqlite":
                    for title, rows, headers, status in self.executor.run(query):
                        if rows is not None and headers is not None:
                            table_str = format_table(rows, headers, format_name=format_name)
                            if table_str:
                                output_blocks.append(table_str)
                                if status:
                                    output_blocks.append("")
                        if status:
                            output_blocks.append(f"[{status}]")
                        output_blocks.append("")
                else:
                    return {"success": False, "error": f"Unknown database type: {self.db_type}"}

            elapsed_ms = (time.time() - t_start) * 1000
            formatted_output = "\n".join(output_blocks).strip()
            
            # Auto refresh metadata if DDL statement succeeded
            if not has_error and query:
                ddl_pattern = r'\b(CREATE|ALTER|DROP|RENAME|TRUNCATE)\b'
                if re.search(ddl_pattern, query, re.IGNORECASE):
                    logger.info(f"DDL statement detected, triggering background metadata refresh for {self.uri}")
                    threading.Thread(target=self.refresh, daemon=True).start()

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
        if u.startswith("mysql://") or u.startswith("mysql2://") or u.startswith("mariadb://"):
            return "mysql"
        if u.startswith("sqlite://") or u.startswith("sqlite:") or u.endswith(".db") or u.endswith(".sqlite") or u.endswith(".sqlite3") or u == ":memory:":
            return "sqlite"
        if os.path.isfile(os.path.expanduser(u)):
            return "sqlite"
        return "sqlite"

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

            req = None
            try:
                req = json.loads(line)
                resp = service.handle_request(req)
            except Exception as e:
                resp = {
                    "id": req.get("id") if isinstance(req, dict) else None,
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
