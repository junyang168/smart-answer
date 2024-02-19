from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

options = Options()
options.headless = True
options.add_argument("--window-size=1920,1200")

driver = webdriver.Chrome(options=options)

def get_firmware( firmware ):
   return {
      "Device Drivers" : firmware["DeviceDriver"],
      "Firmware Version" : firmware["FirmwareVersion"],
      "Transport Type" : firmware["Transport_Type"]
   }

def getModelDetail(category, url):
    model = {}
    loop = True
    while loop:
      try:    
         driver.get( f"https://www.vmware.com/resources/compatibility/{url}" )
         xapth = None
         match category:
            case "io":
               xpath = "//table[@class='details_table_tab1']//th[text()='Notes:']/following-sibling::td"
            case "sesrver":
               xpath = "//table[@class='details_table_tab1']//ul[text()='Notes:']/parent"
            case "san":
               xpath = "//table[@class='details_table_tab1']//span[text()='Notes:']/following-sibling::ul"
            case _:
               xpath = None
         e = driver.find_element(By.XPATH,xpath)
         if e:
            model["notes"] = e.get_attribute("innerText")
         loop = False
      except Exception as e:
         time.sleep(30)

    if category == 'san':
      details = driver.execute_script(
         """
         return { 
         'details': details,
         'cert_features':cert_features, 
         'col_CategoryName':col_CategoryName, 
         'col_Features':col_Features,
         'more_details': more_details
         }
         """)    
      more_details = details["more_details"]
    else:
      details = driver.execute_script(
         """
         return { 
         'details': details,
         'cert_features':cert_features, 
         'col_CategoryName':col_CategoryName, 
         'col_Feature_Category_Id':col_Feature_Category_Id,
         'col_Features':col_Features,
         'col_Feature_value':col_Feature_value
         }
         """)
    
    cert_features = details["cert_features"]

    releases = []
    model["Compatible Releases"]  = releases
    for rel in details["details"]:
      release = {}
      releases.append(release)
      release["Release"] = rel["ReleaseVersion"]
      match category:
         case "server":
            release["BIOS"] = rel["BIOS"] 
            release["Device Drivers"] = rel["DeviceDrivers"]
            release["Firmware Version"] = rel["FirmwareVersion"]
      certId = rel["CertDetail_Id"]     
      if category == 'san':
         firmwares =  [ d for d in more_details if d["Release_Id"] == rel["Release_Id"]]
         release["Firmwares"] = [  get_firmware(f) for f in firmwares]
         cerf = cert_features.get( str(rel["CertDetail_Id"]) )
         if cerf: 
            release["Features"] = [ {"category":cerf[details["col_CategoryName"]], "features": cerf[details["col_Features"]]} ]
      else:
         i = 1
         rel_features = []
         release["Features"] = rel_features
         release["Firmwares"] = [ {
            "Device Drivers" : rel["DeviceDrivers"],
            "Firmware Version" : rel["FirmwareVersion"] if rel["FirmwareVersion"] != 'N/A' else ''
         } ]

         while f"{certId}-{i}" in cert_features:
            cert = cert_features[f"{certId}-{i}"]
            rel_features.append(
               {
                  "category": cert[details["col_CategoryName"]],
                  "features": cert[details["col_Features"]]
                  })
            
            i += 1
    return model

import pandas as pd
import json
import sqlalchemy
import os
import psycopg2


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

def get_urls(category):
    conn = psycopg2.connect(CONNECTION_STRING)
    cur = conn.cursor()
    sql = "select url, content from compatibility_content where action is null and category= %s"
    cur.execute(sql,(category,) )
    ids = cur.fetchall()
    conn.close()    
       
    return list( map(lambda r: (r[0], json.loads(r[1])), ids ) ) 

def save_data(url, obj):
    conn = psycopg2.connect(CONNECTION_STRING)
    cur = conn.cursor()
    sql = "update compatibility_content set content = %s, action='Done' where url= %s "
    s = json.dumps(obj)
    cur.execute(sql,(s, url,  ) )
    conn.commit()
    cur.close()
    conn.close()

category = "vsanio"
#save_models(category)

urls = get_urls(category)
for url, content in urls:
  row = getModelDetail(category,url)
  for k in content:
     if not isinstance(content[k],list ) and not isinstance(content[k],dict ):
        row[k] = content[k] 
  save_data(url, row )  

print("Done")
