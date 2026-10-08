from bs4 import BeautifulSoup
from urllib.parse import urljoin

from ..utils import get_relevant_images, extract_title, get_text_from_soup, clean_soup

class BeautifulSoupScraper:

    def __init__(self, link, session=None):
        self.link = link
        self.session = session

    def scrape(self):
        """
        This function scrapes content from a webpage by making a GET request, parsing the HTML using
        BeautifulSoup, and extracting script and style elements before returning the cleaned content.
        
        Returns:
          The `scrape` method is returning the cleaned and extracted content from the webpage specified
        by the `self.link` attribute. The method fetches the webpage content, removes script and style
        tags, extracts the text content, and returns the cleaned content as a string. If any exception
        occurs during the process, an error message is printed and an empty string is returned.
        """
        try:
            response = self.session.get(self.link, timeout=(5, 20))
            response.raise_for_status()
            if b'%PDF-' in response.content[:1024]:
                import fitz
                with fitz.open(stream=response.content, filetype='pdf') as document:
                    text = '\n'.join(page.get_text('text') for page in document)
                    return text, [], document.metadata.get('title') or self.link
            soup = BeautifulSoup(
                response.content, "lxml", from_encoding=response.encoding
            )

            soup = clean_soup(soup)

            content = get_text_from_soup(soup)

            image_urls = get_relevant_images(soup, self.link)
            
            # Extract the title using the utility function
            title = extract_title(soup)

            return content, image_urls, title

        except Exception as e:
            from ...retrievers.search_diagnostics import record_search
            response = getattr(e, 'response', None)
            record_search('web_fetch', self.link, 'failed',
                          http_status=response.status_code if response is not None else None,
                          message='网页抓取失败（' + type(e).__name__ + '）')
            return "", [], ""
