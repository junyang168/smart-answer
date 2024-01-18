from smart_answer_core.base_tool import base_tool
from smart_answer_core.tool_example import tool_example

## Loading Environment Variables
from dotenv import load_dotenv
import psycopg2

from langchain.embeddings import OpenAIEmbeddings
from langchain.vectorstores.pgvector import PGVector
from langchain.vectorstores.pgvector import DistanceStrategy
import json
from smart_answer_core.logger import logger
import numpy as np
import time
from datetime import datetime
from datetime import timezone 
from operator import itemgetter
import smart_answer_core.util as util

#from tools.configMax import ConfigMaxTool
from pgvector.psycopg2 import register_vector


class KB_DocTool(base_tool):
    name = "VMWare Knowledge Base"
    description = """This is the default tool to understand any VMWare product related issues and questions other tools can't handle. 
      Do not use this tool if other tools can answer the question. Use this tool if other tool returns 'Unable to get data'
      The input to this tool should be a comma separated list of string of length two, representing VMware product release and the topics of the question.
      """
    
    def __init__(self, connection_string = None) -> None:        
        super().__init__()
        if  connection_string:
            self.connection_string = connection_string
        else:
            self.connection_string =  os.environ.get("CONNECTION_STRING") 

    def get_few_shots(self):
        return [
            tool_example("How to configure vGPU in ESXi?",'ESXi, configure vGPU' )
        ]
            

    def _get_context(self, docs):        
        return {
                "content": '\n'.join( [ f'Document {i+1}:\n{d["content"]}' for i, d in enumerate( docs[:3] ) ]),
                "reference": [ { "title": d["metadata"].get("title"), "link":d["metadata"]["url"]}  for d in docs  ]
            }

    def _filter_by_product(self, doc, products ):
        if len(products) == 0:
            return doc
        docProds = doc.metadata.get("product")
        if docProds:
            docProds = docProds.split(',')
            for dp in docProds:
                dp = dp.lower().replace('vmware','').strip()
                for p in products:
                    if dp.find(p) >= 0:
                        return doc
        return None
    
    def __hasConfigMax(self, rel_docs):
        for d in rel_docs:
            if d["metadata"].get('source') == 'ConfigMax' and d['score'] >= 0.85:
                return True
        return False
                
    def retrieve(self, args :str, question : str):
        
        logger.info( self.name + " " + question)

        params = args.split(',')
        
        relevant_docs = self.get_relevant_docs(question)

#        if self.__hasConfigMax(relevant_docs):
#             cfgTool = ConfigMaxTool()
#             return cfgTool._run(args, question )

        context = self._get_context(relevant_docs)

        top_doc = self._rereank(question,context)
        if top_doc is not None:
            context['content'] = relevant_docs[top_doc].get('content')


#        if len( response ) > 0 and respCfgMax and respCfgMax.get('maxScore') > response[0].get('score'):
#            return respCfgMax        
        return context
    
    def _rereank(self, question, context):        
        result = util.ask_llm(self._get_reranking_prompt_template(),question=question, context=context)
        if not result:
            return None            
        ranks = [ l for l in  result.split('\n') if len(l.strip()) > 0 ]
        print(ranks)
        if len(ranks) == 0:
            return None
        doc_title = ranks[0].split(',')[0].split(':')
        top_doc_idx =  doc_title[1].strip()
        if not top_doc_idx.isdigit():
            return None
        return int(top_doc_idx) - 1


    def search_similar_content(self, query):
        emb = util.calculate_embedding([query])[0]
        conn = psycopg2.connect(self.connection_string)
        register_vector(conn)
        cur = conn.cursor()
        sql = """
            select ic.id , ic."content" as content , ic.metadata, (1 - e.score) as score
            from (
                select emb.content_id, min(emb.score) score
                from (
                    select ce.content_id, ce."content", ce.embedding <=> %s score  
                    from content_embedding ce
            	    order by ce.embedding <=> %s
                    limit 30
                ) emb
	            group by emb.content_id
            	order by min(emb.score)
                limit 5
            ) e join ingestion_content ic on ic.id = e.content_id	
            """
        cur.execute(sql, (emb,emb) )
        ds = cur.fetchall()
        cur.close()
        conn.close()            
        return ds


    def get_relevant_docs(self, question):
        out = self.search_similar_content(question)
        response = []
        
        for r in out:            
            id = r[0]
            content = r[1]
            metadata = r[2]
            score = r[3]
            last_updated = None
            ts_str =  metadata.get("lastmod")
            if ts_str:
                if ts_str.find('.') > 0:
                    now = datetime.now(timezone.utc)
                    last_updated = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S.%f%z")
                else:
                    now = datetime.now()
                    last_updated = datetime.strptime(ts_str, "%Y-%m-%d")


            if last_updated:
                delta = now - last_updated                
                time_penalty = delta.days / 365 * 0.001 if delta.days > 365 else 0
            else:
                time_penalty = 0.01
            score = score - time_penalty
            response.append( {"content":content,"metadata":metadata, "score": score }  )        
        response = sorted(response, key = itemgetter("score"), reverse=True )
        if len(response) > 0 and response[0]["score"] >= 0.7:
            for i in reversed( range(len(response)) ):
                if response[i]["score"] < 0.7:
                    response.pop(i)
        return response

    def _get_reranking_prompt_template(self):
        return  """ 
A list of documents is shown below. Each document has a number next to it followed by the document. A question is also provided.
Respond with the numbers of the documents you should consult to answer the question, in order of relevance, as well
as the relevance score. The relevance score is a number from 1–10 based on how relevant you think the document is to the question.
Do not include any documents that are not relevant to the question.
Response format should be a list of Document Number and Relevance Score. 

            Example format:
            Document 1:
            <Content of the document>
            Document 2:
            <Content of the document>
            Document 3:
            <summary of document 10>
            Question: <question>
            Answer:
            Doc: 2, Relevance: 7
            Doc: 3, Relevance: 4
            Doc: 1, Relevance: 3
            Let's try this now:
            {context}
            Question: {question}
            Answer:

            """
    


