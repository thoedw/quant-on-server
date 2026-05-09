import requests
url = "https://services.entrade.com.vn/chart-api/news?ticker=VNM&page=1&size=10"
response = requests.get(url, timeout=10)
print("DNSE Status:", response.status_code)
print("Headers:", response.headers)
print("Content Preview:", response.text[:300])
