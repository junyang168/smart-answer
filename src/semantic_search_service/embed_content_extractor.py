from langchain_text_splitters import MarkdownHeaderTextSplitter

class embed_content_extractor:
    def get_source(self)->str:
        pass
    def get_content(self, meta:dict, content:dict) -> list[str]:
        pass
    def get_metadata(self, meta:dict, content:dict) -> dict:
        pass

    def split_markdown(self, markdown_document):
        if len(markdown_document) < 6000:
            return [markdown_document]
        else:      
            # MD splits
            headers_to_split_on = [
                ("#", "Header 1"),
                ("##", "Header 2"),
                ("###", "Header 3")
            ]
            markdown_splitter = MarkdownHeaderTextSplitter(
                headers_to_split_on=headers_to_split_on, strip_headers=False
            )
            md_header_splits = markdown_splitter.split_text(markdown_document)
            return [ d.page_content for d in md_header_splits]


    
    
