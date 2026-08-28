project = "tam"
copyright = "Ehud Adler"
author = "Ehud Adler"

extensions = ["myst_parser"]
myst_enable_extensions = ["colon_fence"]
myst_heading_anchors = 3

# myst_heading_anchors generates a real HTML id for every heading (so
# cross-file links like `file.md#some-heading` work in the rendered
# site), but MyST's own xref validator doesn't check those auto-generated
# ids against other documents, so it warns anyway even though the link
# is correct.
suppress_warnings = ["myst.xref_missing"]

source_suffix = {".md": "markdown"}

html_theme = "furo"
html_title = "tam"
