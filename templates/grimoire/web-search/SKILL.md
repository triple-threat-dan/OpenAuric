---
name: web-search
description: Searches the web using the Brave Search API. Requires BRAVE_SEARCH_API_KEY env var.
parameters_json: {"type": "object", "properties": {"query": {"type": "string", "description": "The search query."}, "num_results": {"type": "integer", "description": "Number of results to return (default 10, max 20)."}}, "required": ["query"]}
---

Performs a web search and returns a list of results with titles, snippets, and URLs.

**Parameters:**
- `query` (str): The search query.
- `num_results` (int, optional): Number of results to return (default: 10, max: 20).

**Output:**
JSON object with a "results" key containing a list of result objects.
Each result object has:
- `title` (str)
- `description` (str)
- `url` (str)
- `age` (str, optional)