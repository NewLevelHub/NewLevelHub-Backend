from rest_framework.pagination import CursorPagination, PageNumberPagination


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class FeedCursorPagination(CursorPagination):
    """
    Cursor-based pagination for infinite-scroll feeds (announcements, etc.).

    Contract:
      - ``next``/``previous`` carry an opaque cursor; no ``count`` is returned
        (intentional — the feed is treated as a stream).
      - Default ordering puts pinned items first, then most recent first; this
        matches the AC for the announcements feed and is required for the
        cursor to be stable.
      - ``page_size`` query param lets the client tune the page size up to
        ``max_page_size``.
    """

    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100
    ordering = ('-is_pinned', '-created_at', '-id')
