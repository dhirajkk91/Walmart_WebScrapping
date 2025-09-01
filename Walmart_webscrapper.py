"""
walmart_scraper.py

Usage:
    python walmart_scraper.py
Then type the product name when prompted (e.g. "iphone").

Notes:
- This is a scraper intended for small-scale, occasional use and learning.
"""

from bs4 import BeautifulSoup
import requests
import json
import time
import random
import re
from urllib.parse import quote_plus, urlsplit, urlunsplit

HEADERS = {
    'User-Agent': '____USER_AGENT____',  # replace with a realistic user-agent string
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Referer': 'https://www.walmart.com/',
}

BOT_CHECK_KEYWORDS = [
    "are you a human", "captcha", "bot", "please verify", "verify you are a human",
    "recaptcha", "Access Denied", "unusual traffic"
]


def is_bot_page(html_text: str) -> bool:
    text = html_text[:2000].lower()
    return any(kw.lower() in text for kw in BOT_CHECK_KEYWORDS)


def normalize_product_url(raw: str) -> str:
    """Normalize product URL by removing query and fragment and ensuring full domain."""
    parsed = urlsplit(raw)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or "www.walmart.com"
    path = parsed.path
    return urlunsplit((scheme, netloc, path, "", ""))


def get_search_url(query: str, page: int) -> str:
    return f"https://www.walmart.com/search?query={quote_plus(query)}&page={page}"

def get_link(query: str, page_number: int, session: requests.Session) -> list:
    """
    Return deduped product URLs from a Walmart search page.
    Strategy:
      1) Try to collect anchors (<a href="...">) containing '/ip/'.
      2) If none found, run a regex over the page text to extract '/ip/...' paths embedded in scripts/JSON.
    """
    url = get_search_url(query, page_number)
    resp = session.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    html = resp.text

    if is_bot_page(html):
        raise RuntimeError("Request looks like a bot-check/captcha page. Try different IP / headers / manual verification.")

    soup = BeautifulSoup(html, "html.parser")

    product_urls = []

    # 1) anchor-based extraction (preferred)
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if "/ip/" in href and not href.lower().startswith("/b/"):
            # prefer normalized canonical path without query-string
            # if href lacks scheme/netloc, prepend walmart domain
            try:
                canonical = normalize_product_url(href if href.startswith("http") else "https://www.walmart.com" + href)
            except Exception:
                canonical = "https://www.walmart.com" + href.split("?")[0].split("#")[0]
            product_urls.append(canonical)

    # 2) fallback: regex search for '/ip/...' occurrences in the raw HTML (covers JS-inserted content)
    if not product_urls:
        raw_matches = re.findall(r'(?:(?:https?:)?//[^/]+)?(/ip/[^"\'\\\s<>]+)', html)
        for p in raw_matches:
            # remove query and fragment
            p = p.split('?')[0].split('#')[0]
            if not p.startswith("/ip/"):
                continue
            full = "https://www.walmart.com" + p
            product_urls.append(full)

    # dedupe preserving order
    seen = set()
    deduped = []
    for u in product_urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)

    return deduped


def parse_next_data(script_text: str) -> dict:
    """
    Parse the Next.js __NEXT_DATA__ script block and try to extract product info.
    Returns a dict of fields or raises if structure not as expected.
    """
    data = json.loads(script_text)
    initial_data = data.get("props", {}).get("pageProps", {}).get("initialData", {}).get("data", {})
    # product may be under 'product' or 'products' depending on page
    product = initial_data.get("product") or (initial_data.get("products") and (initial_data.get("products")[0] if isinstance(initial_data.get("products"), list) else None))
    reviews = initial_data.get("reviews", {}) or {}

    if not product:
        raise KeyError("No 'product' key in __NEXT_DATA__ initialData")

    info = {
        "name": product.get("name"),
        "price": (product.get("priceInfo", {}).
                  get("currentPrice", {}).
                  get("price")),
        "availability": product.get("availabilityStatus"),
        "brand": product.get("brand") or product.get("brandName"),
        "model": product.get("modelNumber", "N/A"),
        "features": product.get("keyProductFeatures", []) or product.get("bulletDescriptions", []),
        "rating": reviews.get("customerRating", 0) or product.get("rating"),
        "review_count": product.get("numReviews", 0) or product.get("reviewCount", 0),
    }
    return info


def prod_info(product_url: str, session: requests.Session) -> dict:
    """
    Fetch a product page and extract product information using several fallbacks:
      1) __NEXT_DATA__ (Next.js page JSON)
      2) application/ld+json (schema.org Product JSON-LD)
      3) heuristic search for product JSON in page text
    Returns a dict with product fields or raises an Exception with a helpful message.
    """
    resp = session.get(product_url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    html = resp.text

    if is_bot_page(html):
        raise RuntimeError("Product page looks like a bot-check/captcha. Manual verification required or try different IP/headers.")

    soup = BeautifulSoup(html, "html.parser")

    # 1) try __NEXT_DATA__
    script_next = soup.find("script", id="__NEXT_DATA__")
    if script_next:
        script_text = script_next.string or script_next.get_text()
        if script_text:
            try:
                return parse_next_data(script_text)
            except Exception:
                # fall back to other parsing methods
                pass

    # 2) try application/ld+json (schema.org)
    for s in soup.find_all("script", type="application/ld+json"):
        # some pages have empty or malformed scripts; skip those safely
        jtext = s.string or s.get_text()
        if not jtext:
            continue
        try:
            jd = json.loads(jtext)
        except Exception:
            # sometimes there are multiple JSON objects concatenated or comments - skip
            continue

        candidates = jd if isinstance(jd, list) else [jd]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            t = item.get("@type", "").lower()
            if t == "product" or "offers" in item:
                # Extract fields conservatively
                name = item.get("name")
                brand = None
                b = item.get("brand")
                if isinstance(b, dict):
                    brand = b.get("name")
                elif isinstance(b, str):
                    brand = b
                offers = item.get("offers") or {}
                price = None
                availability = None
                if isinstance(offers, dict):
                    price = offers.get("price") or offers.get("lowPrice")
                    availability = offers.get("availability")
                agg = item.get("aggregateRating") or {}
                rating = agg.get("ratingValue")
                review_count = agg.get("reviewCount") or agg.get("reviewCount")
                return {
                    "name": name,
                    "price": price,
                    "availability": availability,
                    "brand": brand,
                    "model": item.get("sku", "N/A"),
                    "features": item.get("description", ""),
                    "rating": rating,
                    "review_count": review_count,
                }

    # 3) heuristic: search for '"product":' substring and try to parse nearby JSON
    if '"product":' in html:
        # look for a JSON-looking block around "props" or "initialData"
        idx = html.find('{"props"')
        if idx != -1:
            # take a reasonable slice and attempt to fix/truncate to JSON
            snippet = html[idx: idx + 500000]  # large enough to include the JSON block
            # cut off at first closing </script> if present
            end_idx = snippet.find("</script>")
            if end_idx != -1:
                snippet = snippet[:end_idx]
            try:
                cand = json.loads(snippet)
                initial_data = cand.get("props", {}).get("pageProps", {}).get("initialData", {}).get("data", {})
                product = initial_data.get("product")
                if product:
                    return {
                        "name": product.get("name"),
                        "price": (product.get("priceInfo", {}).
                                  get("currentPrice", {}).
                                  get("price")),
                        "availability": product.get("availabilityStatus"),
                        "brand": product.get("brand"),
                        "model": product.get("modelNumber", "N/A"),
                        "features": product.get("keyProductFeatures", []),
                        "rating": initial_data.get("reviews", {}).get("customerRating", 0),
                        "review_count": product.get("numReviews", 0),
                    }
            except Exception:
                pass

    # if nothing worked, raise a detailed error with a short page sample for debugging
    sample = html[:1200].replace("\n", " ").strip()
    raise RuntimeError(f"Could not locate structured product JSON on the page. Page sample: {sample}")


def main():
    session = requests.Session()
    # optional: rotate a list of user-agents in HEADERS if needed (kept simple here)
    query = input("Enter the product to search: ").strip()
    if not query:
        print("No query provided. Exiting.")
        return

    try:
        max_results = int(input("Max products to fetch (default 12): ").strip() or 12)
    except Exception:
        max_results = 12
    try:
        max_pages = int(input("Max search pages to scan (default 6): ").strip() or 6)
    except Exception:
        max_pages = 6

    collected_urls = []
    collected_set = set()
    print(f"Searching Walmart for: '{query}' (up to {max_results} products, scanning up to {max_pages} pages)")

    for page in range(1, max_pages + 1):
        try:
            urls = get_link(query, page, session)
        except Exception as e:
            print(f"Failed to fetch/parse search page {page}: {e}")
            break

        for u in urls:
            if u not in collected_set:
                collected_set.add(u)
                collected_urls.append(u)
                if len(collected_urls) >= max_results:
                    break
        if len(collected_urls) >= max_results:
            break

        # polite delay between search pages
        time.sleep(random.uniform(0.9, 1.6))

    if not collected_urls:
        print("No product URLs found. Try a different query, increase max_pages, or run from a different IP (bot-checks possible).")
        return

    results = []
    for i, url in enumerate(collected_urls[:max_results], start=1):
        print(f"[{i}/{min(max_results, len(collected_urls))}] Fetching: {url}")
        try:
            info = prod_info(url, session)
            info["url"] = url
            results.append(info)
        except Exception as e:
            print(f"  -> Failed to parse product page: {e}")
        # courteous delay between product requests
        time.sleep(random.uniform(0.9, 1.6))

    print("\nResults:")
    print(json.dumps(results, indent=2, ensure_ascii=False))

    # save results
    fname = "walmart_results.json"
    try:
        with open(fname, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\nSaved results to {fname}")
    except Exception as e:
        print(f"Could not save results: {e}")


if __name__ == "__main__":
    main()
