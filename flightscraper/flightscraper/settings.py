import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()


# Windows-Konfiguration
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# Scrapy-Projekt
BOT_NAME = "flightscraper"

SPIDER_MODULES = ["flightscraper.spiders"]
NEWSPIDER_MODULE = "flightscraper.spiders"

ADDONS = {}


# Umgebungsvariablen
SCRAPEOPS_API_KEY = os.getenv("SCRAPEOPS_API_KEY")
MDB_CONNECTION_STRING = os.getenv(
    "MDB_CONNECTION_STRING",
    "mongodb://localhost:27017/?directConnection=true",
)
CRAWL_DATE = os.getenv("CRAWL_DATE")


# ScrapeOps
SCRAPEOPS_FAKE_USER_AGENT_ENDPOINT = "https://headers.scrapeops.io/v1/user-agents"
SCRAPEOPS_FAKE_USER_AGENT_ENABLED = bool(SCRAPEOPS_API_KEY)
SCRAPEOPS_NUM_RESULTS = 100


# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "loggers": {
        "asyncio": {
            "level": "CRITICAL",
        },
    },
}


# Robots.txt
ROBOTSTXT_OBEY = False


# Playwright
DOWNLOAD_HANDLERS = {
    "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}

TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"

# Der Idealo-Spider verwendet einen Browserkontext.
# Die maximale Seitenzahl steht in IdealoSpider.custom_settings.
PLAYWRIGHT_MAX_CONTEXTS = 1


# Downloader-Middlewares
DOWNLOADER_MIDDLEWARES = {
    "flightscraper.middlewares.ScrapeOpsFakeBrowserHeaderAgentMiddleware": 400,
}


# Item-Pipelines
ITEM_PIPELINES = {
    "flightscraper.pipelines.FlightscraperPipeline": 300,
    "flightscraper.pipelines.SaveToMongoDBPipeline": 400,
}


# Export
FEED_EXPORT_ENCODING = "utf-8"
