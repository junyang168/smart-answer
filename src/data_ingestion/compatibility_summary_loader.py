
import requests
import json
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

import pandas as pd
import numpy as np
from unicodedata import normalize

from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

def set_col_value(comp_row, column_name, cell):    
    for c in cell.children:
        match c.name:
            case "a":
                comp_row[column_name] = c.get_text()
                comp_row[column_name + "_link"] = c.get("href")
            case "table":
                rel_cells = []
                for r in c.find_all("td"):
                    for rc in r.children:
                        if rc.name != "table":
                            rel_cells.append(r)               
                if rel_cells:
                    startIdx = 0
                    prefix = ""
                    if rel_cells[0].get_text() == 'ESXi': 
                        startIdx = 1
                        prefix = 'ESXi '
                    comp_row[column_name] = ",".join(map(lambda c : prefix + c.get_text(), rel_cells[startIdx:] )) 
            case _ :
                comp_row[column_name] = cell.get_text()
        break    


def get_comp_data(driver, columns):
    table = driver.find_element(By.ID,"search_results_table_body" )

    html = table.get_attribute("outerHTML")
    soup = BeautifulSoup(html, "html.parser")

    comps = []

    for c in soup.children:
        rows = c.children
        for row in rows:
            if row.name != "tr":
                continue
            col_nodes = row.children
            if col_nodes:
                cols = list(col_nodes)
                comp_row = {}
                for idx in range(len(cols)):
                    set_col_value(comp_row, columns[idx], cols[idx] )
                comps.append(comp_row)
    return comps



import pandas as pd
import json
import sqlalchemy
import os


CONNECTION_STRING =  os.environ.get("CONNECTION_STRING") 

def save_models(category):
  with open(f"compatibility/{category}.json", "r") as f:
      models = json.load(f)
      urls = list(map(lambda m: [ category, m["Model_link"], json.dumps(m) ], models ))
      df = pd.DataFrame(urls, columns=["category","url","content"])

      # Create a database engine
      engine = sqlalchemy.create_engine(CONNECTION_STRING)

      # Save the dataframe to a table in the database
      df.to_sql("compatibility_content", engine, index=False, if_exists="append")
      # Close the engine
      engine.dispose()


options = Options()
options.headless = True
options.add_argument("--window-size=1920,1200")

driver = webdriver.Chrome(options=options)

page_no = 1
page_size = 500
url_template = """https://www.vmware.com/resources/compatibility/search.php?deviceCategory={category}&details=1&page={page_no}&display_interval={page_size}&sortColumn=Partner&sortOrder=Asc"""

categories = ["dsdk","hsm","vmdirect","server","io","san"]
categories = ["vsanio"]
for category in categories: 
    print(category)
    url = url_template.format(category=category, page_no=page_no,page_size=page_size)
    driver.get( url )

    total_element = driver.find_element(By.ID,"search_results_total" )
    total_text = total_element.get_attribute("innerText") 
    total_item = int( total_text[:total_text.index(" ")])

    print(f"total item {total_item}")

    header_row = driver.find_element(By.ID,"search_results_heading" )
    header_cells = header_row.find_elements(By.XPATH, '*')
    colomns = list( map(lambda th: th.get_attribute("innerText"), header_cells))

    comps = get_comp_data(driver, colomns)

    item_no = page_size
    while item_no < total_item:
        page_no = page_no + 1
        url = url_template.format(category=category, page_no=page_no,page_size=page_size)
        driver.get( url )
        comps.extend(get_comp_data(driver,colomns))
        item_no = item_no + page_size

    with open(f"compatibility/{category}.json", "w") as f:
        json.dump(comps, f)
        
    save_models(category)

