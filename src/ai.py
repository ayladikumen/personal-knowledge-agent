import os
import google.generativeai as genai
from typing import Dict, Any

class AIEngine:
    def __init__(self, api_key: str):
        if api_key:
            genai.configure(api_key=api_key)
        
        # We use gemini-1.5-flash as it's fast and good enough for summarization
        self.model = genai.GenerativeModel('gemini-1.5-flash')

    def analyze_content(self, text_content: str, source_url: str = None) -> Dict[str, Any]:
        """
        Analyzes the extracted text content and generates a summary, potential use cases, and tags.
        """
        prompt = f"""
        You are a personal knowledge assistant. Analyze the following content.
        Your goal is to extract the core value of this content so the user can find it useful later.
        
        Content:
        {text_content[:25000]}  # limit context size to avoid blowing up tokens if it's huge
        """
        
        if source_url:
            prompt += f"\nSource URL: {source_url}"
            
        prompt += """
        
        Please provide your analysis in the following format (ensure it's clean Markdown):
        
        # [Title of the Content]
        
        ## Summary
        [A brief 2-3 sentence summary of what this is]
        
        ## Why this is useful
        [List 2-3 specific project ideas or situations where the user should come back to this resource]
        
        ## Key Takeaways
        - [Point 1]
        - [Point 2]
        
        At the very end of your response, on a new line, provide exactly 3 to 5 comma-separated tags relevant to this content, prefixed with TAGS:. For example:
        TAGS: python, web-scraping, tool, inspiration
        """

        response = self.model.generate_content(prompt)
        result_text = response.text
        
        # Parse out the tags
        tags = []
        clean_text = result_text
        if "TAGS:" in result_text:
            parts = result_text.split("TAGS:")
            clean_text = parts[0].strip()
            tags_part = parts[1].strip()
            tags = [t.strip() for t in tags_part.split(",") if t.strip()]
        
        # Extract title from the first line if it's a heading
        title = "Saved Item"
        lines = clean_text.split('\n')
        for line in lines:
            if line.startswith("# "):
                title = line.replace("# ", "").strip()
                break
                
        return {
            "title": title,
            "markdown_content": clean_text,
            "tags": tags
        }

    def analyze_image(self, image_data: bytes) -> Dict[str, Any]:
        """
        Analyzes an image using Gemini Vision.
        """
        prompt = """
        You are a personal knowledge assistant. Analyze this image.
        Extract any text, describe what it is, and extract the core value so the user can find it useful later.
        
        Please provide your analysis in the following format (ensure it's clean Markdown):
        
        # [Title of the Content]
        
        ## Summary
        [A brief 2-3 sentence summary of what this is]
        
        ## Why this is useful
        [List 2-3 specific project ideas or situations where the user should come back to this resource]
        
        ## Image Contents
        [Describe the image and any text found in it]
        
        At the very end of your response, on a new line, provide exactly 3 to 5 comma-separated tags relevant to this content, prefixed with TAGS:. For example:
        TAGS: python, ui-design, inspiration
        """
        
        image_part = {
            "mime_type": "image/jpeg",
            "data": image_data
        }
        
        response = self.model.generate_content([prompt, image_part])
        result_text = response.text
        
        # Parse tags
        tags = []
        clean_text = result_text
        if "TAGS:" in result_text:
            parts = result_text.split("TAGS:")
            clean_text = parts[0].strip()
            tags_part = parts[1].strip()
            tags = [t.strip() for t in tags_part.split(",") if t.strip()]
            
        title = "Saved Image"
        lines = clean_text.split('\n')
        for line in lines:
            if line.startswith("# "):
                title = line.replace("# ", "").strip()
                break
                
        return {
            "title": title,
            "markdown_content": clean_text,
            "tags": tags
        }
