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


class PubuBooks(Source):

    name = 'Pubu Books'
    author = 'Nelson'
    version = (1, 0, 0)
    minimum_calibre_version = (5, 0, 0)

    description = _(
        'Downloads metadata and covers from Pubu (pubu.com.tw). '
        'Useful for Traditional Chinese ebooks.'
    )

    capabilities = frozenset(['identify', 'cover'])
    touched_fields = frozenset([
        'title', 'authors', 'tags', 'pubdate', 'comments', 'publisher',
        'identifier:isbn', 'identifier:pubu', 'languages', 'series'
    ])
    supports_gzip_transfer_encoding = True
    cached_cover_url_is_reliable = True

    SEARCH_URL = 'https://www.pubu.com.tw/search'
    BOOK_URL = 'https://www.pubu.com.tw/ebook/%s'

    def get_book_url(self, identifiers):
        pubu_id = identifiers.get('pubu', None)
        if pubu_id:
            return ('pubu', pubu_id, self.BOOK_URL % pubu_id)
        return None

    def get_cached_cover_url(self, identifiers):
        pubu_id = identifiers.get('pubu', None)
        if pubu_id:
            return self.cached_identifier_to_cover_url(pubu_id)
        isbn = identifiers.get('isbn', None)
        if isbn:
            pubu_id = self.cached_isbn_to_identifier(isbn)
            if pubu_id:
                return self.cached_identifier_to_cover_url(pubu_id)
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
        query = self._build_query(title, authors, identifiers)
        if not query:
            log.error('Pubu: Insufficient metadata to construct query')
            return

        book_ids = self._search(log, query, timeout)
        if not book_ids and title:
            title_tokens = list(self.get_title_tokens(title))
            if title_tokens:
                fallback = ' '.join(title_tokens)
                if fallback != query:
                    book_ids = self._search(log, fallback, timeout)

        if not book_ids:
            log.info('Pubu: No results found')
            return

        isbn = check_isbn(identifiers.get('isbn', None))

        matched = []
        unmatched = []
        for relevance, pubu_id in enumerate(book_ids[:5]):
            if abort.is_set():
                break

            mi = self._fetch_book_metadata(log, pubu_id, timeout)
            if mi is None:
                continue

            mi.source_relevance = relevance

            if mi.isbn:
                self.cache_isbn_to_identifier(mi.isbn, pubu_id)
            cover_url = getattr(mi, '_pubu_cover_url', None)
            if cover_url:
                self.cache_identifier_to_cover_url(pubu_id, cover_url)

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
            log.info('Pubu: No cached cover found, running identify')
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
            log.info('Pubu: No cover found')
            return

        if abort.is_set():
            return

        log('Pubu: Downloading cover from:', cached_url)
        br = self.browser
        try:
            cdata = br.open_novisit(cached_url, timeout=timeout).read()
            if cdata:
                result_queue.put((self, cdata))
        except Exception:
            log.exception('Pubu: Failed to download cover from:', cached_url)

    def _build_query(self, title, authors, identifiers):
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
        url = '%s?q=%s' % (self.SEARCH_URL, quote(query))
        log.info('Pubu: Searching: %s' % url)

        br = self.browser
        try:
            raw = br.open_novisit(url, timeout=timeout).read()
        except Exception as e:
            log.exception('Pubu: Search failed: %s' % e)
            return []

        html = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else raw

        ids = re.findall(r'href="/ebook/(?:[^"]*?)(\d+)"', html)
        unique_ids = list(dict.fromkeys(ids))
        log.info('Pubu: Found %d results' % len(unique_ids))
        return unique_ids

    def _fetch_book_metadata(self, log, pubu_id, timeout):
        url = self.BOOK_URL % pubu_id
        log.info('Pubu: Fetching: %s' % url)

        br = self.browser
        try:
            raw = br.open_novisit(url, timeout=timeout).read()
        except Exception as e:
            log.exception('Pubu: Fetch failed: %s' % e)
            return None

        html = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else raw
        return self._parse_book_page(log, html, pubu_id)

    def _parse_book_page(self, log, html, pubu_id):
        import json
        from calibre.utils.date import parse_date, utcnow

        title = None
        authors = []
        publisher = None
        isbn_val = None
        language = None
        cover_url = None

        # --- JSON-LD Book schema (primary structured data) ---
        for block in re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html, re.DOTALL
        ):
            if '"Book"' in block:
                try:
                    data = json.loads(block.strip())
                    title = data.get('name', '').strip() or None
                    author_str = data.get('author', '').strip()
                    if author_str:
                        author_str = re.sub(r'\s*追蹤作者\s*', '', author_str)
                        authors = [a.strip() for a in re.split(r'[,，、;；]', author_str) if a.strip()]
                    publisher = data.get('publisher', '').strip() or None
                    raw_isbn = data.get('isbn', '').strip()
                    if not raw_isbn:
                        raw_isbn = data.get('gtin13', '').strip()
                    if check_isbn(raw_isbn):
                        isbn_val = raw_isbn
                    lang = data.get('inLanguage', '').strip()
                    if lang:
                        language = lang
                    img = data.get('image', '').strip()
                    if img and img.startswith('http'):
                        cover_url = img
                except Exception:
                    pass
                break

        # --- Fallback title from og:title: " | title | Pubu - " ---
        if not title:
            og = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html)
            if og:
                parts = og.group(1).split(' | ')
                for p in parts:
                    p = p.strip()
                    if p and p != 'Pubu' and not p.startswith('Pubu'):
                        title = p
                        break

        if not title:
            log.error('Pubu: No title found for %s' % pubu_id)
            return None

        if not authors:
            authors = [_('Unknown')]

        mi = Metadata(title, authors)
        mi.identifiers = {'pubu': pubu_id}

        if publisher:
            mi.publisher = publisher
        if isbn_val:
            mi.isbn = isbn_val

        # --- Language ---
        if language:
            lang_map = {
                'zh-TW': 'zho', 'zh-tw': 'zho', 'zh': 'zho',
                'en': 'eng', 'ja': 'jpn', 'ko': 'kor',
            }
            iso = lang_map.get(language)
            if iso:
                mi.languages = [iso]
            elif 'Chinese' in language or '中文' in language:
                mi.languages = ['zho']
        else:
            mi.languages = ['zho']

        # --- Cover (prefer _xl over _l for higher resolution) ---
        if not cover_url:
            og_img = re.search(
                r'<meta\s+property="og:image"\s+content="([^"]+)"', html
            )
            if og_img:
                cover_url = og_img.group(1).strip()
        if cover_url:
            cover_url = re.sub(r'_l\.jpg$', '_xl.jpg', cover_url)
        mi._pubu_cover_url = cover_url

        # --- Release date from HTML: <sapn>Released</sapn> ... <span>YYYY/MM/DD</span> ---
        date_match = re.search(
            r'Released</sapn>\s*</div>\s*<div[^>]*>\s*<span>(\d{4}/\d{2}/\d{2})</span>',
            html
        )
        if date_match:
            try:
                mi.pubdate = parse_date(
                    date_match.group(1), assume_utc=True,
                    default=utcnow().replace(day=15)
                )
            except Exception:
                pass

        # --- Series from HTML ---
        series_match = re.search(
            r'Series\s*</(?:sapn|span|div)>\s*'
            r'(?:<[^>]*>)*\s*'
            r'<a[^>]*>([^<]+)</a>',
            html, re.DOTALL
        )
        if series_match:
            mi.series = series_match.group(1).strip()

        # --- Description from #info-content ---
        desc = self._extract_description(html)
        if desc:
            mi.comments = desc

        # --- Tags from breadcrumbs ---
        crumbs = re.findall(
            r'class="breadcrumb-item"[^>]*>\s*<a[^>]*>([^<]+)</a>', html
        )
        skip = {'Home', 'Books', 'home', 'books', title}
        tags = [c.strip() for c in crumbs if c.strip() and c.strip() not in skip]
        if tags:
            mi.tags = tags

        log.info('Pubu: Parsed "%s" by %s [ISBN:%s]' % (
            mi.title, ', '.join(mi.authors), mi.isbn or 'N/A'
        ))
        return mi

    def _extract_description(self, html):
        # #info-content → .collapse → .font-base → <div>description</div>
        info = re.search(
            r'id="info-content"[^>]*>(.*?)</section>',
            html, re.DOTALL
        )
        if not info:
            info = re.search(
                r'id="info-content"[^>]*>(.*?)(?:id="info-|class="list-border")',
                html, re.DOTALL
            )
        if info:
            content = info.group(1)
            content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL)
            font_base = re.search(
                r'class="font-base[^"]*"[^>]*>\s*(.*?)\s*</div>\s*'
                r'(?:</div>\s*<div[^>]*collapse-btnblock|</div>\s*</div>)',
                content, re.DOTALL
            )
            if font_base:
                desc = font_base.group(1).strip()
                if desc:
                    return desc

        # Fallback: og:description, strip "Publisher: X, Author: Y, " prefix
        og = re.search(
            r'<meta\s+property="og:description"\s+content="([^"]+)"', html
        )
        if og:
            desc = og.group(1).strip()
            desc = re.sub(r'^Publisher:\s*[^,]+,\s*Author:\s*[^,]+,\s*', '', desc)
            if desc:
                return desc
        return None


if __name__ == '__main__':
    from calibre.ebooks.metadata.sources.test import (
        test_identify_plugin, title_test
    )
    test_identify_plugin(
        PubuBooks.name, [
            (
                {'title': '原子習慣'},
                [title_test('原子習慣', exact=False)]
            ),
        ]
    )
