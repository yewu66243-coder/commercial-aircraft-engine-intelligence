"""Context compression utilities for GPT Researcher.

This module provides classes for compressing and retrieving relevant
context from documents using embeddings and similarity filtering.

The compression pipeline:
1. Splits documents into chunks
2. Filters chunks by embedding similarity to the query
3. Returns the most relevant chunks as context

Classes:
    VectorstoreCompressor: Retrieves context from a vector store.
    ContextCompressor: Compresses raw documents using embedding similarity.
    WrittenContentCompressor: Compresses previously written content sections.
"""

import asyncio
import os
import re
from typing import Optional

from langchain_classic.retrievers import ContextualCompressionRetriever
from langchain_classic.retrievers.document_compressors import (
    DocumentCompressorPipeline,
    EmbeddingsFilter,
)
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from ..memory.embeddings import OPENAI_EMBEDDING_MODEL
from ..prompts import PromptFamily
from ..utils.costs import estimate_embedding_cost
from ..vector_store import VectorStoreWrapper
from .retriever import SearchAPIRetriever, SectionRetriever


WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-./+]*")
CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def _query_terms(query: str) -> list[str]:
    query = query or ""
    terms: set[str] = set()
    lowered = query.lower()
    for word in WORD_RE.findall(lowered):
        word = word.strip("._-/+")
        if len(word) >= 2:
            terms.add(word)
    for block in CJK_RE.findall(query):
        if len(block) <= 12:
            terms.add(block)
        for size in (2, 3, 4):
            if len(block) >= size:
                for index in range(len(block) - size + 1):
                    terms.add(block[index : index + size])
    return sorted(terms, key=lambda item: (-len(item), item))


def _keyword_score(text: str, terms: list[str]) -> float:
    lowered = text.lower()
    score = 0.0
    for term in terms:
        count = lowered.count(term.lower())
        if count:
            score += min(count, 5) * max(len(term), 2)
    return score


class VectorstoreCompressor:
    """Retrieves and compresses context from a vector store.

    Uses similarity search on an existing vector store to find
    relevant documents for a given query.

    Attributes:
        vector_store: The vector store wrapper to search.
        max_results: Maximum number of results to return.
        filter: Optional filter for vector store queries.
    """

    def __init__(
        self,
        vector_store: VectorStoreWrapper,
        max_results: int = 7,
        filter: Optional[dict] = None,
        prompt_family: type[PromptFamily] | PromptFamily = PromptFamily,
        **kwargs,
    ):
        """Initialize the VectorstoreCompressor.

        Args:
            vector_store: The vector store to search.
            max_results: Maximum number of results to return.
            filter: Optional filter dictionary for queries.
            prompt_family: Prompt family for formatting output.
            **kwargs: Additional keyword arguments.
        """
        self.vector_store = vector_store
        self.max_results = max_results
        self.filter = filter
        self.kwargs = kwargs
        self.prompt_family = prompt_family

    async def async_get_context(self, query: str, max_results: int = 5) -> str:
        """Get relevant context from the vector store.

        Args:
            query: The search query.
            max_results: Maximum number of results to return.

        Returns:
            Formatted string of relevant document content.
        """
        results = await self.vector_store.asimilarity_search(query=query, k=max_results, filter=self.filter)
        return self.prompt_family.pretty_print_docs(results)


class ContextCompressor:
    """Compresses raw documents to extract relevant context.

    Uses embedding similarity to filter document chunks and return
    only the most relevant content for a given query.

    Attributes:
        documents: List of documents to compress.
        embeddings: Embedding model for similarity calculation.
        max_results: Maximum number of results to return.
        similarity_threshold: Minimum similarity score for inclusion.
    """

    def __init__(
        self,
        documents,
        embeddings,
        max_results: int = 5,
        prompt_family: type[PromptFamily] | PromptFamily = PromptFamily,
        **kwargs,
    ):
        """Initialize the ContextCompressor.

        Args:
            documents: List of documents to compress.
            embeddings: Embedding model instance.
            max_results: Maximum number of results to return.
            prompt_family: Prompt family for formatting output.
            **kwargs: Additional keyword arguments.
        """
        self.max_results = max_results
        self.documents = documents
        self.kwargs = kwargs
        self.embeddings = embeddings
        self.similarity_threshold = os.environ.get("SIMILARITY_THRESHOLD", 0.35)
        self.prompt_family = prompt_family

    def __get_contextual_retriever(self):
        """Build the contextual compression retriever pipeline.

        Returns:
            A ContextualCompressionRetriever configured with text splitting
            and embedding-based filtering.
        """
        splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
        relevance_filter = EmbeddingsFilter(embeddings=self.embeddings,
                                            similarity_threshold=self.similarity_threshold)
        pipeline_compressor = DocumentCompressorPipeline(
            transformers=[splitter, relevance_filter]
        )
        base_retriever = SearchAPIRetriever(
            pages=self.documents
        )
        contextual_retriever = ContextualCompressionRetriever(
            base_compressor=pipeline_compressor, base_retriever=base_retriever
        )
        return contextual_retriever

    def __get_keyword_context(self, query: str, max_results: int = 5) -> str:
        terms = _query_terms(query)
        if not terms:
            terms = _query_terms(" ".join(str(doc.get("url", "")) for doc in self.documents))

        splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
        ranked_docs: list[tuple[float, int, Document]] = []
        for doc_index, doc in enumerate(self.documents):
            content = str(doc.get("raw_content", ""))
            if not content.strip():
                continue
            source = doc.get("url") or doc.get("source") or ""
            title = doc.get("title") or source
            chunks = splitter.split_text(content)
            for chunk_index, chunk in enumerate(chunks):
                metadata = {
                    **doc,
                    "url": source,
                    "source": source,
                    "title": title,
                    "chunk_index": chunk_index,
                }
                haystack = f"{source}\n{chunk}"
                score = _keyword_score(haystack, terms)
                if score > 0:
                    ranked_docs.append((score, doc_index, Document(page_content=chunk, metadata=metadata)))

        if not ranked_docs:
            fallback_docs = [
                Document(
                    page_content=str(doc.get("raw_content", ""))[:1200],
                    metadata={
                        **doc,
                        "source": doc.get("source") or doc.get("url"),
                        "title": doc.get("title") or doc.get("url"),
                    },
                )
                for doc in self.documents[:max_results]
                if str(doc.get("raw_content", "")).strip()
            ]
            return self.prompt_family.pretty_print_docs(fallback_docs, max_results)

        ranked_docs.sort(key=lambda item: (-item[0], item[1]))
        selected = [item[2] for item in ranked_docs[:max_results]]
        return self.prompt_family.pretty_print_docs(selected, max_results)

    async def async_get_context(self, query: str, max_results: int = 5, cost_callback=None) -> str:
        """Get relevant context from documents asynchronously.

        Optimization: Skip expensive compression pipeline for small document sets.
        When documents are already concise, directly use them without embedding-based filtering.

        Args:
            query: The search query.
            max_results: Maximum number of results to return.
            cost_callback: Optional callback for tracking embedding costs.

        Returns:
            Formatted string of relevant document content.
        """
        # Optimization: Calculate total content size
        total_chars = sum(len(str(doc.get('raw_content', ''))) for doc in self.documents)
        chunk_threshold = int(os.environ.get("COMPRESSION_THRESHOLD", "8000"))

        # If total content is small, skip expensive compression and return directly
        if total_chars < chunk_threshold and len(self.documents) <= max_results:
            print(
                f"[Embedding][SKIP] 文档内容较短，跳过正文压缩："
                f"输入文档 {len(self.documents)} 篇，正文字符 {total_chars}，阈值 {chunk_threshold}。"
            )
            # Fast path: no compression needed
            direct_docs = [
                Document(
                    page_content=doc.get('raw_content', ''),
                    metadata={
                        **doc,
                        "source": doc.get("source") or doc.get("url"),
                        "title": doc.get("title") or doc.get("url"),
                    }
                )
                for doc in self.documents[:max_results]
            ]
            return self.prompt_family.pretty_print_docs(direct_docs, max_results)

        # Standard path: use compression for large content
        print(
            f"[Embedding][RUN] 正在进行正文压缩："
            f"输入文档 {len(self.documents)} 篇，正文字符 {total_chars}，相似度阈值 {self.similarity_threshold}。"
        )
        compressed_docs = self.__get_contextual_retriever()
        if cost_callback:
            cost_callback(estimate_embedding_cost(model=OPENAI_EMBEDDING_MODEL, docs=self.documents))
        try:
            relevant_docs = await asyncio.to_thread(compressed_docs.invoke, query, **self.kwargs)
            print(
                f"[Embedding][OK] 正文压缩完成："
                f"输入文档 {len(self.documents)} 篇，筛出片段 {len(relevant_docs)} 段。"
            )
            return self.prompt_family.pretty_print_docs(relevant_docs, max_results)
        except Exception as exc:
            print(f"[Embedding][FALLBACK] Embedding 失败，已切换关键词保底：{exc}")
            return self.__get_keyword_context(query, max_results)


class WrittenContentCompressor:
    """Compresses previously written content sections.

    Specialized compressor for finding relevant sections from
    previously written report content, preserving section titles
    and structure.

    Attributes:
        documents: List of written content sections.
        embeddings: Embedding model for similarity calculation.
        similarity_threshold: Minimum similarity score for inclusion.
    """

    def __init__(self, documents, embeddings, similarity_threshold: float, **kwargs):
        """Initialize the WrittenContentCompressor.

        Args:
            documents: List of written content sections.
            embeddings: Embedding model instance.
            similarity_threshold: Minimum similarity score for inclusion.
            **kwargs: Additional keyword arguments.
        """
        self.documents = documents
        self.kwargs = kwargs
        self.embeddings = embeddings
        self.similarity_threshold = similarity_threshold

    def __get_contextual_retriever(self):
        """Build the contextual compression retriever for sections.

        Returns:
            A ContextualCompressionRetriever configured for section retrieval.
        """
        splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
        relevance_filter = EmbeddingsFilter(embeddings=self.embeddings,
                                            similarity_threshold=self.similarity_threshold)
        pipeline_compressor = DocumentCompressorPipeline(
            transformers=[splitter, relevance_filter]
        )
        base_retriever = SectionRetriever(
            sections=self.documents
        )
        contextual_retriever = ContextualCompressionRetriever(
            base_compressor=pipeline_compressor, base_retriever=base_retriever
        )
        return contextual_retriever

    def __pretty_docs_list(self, docs, top_n: int) -> list[str]:
        """Format documents as a list of title/content strings.

        Args:
            docs: List of documents to format.
            top_n: Maximum number of documents to include.

        Returns:
            List of formatted document strings.
        """
        return [f"Title: {d.metadata.get('section_title')}\nContent: {d.page_content}\n" for i, d in enumerate(docs) if i < top_n]

    async def async_get_context(self, query: str, max_results: int = 5, cost_callback=None) -> list[str]:
        """Get relevant written content sections asynchronously.

        Args:
            query: The search query.
            max_results: Maximum number of results to return.
            cost_callback: Optional callback for tracking embedding costs.

        Returns:
            List of formatted section strings.
        """
        compressed_docs = self.__get_contextual_retriever()
        if cost_callback:
            cost_callback(estimate_embedding_cost(model=OPENAI_EMBEDDING_MODEL, docs=self.documents))
        relevant_docs = await asyncio.to_thread(compressed_docs.invoke, query, **self.kwargs)
        return self.__pretty_docs_list(relevant_docs, max_results)
