import os
from mcp.server.fastmcp import FastMCP
from rag import RAGSearch

# Load environment variables (ensure they are loaded if this is run independently)
from dotenv import load_dotenv
load_dotenv()

# Initialize RAG
vault_path = os.getenv("OBSIDIAN_VAULT_PATH", "./vault")
rag = RAGSearch()

# Create MCP server
mcp = FastMCP("PersonalKnowledgeBase")

@mcp.tool()
def search_knowledge_base(query: str) -> str:
    """
    Search the user's personal knowledge base (Obsidian vault) for saved tools, code snippets, repositories, or inspiration.
    Use this tool whenever you need to find something the user previously saved.
    
    Args:
        query: The search query to look for in the knowledge base.
        
    Returns:
        A formatted string containing the best matching notes.
    """
    results = rag.search(query, n_results=5)
    
    if not results:
        return f"No results found in the knowledge base for '{query}'."
        
    response = f"Found {len(results)} results:\n\n"
    for idx, res in enumerate(results, 1):
        response += f"--- Result {idx} ---\n"
        response += f"Title: {res['title']}\n"
        response += f"Content Snippet: {res['content_snippet']}\n"
        response += f"Filepath: {res['filepath']}\n\n"
        
    return response

if __name__ == "__main__":
    print("Starting Personal Knowledge Base MCP Server...")
    # FastMCP run() method automatically configures stdio transport for the MCP server.
    mcp.run()
