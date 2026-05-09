import requests
import json
url = "https://finfo-api.vndirect.com.vn/v4/news?q=symbols:VNM&size=50&page=1"
headers = {'User-Agent': 'Mozilla/5.0'}
response = requests.get(url, headers=headers, timeout=10)
print("VND Status:", response.status_code)
if response.status_code == 200:
    data = response.json()
    items = data.get('data', [])
    print("Found:", len(items))
    if items:
        print("Sample Title:", items[-1].get('newsTitle'))
        print("Sample Publish Date:", items[-1].get('publishDate'))
        print("Sample Link:", items[-1].get('newsUrl'))
