import sys
import json
import os
import urllib.request
import urllib.parse
import urllib.error

def main():
    # 1. Parse Arguments
    try:
        args_str = sys.argv[1] if len(sys.argv) > 1 else '{}'
        args = json.loads(args_str)
    except json.JSONDecodeError:
        print(json.dumps({"error": "Invalid JSON arguments provided."}))
        sys.exit(1)

    query = args.get("query")
    if not query:
        print(json.dumps({"error": "Missing required argument: 'query'. Please usage 'query' parameter for web searches."}))
        sys.exit(1)

    count = int(args.get("num_results", 10))
    if count > 20: count = 20 # Brave API max per page is usually 20

    # 2. Check API Key
    api_key = os.environ.get("BRAVE_SEARCH_API_KEY")
    if not api_key:
        print(json.dumps({"error": "BRAVE_SEARCH_API_KEY environment variable not set."}))
        sys.exit(1)

    # 3. Build Request
    base_url = "https://api.search.brave.com/res/v1/web/search"
    params = {
        "q": query,
        "count": count
    }
    url = f"{base_url}?{urllib.parse.urlencode(params)}"

    req = urllib.request.Request(url)
    req.add_header("X-Subscription-Token", api_key)
    req.add_header("Accept", "application/json")

    # 4. Execute Request
    try:
        with urllib.request.urlopen(req) as response:
            if response.status != 200:
                print(json.dumps({"error": f"API request failed with status {response.status}"}))
                sys.exit(1)
            
            data = json.loads(response.read().decode('utf-8'))
            
            # 5. Parse Results
            web_results = data.get("web", {}).get("results", [])
            output = []
            
            for item in web_results:
                result = {
                    "title": item.get("title", ""),
                    "description": item.get("description", ""),
                    "url": item.get("url", ""),
                    "age": item.get("age", "")
                }
                output.append(result)
            
            print(json.dumps({"results": output}, indent=2))

    except urllib.error.HTTPError as e:
        print(json.dumps({"error": f"HTTP Error: {e.code} - {e.reason}"}))
        sys.exit(1)
    except urllib.error.URLError as e:
        print(json.dumps({"error": f"URL Error: {e.reason}"}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": f"Unexpected error: {str(e)}"}))
        sys.exit(1)

if __name__ == "__main__":
    main()
