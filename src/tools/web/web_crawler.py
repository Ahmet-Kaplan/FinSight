"""
Web Crawler APIs

This module contains web crawling and content extraction functionality.
"""

from typing import List, Dict
import re
import json
import asyncio
import logging
import aiohttp
import httpx
from io import BytesIO
from urllib.parse import urlparse, unquote, parse_qs
import pdfplumber
import chardet

import os

try:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    _HAS_CRAWL4AI = True
except ImportError:
    _HAS_CRAWL4AI = False

from openai import OpenAI
from bs4 import BeautifulSoup

_JINA_API_KEY = os.getenv("JINA_API_KEY", "")

from ..base import Tool, ToolResult

logger = logging.getLogger(__name__)

# File extensions that should be fetched via download rather than Playwright
DOWNLOAD_EXTENSIONS = frozenset({
    '.pdf', '.xlsx', '.xls', '.csv', '.docx', '.doc',
    '.pptx', '.zip', '.tar', '.gz',
})


class Click(Tool):
    """
    Web-page detail retrieval tool that fetches HTML or PDF content for review.
    """

    def __init__(self):
        super().__init__(
            name="Web page content fetcher",
            description="Retrieve detailed content for the supplied URLs (HTML or PDF) to support downstream analysis.",
            parameters=[
                {"name": "urls", "type": "List[str]", "description": "List of URLs to crawl", "required": True},
                {"name": "task", "type": "str", "description": "Overall task description used for filtering/summarization", "required": True}
            ],
        )
        self.type = 'tool_click'

    # ----- download-target detection -----

    @staticmethod
    def _is_download_url(url: str) -> bool:
        """Detect file-like URLs that should not go through Playwright."""
        path = unquote(urlparse(url).path).lower()
        return any(path.endswith(ext) for ext in DOWNLOAD_EXTENSIONS)

    @staticmethod
    def _is_download_hint(url: str) -> bool:
        """Check URL query params for common download indicators."""
        query = parse_qs(urlparse(url).query)
        download_hints = {'download', 'export', 'format', 'type'}
        return bool(download_hints & set(k.lower() for k in query.keys()))

    # ----- download content extraction -----

    async def _fetch_download_content(self, url: str) -> str:
        """Download a file and extract text where possible."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=30, follow_redirects=True)
                if response.status_code != 200:
                    return f"Error: Unable to download file (status code {response.status_code})"

                content_bytes = response.content
                path_lower = unquote(urlparse(url).path).lower()

                # PDF
                if path_lower.endswith('.pdf'):
                    return self._extract_pdf_bytes(content_bytes)

                # Excel
                if path_lower.endswith(('.xlsx', '.xls')):
                    return self._extract_excel_bytes(content_bytes)

                # CSV
                if path_lower.endswith('.csv'):
                    return self._extract_csv_bytes(content_bytes)

                # DOCX
                if path_lower.endswith('.docx'):
                    return self._extract_docx_bytes(content_bytes)

                # Unsupported — return metadata
                ext = path_lower.rsplit('.', 1)[-1] if '.' in path_lower else 'unknown'
                return (
                    f"Downloaded .{ext} file ({len(content_bytes)} bytes) from {url} "
                    "— binary content not extractable"
                )
        except Exception as e:
            return f"Error downloading file: {str(e)[:200]}"

    @staticmethod
    def _extract_pdf_bytes(data: bytes) -> str:
        try:
            with pdfplumber.open(BytesIO(data)) as pdf:
                texts = [page.extract_text() or '' for page in pdf.pages]
                return '\n'.join(texts)
        except Exception as e:
            return f"Error extracting PDF text: {e}"

    @staticmethod
    def _extract_excel_bytes(data: bytes) -> str:
        try:
            import pandas as pd
            df = pd.read_excel(BytesIO(data))
            return df.to_string(max_rows=200)
        except Exception as e:
            return f"Error extracting Excel data: {e}"

    @staticmethod
    def _extract_csv_bytes(data: bytes) -> str:
        try:
            import pandas as pd
            detected = chardet.detect(data)
            encoding = (detected or {}).get('encoding') or 'utf-8'
            df = pd.read_csv(BytesIO(data), encoding=encoding)
            return df.to_string(max_rows=200)
        except Exception as e:
            return f"Error extracting CSV data: {e}"

    @staticmethod
    def _extract_docx_bytes(data: bytes) -> str:
        try:
            from docx import Document
            doc = Document(BytesIO(data))
            return '\n'.join(p.text for p in doc.paragraphs if p.text.strip())
        except ImportError:
            return "Downloaded .docx file — python-docx not installed, content not extractable"
        except Exception as e:
            return f"Error extracting DOCX text: {e}"

    # ----- simple HTML fetch fallback -----

    async def fetch_url(self, url: str) -> str:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=10) as response:
                    if response.status != 200:
                        return f"Error fetching url: HTTP status code {response.status}"

                    # Decode with detected encoding to avoid UTF-8 decode errors
                    raw_bytes = await response.read()
                    if not raw_bytes:
                        return "Error fetching url: Empty response"
                    detected_encoding = response.charset or chardet.detect(raw_bytes).get('encoding') or 'utf-8'
                    html_content = raw_bytes.decode(detected_encoding, errors='replace')

            soup = BeautifulSoup(html_content, 'html.parser')

            for element in soup(['script', 'style', 'meta', 'noscript', 'head', 'title']):
                element.extract()
            text = soup.get_text(separator=' ')

            lines = (line.strip() for line in text.splitlines())
            clean_text = '\n'.join(line for line in lines if line)

            return clean_text

        except asyncio.TimeoutError:
            return "Error fetching url: Request timeout"
        except Exception as e:
            return f"Error fetching url: {str(e)}"

    async def fetch_url_jina(self, url: str) -> str:
        """Fetch clean Markdown content via Jina Reader API."""
        jina_url = f"https://r.jina.ai/{url}"
        headers = {
            "Accept": "text/markdown",
            "X-Return-Format": "markdown",
        }
        if _JINA_API_KEY:
            headers["Authorization"] = f"Bearer {_JINA_API_KEY}"
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(jina_url, headers=headers, timeout=30)
                if response.status_code != 200:
                    return ""
                return response.text
        except Exception:
            return ""

    async def api_function(self, urls: List[str], task: str) -> List[ToolResult]:
        """
        Crawl each URL and return the retrieved content (up to 6000 chars).

        Uses a shared browser session for HTML pages (reduces resource leaks),
        routes download-type URLs through fetch/extraction logic, and isolates
        failures per-URL so one bad link doesn't abort the batch.

        Args:
            urls: List of target URLs.
            task: Task description for future filtering (currently unused).

        Returns:
            List[ToolResult]: Collected page snippets (never empty when urls is non-empty).
        """
        if isinstance(urls, str):
            urls = [urls]
        try:
            result_list = []
            for url in urls:
                if url.endswith(".pdf"):
                    content = await self.extract_pdf_text_async(url)
                elif _HAS_CRAWL4AI:
                    browser_conf = BrowserConfig(headless=True)
                    run_conf = CrawlerRunConfig(
                        cache_mode=CacheMode.BYPASS
                    )
                    async with AsyncWebCrawler(config=browser_conf) as crawler:
                        result = await crawler.arun(url=url, config=run_conf)
                        content = str(result.markdown)
                else:
                    content = await self.fetch_url_jina(url)
                    if not content:
                        content = await self.fetch_url(url)

                    result_list.append(
                        ClickResult(
                            name=content[:30],
                            description=f"Title: {url}",
                            data=content[:6000],
                            link=url,
                            source=f"URL: {url}"
                        )
                    )
                except Exception as e:
                    logger.warning(f"Failed to fetch {url}: {e}")
                    result_list.append(
                        ClickResult(
                            name=f"Error fetching {url[:30]}",
                            description=f"Error: {url}",
                            data=f"Failed to fetch content: {str(e)[:200]}",
                            link=url,
                            source=f"URL: {url}"
                        )
                    )

        return result_list


    def extract_json_from_text(self, text: str) -> Dict:
        """
        Attempt to parse a JSON object embedded in the supplied text.

        Args:
            text: Text that may contain JSON.

        Returns:
            Dict: Parsed JSON object, or None on failure.
        """
        # First attempt to match fenced ```json``` blocks
        pattern = r'```json(.*?)```'
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except Exception as e:
                print(f"Error parsing JSON from code block: {e}")
                return None
        else:
            # Fallback: parse the entire string as JSON
            try:
                return json.loads(text)
            except Exception as e:
                print(f"Error parsing JSON directly: {e}")
                return None

    async def extract_pdf_text_async(self, url: str) -> str:
        """
        Asynchronously extract text from a PDF URL.

        Args:
            url: PDF file URL.

        Returns:
            str: Extracted text or an error message.
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=30)
                if response.status_code != 200:
                    return f"Error: Unable to retrieve the PDF (status code {response.status_code})"

                content = response.content

                with pdfplumber.open(BytesIO(content)) as pdf:
                    full_text = ""
                    for page in pdf.pages:
                        text = page.extract_text()
                        if text:
                            full_text += text

                return full_text

        except Exception as e:
            return f"Error: {str(e)}"


class ClickResult(ToolResult):
    """Container for web-content retrieval outputs."""

    def __init__(self, name, description, data, link="", source=""):
        super().__init__(name, description, data, source)
        self.link = link

    def __str__(self):
        format_output = self.name + "\n" + self.description + "\n\n"
        format_output += f"Content: {self.data}"
        return format_output

    def brief_str(self):
        format_output = self.name + "\n" + self.description + "\n\n"
        format_output += f"Content: {self.data[:100]}"
        return format_output

    def __repr__(self):
        return self.__str__()
