import markdownify

from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
  
def extract_data_from_page(url, conent_id, xpath):
    options = Options()
    options.headless = True
    options.add_argument("--window-size=1920,1200")

    driver = webdriver.Chrome(options=options)
    driver.get( url )

    content_element = driver.find_elements(By.XPATH,"//div[@id='all_words']//li/a[@onclick]" )

    return [  e.text for e in content_element ]

def convert_html_to_md( html):
    return markdownify.markdownify(html, heading_style="ATX")

