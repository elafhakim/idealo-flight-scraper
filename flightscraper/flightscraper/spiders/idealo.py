import csv
import time
import scrapy
import asyncio
from scrapy_playwright.page import PageMethod
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse
from flightscraper.items import FlightscraperItem
from datetime import date, timedelta, datetime, timezone
from flightscraper.utils.status_handler import StatusHandler

class IdealoSpider(scrapy.Spider):
    name = "idealo"
    allowed_domains = ["flug.idealo.de"]

    PLAYWRIGHT_ABORT_REQUEST = lambda req: req.resource_type in ["image", "stylesheet", "media", "font"]
    #   Diese überschreiben die Werte aus settings.py.
    custom_settings = {
        "PLAYWRIGHT_MAX_PAGES_PER_CONTEXT": 12,
        "DOWNLOAD_TIMEOUT": 60,
        "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": 30000,  
        "PLAYWRIGHT_DEFAULT_TIMEOUT": 30000,
        "DOWNLOAD_DELAY": 0.5,
        "RANDOMIZE_DOWNLOAD_DELAY": True,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_MAX_DELAY": 20,
        "AUTOTHROTTLE_START_DELAY": 2.0,  # war 3
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 12.0,  # war 1
        "CONCURRENT_REQUESTS": 12,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 12,
        "COOKIES_ENABLED": True,
        "RETRY_ENABLED": True,
        "RETRY_TIMES": 1,
        "PLAYWRIGHT_ABORT_REQUEST": PLAYWRIGHT_ABORT_REQUEST,
    }
    DEPARTURE_START = date(2026, 10, 12)
    DEPARTURE_END = date(2026, 10, 18) 
    WEEKDAYS_DE = ["Mo.", "Di.", "Mi.", "Do.", "Fr.", "Sa.", "So."] 
    FLIGHT_CLASS_NAME = ( "business" ) 
    COMFORT_CLASS = "2"  # 2 for business, 1 for economy

    def __init__(self, batch_start=0, limit=1, status_file=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.batch_start = int(batch_start)
        self.limit = int(limit)
        self.status_file = status_file or f"batch.txt"
        self.status_handler = StatusHandler(self.status_file)

    # Abflugtage erzeugen und in die Formate umwandeln, die Idealo benötigt
    def iter_departure_dates(self):
        departure_date = self.DEPARTURE_START
        while departure_date <= self.DEPARTURE_END:
            yield departure_date  # Gib ein Datum zurück und merke dir die aktuelle Position. Beim nächsten Aufruf geht es mit dem nächsten Tag weiter.
            departure_date += timedelta(days=1)

    # Idealo erwartet im Suchformular diese spezifische Form Mo. dd.mm.yy
    def idealo_go_date(self, d):
        return f"{self.WEEKDAYS_DE[d.weekday()]} {d:%d.%m.%y}"

    # Wandelt das Datum in das Format um, das getResults.php erwartet date(2026, 6, 22) wird zu 22.06.2026
    def idealo_outbound_date(self, d):
        return d.strftime("%d.%m.%Y")

    # weil die JSON-Antwort von Idealo so aussieht {"start_date": "2026-06-22"} strftime formatiert n Datum in genau das Textformat, das du brauchst.
    def target_departure_date(self, d):
        return d.strftime("%Y-%m-%d")

    def start_requests(self):

        # routes = self.load_routes("flight_routes.csv")
        routes = self.load_routes("data/naechster_crawl_de_strecken.csv")
        routes = routes[self.batch_start : self.batch_start + self.limit]
        blacklisted_iata = self.load_blacklisted_iata()
        completed_keys = self.status_handler.load_completed_routes()
       
        for route in routes:
            for departure_date in self.iter_departure_dates():
                if (route["uid"], departure_date.isoformat()) in completed_keys:
                    continue

                if route["from"] == route["to"]:
                    self.status_handler.mark_route_status(route, departure_date, "same_iata")
                    continue

                if route["from"] in blacklisted_iata or route["to"] in blacklisted_iata:
                    self.status_handler.mark_route_status(route, departure_date, "blacklisted_iata")
                    continue

                self.status_handler.mark_route_status(route, departure_date, "queued")
                # Route aus csv gelesen und für sie eine HTTP-POST-Request(Playwright-Request) an Scrapy übergeben
                yield scrapy.FormRequest(  # intern passiert await page.goto("https://flug.idealo.de/search.php?action=search")
                    url="https://flug.idealo.de/search.php?action=search",  # Flugsuche über das API Call search.php?action=search
                    formdata={
                        "adults": "1",
                        "children": "0",
                        "infants": "0",
                        "comfortclass": self.COMFORT_CLASS,
                        "direct": "1",  # only direct flights
                        "flexdates": "0",
                        "from": route["from"],
                        "to": route["to"],
                        "from_short": route["from"],
                        "to_short": route["to"],
                        "go_date": self.idealo_go_date(departure_date),
                        "type": "oneway",
                        "form_type": "simple",
                    },
                    callback=self.parse_search_response,  # wird aufgerufen wenn die Suchseite von Idealo Flugsuche erfolgreich geladen wurde
                    errback=self.handle_search_error,  # wird aufgerufen wenn die Suchseite von Idealo fehlgeschlagen  wurde
                    headers=self.search_headers(route),
                    cb_kwargs={
                        "route": route,
                        "departure_date": departure_date,
                        "seen_last": set(),
                        "seen_keys": set(),
                    },
                    meta={
                        "playwright": True,  #    Playwright/Browser ladet diese post Request(search-request)
                        "playwright_include_page": True,  # Scrapy-Playwright gib  mir das echte Playwright-Page-Objek
                        "playwright_page_goto_kwargs": {
                            "wait_until": "domcontentloaded",
                            "timeout": 30000,
                        },
                        "playwright_page_methods": [
                            PageMethod(
                                "wait_for_timeout", 3000
                            ),  # Dann hat Idealo mehr Zeit, startSearch.php oder getResults.php zu laden und somit search_id finden kann
                            # PageMethod("wait_for_load_state", "networkidle")
                        ],
                        "download_timeout": 60,
                        "handle_httpstatus_list": [
                            503
                        ],  # Falls ne 503-Response zurückkommt, soll trotzdem parse_search_response() aufgerufen werden
                    },
                    dont_filter=True,
                )

    def search_headers(self, route):  # für die search request
        return {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
            "content-type": "application/x-www-form-urlencoded",
            "origin": "https://flug.idealo.de",
            "referer": "https://flug.idealo.de/",  # flugroute/Frankfurt-FRA/New-York-JFK/
            "user-agent": self.get_user_agent(),
        }

    def load_routes(self, csv_path):
        routes = []

        with Path(csv_path).open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)

            for row in reader:
                routes.append(
                    {
                        "uid": row["flugroute_id"].strip(),
                        "from": row["abflug_iata"].strip().upper(),
                        "to": row["ankunft_iata"].strip().upper(),
                    }
                )
        return routes

    # wird aufgerufen wenn die Suchseite von Idealo geladen und Responnse zurück ist
    async def parse_search_response(self, response, route, departure_date, seen_last, seen_keys):
        self.status_handler.mark_route_status(route, departure_date, "response_received")  # Idealo-Suchseite wurde geladen.
        page = response.meta["playwright_page"]  #  auf Browser-Objekt greifen

        try:  #   try finally sollte den playwright schritt schützen
            if response.status == 503:
                self.status_handler.mark_route_status(route, departure_date, "503_service_unavailable")
                return  # geh zu fininally, keine Suchergebnisse -> keine normale Suche möglich
            tiny_id = response.url.rstrip("/").split("/")[
                -1
            ]  # tiny_id wird aus der URL gelesen
            #   alle geladenen Netzwerk-Ressourcen auslesen, dabei werden startSearch.php, getResults.php geladen
            #   Daraus extrahiere die echte searchid, die für weitere API-Aufrufe benötigt wird.
            resource_urls = await asyncio.wait_for(
                page.evaluate(""" () => performance.getEntriesByType('resource').map(e => e.name) """),
                timeout=10,
            )
        except Exception as e:
            self.logger.error(f"PLAYWRIGHT PARSE ERROR | Route {route['uid']} | {route['from']} -> {route['to']} | {e}")
            # Playwright konnte die geladenenen Browser-Ressourcen nicht lesen
            self.status_handler.mark_route_status(route, departure_date, "resource_extraction_error")
            return

        finally:
            try:  # Bei erfolgreichem Ablauf das playwright-page (Playwright-Browser-Tab) schließen
                await asyncio.wait_for( page.close(), timeout=5 )
            except (
                Exception
            ) as e:  # falls Playwright beim Schließen der Page ein Problem hat dann
                self.status_handler.mark_route_status(route, departure_date, "page_not_closed")

        search_id = None
        #  Nachdem die Suchseite geladen wurde, alle vom Browser geladenen Ressourcen auslesen
        for url in resource_urls:
            if "startSearch.php" in url or "getResults.php" in url:

                query = parse_qs(urlparse(url).query)
                # searchid aus den geladenen Ressourcen (url) lesen - searchid in result-page finden
                search_ids = query.get("searchid")
                if search_ids:
                    search_id = search_ids[0]
                    break

        if not search_id:       # Resourcen wurde gelesen, aber Idealo hat keine searchid geliefert
            self.status_handler.mark_route_status(route, departure_date, "no_searchid_found")
            return

        for request in self.start_api_request(
            route=route,
            departure_date=departure_date,
            seen_last=seen_last,
            seen_keys=seen_keys,
            tiny_id=tiny_id,
            search_id=search_id,
        ):
            yield request

    def start_api_request(self, route, departure_date, seen_last, seen_keys, tiny_id, search_id):
        params = {
            "searchid": search_id,
            "tinyId": tiny_id,
            "last": "0",
            "formtype": "simple",
            "type": "oneway",
            "outboundAirportStartCode": route["from"],
            "outboundAirportArrivalCode": route["to"],
            "outboundDate": self.idealo_outbound_date(departure_date),
            "personCount": "1",
            "adults": "1",
            "infants": "0",
            "children": "0",
            "comfortclass": self.COMFORT_CLASS,
            "_": str(int(time.time() * 1000)),
        }
        api_url = "https://flug.idealo.de/ajax/getResults.php?" + urlencode(params)

        yield scrapy.Request(
            url=api_url,
            callback=self.parse_api,
            errback=self.handle_api_error,  # jeder API-Request sollte eine Fehlerbehandlung bekommen.
            headers=self.api_headers(tiny_id),
            cb_kwargs={
                "route": route,
                "departure_date": departure_date,
                "seen_last": seen_last,
                "seen_keys": seen_keys,
                "tiny_id": tiny_id,
            },
            dont_filter=True,
        )

    def api_headers(self, tiny_id):
        return {
            "accept": "application/json, text/javascript, */*; q=0.01",
            "accept-language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
            "referer": f"https://flug.idealo.de/ergebnis/{tiny_id}",
            "priority": "u=1, i",
            "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": self.get_user_agent(),
            "x-abgroup": "A",
            "x-requested-with": "XMLHttpRequest",
            # "cookie": " ",
        }

    def get_user_agent(self):
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/147.0.0.0 Safari/537.36"
        )
    
    # parst die API JSON RESPONSE von getResults.php, extrahiert Flugangebote, behandelt Pagination, setzt am Ende completed
    def parse_api(self, response, route, departure_date, seen_last, seen_keys, tiny_id):
        try:
            data = response.json()
        except Exception as e:
            self.logger.error(f"JSON ERROR | Route {route['uid']} | {route['from']} -> {route['to']} | {e}")
            self.status_handler.mark_route_status(route, departure_date, "json_error")
            return

        try:
            offers = data.get("offers", [])
            # if offers:
            # offer_data = offers[0].get("offer", {})#neu
            # print(json.dumps(offers[0], indent=2, ensure_ascii=False))

            for offer in offers:
                airport = offer.get("flight", {}).get("out", {}).get("airport", {})
                # Direktflugfilter
                out = offer.get("flight", {}).get("out", {})
                airport = out.get("airport", {})
                stops_airports = out.get("stops_airports", [])

                 # Nur die gewünschte Route
                if ( airport.get("start_code") != route["from"] or airport.get("arrival_code") != route["to"] ):
                    continue
    
                if len(stops_airports) != 0:
                    continue

                if airport.get("start_date") != self.target_departure_date(departure_date):
                    continue

                item = self.extract_flight_data(offer, response.url, route)
                key = self.build_unique_flight_key(offer, item)

                if key in seen_keys:
                    continue

                seen_keys.add(key)
                yield item

            # Pagination-Block
            next_last = data.get("last")  # Zeiger(Cursor) auf die nächste Ergebnisseite. Wird vollständig vom Idealo-Server bestimmt.

            if next_last and next_last not in seen_last:
                seen_last.add(next_last)

                next_url = self.replace_query_param(response.url, "last", str(next_last))
                next_url = self.replace_query_param(next_url, "_", str(int(time.time() * 1000)))

                yield scrapy.Request(
                    url=next_url,
                    callback=self.parse_api,
                    errback=self.handle_api_error,  # Bei jedem next_url Request ebenfalls:
                    headers=self.api_headers(tiny_id),
                    cb_kwargs={
                        "route": route,
                        "departure_date": departure_date,
                        "seen_last": seen_last,
                        "seen_keys": seen_keys,
                        "tiny_id": tiny_id,
                    },
                    dont_filter=True,
                )

            elif not next_last:  # keine offers mehr dann Route als fertig markieren
                self.status_handler.mark_route_status(route, departure_date, "completed")
            else:  # Wenn next_last existiert, aber schon in seen_last ist, dann passiert nichts deswegen
                self.status_handler.mark_route_status(route, departure_date, "completed")
        except Exception as e:
            self.logger.error(f"PARSE_API ERROR | Route {route['uid']} | {route['from']} -> {route['to']} | {e}")
            self.status_handler.mark_route_status(route, departure_date, "parse_api_error") # error Verarbeiten gültigen JSON-RESPONSE_Daten
            return

    def extract_flight_data(self, offer, route):
        item = FlightscraperItem()
        out = offer.get("flight", {}).get("out", {})
        airport = out.get("airport", {})
        offer_data = offer.get("offer", {})
        item["crawled_at"] = datetime.now(timezone.utc).isoformat()
        item["price"] = offer.get("offer", {}).get("total_price") or ""
        airlines = out.get("airlines", [])
        item["airline_name"] = ",".join(airline.get("name", "") for airline in airlines)
        item["airline_iata"] = ",".join(airline.get("code", "") for airline in airlines)
        item["duration"] = airport.get("flightduration", "")
        item["stops"] = len(out.get("stops_airports", []))
        departure_time = airport.get("start_time", "")
        departure_date = airport.get("start_date", "")
        item["departure"] = f"{departure_date}T{departure_time}:00"
        arrival_time = airport.get("arrival_time")
        arrival_date = airport.get("arrival_date")
        item["arrival"] = f"{arrival_date}T{arrival_time}:00"
        item["from_iata"] = airport.get("start_code", route["from"])
        item["to_iata"] = airport.get("arrival_code", route["to"])
        item["flight_route_id"] = route["uid"]
        item["flight_class"] = self.FLIGHT_CLASS_NAME
        item["checked_baggage_included"] = offer_data.get("baggage_included")
        item["carry_on_baggage_included"] = offer_data.get("handBaggage", {}).get("included")
        item["additional_baggage"] = offer_data.get("additional_baggage")
        item["baggage_info_text"] = offer_data.get("baggage_info_text")
        item["personal_item_included"] = offer_data.get("personal_item_included")
        item["remaining_seats"] = offer_data.get("remaining_seats")

        return item

    def build_unique_flight_key(self, offer, item):
        out = offer.get("flight", {}).get("out", {})
        flightsteps = out.get("flightsteps", "")
        #flight_numbers = tuple(airline.get("flight_number", "") for airline in out.get("airlines", []) )
        
        return (
            item["from_iata"],
            item["to_iata"],
            item["departure"],
            item["duration"],
            flightsteps,
        )

    def replace_query_param(self, url, key, value):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        query[key] = [value]

        new_query = urlencode(query, doseq=True)

        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                new_query,
                parsed.fragment,
            )
        )

    def handle_api_error(self, failure):
        route = failure.request.cb_kwargs["route"]
        departure_date = failure.request.cb_kwargs["departure_date"]

        self.logger.error( f"API ERROR | Route {route['uid']} | " f"{route['from']} -> {route['to']} | "f"{failure.value}")
        self.status_handler.mark_route_status(route, departure_date, "api_error")

    async def handle_search_error(self, failure):
        route = failure.request.cb_kwargs["route"]
        departure_date = failure.request.cb_kwargs["departure_date"]
        error_text = str(failure.value)
        self.logger.error(f"SEARCH ERROR | Route {route['uid']} | {route['from']} -> {route['to']} | {error_text}")
        # page=Browser-Tab von Playwright schließen, wenn beim Laden der Suchseite ein Fehler passiert.
        page = failure.request.meta.get("playwright_page")
        if page:
            try:
                await asyncio.wait_for(page.close(), timeout=5)  #    playwright-page schließen
            except Exception:
                pass
        # Temporäre Playwright-/Browser-Probleme ( zb Page.goto: Timeout 30000ms exceeded ) behandeln
        if  "Timeout" in error_text:  # Playwright konnte im Moment die Suchseite nicht rechtzeitig laden, später kann es funktioneiren
            self.status_handler.mark_route_status(route, departure_date, "Playwright_Timeout")
            return

        if "Page crashed" in error_text:
            self.status_handler.mark_route_status(route, departure_date, "page_crashed")
            return

        if "Connection closed" in error_text:
            self.status_handler.mark_route_status(route, departure_date, "connection_closed")
            return
        # Playwright wollte neuen Browser-Tab/Page erzeugen, aber Chromium/Driver war gerade instabil oder schon teilweise geschlossen.
        if "Target.createTarget" in error_text:
            self.status_handler.mark_route_status(route, departure_date, "target_create_target")
            return
        # else
        self.status_handler.mark_route_status(route, departure_date, "search_error")

    def load_blacklisted_iata(self):
        path = Path("preprocessing/blacklisted_iata_for_idealo.txt")

        if not path.exists():
            return set()

        return {
            line.strip().upper()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }