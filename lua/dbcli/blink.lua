local dbcli = require("dbcli")

local source = {}

local kind_map = {
  Text = vim.lsp.protocol.CompletionItemKind.Text or 1,
  Method = vim.lsp.protocol.CompletionItemKind.Method or 2,
  Function = vim.lsp.protocol.CompletionItemKind.Function or 3,
  Constructor = vim.lsp.protocol.CompletionItemKind.Constructor or 4,
  Field = vim.lsp.protocol.CompletionItemKind.Field or 5,
  Variable = vim.lsp.protocol.CompletionItemKind.Variable or 6,
  Class = vim.lsp.protocol.CompletionItemKind.Class or 7,
  Interface = vim.lsp.protocol.CompletionItemKind.Interface or 8,
  Module = vim.lsp.protocol.CompletionItemKind.Module or 9,
  Property = vim.lsp.protocol.CompletionItemKind.Property or 10,
  Unit = vim.lsp.protocol.CompletionItemKind.Unit or 11,
  Value = vim.lsp.protocol.CompletionItemKind.Value or 12,
  Enum = vim.lsp.protocol.CompletionItemKind.Enum or 13,
  Keyword = vim.lsp.protocol.CompletionItemKind.Keyword or 14,
  Snippet = vim.lsp.protocol.CompletionItemKind.Snippet or 15,
  Color = vim.lsp.protocol.CompletionItemKind.Color or 16,
  File = vim.lsp.protocol.CompletionItemKind.File or 17,
  Reference = vim.lsp.protocol.CompletionItemKind.Reference or 18,
  Folder = vim.lsp.protocol.CompletionItemKind.Folder or 19,
  EnumMember = vim.lsp.protocol.CompletionItemKind.EnumMember or 20,
  Constant = vim.lsp.protocol.CompletionItemKind.Constant or 21,
  Struct = vim.lsp.protocol.CompletionItemKind.Struct or 22,
  Event = vim.lsp.protocol.CompletionItemKind.Event or 23,
  Operator = vim.lsp.protocol.CompletionItemKind.Operator or 24,
  TypeParameter = vim.lsp.protocol.CompletionItemKind.TypeParameter or 25,
}

function source.new(opts)
  local self = setmetatable({}, { __index = source })
  self.opts = opts or {}
  dbcli.setup(opts)
  return self
end

function source:get_trigger_characters(context)
  local bufnr = context and context.bufnr or vim.api.nvim_get_current_buf()
  local db_uri = dbcli.get_buf_db(bufnr)
  if db_uri and db_uri ~= "" then
    local u = db_uri:lower()
    if u:find("^postgresql://") or u:find("^postgres://") then
      return { ".", '"' }
    elseif u:find("^mysql://") or u:find("^mysql2://") or u:find("^mariadb://") then
      return { ".", "`", '"' }
    end
  end
  return { ".", "`", '"' }
end

function source:get_completions(context, callback)
  local bufnr = context.bufnr
  local ft = vim.bo[bufnr].filetype
  if ft ~= "sql" then
    callback({ items = {} })
    return
  end

  local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
  local text = table.concat(lines, "\n")

  -- Calculate cursor offset in entire buffer (byte offset)
  local cursor_row = context.cursor[1] -- 1-indexed
  local cursor_col = context.cursor[2] -- 0-indexed byte

  local cursor_pos = 0
  for i = 1, math.min(cursor_row - 1, #lines) do
    cursor_pos = cursor_pos + #lines[i] + 1
  end
  cursor_pos = cursor_pos + cursor_col

  local db_uri = dbcli.get_buf_db(bufnr)

  local req_id = dbcli.send_request("complete", {
    db = db_uri,
    text = text,
    cursor_pos = cursor_pos,
  }, function(resp)
    if resp.status ~= "ok" or not resp.items then
      callback({ items = {} })
      return
    end

    local curr_line = lines[cursor_row] or ""
    local items = {}
    for _, it in ipairs(resp.items) do
      local start_pos = it.startPosition or 0
      local quote_char = it.quoteChar
      if quote_char == vim.NIL then quote_char = nil end
      if not quote_char and start_pos < 0 then
        local prefix_char = curr_line:sub(cursor_col + start_pos + 1, cursor_col + start_pos + 1)
        if prefix_char == '"' or prefix_char == "`" then
          quote_char = prefix_char
        end
      end

      local start_char = math.max(0, cursor_col + start_pos)
      local end_char = cursor_col
      if quote_char then
        local closing_char = (quote_char == "[" and "]") or quote_char
        local next_char = curr_line:sub(cursor_col + 1, cursor_col + 1)
        if next_char == closing_char then
          end_char = end_char + 1
        end
      end

      local insert_text = it.insertText or it.label
      table.insert(items, {
        label = it.label,
        kind = kind_map[it.kind] or 1,
        detail = (it.detail and it.detail ~= "") and it.detail or nil,
        filterText = it.filterText or it.display or it.label,
        insertText = insert_text,
        textEdit = {
          newText = insert_text,
          range = {
            start = { line = cursor_row - 1, character = start_char },
            ["end"] = { line = cursor_row - 1, character = end_char },
          },
        },
      })
    end

    callback({
      is_incomplete_forward = false,
      is_incomplete_backward = false,
      items = items,
    })
  end)

  return function()
    if req_id then
      dbcli.cancel_request(req_id)
    end
  end
end

return source
