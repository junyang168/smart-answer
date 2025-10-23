
from typing import List, Union, Optional
from pydantic import BaseModel
import os
import json
import pytz
import datetime
import xml.etree.ElementTree as ET


class Sermon(BaseModel):
    item :str
    author: Optional[str] = None
    author_name: Optional[str] = None
    last_updated: Optional[str] = None
    status: Optional[str] = None
    assigned_to: Optional[str] = None
    assigned_to_name: Optional[str] = None
    assigned_to_date: Optional[str] = None
    title: Optional[str] = None
    deliver_date: Optional[str] = None
    published_date: Optional[str] = None
    summary: Optional[str] = None
    type: Optional[str] = None
    theme: Optional[str] = None
    thumbnail: Optional[str] = None
    keypoints: Optional[str] = None
    core_bible_verse: Optional[List[dict]] = None
    source: Optional[str] = None

class SermonMetaManager:

    def __init__(self, base_folder, user_getter) -> None:
        self.base_folder = base_folder
        self.config_folder =  os.path.join(self.base_folder, "config")
        self.metadata_file_path =  os.path.join(self.config_folder,"sermon.json")
        self.user_getter = user_getter
#        aws_access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
#        aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")
#        self.s3 = boto3.client('s3', aws_access_key_id=aws_access_key_id, aws_secret_access_key=aws_secret_access_key)
#        self.bucket_name = os.getenv('AWS_S3_BUCKET_NAME')
#        print(f'bucket_name: {self.bucket_name}') 
        self.load_sermon_metadata()
#        self.load_sermons_from_s3()

    def load_sermons_from_s3(self):
        response = self.s3.list_objects_v2(Bucket=self.bucket_name, Prefix='script_review')
        
        for obj in response['Contents']:
            file_name = obj['Key']
            last_updated = self.convert_datetime_to_cst_string(obj['LastModified'])
            metadata = self.s3.head_object(Bucket=self.bucket_name, Key=file_name)['Metadata']
            author = metadata.get('author')
            author_name = self.user_getter(author).get('name')

            item_name = file_name.split('/')[-1].split('.')[0].strip()
            sermon = next((s for s in self.sermons if s.item == item_name ), None)
            if sermon:
                sermon.last_updated = last_updated
                sermon.author = author
                sermon.author_name = author_name

    def get_refresher(self):
        return ('sermon.json', self.load_sermon_metadata)
    
    def load_dev_sermon(self):
        response = self.s3.get_object(Bucket=self.bucket_name, Key='config/sermon_dev.json')
        sermons_data =  response['Body'].read().decode('utf-8')
        sermons =  json.loads(sermons_data)
        return sermons

    def merge_dev(self, sermons, sermon_dev):
        for i in reversed(range(len(sermons))):
            if sermons[i]['status'] == 'in development':
                sermons.pop(i)

        for sd in sermon_dev:
            sermon = next((s for s in sermons if s['item'] == sd['item']), None)
            if not sermon:
                sermons.append(sd) 
    
    def format_delivery_date(self):
        for s in self.sermons:
            deliver_date = s.deliver_date
            if deliver_date:
                dp = deliver_date.split('-')
                if len(dp) == 3:
                    deliver_date = datetime.datetime(int(dp[0]), int(dp[1]), int(dp[2]))    
                    s.deliver_date = deliver_date.strftime('%Y-%m-%d')
    
    def kps_to_str(self, kps):
        if isinstance(kps, list):
            return '\n'.join(str(kp) for kp in kps)
        else:
            return kps

    def load_sermon_metadata(self):        
        with open(self.metadata_file_path) as f:            
            self.sermon_meta = json.load(f)

#        sermon_dev = self.load_dev_sermon()
#        self.merge_dev(sermon_meta, sermon_dev)
        

        
        self.sermons = [Sermon( item=m.get('item'),
                                       assigned_to= m.get('assigned_to'), 
                                       assigned_to_date=m.get('assigned_to_date'),
                                       status= m.get('status'), 
                                       summary=m.get('summary'), 
                                       theme=m.get('theme'),
                                       title=m.get('title') ,
                                       deliver_date=m.get('deliver_date'),
                                       last_updated= m.get('last_updated') if m.get('last_updated') else '2025-03-01 17:33',
                                       type=m.get('type'),
                                       published_date=  m.get('published_date'),
                                       thumbnail= '/web/data/thumbnail/' + m.get('item') + '.jpg',
                                       keypoints= self.kps_to_str(m.get('keypoints')),
                                       core_bible_verse=m.get('core_bible_verse', []),
                                       source=m.get('source')  
                                       ) for m in self.sermon_meta]
        self.format_delivery_date()

        for s in self.sermons:
            s.assigned_to_name = self.user_getter(s.assigned_to).get('name')

    def get_sermon_meta_str(self) -> str:
        return json.dumps(self.sermon_meta, ensure_ascii=False, indent=2)

    def convert_datetime_to_cst_string(self, dt: datetime.datetime) -> str:
        central = pytz.timezone('America/Chicago')
        cst_dt = dt.astimezone(central)
        cst_string = cst_dt.strftime('%Y-%m-%d %H:%M')
        return cst_string

    
    def get_sermon_metadata(self, user_id:str, item:str) -> Sermon:
        sermon = next((s for s in self.sermons if s.item == item.strip()), None)
        return sermon
  
    def update_sermon_metadata(
        self,
        user_id: str,
        item: str,
        title: Optional[str] = None,
        *,
        summary: Optional[str] = None,
        keypoints: Optional[str] = None,
        core_bible_verse: Optional[List[dict]] = None,
    ):
        sermon = self.get_sermon_metadata(user_id, item)
        if not sermon:
            return

        sermon.last_updated = self.convert_datetime_to_cst_string(datetime.datetime.now())
        sermon.author = user_id
        sermon.author_name = self.user_getter(user_id).get('name')
        if title is not None:
            sermon.title = title
        if summary is not None:
            sermon.summary = summary
        if keypoints is not None:
            sermon.keypoints = keypoints
        if core_bible_verse is not None:
            sermon.core_bible_verse = core_bible_verse

    def save_sermon_metadata(self):
        with open(self.metadata_file_path, "w") as f:
            json.dump([s.dict() for s in self.sermons], f, ensure_ascii=False, indent=4)

    def generate_sitemap(self, sermons, domain):
        urlset = ET.Element('urlset', xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")

        for s in sermons:
            url = f'/public/{s.item}' 
            url_element = ET.SubElement(urlset, 'url')
            ET.SubElement(url_element, 'loc').text = domain + url
            ET.SubElement(url_element, 'lastmod').text = s.published_date if s.status == 'published' else s.last_updated
            ET.SubElement(url_element, 'changefreq').text = 'weekly'
            ET.SubElement(url_element, 'priority').text = '0.5'

        tree = ET.ElementTree(urlset)
        ET.indent(tree, space="   ")
        return ET.tostring(urlset, encoding='utf-8', method='xml').decode()

    def get_sitemap(self):
        domain = 'https://holylogos.org'
        published_sermons = [ s for s in self.sermons if s.status != 'in development']
        return self.generate_sitemap(published_sermons, domain)
    
    def get_latest_sermons(self, count:int = 2) -> List[Sermon]:
        sermons = [s for s in self.sermons if s.status == 'published']
        sermons.sort(key=lambda x: x.published_date, reverse=True)
        return sermons[:count]
