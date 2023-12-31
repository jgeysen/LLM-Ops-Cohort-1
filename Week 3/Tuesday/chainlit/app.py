from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.document_loaders import PyMuPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.vectorstores import Chroma
from langchain.chains import RetrievalQAWithSourcesChain
from langchain.chat_models import ChatOpenAI
from langchain.prompts.chat import (
    ChatPromptTemplate,
    SystemMessagePromptTemplate,
    HumanMessagePromptTemplate,
)
import requests
import arxiv
import chainlit as cl

from typing import Any, List, Mapping, Optional
from langchain.callbacks.manager import CallbackManagerForLLMRun
from langchain.llms.base import LLM

import json
import time

API_HOST = "fastapi"
API_PORT = 80

@cl.langchain_factory(use_async=False)
async def init():
    arxiv_query = None

    # Wait for the user to ask an Arxiv question
    while arxiv_query == None:
        arxiv_query = await cl.AskUserMessage(
            content="Please enter a topic to begin!", timeout=15
        ).send()

    # Obtain the top 3 results from Arxiv for the query
    search = arxiv.Search(
        query=arxiv_query["content"],
        max_results=3,
        sort_by=arxiv.SortCriterion.Relevance,
    )

    await cl.Message(content="Downloading and chunking articles...").send()
    # download each of the pdfs
    pdf_data = []
    for result in search.results():
        loader = PyMuPDFLoader(result.pdf_url)
        loaded_pdf = loader.load()

        for document in loaded_pdf:
            document.metadata["source"] = result.entry_id
            document.metadata["file_path"] = result.pdf_url
            document.metadata["title"] = result.title
            pdf_data.append(document)

    # Create a Chroma vector store
    embeddings = OpenAIEmbeddings(
        disallowed_special=(),
    )
    
    # If operation takes too long, make_async allows to run in a thread
    # docsearch = await cl.make_async(Chroma.from_documents)(pdf_data, embeddings) 
    docsearch = Chroma.from_documents(pdf_data, embeddings)

    # Create a chain that uses the Chroma vector store
    chain = RetrievalQAWithSourcesChain.from_chain_type(
        CustomLLM(),
        chain_type="stuff",
        retriever=docsearch.as_retriever(),
        return_source_documents=True,
    )

    # Let the user know that the system is ready
    await cl.Message(
        content=f"We found a few papers about `{arxiv_query['content']}` you can now ask questions!"
    ).send()

    return chain


@cl.langchain_postprocess
async def process_response(res):
    answer = res["answer"]
    source_elements_dict = {}
    source_elements = []
    for idx, source in enumerate(res["source_documents"]):
        title = source.metadata["title"]

        if title not in source_elements_dict:
            source_elements_dict[title] = {
                "page_number": [source.metadata["page"]],
                "url": source.metadata["file_path"],
            }

        else:
            source_elements_dict[title]["page_number"].append(source.metadata["page"])

        # sort the page numbers
        source_elements_dict[title]["page_number"].sort()

    for title, source in source_elements_dict.items():
        # create a string for the page numbers
        page_numbers = ", ".join([str(x) for x in source["page_number"]])
        text_for_source = f"Page Number(s): {page_numbers}\nURL: {source['url']}"
        source_elements.append(
            cl.Text(name=title, content=text_for_source, display="inline")
        )

    await cl.Message(content=answer, elements=source_elements).send()


class CustomLLM(LLM):
    max_new_tokens: int = 256
    top_p: float = 0.9
    temperature: float = 0.1

    @property
    def _llm_type(self) -> str:
        return "CustomLLM"

    def _call(
        self,
        prompt: str,
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
    ) -> str:
        if stop is not None:
            raise ValueError("stop kwargs are not permitted.")

        task_id = generate_text(prompt)
        while True:
            status = get_task_status(task_id)
            if "Task Pending" not in status:
                return status
            time.sleep(2)

    @property
    def _identifying_params(self) -> Mapping[str, Any]:
        """Get the identifying parameters."""
        return {
            "max_new_tokens" : self.max_new_tokens,
            "top_p" : self.top_p,
            "temperature" : self.temperature
        }


def generate_text(prompt):
    headers = {"Content-type": "application/json"}
    data = {"prompt": prompt}
    json_data = json.dumps(data)
    response = requests.post("http://fastapi:80/generateText/", json_data, headers)
    result = response.json()
    return result["task_id"]

def get_task_status(task_id):
    response = requests.get(f"http://fastapi:80/task/{task_id}")
    status = response.json()
    return status