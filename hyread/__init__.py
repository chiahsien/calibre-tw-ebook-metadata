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


class HyReadBooks(Source):

    name = 'HyRead Books'
    author = 'Nelson'
    version = (1, 0, 0)
    minimum_calibre_version = (5, 0, 0)

    description = _(
        'Downloads metadata and covers from HyRead ebook (ebook.hyread.com.tw). '
        'Useful for Traditional Chinese ebooks.'
    )

    capabilities = frozenset(['identify', 'cover'])
    touched_fields = frozenset([
        'title', 'authors', 'tags', 'pubdate', 'comments', 'publisher',
        'identifier:isbn', 'identifier:hyread', 'languages'
    ])
    supports_gzip_transfer_encoding = True
    cached_cover_url_is_reliable = True

    SEARCH_URL = 'https://ebook.hyread.com.tw/searchList.jsp'
    BOOK_URL = 'https://ebook.hyread.com.tw/bookDetail.jsp?id=%s'

    def get_book_url(self, identifiers):
        hyread_id = identifiers.get('hyread', None)
        if hyread_id:
            return ('hyread', hyread_id, self.BOOK_URL % hyread_id)
        return None

    def get_cached_cover_url(self, identifiers):
        hyread_id = identifiers.get('hyread', None)
        if hyread_id:
            return self.cached_identifier_to_cover_url(hyread_id)
        isbn = identifiers.get('isbn', None)
        if isbn:
            hyread_id = self.cached_isbn_to_identifier(isbn)
            if hyread_id:
                return self.cached_identifier_to_cover_url(hyread_id)
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
        isbn = check_isbn(identifiers.get('isbn', None))

        book_ids = []
        if isbn:
            book_ids = self._search(log, isbn, 'ISBN', timeout)

        if not book_ids:
            query = self._build_query(title, authors)
            if not query:
                log.error('HyRead: Insufficient metadata to construct query')
                return
            book_ids = self._search(log, query, 'FullText', timeout)

        if not book_ids:
            log.info('HyRead: No results found')
            return

        matched = []
        unmatched = []
        for relevance, hyread_id in enumerate(book_ids[:5]):
            if abort.is_set():
                break

            mi = self._fetch_book_metadata(log, hyread_id, timeout)
            if mi is None:
                continue

            mi.source_relevance = relevance

            if mi.isbn:
                self.cache_isbn_to_identifier(mi.isbn, hyread_id)
            cover_url = getattr(mi, '_hyread_cover_url', None)
            if cover_url:
                self.cache_identifier_to_cover_url(hyread_id, cover_url)

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
            log.info('HyRead: No cached cover found, running identify')
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
            log.info('HyRead: No cover found')
            return

        if abort.is_set():
            return

        log('HyRead: Downloading cover from:', cached_url)
        br = self.browser
        try:
            cdata = br.open_novisit(cached_url, timeout=timeout).read()
            if cdata:
                result_queue.put((self, cdata))
        except Exception:
            log.exception('HyRead: Failed to download cover from:', cached_url)

    def _build_query(self, title, authors):
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

    def _search(self, log, query, field, timeout):
        url = '%s?search_field=%s&search_input=%s' % (
            self.SEARCH_URL, field, quote(query)
        )
        log.info('HyRead: Searching: %s' % url)

        br = self.browser
        try:
            raw = br.open_novisit(url, timeout=timeout).read()
        except Exception as e:
            log.exception('HyRead: Search failed: %s' % e)
            return []

        html = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else raw

        ids = re.findall(r'href="bookDetail\.jsp\?id=(\d+)"', html)
        unique_ids = list(dict.fromkeys(ids))
        log.info('HyRead: Found %d results' % len(unique_ids))
        return unique_ids

    def _fetch_book_metadata(self, log, hyread_id, timeout):
        url = self.BOOK_URL % hyread_id
        log.info('HyRead: Fetching: %s' % url)

        br = self.browser
        try:
            raw = br.open_novisit(url, timeout=timeout).read()
        except Exception as e:
            log.exception('HyRead: Fetch failed: %s' % e)
            return None

        html = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else raw
        return self._parse_book_page(log, html, hyread_id)

    def _parse_book_page(self, log, html, hyread_id):
        from calibre.utils.date import parse_date, utcnow
        import json

        title = None
        authors = []
        publisher = None
        isbn_val = None
        pubdate_str = None
        series = None

        # --- Parse book_cont_list items (most reliable structured data) ---
        list_section = re.search(
            r'class="book_cont_list"(.*?)</ul>', html, re.DOTALL
        )
        if list_section:
            items = re.findall(
                r'<li class="list-item">(.*?)</li>',
                list_section.group(1), re.DOTALL
            )
            for item in items:
                text = re.sub(r'<[^>]+>', '', item).strip()
                text = re.sub(r'\s+', ' ', text)

                if text and not title and not any(
                    text.startswith(p) for p in
                    ['點閱', '譯自', '作者', '出版社', '出版年',
                     '集叢名', 'ISBN', 'EISBN', '格式', '字數', '●']
                ):
                    title = text

                if '作者：' in text or '作者:' in text:
                    author_text = re.sub(r'^作者[：:]', '', text).strip()
                    authors = self._parse_authors(author_text, item)

                elif text.startswith('出版社：') or text.startswith('出版社:'):
                    publisher = re.sub(r'^出版社[：:]', '', text).strip()

                elif text.startswith('出版年：') or text.startswith('出版年:'):
                    pubdate_str = re.sub(r'^出版年[：:]', '', text).strip()

                elif text.startswith('ISBN：') or text.startswith('ISBN:'):
                    raw_isbn = re.sub(r'^ISBN[：:]', '', text).strip()
                    if check_isbn(raw_isbn):
                        isbn_val = raw_isbn

                elif text.startswith('集叢名：') or text.startswith('集叢名:'):
                    series = re.sub(r'^集叢名[：:]', '', text).strip()

        # --- Fallback title from og:title ---
        if not title:
            og_title = re.search(
                r'<meta\s+property="og:title"\s+content="([^"]+)"', html
            )
            if og_title:
                parts = og_title.group(1).split(' | ')
                if parts:
                    title = parts[0].strip()

        if not title:
            log.error('HyRead: No title found for %s' % hyread_id)
            return None

        # --- Fallback authors from og:title ---
        if not authors:
            og_title = re.search(
                r'<meta\s+property="og:title"\s+content="([^"]+)"', html
            )
            if og_title:
                parts = og_title.group(1).split(' | ')
                if len(parts) >= 2:
                    authors = self._parse_authors(parts[1], '')

        if not authors:
            authors = [_('Unknown')]

        mi = Metadata(title, authors)
        mi.identifiers = {'hyread': hyread_id}

        if publisher:
            mi.publisher = publisher
        if isbn_val:
            mi.isbn = isbn_val
        if series:
            mi.series = series

        # --- Pub date ---
        if pubdate_str:
            year_match = re.search(r'(\d{4})', pubdate_str)
            if year_match:
                try:
                    default = utcnow().replace(month=1, day=1)
                    mi.pubdate = parse_date(
                        year_match.group(1), assume_utc=True, default=default
                    )
                except Exception:
                    pass

        # --- Fallback publisher from JSON-LD ---
        if not mi.publisher:
            jsonld_match = re.search(
                r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                html, re.DOTALL
            )
            if jsonld_match and 'Product' in jsonld_match.group(1):
                pass
            for block in re.findall(
                r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                html, re.DOTALL
            ):
                if '"Product"' in block:
                    try:
                        data = json.loads(block.strip())
                        if data.get('brand'):
                            mi.publisher = data['brand']
                    except Exception:
                        pass
                    break

        # --- Language: default to Chinese ---
        mi.languages = ['zho']

        # --- Cover: og:image ---
        cover_match = re.search(
            r'<meta\s+property="og:image"\s+content="([^"]+)"', html
        )
        mi._hyread_cover_url = None
        if cover_match:
            cover_url = cover_match.group(1).strip()
            if cover_url:
                mi._hyread_cover_url = cover_url

        # --- Description: #int tab + collapsed #jumpBookDesc ---
        comments = self._extract_description(html)
        if comments:
            mi.comments = comments

        # --- Tags: JSON-LD BreadcrumbList categories ---
        tags = []
        for block in re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html, re.DOTALL
        ):
            if 'BreadcrumbList' in block:
                try:
                    data = json.loads(block.strip())
                    items = data.get('itemListElement', [])
                    for item in items[:-1]:
                        name = item.get('item', {}).get('name', '')
                        name = name.strip().strip('\xa0')
                        if name and name != 'HyRead ebook':
                            tags.append(name)
                except Exception:
                    pass
                break
        if tags:
            mi.tags = tags

        log.info('HyRead: Parsed "%s" by %s [ISBN:%s]' % (
            mi.title, ', '.join(mi.authors), mi.isbn or 'N/A'
        ))
        return mi

    def _parse_authors(self, text, html_context):
        text = text.strip()
        text = re.sub(r'\s*[,，;；]\s*', ',', text)

        parts = re.split(r'[,]', text)
        authors = []
        for part in parts:
            part = part.strip()
            part = re.sub(
                r'(合著|合譯|合編|著|作|譯|編|繪|朗讀|主編|審訂|校注|注釋|原著|改寫|監修)$',
                '', part
            )
            part = part.strip()
            if part:
                authors.append(part)
        return authors if authors else []

    def _extract_description(self, html):
        int_tab = re.search(
            r'<div\s+id="int"[^>]*>(.*?)</div>\s*(?:<div\s+id="author"|<!--)',
            html, re.DOTALL
        )
        if not int_tab:
            int_tab = re.search(
                r'<div\s+id="int"[^>]*>(.*?)<div\s+class="more_wrap"',
                html, re.DOTALL
            )
        if int_tab:
            desc = int_tab.group(1)
            desc = re.sub(
                r'<div\s+id="jumpBookDesc"[^>]*>', '', desc
            )
            desc = re.sub(r'<div\s+class="more_wrap".*', '', desc, flags=re.DOTALL)
            desc = desc.strip()
            if desc:
                return desc

        # Fallback: og:description
        og_desc = re.search(
            r'<meta\s+property="og:description"\s+content="([^"]+)"', html
        )
        if og_desc:
            return og_desc.group(1).strip()
        return None


if __name__ == '__main__':
    from calibre.ebooks.metadata.sources.test import (
        test_identify_plugin, title_test, authors_test
    )
    test_identify_plugin(
        HyReadBooks.name, [
            (
                {'title': '原子習慣', 'authors': ['詹姆斯‧克利爾']},
                [title_test('原子習慣', exact=False)]
            ),
        ]
    )
