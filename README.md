# Walmart Scraper

**Walmart Scraper** is a small Python CLI utility that searches Walmart.com for products and extracts structured product information (name, price, availability, brand, model, features, rating, review count) using the page JSON (Next.js `__NEXT_DATA__`) or JSON-LD fallbacks. It's built for small-scale, manual use and learning — not for heavy automated scraping.

---

## Features

- Search Walmart for a product query and collect product pages.
- Robust URL extraction (anchors + regex fallback for JS-injected results).
- JSON-based parsing with multiple fallbacks (`__NEXT_DATA__`, `application/ld+json`, heuristics).
- Polite delays and session re-use.
- Prints JSON results and saves to `walmart_results.json`.
- Optional guidance for using Playwright (when page content is JS-rendered or blocked).

---

## Requirements

- Python 3.8+
- See `requirements.txt` for the full list.

---

## Quick start

1. Clone the repo:
git clone https://github.com/<your-username>/walmart-scraper.git
cd walmart-scraper
