"""
Blokkfast.se → Meta Ads Home Listings XML Feed Generator
v4 — fix: availability-värde, city i XML, postkod
"""

import requests
from bs4 import BeautifulSoup
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom
import re
import time
import datetime

BASE_URL = "https://blokkfast.se"
LISTING_PAGE = "https://blokkfast.se/till-salu/"
OUTPUT_FILE = "meta_feed.xml"
MAX_OBJECTS = 5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def fetch_soup(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def get_listing_urls(soup: BeautifulSoup) -> list:
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r"/(objekt|object|listing|till-salu)/[^/]+/?$", href, re.IGNORECASE):
            full = href if href.startswith("http") else BASE_URL + href
            if full not in links:
                links.append(full)
        elif re.search(r"blokkfast\.se/[^/]+-\d+/?$", href):
            if href not in links:
                links.append(href)

    if not links:
        for a in soup.select("a.object-card, a.listing-card, a.obj_card, .object_list a, .listings a"):
            href = a.get("href", "")
            if href:
                full = href if href.startswith("http") else BASE_URL + href
                if full not in links:
                    links.append(full)

    return links[:MAX_OBJECTS]


def get_all_object_links(soup: BeautifulSoup) -> list:
    seen = set()
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("javascript"):
            continue
        full = href if href.startswith("http") else BASE_URL.rstrip("/") + "/" + href.lstrip("/")
        skip_patterns = re.compile(r"/(kontakt|om-oss|blogg|integritet|cookie|gdpr|nyheter|till-salu/?$)", re.I)
        if skip_patterns.search(full):
            continue
        if "blokkfast.se" in full and full not in seen:
            seen.add(full)
            links.append(full)
    return links


def get_accordion_value(soup, api_label: str) -> str:
    """
    Hämtar värde från accordion-lista.
    Struktur: <li><label api-label="...">Text: </label>VÄRDE</li>
    api-label sitter på <label> inuti <li>.
    """
    # Primär: api-label på <label>
    for label_el in soup.find_all("label", attrs={"api-label": api_label}):
        parent = label_el.parent  # <li>
        full_text = parent.get_text(strip=True)
        label_text = label_el.get_text(strip=True)
        value = full_text.replace(label_text, "").strip()
        if value:
            return value

    # Fallback: api-label direkt på <li>
    for el in soup.find_all(attrs={"api-label": api_label}):
        if el.name == "label":
            continue
        label = el.find("label")
        full_text = el.get_text(strip=True)
        if label:
            value = full_text.replace(label.get_text(strip=True), "").strip()
        else:
            value = full_text.strip()
        if value:
            return value

    return ""


def clean_price(price_raw: str) -> str:
    """Extraherar enbart siffrorna och returnerar 'XXXXX SEK'."""
    only_digits = re.sub(r"[^\d\s]", "", price_raw)
    m = re.search(r"[\d][\d\s]*[\d]", only_digits)
    if m:
        number = re.sub(r"\s", "", m.group())
        return f"{number} SEK"
    digits = re.sub(r"\D", "", price_raw)
    return f"{digits} SEK" if digits else "0 SEK"


def scrape_detail(url: str) -> dict:
    try:
        soup = fetch_soup(url)
    except Exception as e:
        print(f"  ⚠ Kunde inte hämta {url}: {e}")
        return {}

    data = {"url": url}

    # Namn
    h1 = soup.find("h1", class_="banner_obj_sellingTextSubject")
    data["name"] = h1.get_text(strip=True) if h1 else ""

    # Pris
    price_raw = get_accordion_value(soup, "deals_saleInformation_price")
    if not price_raw:
        for li in soup.select(".object_info_banner ul li"):
            txt = li.get_text(strip=True)
            if "kr" in txt:
                price_raw = txt
                break
    data["price_raw"] = price_raw
    data["price"] = clean_price(price_raw)

    # Adressfält
    data["addr1"]       = get_accordion_value(soup, "deals_location_streetAddress")
    data["postal_code"] = get_accordion_value(soup, "deals_location_postalCode")
    data["city"]        = get_accordion_value(soup, "deals_location_city")
    data["region"]      = get_accordion_value(soup, "deals_location_municipality_name")
    data["county"]      = get_accordion_value(soup, "deals_location_county_name")

    # Fallbacks
    if not data["addr1"]:
        data["addr1"] = data["name"]
    if not data["city"]:
        # Försök banner
        lis = soup.select(".object_info_banner ul li")
        if lis:
            data["city"] = lis[0].get_text(strip=True)
    if not data["region"]:
        data["region"] = data["county"] or data["city"] or "Sverige"

    # Fastighetsbeteckning → ID
    listing_id = get_accordion_value(soup, "estate_propertyDesignation")
    safe_id = re.sub(r"[^A-Za-z0-9_]", "_", listing_id) if listing_id else \
              re.sub(r"[^A-Za-z0-9_]", "_", url.rstrip("/").split("/")[-1])
    data["listing_id"] = safe_id or "unknown"

    # Objekttyp
    data["property_type"] = get_accordion_value(soup, "objectsubtypetext")

    # Boarea
    data["living_area"] = get_accordion_value(soup, "customfields_data_boa")

    # Bilder
    images = []
    for li in soup.select("#lightgallery li[data-src]"):
        src = li.get("data-src", "").strip()
        tag_el = li.select_one(".gallery_img_text")
        tag_text = tag_el.get_text(strip=True) if tag_el else ""
        if src and src not in [i["url"] for i in images]:
            images.append({"url": src, "tag": tag_text})

    if not images:
        for a in soup.select(".gallery_normal li a"):
            style = a.get("style", "")
            m = re.search(r"url\(['\"]?(.*?)['\"]?\)", style)
            if m and m.group(1) not in [i["url"] for i in images]:
                images.append({"url": m.group(1), "tag": ""})

    if not images:
        for img in soup.find_all("img", src=True):
            src = img["src"]
            if "blokkfast" in src and "big_img" in src:
                if src not in [i["url"] for i in images]:
                    images.append({"url": src, "tag": ""})

    data["images"] = images[:10]

    # Koordinater
    lat, lng = "", ""
    for s in soup.find_all("script"):
        txt = s.get_text()
        m_lat = re.search(r"lat[itude]*['\"]?\s*[:=]\s*['\"]?([\d\.\-]+)", txt, re.IGNORECASE)
        m_lng = re.search(r"l(ng|on)[gitude]*['\"]?\s*[:=]\s*['\"]?([\d\.\-]+)", txt, re.IGNORECASE)
        if m_lat:
            lat = m_lat.group(1)
        if m_lng:
            lng = m_lng.group(2)
        if lat and lng:
            break
    data["lat"] = lat
    data["lng"] = lng

    return data


def build_xml(objects: list) -> str:
    root = Element("listings")
    root.set("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance")
    root.set("xsi:noNamespaceSchemaLocation",
             "http://www.facebook.com/ads/external/schema/home_listings.xsd")

    seen_ids = {}

    for obj in objects:
        if not obj:
            continue

        listing = SubElement(root, "listing")

        # Bilder
        if obj.get("images"):
            for img in obj["images"]:
                img_el = SubElement(listing, "image")
                SubElement(img_el, "url").text = img["url"]
                if img.get("tag"):
                    SubElement(img_el, "tag").text = img["tag"]
        else:
            img_el = SubElement(listing, "image")
            SubElement(img_el, "url").text = "https://blokkfast.se/wp-content/uploads/logo.png"

        # Unikt ID
        base_id = obj.get("listing_id", "unknown")
        if base_id in seen_ids:
            seen_ids[base_id] += 1
            unique_id = f"{base_id}_{seen_ids[base_id]}"
        else:
            seen_ids[base_id] = 0
            unique_id = base_id

        SubElement(listing, "home_listing_id").text = unique_id

        # availability — Meta stöder: for_sale, for_rent, sold, off_market
        SubElement(listing, "availability").text = "for_sale"

        SubElement(listing, "url").text = obj.get("url", "")
        SubElement(listing, "name").text = obj.get("name", "")
        SubElement(listing, "price").text = obj.get("price", "0 SEK")

        # Adress
        addr = SubElement(listing, "address")
        addr.set("format", "simple")

        def comp(name, val):
            c = SubElement(addr, "component")
            c.set("name", name)
            c.text = val or ""

        comp("addr1",       obj.get("addr1", ""))
        comp("city",        obj.get("city", ""))
        comp("region",      obj.get("region") or obj.get("city") or "Sverige")
        comp("postal_code", obj.get("postal_code", ""))
        comp("country",     "Sweden")

        if obj.get("lat"):
            SubElement(listing, "latitude").text = obj["lat"]
        if obj.get("lng"):
            SubElement(listing, "longitude").text = obj["lng"]

        if obj.get("city"):
            SubElement(listing, "neighborhood").text = obj["city"]
        if obj.get("living_area"):
            SubElement(listing, "living_area").text = obj["living_area"]
        if obj.get("property_type"):
            SubElement(listing, "property_type").text = obj["property_type"]

    raw = tostring(root, encoding="unicode")
    parsed = minidom.parseString(raw)
    return parsed.toprettyxml(indent="  ", encoding=None)


def main():
    print(f"\n{'='*55}")
    print(f"  Blokkfast Meta Feed v4 — {datetime.datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'='*55}\n")

    print("📡 Hämtar listningssida …")
    try:
        soup = fetch_soup(LISTING_PAGE)
    except Exception as e:
        print(f"❌ Kunde inte hämta {LISTING_PAGE}: {e}")
        return

    urls = get_listing_urls(soup)

    if not urls:
        print("  Primär sökning gav inga träffar, provar bredare sökning …")
        all_links = get_all_object_links(soup)
        nav_patterns = re.compile(r"/(kontakt|om-oss|blogg|integritet|cookie|gdpr|nyheter)", re.I)
        urls = [l for l in all_links if not nav_patterns.search(l)][:MAX_OBJECTS]

    if not urls:
        print("❌ Hittade inga objektlänkar.")
        return

    print(f"✅ Hittade {len(urls)} objektlänkar:\n")
    for u in urls:
        print(f"   {u}")

    objects = []
    for i, url in enumerate(urls, 1):
        print(f"\n[{i}/{len(urls)}] Skrapar: {url}")
        obj = scrape_detail(url)
        if obj:
            print(f"   → {obj.get('name','?')}")
            print(f"     Pris:     {obj.get('price_raw','?')} → {obj.get('price','?')}")
            print(f"     Adress:   {obj.get('addr1','?')}")
            print(f"     Stad:     {obj.get('city','?')}")
            print(f"     Region:   {obj.get('region','?')}")
            print(f"     Postkod:  {obj.get('postal_code','?')}")
            print(f"     Bilder:   {len(obj.get('images',[]))}")
        objects.append(obj)
        time.sleep(0.8)

    xml_str = build_xml(objects)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(xml_str)

    print(f"\n✅ XML-fil sparad: {OUTPUT_FILE}")
    print(f"   Objekt inkluderade: {len([o for o in objects if o])}/{MAX_OBJECTS}")
    print(f"\n{'='*55}\n")


if __name__ == "__main__":
    main()
