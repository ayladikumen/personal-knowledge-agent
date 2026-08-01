import re
import requests
from bs4 import BeautifulSoup
# yt-dlp is powerful for extracting youtube info/transcripts easily if needed,
# though for a lightweight script, fetching the webpage or using an API is an alternative.
# We'll use a simple requests approach for general web pages and a basic youtube parser.

import yt_dlp

class ContentProcessor:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })

    def is_url(self, text: str) -> bool:
        url_pattern = re.compile(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')
        return bool(url_pattern.search(text))
        
    def extract_url(self, text: str) -> str:
        url_pattern = re.compile(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')
        match = url_pattern.search(text)
        if match:
            return match.group(0)
        return None

    def process_message(self, text: str) -> dict:
        url = self.extract_url(text)
        if not url:
            # It's just text
            return {
                "type": "text",
                "content": text,
                "url": None
            }
            
        if "youtube.com" in url or "youtu.be" in url:
            return self._process_youtube(url)
        elif "github.com" in url:
            return self._process_github(url)
        else:
            return self._process_general_url(url)

    def _process_youtube(self, url: str) -> dict:
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True # For speed, just get metadata if transcript isn't immediately available
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                title = info.get('title', 'Unknown YouTube Video')
                description = info.get('description', '')
                
                content = f"Title: {title}\n\nDescription:\n{description}"
                return {
                    "type": "youtube",
                    "content": content,
                    "url": url
                }
        except Exception as e:
            return {"type": "error", "content": f"Failed to extract YouTube info: {str(e)}", "url": url}

    def _process_github(self, url: str) -> dict:
        # A simple way to process Github is to fetch the README
        try:
            # Convert https://github.com/user/repo to https://raw.githubusercontent.com/user/repo/main/README.md
            # This is a naive approach; it might fail if default branch is master
            parts = url.replace("https://github.com/", "").split("/")
            if len(parts) >= 2:
                user, repo = parts[0], parts[1]
                
                # try main
                raw_url = f"https://raw.githubusercontent.com/{user}/{repo}/main/README.md"
                r = self.session.get(raw_url)
                if r.status_code == 404:
                    # try master
                    raw_url = f"https://raw.githubusercontent.com/{user}/{repo}/master/README.md"
                    r = self.session.get(raw_url)
                
                if r.status_code == 200:
                    return {
                        "type": "github",
                        "content": f"Github Repo: {user}/{repo}\n\nREADME:\n{r.text[:5000]}", # truncate to avoid massive readmes
                        "url": url
                    }
            
            # Fallback to general scraping
            return self._process_general_url(url)
        except Exception as e:
            return {"type": "error", "content": f"Failed to extract Github info: {str(e)}", "url": url}

    def _process_general_url(self, url: str) -> dict:
        try:
            r = self.session.get(url, timeout=10)
            soup = BeautifulSoup(r.text, 'html.parser')
            
            title = soup.title.string if soup.title else "Unknown Page"
            
            # Extract paragraphs
            paragraphs = soup.find_all('p')
            text_content = "\n".join([p.get_text() for p in paragraphs])
            
            return {
                "type": "url",
                "content": f"Title: {title}\n\nContent:\n{text_content[:5000]}",
                "url": url
            }
        except Exception as e:
            return {"type": "error", "content": f"Failed to extract webpage: {str(e)}", "url": url}
