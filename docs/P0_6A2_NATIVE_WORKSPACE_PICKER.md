# P0.6A.2 — Native Workspace Picker

The Local Gateway can open a native macOS file/folder chooser after an
explicit authenticated UI request. The selected path is still passed through
WorkspaceRegistry authorization and safety checks.

- POST /workspaces/choose with `kind: folder|file`
- session required
- user cancellation is non-fatal
- root/home/sensitive-path checks remain enforced
- filesystem write remains disabled
