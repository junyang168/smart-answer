
import pickle
from dotenv import load_dotenv
load_dotenv()
import os
import torch
import numpy as np
import itertools 
from typing import cast, List, Union, Tuple, Optional, Dict
import timeit
from safetensors.torch import save_file
import json
from safetensors import safe_open

class HybridScore:
    def __init__(self, id :str , dense_score:float,bm25_score:float,colbert_score:float) -> None:
        self.id = id
        self.dense_score = dense_score
        self.bm25_score = bm25_score
        self.colbert_score = colbert_score
        self.hybrid_score = 0.6*colbert_score + 0.4*dense_score + 0.2 * bm25_score 

    def __str__(self) -> str:
        return self.__repr__() 

    def __repr__(self) -> str:
        return f"Result - id: {self.id} score:{self.hybrid_score} dense:{self.dense_score} bm25:{self.bm25_score} colbert:{self.colbert_score}\n"




class VectorStore:    
    ids_file_name = 'smart_answer_id.json'
    tensor_file_name = 'smart_answer.tensor'
    bm25_encodings_old = []
    ids = []
    idmap = {}

    def __init__(self, dense_vector_dimension = 1024, load_data = True) -> None:
        self.dense_embeddings = torch.Tensor(0,dense_vector_dimension)
        self.embedings = {}
        self.base_dir = os.getenv('base_dir')
        if load_data:
            self.load_embeddings()




        
    def initialize(self):
        self.embedings = {}
        self.dense_bm25_vecs = []
        self.tokens = {}


    def save(self, passage_embeddings:dict):
        for i in range(len(passage_embeddings['dense_vecs'])):
            id = passage_embeddings['ids'][i]
            dense_vecs = torch.from_numpy(passage_embeddings['dense_vecs'][i])    
            bm25_vecs = [ (t,w) for t, w in passage_embeddings['lexical_weights'][i].items()]               
            self.dense_bm25_vecs.append( ( id, dense_vecs , bm25_vecs ) )

            colbert_vecs = torch.from_numpy(passage_embeddings['colbert_vecs'][i])
            colbert_vecs  = colbert_vecs.to(torch.float16)
            self.embedings[id] = colbert_vecs 
            

    def persist(self):
        tokens = {}
        bm25_position_X = []
        bm25_position_Y = []
        bm25_value = []
        for i in range(len(self.dense_bm25_vecs)) :
            bm25_vecs = self.dense_bm25_vecs[i][2]
            for t, w in bm25_vecs:
                pos = tokens.get(t)
                if not pos:
                    pos =  len(tokens)
                    tokens[t] = pos
                bm25_position_X.append(i)
                bm25_position_Y.append(pos)
                bm25_value.append(w)                                             
        bm25_index_t = torch.tensor([bm25_position_X,bm25_position_Y ])
        bm25_value_t = torch.tensor(bm25_value)

        id_tokens = {
            'ids':  [ d[0] for d in self.dense_bm25_vecs ],
            'tokens' : tokens
        }


        file_path = os.path.join(self.base_dir, 'vector_store', self.ids_file_name)
        with open(file_path,'w') as f:
            json.dump(id_tokens, f)

        dense_t = torch.stack( [ d[1] for d in self.dense_bm25_vecs ]  ,dim=0)

        self.embedings["dense"] =  dense_t           
        self.embedings["bm25_index"] = bm25_index_t
        self.embedings["bm25_value"] = bm25_value_t        

        save_file(self.embedings, self.tensor_file_name)

    def load_embeddings(self):
        file_path = os.path.join(self.base_dir, 'vector_store', self.ids_file_name)
        with open(file_path,'r') as f:
            id_tokens = json.load(f)
            self.ids = id_tokens['ids']
            self.tokens = id_tokens['tokens']

        file_path = os.path.join(self.base_dir, 'vector_store', self.tensor_file_name)
        with safe_open(file_path, framework="pt", device=0) as f:
            self.dense_embeddings = f.get_tensor('dense').cuda()
            bm25_index = f.get_tensor('bm25_index')
            bm25_value = f.get_tensor('bm25_value')

        sz = (len(self.ids), len(self.tokens))
        self.bm25_encodings = torch.sparse_coo_tensor(bm25_index, bm25_value).float().cuda()
        pass

                    

        
#    def compute_lexical_matching_score(self, lexical_weights_1: Dict, lexical_weights_2: Dict):
#        scores = 0
#        for token, weight in lexical_weights_1.items():
#            if token in lexical_weights_2:
#                scores += weight * lexical_weights_2[token]
#        return scores

    def get_bm25_score(self, q_weights:Dict):


        q_w = np.zeros((self.bm25_encodings.shape[1],1),dtype=np.float32)
        for t, w in q_weights.items():
            p = self.tokens[t]
            if p:
                q_w[p,0] = w

        q_t = torch.from_numpy(q_w).cuda()
        bm25_scores = torch.sparse.mm(self.bm25_encodings, q_t)

        return bm25_scores[:,0].cpu().numpy()
    

    def calculate_colbert_score(self,q_reps, p_reps ):
        token_scores = torch.einsum('in,jn->ij', q_reps, p_reps)
        scores, _ = token_scores.max(-1)
        scores = torch.sum(scores) / q_reps.size(0)
        return scores.item()


    def get_colbert_score(self, q_reps, p_vecs ):
        q = torch.from_numpy(q_reps).to(torch.float16).cuda() 
        calcFunc = lambda p: self.calculate_colbert_score(q, p['emb'])
        calcAll = np.vectorize(calcFunc )
        colbert_scores = calcAll(p_vecs)
        return zip( p_vecs, colbert_scores )

        

    def map_to_id(self, idx ):
        return self.ids[idx]

    def retrieve(self, query_embeddings, topN = 1000): 
    # pass 1: tense + bm25
    
        t_0 = timeit.default_timer()

        # dense score
        q_embs = torch.from_numpy(np.array([query_embeddings['dense_vecs'][0]])).cuda()    
        dense_score = torch.matmul(self.dense_embeddings,  q_embs.transpose(0,1) )

        # sparse score
        bm25_score = self.get_bm25_score( query_embeddings['lexical_weights'][0] )

 
        pass1_score = dense_score.squeeze(dim=-1) + torch.from_numpy(bm25_score).cuda()/2.0
        m = torch.sort(pass1_score, descending=True )

        topN_index = m[1][:topN].cpu().numpy()

        t_2 = timeit.default_timer()
        elapsed_time = round((t_2 - t_0) , 3)
        print(f"pass 1: {elapsed_time} s")

    #pass 2
        # get ids of top N 
        t_3 = timeit.default_timer()
        colbert_id_embs = self.load_colbert(topN_index)

        # colbert score
        q_embs =  query_embeddings['colbert_vecs'][0]   
        colbert_id_score = self.get_colbert_score( q_embs, colbert_id_embs)

        hybrid_scores = [
            HybridScore( id=self.ids[r[0]['idx']], colbert_score=r[1], dense_score=dense_score[r[0]['idx']].item(), bm25_score= bm25_score[r[0]['idx']])
            for r in colbert_id_score ]
        

        hybrid_scores.sort(key=lambda r: r.hybrid_score, reverse=True)

        t_4 = timeit.default_timer()
        elapsed_time = round((t_4 - t_3) , 3)
        print(f"pass 2 : {elapsed_time} s")

        return hybrid_scores
    

    def load_colbert(self, topN_index : np.ndarray):
        t_0 = timeit.default_timer()

        colbert_embeddings = []
        file_path = os.path.join(self.base_dir, 'vector_store', self.tensor_file_name)
        with safe_open(file_path, framework="pt", device=0) as f:
            for idx in topN_index:
                id = self.ids[idx]
                col_vecs = f.get_tensor(id)
                colbert_embeddings.append( {'idx':idx, 'emb':col_vecs})

        t_1 = timeit.default_timer()
        elapsed_time = round((t_1 - t_0) , 3)
        print(f"load colbert: {elapsed_time} s")

        return np.array(colbert_embeddings) 

if __name__ == '__main__':
    vs = VectorStore()
    pass