# Define your item pipelines here
#
# Don't forget to add your pipeline to the ITEM_PIPELINES setting
# See: https://docs.scrapy.org/en/latest/topics/item-pipeline.html


# useful for handling different item types with a single interface
from itemadapter import ItemAdapter
import markdownify as mf
import json


class MarkdownPipeline:
        
    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        content_raw = adapter.get('content_raw')
        content = {}
        if content_raw:
            for sec in content_raw:
                for sec_name, sec_value in sec.items():
                    content[sec_name] = mf.markdownify(sec_value)
            try:
                adapter['content'] = json.dumps(content)
                adapter['content_raw'] = json.dumps(content_raw)
                adapter['meta'] = json.dumps( adapter['meta'])
            except Exception as err:
                print(err)
        else:
            adapter['content'] = ''
            adapter['content_raw'] = ''
            adapter['meta'] = '{}'


        return item
            
