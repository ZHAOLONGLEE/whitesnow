"""
Metadata scraping service.
Queries CSPT (Douban-like) and TMDB to fetch media details.
"""

import httpx
import re
import asyncio
from typing import Optional, Dict
from config import settings
from database import db


# CSPT API Config
CSPT_API_URL = settings.douban_api_url
CSPT_TOKEN = settings.douban_api_token


class MetadataScraper:
    """Scrapes metadata from external APIs."""

    async def scrape(self, query: str) -> Dict:
        """
        Fetch metadata for a given title.
        Priority: CSPT -> TMDB -> Empty
        """
        # Try CSPT first (best for Chinese content)
        if CSPT_API_URL and CSPT_TOKEN:
            result = await self._fetch_cspt(query)
            if result:
                return result

        # Fallback to TMDB
        if settings.tmdb_api_key:
            result = await self._fetch_tmdb(query)
            if result:
                return result

        return {}

    async def _fetch_cspt(self, query: str) -> Optional[Dict]:
        """Fetch from CSPT (Douban proxy)."""
        try:
            # Construct URL with token
            url = f"{CSPT_API_URL}/{query}"
            
            # Some CSPT implementations require the token in the path or header.
            # Based on the provided URL structure, the token is part of the path.
            # We will try a GET request.
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()

                # Parse CSPT response (Standard Douban Proxy format)
                # Expected fields: title, cover, summary, year, rating, etc.
                return {
                    "title": data.get("title") or query,
                    "cover_url": data.get("cover"),
                    "description": data.get("summary") or data.get("intro"),
                    "year": int(data.get("year", 0)) if data.get("year") else None,
                    "rating": float(data.get("rating", 0)) if data.get("rating") else None,
                    "source": "cspt"
                }
        except Exception as e:
            print(f"CSPT fetch failed for '{query}': {e}")
            return None

    async def _fetch_tmdb(self, query: str) -> Optional[Dict]:
        """Fetch from TMDB."""
        try:
            # Clean query for TMDB (remove special chars)
            clean_query = re.sub(r'[^\w\s]', '', query)
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Search
                search_url = f"https://api.themoviedb.org/3/search/multi"
                params = {
                    "api_key": settings.tmdb_api_key,
                    "query": clean_query,
                    "language": "zh-CN"
                }
                response = await client.get(search_url, params=params)
                response.raise_for_status()
                data = response.json()

                if data["results"]:
                    item = data["results"][0]
                    
                    # Get details for better description
                    media_type = item["media_type"]
                    detail_url = f"https://api.themoviedb.org/3/{media_type}/{item['id']}"
                    detail_response = await client.get(detail_url, params=params)
                    detail_data = detail_response.json()

                    return {
                        "title": detail_data.get("title") or detail_data.get("name") or query,
                        "cover_url": f"https://image.tmdb.org/t/p/w500{detail_data.get('poster_path')}" if detail_data.get('poster_path') else None,
                        "description": detail_data.get("overview"),
                        "year": int(detail_data.get("release_date", "0000")[:4]) if detail_data.get("release_date") else None,
                        "rating": detail_data.get("vote_average"),
                        "source": "tmdb"
                    }
        except Exception as e:
            print(f"TMDB fetch failed for '{query}': {e}")
            return None

    async def download_and_save_cover(self, cover_url: str, filename: str) -> Optional[str]:
        """Download cover image to local storage."""
        if not cover_url:
            return None
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(cover_url)
                response.raise_for_status()
                
                # Determine extension
                ext = ".jpg"
                if "png" in response.headers.get("content-type", ""):
                    ext = ".png"
                
                dest_path = settings.cover_storage / f"{filename}{ext}"
                dest_path.write_bytes(response.content)
                
                return f"/static/covers/{dest_path.name}"
        except Exception as e:
            print(f"Failed to download cover for '{filename}': {e}")
            return None
