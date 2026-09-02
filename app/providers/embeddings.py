"""Embedding provider — a LangChain `Embeddings` implementation backed by Gemini.

Written as a thin adapter rather than using the off-the-shelf wrapper for two
reasons that matter here:

  * `output_dimensionality=768` must be pinned. gemini-embedding-001 returns
    3072 dimensions by default, which would not fit the vector(768) column.
  * Documents and questions must be embedded with *different* task types
    (RETRIEVAL_DOCUMENT vs RETRIEVAL_QUERY). Gemini measurably improves recall
    when told which side of the search it is embedding.

Swapping to Bedrock/Titan or OpenAI later means writing one more class with
these two methods — nothing in core/ changes.
"""
from __future__ import annotations

from google import genai
from google.genai import types
from langchain_core.embeddings import Embeddings

from app.config import settings

_client = genai.Client(api_key=settings.gemini_api_key)


class GeminiEmbeddings(Embeddings):
    """LangChain embeddings over Gemini, pinned to the catalogue's vector size."""

    def __init__(self, model: str | None = None, dimensions: int | None = None) -> None:
        self.model = model or settings.embedding_model
        self.dimensions = dimensions or settings.embedding_dim

    def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        response = _client.models.embed_content(
            model=self.model,
            contents=texts,
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=self.dimensions,
            ),
        )
        return [list(e.values) for e in response.embeddings]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "RETRIEVAL_QUERY")[0]


embeddings = GeminiEmbeddings()
