from langchain_community.embeddings import HuggingFaceBgeEmbeddings
import os
import torch

class Model:
    def __init__(self):
        model_name = "BAAI/bge-large-en-v1.5"
        encode_kwargs = {'normalize_embeddings': True} # set True to compute cosine similarity
        model_name = "BAAI/bge-large-en-v1.5"
        encode_kwargs = {'normalize_embeddings': True} # set True to compute cosine similarity

        use_gpu =  os.environ.get("USE_GPU") 

        print(f"use gpu: {use_gpu}")

        if use_gpu == 'True' and torch.cuda.is_available():
            print(f"cuda available: {torch.cuda.is_available()}")
            model_kwargs = {'device': 'cuda'}
        else:
            model_kwargs = {'device': 'cpu'}
            print("use CPU for embedding")

        self.model = HuggingFaceBgeEmbeddings(
            model_name=model_name,
            model_kwargs=model_kwargs,
            encode_kwargs=encode_kwargs,
            query_instruction="Represent this sentence for searching relevant passages:"
        )
        self.model.query_instruction = "Represent this sentence for searching relevant passages:"
    
    def embed(self,input):
        return self.model.embed_query(input)  


model = Model()


def get_model():
    return model