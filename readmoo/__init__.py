#!/usr/bin/env python

__license__ = 'GPL v3'
__copyright__ = '2026'
__docformat__ = 'restructuredtext en'

import re
from urllib.parse import quote

try:
    from queue import Empty, Queue
except ImportError:
    from Queue import Empty, Queue

from calibre.ebooks.metadata import check_isbn
from calibre.ebooks.metadata.book.base import Metadata
from calibre.ebooks.metadata.sources.base import Source


class ReadmooBooks(Source):

    name = 'Readmoo Books'
    author = 'Nelson'
    version = (1, 0, 0)
    minimum_calibre_version = (5, 0, 0)

    description = _(
        'Downloads metadata and covers from Readmoo.com. '
        'Useful for Traditional Chinese ebooks.'
    )

    capabilities = frozenset(['identify', 'cover'])
    touched_fields = frozenset([
        'title', 'authors', 'tags', 'pubdate', 'comments', 'publisher',
        'identifier:isbn', 'identifier:readmoo', 'languages'
    ])
    supports_gzip_transfer_encoding = True
    cached_cover_url_is_reliable = True

    SUGGEST_URL = 'https://readmoo.com/search/suggest'
    BOOK_URL = 'https://readmoo.com/book/%s'

    def get_book_url(self, identifiers):
        readmoo_id = identifiers.get('readmoo', None)
        if readmoo_id:
            return ('readmoo', readmoo_id, self.BOOK_URL % readmoo_id)
        return None

    def get_cached_cover_url(self, identifiers):
        readmoo_id = identifiers.get('readmoo', None)
        if readmoo_id:
            return self.cached_identifier_to_cover_url(readmoo_id)
        isbn = identifiers.get('isbn', None)
        if isbn:
            readmoo_id = self.cached_isbn_to_identifier(isbn)
            if readmoo_id:
                return self.cached_identifier_to_cover_url(readmoo_id)
        return None

    def identify(
        self,
        log,
        result_queue,
        abort,
        title=None,
        authors=None,
        identifiers={},
        timeout=30,
    ):
        query = self._build_search_query(title, authors, identifiers)
        if not query:
            log.error('Readmoo: Insufficient metadata to construct query')
            return

        book_urls = self._search(log, query, timeout)
        if not book_urls and title:
            log.info('Readmoo: No results for query: %s, trying title' % query)
            title_tokens = list(self.get_title_tokens(title))
            if authors:
                author_tokens = list(
                    self.get_author_tokens(authors, only_first_author=True)
                )
                title_tokens.extend(author_tokens)
            fallback_query = ' '.join(title_tokens)
            if fallback_query and fallback_query != query:
                book_urls = self._search(log, fallback_query, timeout)
            if not book_urls and authors:
                title_only = ' '.join(self.get_title_tokens(title))
                if title_only and title_only != fallback_query:
                    book_urls = self._search(log, title_only, timeout)
        if not book_urls:
            log.info('Readmoo: No results found')
            return

        isbn = check_isbn(identifiers.get('isbn', None))

        matched = []
        unmatched = []
        for relevance, url in enumerate(book_urls[:5]):
            if abort.is_set():
                break

            readmoo_id = self._extract_id_from_url(url)
            if not readmoo_id:
                continue

            mi = self._fetch_book_metadata(log, readmoo_id, timeout)
            if mi is None:
                continue

            mi.source_relevance = relevance

            if mi.isbn:
                self.cache_isbn_to_identifier(mi.isbn, readmoo_id)
            cover_url = getattr(mi, 'has_readmoo_cover', None)
            if cover_url:
                self.cache_identifier_to_cover_url(readmoo_id, cover_url)

            if isbn and mi.isbn and check_isbn(mi.isbn) != isbn:
                unmatched.append(mi)
            else:
                matched.append(mi)

        results = matched if matched else unmatched
        for mi in results:
            self.clean_downloaded_metadata(mi)
            result_queue.put(mi)

    def download_cover(
        self,
        log,
        result_queue,
        abort,
        title=None,
        authors=None,
        identifiers={},
        timeout=30,
        get_best_cover=False,
    ):
        cached_url = self.get_cached_cover_url(identifiers)
        if cached_url is None:
            log.info('Readmoo: No cached cover found, running identify')
            rq = Queue()
            self.identify(
                log, rq, abort,
                title=title, authors=authors, identifiers=identifiers
            )
            if abort.is_set():
                return
            results = []
            while True:
                try:
                    results.append(rq.get_nowait())
                except Empty:
                    break
            results.sort(
                key=self.identify_results_keygen(
                    title=title, authors=authors, identifiers=identifiers
                )
            )
            for mi in results:
                cached_url = self.get_cached_cover_url(mi.identifiers)
                if cached_url is not None:
                    break

        if cached_url is None:
            log.info('Readmoo: No cover found')
            return

        if abort.is_set():
            return

        log('Readmoo: Downloading cover from:', cached_url)
        br = self.browser
        try:
            cdata = br.open_novisit(cached_url, timeout=timeout).read()
            if cdata:
                result_queue.put((self, cdata))
        except Exception:
            log.exception('Readmoo: Failed to download cover from:', cached_url)

    def _build_search_query(self, title, authors, identifiers):
        isbn = check_isbn(identifiers.get('isbn', None))
        if isbn:
            return isbn

        tokens = []
        if title:
            title_tokens = list(self.get_title_tokens(title))
            if title_tokens:
                tokens.extend(title_tokens)
        if authors:
            author_tokens = list(
                self.get_author_tokens(authors, only_first_author=True)
            )
            if author_tokens:
                tokens.extend(author_tokens)

        return ' '.join(tokens) if tokens else None

    def _search(self, log, query, timeout):
        import json

        url = '%s?keyword=%s&typeahead=1' % (self.SUGGEST_URL, quote(query))
        log.info('Readmoo: Searching: %s' % url)

        br = self.browser
        try:
            raw = br.open_novisit(url, timeout=timeout).read()
        except Exception as e:
            log.exception('Readmoo: Search failed: %s' % e)
            return []

        try:
            results = json.loads(raw)
        except Exception as e:
            log.exception('Readmoo: JSON parse failed: %s' % e)
            return []

        if not isinstance(results, list):
            return []

        urls = [
            item['url'] for item in results
            if item.get('type') == 'product' and item.get('url')
        ]
        log.info('Readmoo: Found %d results' % len(urls))
        return urls

    def _extract_id_from_url(self, url):
        m = re.search(r'/book/(\d+)', url)
        return m.group(1) if m else None

    def _fetch_book_metadata(self, log, readmoo_id, timeout):
        url = self.BOOK_URL % readmoo_id
        log.info('Readmoo: Fetching: %s' % url)

        br = self.browser
        try:
            raw = br.open_novisit(url, timeout=timeout).read()
        except Exception as e:
            log.exception('Readmoo: Fetch failed: %s' % e)
            return None

        html = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else raw
        return self._parse_book_page(log, html, readmoo_id)

    def _parse_book_page(self, log, html, readmoo_id):
        from calibre.utils.date import parse_date, utcnow

        # --- Title: "<title>書名 - 作者 | Readmoo 讀墨電子書</title>" ---
        title = None
        title_match = re.search(r'<title>([^<]+)</title>', html)
        if title_match:
            raw_title = title_match.group(1).strip()
            parts = raw_title.split(' | ')[0]
            title_parts = parts.rsplit(' - ', 1)
            title = title_parts[0].strip()

        if not title:
            breadcrumbs = re.findall(
                r'itemtype="http://schema.org/ListItem"[^>]*>.*?'
                r'itemprop="name"[^>]*>([^<]+)',
                html, re.DOTALL
            )
            if breadcrumbs:
                title = breadcrumbs[-1].strip()

        if not title:
            log.error('Readmoo: No title found for %s' % readmoo_id)
            return None

        # --- Authors: <a itemprop="name"> (contributor links) ---
        authors = []
        for name in re.findall(r'<a[^>]*itemprop="name"[^>]*>([^<]+)</a>', html):
            name = name.strip()
            if name and '寫了書評' not in name:
                authors.append(name)

        if not authors and title_match:
            raw_title = title_match.group(1).strip()
            parts = raw_title.split(' | ')[0]
            title_parts = parts.rsplit(' - ', 1)
            if len(title_parts) > 1:
                authors = [title_parts[1].strip()]

        if not authors:
            authors = [_('Unknown')]

        mi = Metadata(title, authors)
        mi.identifiers = {'readmoo': readmoo_id}

        # --- Publisher ---
        pub_match = re.search(r'<a[^>]*itemprop="publisher"[^>]*>([^<]+)</a>', html)
        if pub_match:
            mi.publisher = pub_match.group(1).strip()

        # --- ISBN ---
        isbn_match = re.search(r'itemprop="isbn"[^>]*>([^<]+)<', html)
        if isbn_match:
            isbn_val = isbn_match.group(1).strip()
            if check_isbn(isbn_val):
                mi.isbn = isbn_val

        # --- Pub Date: <meta itemprop="datePublished" content="YYYY/MM/DD"> ---
        date_match = re.search(r'itemprop="datePublished"[^>]*content="([^"]+)"', html)
        if date_match:
            try:
                default = utcnow().replace(day=15)
                mi.pubdate = parse_date(date_match.group(1), assume_utc=True, default=default)
            except Exception:
                log.error('Readmoo: Bad date: %s' % date_match.group(1))

        # --- Language: map Chinese label to ISO 639-2 ---
        lang_match = re.search(r'itemprop="inLanguage"[^>]*>([^<]+)<', html)
        if lang_match:
            lang = lang_match.group(1).strip()
            if '中文' in lang:
                mi.languages = ['zho']
            elif 'English' in lang or '英文' in lang:
                mi.languages = ['eng']
            elif '日' in lang:
                mi.languages = ['jpn']

        # --- Description: full HTML from book-detail-description div ---
        desc_div = re.search(
            r'id="book-detail-description"[^>]*>(.*?)</div>\s*<div',
            html, re.DOTALL
        )
        if desc_div:
            desc_html = desc_div.group(1)
            desc_html = re.sub(r'<h2[^>]*>.*?</h2>', '', desc_html, flags=re.DOTALL)
            desc_html = desc_html.strip()
            if desc_html:
                mi.comments = desc_html
        if not mi.comments:
            desc_match = re.search(
                r'<meta\s+name="description"\s+content="([^"]+)"', html
            )
            if desc_match:
                desc = desc_match.group(1).strip()
                desc = re.sub(r'^《[^》]+》電子書\s*-\s*', '', desc)
                mi.comments = desc

        # --- Cover: og:image meta tag ---
        cover_match = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', html)
        mi.has_readmoo_cover = None
        if cover_match:
            cover_url = cover_match.group(1).strip()
            if cover_url:
                mi.has_readmoo_cover = cover_url

        # --- Tags: breadcrumb categories (skip nav items) ---
        breadcrumbs = re.findall(r'<span\s+itemprop="name"[^>]*>([^<]+)</span>', html)
        skip = {'分類導覽', title, '全站活動'}
        tags = [
            bc.strip() for bc in breadcrumbs
            if bc.strip() and bc.strip() not in skip and '寫了書評' not in bc
        ]
        if tags:
            mi.tags = tags

        log.info('Readmoo: Parsed "%s" by %s [ISBN:%s]' % (
            mi.title, ', '.join(mi.authors), mi.isbn or 'N/A'
        ))
        return mi


if __name__ == '__main__':
    from calibre.ebooks.metadata.sources.test import (
        test_identify_plugin, title_test, authors_test
    )
    test_identify_plugin(
        ReadmooBooks.name, [
            (
                {'title': '三體', 'authors': ['劉慈欣']},
                [title_test('三體', exact=False)]
            ),
        ]
    )
