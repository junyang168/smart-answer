import psycopg
import json
import markdownify as mf

from embed_content_extractor import embed_content_extractor

class kb_extractor(embed_content_extractor):

   def get_source(self):
      return "KB2"
  

   def add_secion(self, content, section_name):
     sec = content.get(section_name)
     if sec:
        return f"##{section_name}\n{sec}" 
     else:
        return ''
   


   def get_content(self, meta : dict,  content:dict)->list[str]: 
         chunks = []   
         chunk = ""
         common_content = f"#{meta.get('title')}"
         products = meta.get("product_versions")
         if products:
            rel_products = products.get("relatedProducts")
            if rel_products:
               common_content += f" for {' '.join(rel_products)} "

            releases = products.get("relatedVersions")
            if releases:
               common_content += ' '.join(releases)
         common_content += '\n'

         chunk = ''
         chunk += self.add_secion(content,'Purpose')
         chunk += self.add_secion(content,'Symptoms')
#         if chunk:
         chunk = common_content + chunk
         chunks.append(chunk)

         chunk = ''
         chunk += self.add_secion(content,'Cause')
         chunk += self.add_secion(content,'Detail')
         chunk += self.add_secion(content,'Solution')
         chunk += self.add_secion(content,'Walkaround')
         chunk += self.add_secion(content,'Resolution')
         if chunk:
            md_chunks = [common_content + c for c in self.split_markdown(chunk) ]
#            chunks.extend(md_chunks)
            
         return chunks

   def get_metadata(self, meta:dict, content:dict) -> dict:
      return {
         "language": meta.get('Language'),
         "last_updated": meta.get('lastModifiedDate')
      }

            






     

