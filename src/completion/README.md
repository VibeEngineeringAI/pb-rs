The shell templates are adapted from github.com/spf13/cobra v1.8.1:

- bash.tmpl: bash_completionsV2.go, genBashComp
- fish.tmpl: fish_completions.go, genFishComp
- powershell.tmpl: powershell_completions.go, genPowerShellComp
- zsh.tmpl: zsh_completions.go, genZshComp

Copyright 2013-2023 The Cobra Authors. Licensed under Apache-2.0; the
unmodified license is in LICENSE-Cobra.txt. Templates retain Cobra's indexed
format parameters. The Rust renderer resolves those parameters and percent
escapes; PowerShell's concatenated Go string literals are joined into shell
source. Fish's separately formatted header is emitted by the Rust generator.
The templates are source adapters, not snapshots taken from the Go executable.
