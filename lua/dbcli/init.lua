local M = {}

local uv = vim.uv or vim.loop
local proc = {
  handle = nil,
  stdin = nil,
  stdout = nil,
  stderr = nil,
}

local req_counter = 0
local callbacks = {}
local stdout_buffer = ""

-- Result buffer and window references
M.result_buf = nil
M.result_win = nil
M.config = {
  split_direction = "horizontal", -- "horizontal" or "vertical"
  split_size = 15,
  table_format = "psql",
  default_keymaps = true,
}

local function get_server_path()
  local info = debug.getinfo(1, "S")
  local script_dir = info.source:sub(2):match("(.*/)")
  if script_dir then
    return script_dir .. "server.py"
  end
  return vim.fn.expand("~/.config/nvim/my-plugins/dbcli.nvim/lua/dbcli/server.py")
end

local function cleanup_process()
  if proc.stdin and not proc.stdin:is_closing() then proc.stdin:close() end
  if proc.stdout and not proc.stdout:is_closing() then proc.stdout:close() end
  if proc.stderr and not proc.stderr:is_closing() then proc.stderr:close() end
  if proc.handle and not proc.handle:is_closing() then proc.handle:close() end
  proc.handle = nil
  proc.stdin = nil
  proc.stdout = nil
  proc.stderr = nil
  callbacks = {}
  stdout_buffer = ""
end

function M.ensure_server()
  if proc.handle and not proc.handle:is_closing() then
    return true
  end

  local server_path = get_server_path()
  if vim.fn.filereadable(server_path) == 0 then
    vim.notify("[dbcli] server.py not found at: " .. server_path, vim.log.levels.ERROR)
    return false
  end

  cleanup_process()

  proc.stdin = uv.new_pipe(false)
  proc.stdout = uv.new_pipe(false)
  proc.stderr = uv.new_pipe(false)

  proc.handle = uv.spawn("python3", {
    args = { server_path },
    stdio = { proc.stdin, proc.stdout, proc.stderr },
  }, function(code, signal)
    vim.schedule(function()
      cleanup_process()
      if code ~= 0 and code ~= 143 and code ~= 130 then
        vim.notify(string.format("[dbcli] Server stopped (code: %s, signal: %s)", tostring(code), tostring(signal)), vim.log.levels.WARN)
      end
    end)
  end)

  if not proc.handle then
    cleanup_process()
    vim.notify("[dbcli] Failed to spawn python process", vim.log.levels.ERROR)
    return false
  end

  proc.stdout:read_start(function(err, data)
    if err then return end
    if data then
      stdout_buffer = stdout_buffer .. data
      while true do
        local nl = stdout_buffer:find("\n")
        if not nl then break end
        local line = stdout_buffer:sub(1, nl - 1)
        stdout_buffer = stdout_buffer:sub(nl + 1)
        if line:match("%S") then
          local ok, decoded = pcall(vim.json.decode, line)
          if ok and decoded and decoded.id then
            local cb = callbacks[decoded.id]
            if cb then
              callbacks[decoded.id] = nil
              vim.schedule(function() cb(decoded) end)
            end
          end
        end
      end
    end
  end)

  return true
end

function M.send_request(action, params, callback)
  if not M.ensure_server() then
    if callback then callback({ status = "error", message = "server not running" }) end
    return nil
  end

  req_counter = req_counter + 1
  local id = req_counter
  local payload = vim.tbl_extend("force", params or {}, {
    id = id,
    action = action,
  })

  if callback then
    callbacks[id] = callback
  end

  local ok, encoded = pcall(vim.json.encode, payload)
  if not ok or not proc.stdin or proc.stdin:is_closing() then
    callbacks[id] = nil
    if callback then callback({ status = "error", message = "stdin not writable" }) end
    return nil
  end

  proc.stdin:write(encoded .. "\n")
  return id
end

function M.cancel_request(id)
  if id and callbacks[id] then
    callbacks[id] = nil
  end
end

--- Detect database URL for a given buffer
function M.get_buf_db(bufnr)
  bufnr = bufnr or vim.api.nvim_get_current_buf()
  if not vim.api.nvim_buf_is_valid(bufnr) then
    return ""
  end

  -- 1. Check buffer-local variable vim.b.db
  local b_db = vim.b[bufnr].db
  if b_db and type(b_db) == "string" and b_db ~= "" then
    return b_db
  end

  -- 2. Check vim-dadbod-ui connection key
  local db_ui = vim.b[bufnr].db_ui_db_key_name
  if db_ui and type(db_ui) == "string" and db_ui ~= "" then
    return db_ui
  end

  -- 3. Scan first 15 lines of buffer for header comments
  local lines = vim.api.nvim_buf_get_lines(bufnr, 0, 15, false)
  for _, line in ipairs(lines) do
    local url = line:match("^%s*%-%-%s*:?DB%s*=?%s*(%S+)")
      or line:match("^%s*%-%-%s*db%s*:%s*(%S+)")
      or line:match("^%s*%-%-%s*db%s*=%s*(%S+)")
      or line:match("^%s*%-%-%s*database%s*:%s*(%S+)")
    if url and url ~= "" then
      vim.b[bufnr].db = url
      return url
    end
  end

  -- 4. Check global default
  return vim.g.dbcli_default_db or vim.g.db or ""
end

--- Detect output table format for a given buffer
function M.get_buf_format(bufnr)
  bufnr = bufnr or vim.api.nvim_get_current_buf()
  if not vim.api.nvim_buf_is_valid(bufnr) then return M.config.table_format or "psql" end

  -- 1. Check buffer-local variable
  local b_fmt = vim.b[bufnr].dbcli_format
  if b_fmt and type(b_fmt) == "string" and b_fmt ~= "" then return b_fmt end

  -- 2. Scan first 15 lines of buffer for header comments (-- format: xxx or -- mode: xxx)
  local lines = vim.api.nvim_buf_get_lines(bufnr, 0, 15, false)
  for _, line in ipairs(lines) do
    local fmt = line:match("^%s*%-%-%s*format%s*:%s*(%S+)")
      or line:match("^%s*%-%-%s*mode%s*:%s*(%S+)")
      or line:match("^%s*%-%-%s*table_format%s*:%s*(%S+)")
    if fmt and fmt ~= "" then return fmt end
  end

  -- 3. Check global or plugin config
  return vim.g.dbcli_table_format or M.config.table_format or "psql"
end

function M.connect(uri, bufnr)
  bufnr = bufnr or vim.api.nvim_get_current_buf()
  if not uri or uri == "" then
    vim.notify("[dbcli] Please provide a database URI or path", vim.log.levels.WARN)
    return
  end

  vim.b[bufnr].db = uri
  M.send_request("connect", { uri = uri }, function(resp)
    if resp.status == "ok" then
      vim.notify(string.format("[dbcli] Connected to %s (%s)", resp.uri, resp.db_type), vim.log.levels.INFO)
    else
      vim.notify("[dbcli] Connect error: " .. tostring(resp.message or resp.error), vim.log.levels.ERROR)
    end
  end)
end

function M.disconnect(bufnr)
  bufnr = bufnr or vim.api.nvim_get_current_buf()
  local uri = vim.b[bufnr].db
  vim.b[bufnr].db = nil
  if uri and uri ~= "" then
    M.send_request("disconnect", { uri = uri }, function()
      vim.notify("[dbcli] Disconnected from " .. uri, vim.log.levels.INFO)
    end)
  else
    vim.notify("[dbcli] Buffer not bound to any database", vim.log.levels.INFO)
  end
end

function M.refresh(bufnr)
  bufnr = bufnr or vim.api.nvim_get_current_buf()
  local uri = M.get_buf_db(bufnr)
  if uri and uri ~= "" then
    M.send_request("refresh", { uri = uri }, function(resp)
      if resp.status == "ok" then
        vim.notify("[dbcli] Metadata refreshed for " .. uri, vim.log.levels.INFO)
      end
    end)
  else
    vim.notify("[dbcli] No active DB connection for current buffer", vim.log.levels.WARN)
  end
end

function M.status()
  local bufnr = vim.api.nvim_get_current_buf()
  local current_db = M.get_buf_db(bufnr)
  local current_fmt = M.get_buf_format(bufnr)

  M.send_request("status", {}, function(resp)
    local lines = {
      "=== dbcli Status ===",
      "Current Buffer DB: " .. (current_db ~= "" and current_db or "(none)"),
      "Table Format:      " .. current_fmt,
      "",
      "Active Server Connections:",
    }
    if resp.connections and #resp.connections > 0 then
      for _, c in ipairs(resp.connections) do
        table.insert(lines, string.format(" - [%s] %s (ready: %s)", c.db_type, c.uri, tostring(c.ready)))
      end
    else
      table.insert(lines, " (No active connections)")
    end
    vim.notify(table.concat(lines, "\n"), vim.log.levels.INFO)
  end)
end

--- Display SQL results in dedicated window
local function show_results_window(content)
  if not M.result_buf or not vim.api.nvim_buf_is_valid(M.result_buf) then
    M.result_buf = vim.api.nvim_create_buf(false, true)
    vim.bo[M.result_buf].filetype = "dbout"
    vim.bo[M.result_buf].buftype = "nofile"
    vim.bo[M.result_buf].bufhidden = "hide"
    vim.bo[M.result_buf].swapfile = false
    vim.bo[M.result_buf].buflisted = false
    pcall(vim.api.nvim_buf_set_name, M.result_buf, "[dbcli results]")

    vim.keymap.set("n", "q", function()
      if M.result_win and vim.api.nvim_win_is_valid(M.result_win) then
        vim.api.nvim_win_close(M.result_win, true)
        M.result_win = nil
      end
    end, { buffer = M.result_buf, desc = "Close SQL results window", silent = true })
  end

  local lines = vim.split(content, "\n", { plain = true })
  vim.bo[M.result_buf].modifiable = true
  vim.api.nvim_buf_set_lines(M.result_buf, 0, -1, false, lines)
  vim.bo[M.result_buf].modifiable = false

  if not M.result_win or not vim.api.nvim_win_is_valid(M.result_win) then
    for _, win in ipairs(vim.api.nvim_tabpage_list_wins(0)) do
      if vim.api.nvim_win_get_buf(win) == M.result_buf then
        M.result_win = win
        break
      end
    end
  end

  if not M.result_win or not vim.api.nvim_win_is_valid(M.result_win) then
    local current_win = vim.api.nvim_get_current_win()
    if M.config.split_direction == "vertical" then
      vim.cmd("vertical botright split")
      if M.config.split_size then
        vim.cmd("vertical resize " .. tostring(M.config.split_size))
      end
    else
      local size = M.config.split_size or 15
      vim.cmd("botright " .. tostring(size) .. "split")
    end
    M.result_win = vim.api.nvim_get_current_win()
    vim.api.nvim_win_set_buf(M.result_win, M.result_buf)
    vim.wo[M.result_win].number = false
    vim.wo[M.result_win].relativenumber = false
    vim.wo[M.result_win].signcolumn = "no"
    vim.wo[M.result_win].wrap = false
    vim.api.nvim_set_current_win(current_win)
  else
    vim.api.nvim_win_set_buf(M.result_win, M.result_buf)
  end

  if M.result_win and vim.api.nvim_win_is_valid(M.result_win) then
    vim.api.nvim_win_set_cursor(M.result_win, { 1, 0 })
  end
end

--- Execute SQL queries and display output in results window
function M.execute(opts)
  opts = opts or {}
  local bufnr = opts.bufnr or vim.api.nvim_get_current_buf()
  local db_uri = M.get_buf_db(bufnr)

  if not db_uri or db_uri == "" then
    vim.notify(
      "[dbcli] No database connection specified for this buffer.\nUse :DBConnect <path/uri> or add '-- db: <uri>' header.",
      vim.log.levels.WARN
    )
    return
  end

  local query = opts.query
  if not query or query == "" then
    if opts.line1 and opts.line2 then
      local lines = vim.api.nvim_buf_get_lines(bufnr, opts.line1 - 1, opts.line2, false)
      query = table.concat(lines, "\n")
    else
      local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
      query = table.concat(lines, "\n")
    end
  end

  if not query or query:match("^%s*$") then
    vim.notify("[dbcli] No SQL query to execute", vim.log.levels.INFO)
    return
  end

  local format_name = opts.format or M.get_buf_format(bufnr)

  M.send_request("execute", {
    db = db_uri,
    query = query,
    format = format_name,
  }, function(resp)
    local result = resp.result or {}
    local output_text
    if result.output and result.output ~= "" then
      output_text = result.output
    elseif result.error then
      output_text = "[ERROR] " .. tostring(result.error)
    else
      output_text = "[Query executed successfully, no rows returned]"
    end

    show_results_window(output_text)
  end)
end

--- Bind execution keymaps to a SQL buffer (only <space><enter>)
function M.bind_keymaps(bufnr)
  if not M.config.default_keymaps then return end

  bufnr = bufnr or vim.api.nvim_get_current_buf()
  if not vim.api.nvim_buf_is_valid(bufnr) then return end
  local buf_opts = { buffer = bufnr, silent = true }

  -- <space><enter>: Normal mode executes entire file; Visual mode executes selection
  vim.keymap.set(
    "n",
    "<space><enter>",
    "<cmd>DBExecute<cr>",
    vim.tbl_extend("force", buf_opts, { desc = "= [dbcli] execute entire SQL file" })
  )
  vim.keymap.set(
    "x",
    "<space><enter>",
    ":DBExecute<cr>",
    vim.tbl_extend("force", buf_opts, { desc = "= [dbcli] execute selected SQL" })
  )
end

function M.setup(opts)
  if M._is_setup then return end
  M._is_setup = true

  opts = opts or {}
  if opts.default_db then vim.g.dbcli_default_db = opts.default_db end
  if opts.split_direction then M.config.split_direction = opts.split_direction end
  if opts.split_size then M.config.split_size = opts.split_size end
  if opts.table_format then M.config.table_format = opts.table_format end
  if opts.default_keymaps ~= nil then M.config.default_keymaps = opts.default_keymaps end

  -- Auto commands for header detection and buffer keymaps
  vim.api.nvim_create_autocmd({ "BufReadPost", "BufWritePost", "FileType" }, {
    pattern = { "*.sql", "sql" },
    callback = function(ev)
      local uri = M.get_buf_db(ev.buf)
      if uri and uri ~= "" and not vim.b[ev.buf]._dbcli_connected then
        vim.b[ev.buf]._dbcli_connected = true
        M.send_request("connect", { uri = uri })
      end

      M.bind_keymaps(ev.buf)
    end,
  })

  -- Immediately bind to any existing SQL buffers
  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_valid(buf) then
      local ft = vim.bo[buf].filetype
      if ft == "sql" then
        M.bind_keymaps(buf)
      end
    end
  end

  -- User commands
  vim.api.nvim_create_user_command("DBExecute", function(args)
    if args.args and args.args ~= "" then
      M.execute({ query = args.args })
    elseif args.range == 2 then
      M.execute({ line1 = args.line1, line2 = args.line2 })
    else
      M.execute()
    end
  end, {
    range = true,
    nargs = "*",
    desc = "Execute SQL query, selection, or entire file",
  })

  vim.api.nvim_create_user_command("DBConnect", function(args)
    M.connect(args.args)
  end, {
    nargs = 1,
    complete = "file",
    desc = "Connect current buffer to SQLite file or Postgres URI",
  })

  vim.api.nvim_create_user_command("DBDisconnect", function()
    M.disconnect()
  end, {
    desc = "Disconnect dbcli from current buffer",
  })

  vim.api.nvim_create_user_command("DBRefresh", function()
    M.refresh()
  end, {
    desc = "Refresh database metadata for current buffer",
  })

  vim.api.nvim_create_user_command("DBStatus", function()
    M.status()
  end, {
    desc = "Show dbcli status and active connections",
  })

  local supported_formats = {
    "ascii",
    "ascii_escaped",
    "csv",
    "csv-noheader",
    "csv-tab",
    "csv-tab-noheader",
    "double",
    "fancy_grid",
    "github",
    "grid",
    "html",
    "jira",
    "jsonl",
    "jsonl_escaped",
    "latex",
    "latex_booktabs",
    "markdown",
    "mediawiki",
    "minimal",
    "moinmoin",
    "mysql",
    "mysql_heavy",
    "mysql_unicode",
    "orgtbl",
    "pipe",
    "plain",
    "psql",
    "psql_unicode",
    "rst",
    "simple",
    "textile",
    "tsv",
    "tsv_noheader",
    "vertical",
  }
  M.supported_formats = supported_formats
  vim.api.nvim_create_user_command("DBFormat", function(args)
    if args.args and args.args ~= "" then
      vim.b.dbcli_format = args.args
      vim.notify("[dbcli] Table format set to: " .. args.args, vim.log.levels.INFO)
    else
      local cur = M.get_buf_format()
      vim.notify("[dbcli] Current table format: " .. cur, vim.log.levels.INFO)
    end
  end, {
    nargs = "?",
    complete = function() return supported_formats end,
    desc = "Get or set table format for current buffer",
  })
end

return M
