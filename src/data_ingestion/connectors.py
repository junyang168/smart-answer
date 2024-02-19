import sitemap_connector
import sf_connector
import wtf_connector

connectors = [
    sf_connector.SFConnector(),
    sitemap_connector.SitemapConnector(),
    wtf_connector.WTFConnector()
]