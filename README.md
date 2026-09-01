# dbcli.nvim

⚡ **Smart, Context-Aware SQL Completion & Execution for Neovim

Reuses the powerful completion and execution engines of [pgcli](https://github.com/dbcli/pgcli) and [litecli](https://github.com/dbcli/litecli) from the [dbcli](https://www.dbcli.com/) ecosystem.

---

## ✨ Features

- 🧠 **Context-Aware Completion**: Powered by `dbcli`'s AST-based `sqlcompletion.py`:
  - Intelligent suggestions for `SELECT`, `FROM`, `JOIN`, `WHERE`, and subqueries.
  - Automatic `JOIN ON` condition inference.
  - Table alias resolution (`SELECT u. ` automatically suggests columns from `users u`).
  - **Full function completion**: Built-in & user-defined functions with parameter signatures and return types.
- ⚡ **Seamless `blink.cmp` Integration**: First-class custom completion source for `saghen/blink.cmp`.
- 🚀 **High-Performance Async Architecture**: Communicates with the Python backend via non-blocking `vim.uv` pipes without freezing Neovim.
- 📊 **SQL Execution & Formatted Results**:
  - Execute entire files or visually selected statements.
  - Formatted tabular output via `cli_helpers` (`psql`, `fancy_grid`, `github`, `vertical`, etc.).
  - Automatic connection binding via header comments (e.g. `-- db: ./app.db`).

---

## 📦 Requirements

- **Neovim** >= 0.10
- **Python 3** with `pgcli` and/or `litecli`:
  ```bash
  # macOS (Homebrew)
  brew install pgcli litecli

  # or pip / pipx
  pip install pgcli litecli prompt_toolkit cli_helpers
  ```

---

## 🛠️ Installation

### Using [lazy.nvim](https://github.com/folke/lazy.nvim)

```lua
-- lua/plugins/dbcli.lua
return {
  'your-username/dbcli.nvim', -- or dir = vim.fn.stdpath('config') .. '/my-plugins/dbcli.nvim'
  ft = { 'sql', 'mysql', 'plsql' },
  cmd = { 'DBExecute', 'DBConnect', 'DBDisconnect', 'DBFormat', 'DBRefresh', 'DBStatus' },
  opts = {
    table_format = 'psql',          -- Output table style ('psql', 'fancy_grid', 'markdown', 'github', 'double', 'vertical', etc.)
    split_direction = 'horizontal',  -- Result split: 'horizontal' or 'vertical'
    split_size = 15,                -- Split height/width
    default_keymaps = true,         -- Bind <space><enter> in SQL buffers
  },
}
```

### Configure with `blink.cmp`

Add `dbcli` to your `blink.cmp` sources:

```lua
-- lua/plugins/blink-cmp.lua
require('blink.cmp').setup({
  sources = {
    per_filetype = {
      sql = { 'dbcli', 'lsp' },
      mysql = { 'dbcli', 'lsp' },
      plsql = { 'dbcli', 'lsp' },
    },
    providers = {
      dbcli = {
        name = 'dbcli',
        module = 'dbcli.blink',
        score_offset = 100, -- Prioritize dbcli completions
      },
    },
  },
})
```

---

## 🚀 Usage

### 1. Database Connection

#### Option A: Header Comment (Recommended)
Add a comment at the top (first 15 lines) of your `.sql` file:

```sql
-- db: /path/to/app.sqlite3
-- format: fancy_grid
SELECT * FROM users;
```

Or for PostgreSQL:
```sql
-- db: postgresql://user:password@localhost:5432/mydb
SELECT * FROM orders;
```

#### Option B: User Command
```vim
:DBConnect /path/to/app.sqlite3
:DBConnect postgresql://user:pass@localhost:5432/mydb
```

---

### 2. Executing Queries

| Mode | Keymap / Command | Description |
| :--- | :--- | :--- |
| **Normal** | `<space><enter>` | Execute entire SQL buffer |
| **Visual** | `<space><enter>` | Execute visually selected statements |
| **Command** | `:DBExecute` | Execute entire buffer or range (`:'<,'>DBExecute`) |
| **Command** | `:DBExecute SELECT 1;` | Execute inline query |
| **Result Win** | `q` | Close results window |

---

### 3. Formatted Table Styles

Switch styles on the fly:
```vim
:DBFormat fancy_grid   " Tab completion supported!
:DBFormat markdown     " Standard Markdown table (alias: github)
:DBFormat vertical     " Similar to \G (great for wide rows)
:DBFormat psql         " Default classic ASCII table
```

---

## ⚙️ Configuration Options

### 1. Default Configuration (`setup`)

```lua
require('dbcli').setup({
  -- Output table formatting style for query results
  table_format = 'psql',

  -- Results split window direction ('horizontal' or 'vertical')
  split_direction = 'horizontal',

  -- Size of the results split window (height in rows or width in columns)
  split_size = 15,

  -- Automatically bind default keymaps (<space><enter>) in SQL buffers
  default_keymaps = true,

  -- Fallback database URI/path if no buffer-level database is defined
  default_db = nil,
})
```

---

### 2. Detailed Options Reference

| Option | Type | Default | Allowed / Optional Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `table_format` | `string` | `'psql'` | All 34 formats below (e.g. `'psql'`, `'markdown'`, `'fancy_grid'`, `'vertical'`, `'csv'`) | Output table rendering style for query execution results. |
| `split_direction` | `string` | `'horizontal'` | `'horizontal'`, `'vertical'` | Orientation of the query results split window. |
| `split_size` | `number` | `15` | Any positive integer (e.g. `10`, `15`, `20`, `40`, `80`) | Result window height in lines (for `horizontal`) or width in columns (for `vertical`). |
| `default_keymaps` | `boolean` | `true` | `true`, `false` | Whether to automatically bind `<space><enter>` in Normal mode (execute file) and Visual mode (execute selection). |
| `default_db` | `string` \| `nil` | `nil` | Valid SQLite path/URI, PostgreSQL DSN/URI, or `nil` | Global default database URI to connect when neither header nor buffer variable is set. |

---

### 3. Supported Table Formats (`table_format`)

All 34 formats supported by `cli_helpers` / `dbcli` (all available in `:DBFormat` Tab-completion):

- **Database Styles**: `psql` *(default)*, `psql_unicode`, `mysql`, `mysql_unicode`, `mysql_heavy`, `vertical` *(expanded \G record view)*
- **Markdown & Markup**: `markdown` *(alias: `github`)*, `pipe`, `orgtbl`, `html`, `latex`, `latex_booktabs`, `rst`, `mediawiki`, `textile`, `moinmoin`, `jira`
- **Data & Delimited**: `csv`, `csv-noheader`, `csv-tab`, `csv-tab-noheader`, `tsv`, `tsv_noheader`, `jsonl`, `jsonl_escaped`
- **Grids & ASCII**: `fancy_grid`, `grid`, `double`, `simple`, `minimal`, `plain`, `ascii`, `ascii_escaped`

---

### 4. Database URI Specifications (`default_db` / `:DBConnect` / `-- db:`)

`dbcli.nvim` automatically detects the database type based on the provided path or URI:

| Database Type | Supported URI / Path Patterns | Examples |
| :--- | :--- | :--- |
| **SQLite** | File path (relative or absolute), `sqlite://`, `sqlite:`, `:memory:` | `app.db`<br>`./data/test.sqlite3`<br>`sqlite:///var/data/app.db`<br>`:memory:` |
| **PostgreSQL** | `postgresql://` or `postgres://` connection string | `postgresql://user:pass@localhost:5432/mydb`<br>`postgres://postgres@127.0.0.1/production`<br>`postgresql://user:pass@remote-host:5432/dbname?sslmode=require` |

---

### 5. Resolution & Override Hierarchy

`dbcli.nvim` resolves database connections and table formats in the following order of precedence (highest to lowest):

#### Database Connection Precedence:
1. **Buffer-local variable**: `vim.b.db` (set via `:DBConnect <uri>` or Lua `vim.b.db = ...`)
2. **`vim-dadbod-ui` binding**: `vim.b.db_ui_db_key_name`
3. **File Header Comments**: First 15 lines of SQL buffer:
   - `-- db: <uri>`
   - `-- db = <uri>`
   - `-- database: <uri>`
   - `-- DB: <uri>`
   - `-- :DB <uri>`
4. **Global Defaults**: `vim.g.dbcli_default_db` or `vim.g.db` or `opts.default_db`

#### Table Format Precedence:
1. **Buffer-local variable**: `vim.b.dbcli_format` (set via `:DBFormat <format>`)
2. **File Header Comments**: First 15 lines of SQL buffer:
   - `-- format: <format>`
   - `-- mode: <format>`
   - `-- table_format: <format>`
3. **Global / Plugin Config**: `vim.g.dbcli_table_format` or `opts.table_format` (defaults to `'psql'`)

---

### 6. User Commands

| Command | Arguments | Description |
| :--- | :--- | :--- |
| `:DBExecute [query]` | Optional SQL statement | Executes query in argument, selected range (`:'<,'>DBExecute`), or entire buffer. |
| `:DBConnect <uri/path>` | Database URI or path | Binds the current buffer to a SQLite file or PostgreSQL connection. |
| `:DBDisconnect` | *None* | Disconnects the current buffer from its bound database. |
| `:DBFormat [format]` | Optional format name | Gets or sets the table output style for the current buffer (with tab-completion). |
| `:DBRefresh` | *None* | Forces a metadata reload (tables, columns, functions) for the current database. |
| `:DBStatus` | *None* | Displays active database connections, current buffer bindings, and table format. |

---

## 📄 License

MIT License.
