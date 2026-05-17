# Shared adapter — keep in sync with belleq-master/app/embeddings/

from app.embeddings.base import EmbeddingAdapter, EmbeddingError
from app.embeddings.factory import get_embedding_adapter

__all__ = ["EmbeddingAdapter", "EmbeddingError", "get_embedding_adapter"]
