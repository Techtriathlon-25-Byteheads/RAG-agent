import os
from langchain_community.vectorstores import Chroma
from langchain_community.document_loaders import PyPDFLoader
from langchain_openai import OpenAIEmbeddings

def load_and_create_vectorstore(pdf_path, persist_dir, collection_name, openai_api_key):
    loader = PyPDFLoader(pdf_path)
    documents = loader.load()
    
    embeddings = OpenAIEmbeddings(openai_api_key=openai_api_key)

    vectorstore = Chroma.from_documents(
        documents=documents,
        embedding=embeddings,
        persist_directory=persist_dir,
        collection_name=collection_name
    )

    return vectorstore