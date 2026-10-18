"""Use WeasyPrint's fetcher interface while denying all non-inline resources."""

from weasyprint import URLFetcher


class OfflineURLFetcher(URLFetcher):
    def __init__(self):
        super().__init__(allowed_protocols={"data"}, allow_redirects=False)

    def fetch(self, url, *args, **kwargs):
        if not url.startswith("data:"):
            raise ValueError("External resources are disabled for PDF reports.")
        return super().fetch(url, *args, **kwargs)
