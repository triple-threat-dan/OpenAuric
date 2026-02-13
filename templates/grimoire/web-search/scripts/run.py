import sys
import json
from urllib.request import urlopen
from urllib.parse import urlencode

args_str = sys.argv[1] if len(sys.argv) > 1 else '{}'
try:
    args = json.loads(args_str)
except:
    print(json.dumps({"error": "Invalid JSON args"}))
    sys.exit(1)

query = args.get("query")
if not query:
    print(json.dumps({"error": "Query required"}))
    sys.exit(1)

num_results = int(args.get("num_results", 10))

params = {
    "q": query,
    "format": "json",
    "no_html": "1",
    "skip_disambig": "1",
    "no_redirect": "1"
}

url = "https://api.duckduckgo.com/?" + urlencode(params)

try:
    with urlopen(url) as resp:
        data = json.loads(resp.read().decode())

    results = []
    # Abstract/Summary
    abstract = data.get("Abstract")
    if abstract:
        title = data.get("AbstractTitle", "Quick Summary")
        snippet = abstract[:300] + "..." if len(abstract) > 300 else abstract
        results.append({
            "title": title,
            "snippet": snippet,
            "url": data.get("AbstractURL", "")
        })

    # Related topics
    related = data.get("RelatedTopics", [])
    for topic in related[:num_results]:
        if isinstance(topic, dict) and "Text" in topic:
            text = topic["Text"]
            title = topic.get("Name") or text[:100]
            snippet = text[:250] + "..." if len(text) > 250 else text
            url_ = topic.get("FirstURL", "")
            results.append({
                "title": title,
                "snippet": snippet,
                "url": url_
            })

    print(json.dumps({"results": results[:num_results]}, indent=2))
except Exception as e:
    print(json.dumps({"error": str(e)}))
