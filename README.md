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
    table_format = 'psql',          -- Output table style ('psql', 'fancy_grid', 'github', 'double', 'vertical', etc.)
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
:DBFormat github
:DBFormat vertical     " Similar to \G (great for wide rows)
:DBFormat psql         " Default classic ASCII table
```

---

## ⚙️ Configuration Options

```lua
require('dbcli').setup({
  -- Default table format
  table_format = 'psql',

  -- Results split window direction: 'horizontal' or 'vertical'
  split_direction = 'horizontal',
  split_size = 15,

  -- Default keymaps (<space><enter> in SQL buffers)
  default_keymaps = true,

  -- Default database URI if no header is found
  default_db = nil,
})
```

---

## 📄 License

MIT License.
