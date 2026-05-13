# `r/quarto/` folder

## These `.qmd` files are **not** Quarto documents

The `*.qmd` files stored here are **QGIS project / layer metadata XML** (they
start with `<!DOCTYPE qgis ...>`). They were likely saved with a `.qmd`
extension by mistake or by local convention.

- They will **not** render with `quarto render`.
- For real Quarto reporting, add new `.qmd` files with a YAML header
  (`title`, `format: html`, etc.) in this folder or a subfolder such as
  `reports/`.

## What to do with this XML

- Open in QGIS if compatible with your workflow, or
- Rename to `.xml` if you want the repository to make the distinction obvious.
