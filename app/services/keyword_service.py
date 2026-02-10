# app/services/keyword_service.py

import spacy
import numpy as np
from functools import lru_cache
from typing import List, Dict
from sklearn.feature_extraction.text import TfidfVectorizer
from sentence_transformers import SentenceTransformer, util
from app.utils.logger import logger


class KeywordService:

    # --------------------------------------------------------
    # Load NLP + Embedding models (cached)
    # --------------------------------------------------------
    @staticmethod
    @lru_cache()
    def _load_spacy():
        logger.info("Loading spaCy model for keyword extraction...")
        return spacy.load("en_core_web_sm")

    @staticmethod
    @lru_cache()
    def _load_sbert():
        logger.info("Loading Sentence-BERT (all-MiniLM-L6-v2)...")
        return SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

    # --------------------------------------------------------
    # Extract candidate phrases (noun chunks + nouns)
    # --------------------------------------------------------
    @classmethod
    def _extract_candidate_phrases(cls, text: str) -> List[str]:
        if not text.strip():
            return []

        nlp = cls._load_spacy()
        doc = nlp(text)

        candidates = []

        # 1. Noun phrases ("customer service", "payment issue")
        for chunk in doc.noun_chunks:
            phrase = chunk.text.strip().lower()
            if len(phrase.split()) <= 6:
                candidates.append(phrase)

        # 2. Single nouns / proper nouns
        for token in doc:
            if (
                token.pos_ in ("NOUN", "PROPN")
                and not token.is_stop
                and token.is_alpha
            ):
                candidates.append(token.text.lower())

        return list(set(candidates))  # remove duplicates

    # --------------------------------------------------------
    # Keyword ranking = TF-IDF + semantic similarity to text
    # --------------------------------------------------------
    @classmethod
    def extract_keywords(cls, text: str, top_k: int = 10) -> List[str]:
        if not text.strip():
            return []

        candidates = cls._extract_candidate_phrases(text)
        if not candidates:
            return []

        # Create TF-IDF scores
        tfidf = TfidfVectorizer().fit([text])
        tfidf_scores = tfidf.transform([text]).toarray()[0]
        tfidf_vocab = tfidf.get_feature_names_out()

        # Map TF-IDF score for each candidate phrase
        tfidf_dict = {}
        for phrase in candidates:
            phrase_words = phrase.split()
            score = 0.0
            for w in phrase_words:
                if w in tfidf_vocab:
                    idx = list(tfidf_vocab).index(w)
                    score += tfidf_scores[idx]
            tfidf_dict[phrase] = score

        # Embed candidates + full text using Sentence BERT
        sbert = cls._load_sbert()
        text_emb = sbert.encode(text, convert_to_tensor=True)
        cand_emb = sbert.encode(candidates, convert_to_tensor=True)

        # Semantic similarity scores
        sim_scores = util.cos_sim(text_emb, cand_emb)[0]

        # Final weighted ranking = TF-IDF + semantic relevance
        final_scores = {}
        for i, phrase in enumerate(candidates):
            final_scores[phrase] = float(sim_scores[i]) + tfidf_dict.get(phrase, 0.0)

        # Sort by score
        ranked = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)

        keywords = [phrase for phrase, score in ranked[:top_k]]

        return keywords

    # --------------------------------------------------------
    # Keyword extraction per speaker segment
    # --------------------------------------------------------
    @classmethod
    def extract_keywords_per_segment(cls, speaker_segments: List[Dict], top_k=5):
        enriched = []

        for seg in speaker_segments:
            text = seg.get("text", "")
            kw = cls.extract_keywords(text, top_k)
            enriched.append({**seg, "keywords": kw})

        return enriched