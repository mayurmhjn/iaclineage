# Diagnostics

Codes below 100 stop the command with exit `2` and print
`[<command>] ERR Failed: IACxxx: <message>` on stderr. Codes from 100 up are
recorded in the index, printed on stderr as `warning:` lines (the first five),
and included in every report; the command still finishes.

| Code | Message | What to do |
| --- | --- | --- |
| `IAC001` | scan path does not exist | Check the path. |
| `IAC002` | scan path must be a directory | Pass the directory, not a `.tf` file. |
| `IAC003` | Terraform source must be UTF-8 | Re-save the file as UTF-8. |
| `IAC004` | Terraform source could not be read | Check file permissions. |
| `IAC005` | output directory does not exist | Create the directory first. |
| `IAC006` | report could not be written | Check permissions and free space. |
| `IAC009` | `--include-source-values` requires `--format interactive-html` | Change the format or drop the flag. |
| `IAC101` | HCL parse recovery was used; results from this file may be incomplete | Run `terraform validate` or fix the syntax; treat results from that file as partial. |
| `IAC102` | local module source directory was not found | Fix the module `source` path. References into it stay unresolved. |
| `IAC103` | remote module is not downloaded (or its cache entry is unreadable, missing, mismatched, or outside `.terraform/modules`) | Ask the user to run `terraform get` in that root, then scan again. |

Unexpected internal errors print only the exception type, never source text,
and exit `1`.
