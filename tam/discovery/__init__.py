"""tam.discovery -- publish a static HTML research artifact (a Plotly
dashboard from tam.backtest.tearsheet/visualization, or any other
self-contained .html) to Discovery, a private GitHub-authenticated catalog
(see /tam-discovery for the Cloudflare Worker this talks to). Publishing is
immutable by default -- every call creates a new version; pass the same
`name` twice to have a stable URL that always resolves to the latest one.

    from tam.discovery import upload

    result = upload("report.html", title="Earnings Reaction", tags=["earnings"])
    print(result.url)

Also accepts anything satisfying the Uploadable protocol (a Plotly Figure,
or a tam.charting ChartCall/ChartPipeline -- see Uploadable's own
docstring) directly, no need to render to a file first:

    from tam.charting import timeseries
    upload(timeseries(my_series), title="...")

Needs a publishing token -- see tam.discovery.auth for how one gets found
automatically (env var, Colab secret, or `upload-discovery login`'s saved
file), or run `upload-discovery login` once first.
"""

from .upload import Uploadable, UploadResult, upload

__all__ = ["UploadResult", "Uploadable", "upload"]
