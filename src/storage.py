import os
import re
from datetime import datetime

class StorageManager:
    def __init__(self, vault_path: str):
        self.vault_path = vault_path
        if not os.path.exists(self.vault_path):
            os.makedirs(self.vault_path)

    def _sanitize_filename(self, title: str) -> str:
        # Remove invalid characters for Windows/Linux filenames
        sanitized = re.sub(r'[\\/*?:"<>|]', "", title)
        return sanitized.strip()[:100]  # limit length

    def save_note(self, title: str, content: str, original_url: str = None, tags: list = None) -> str:
        """
        Saves a markdown note to the Obsidian vault.
        """
        safe_title = self._sanitize_filename(title)
        if not safe_title:
            safe_title = f"Note_{datetime.now().strftime('%Y%md%H%M%S')}"

        filename = f"{safe_title}.md"
        filepath = os.path.join(self.vault_path, filename)

        # Ensure unique filename
        counter = 1
        while os.path.exists(filepath):
            filename = f"{safe_title} ({counter}).md"
            filepath = os.path.join(self.vault_path, filename)
            counter += 1

        # Format frontmatter
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tags_str = ""
        if tags:
            tags_str = "\n".join([f"  - {tag}" for tag in tags])

        frontmatter = f"---\ntitle: \"{title}\"\ndate: {date_str}\n"
        if original_url:
            frontmatter += f"source: {original_url}\n"
        if tags_str:
            frontmatter += f"tags:\n{tags_str}\n"
        frontmatter += "---\n\n"

        full_content = frontmatter + content

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(full_content)
        
        return filepath
