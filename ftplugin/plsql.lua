local dbcli = require('dbcli')
dbcli.setup()
dbcli.bind_keymaps(vim.api.nvim_get_current_buf())
