"""
extractor.py — LLM-powered pain point extraction chain (Day 2).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Literal

from langchain.output_parsers import PydanticOutputParser
from langchain.prompts import ChatPromptTemplate, SystemMessagePromptTemplate, HumanMessagePromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, field_validator

from config import config
from reddit_fetcher import Thread

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

CategoryType = Literal[
    "Pricing", "UX/Design", "Performance", "Missing Feature",
    "Customer Support", "Onboarding", "Integration",
    "Reliability", "Documentation", "Other",
]


class PainPoint(BaseModel):
    pain_text: str = Field(
        description="Clean 1-2 sentence description of the pain point."
    )
    severity: int = Field(
        ge=1, le=10,
        description="1=minor annoyance, 10=product-destroying frustration."
    )
    category: CategoryType = Field(
        description="One of the predefined category strings."
    )
    verbatim_quote: str = Field(
        description="Near-exact quote from Reddit content, 10-80 words."
    )
    source_url: str = Field(
        description="Reddit post URL the quote came from."
    )
    mention_count: int = Field(
        ge=1,
        description="How many distinct comments/posts express this same pain."
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence this is a real pain point vs. noise."
    )

    @field_validator("verbatim_quote")
    @classmethod
    def quote_length(cls, v: str) -> str:
        words = v.split()
        if len(words) < 10 or len(words) > 80:
            raise ValueError(f"verbatim_quote must be 10-80 words, got {len(words)}")
        return v


class ExtractionResult(BaseModel):
    niche: str
    pain_points: list[PainPoint] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompt constants
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """\
You are a senior product researcher specialising in discovering real user pain \
points from community discussions. Your job is to read Reddit thread content and \
extract structured, evidence-backed pain points that product teams can act on.

STRICT RULES — violating any of these makes your output worthless:
1. ONLY extract pain points that are DIRECTLY EVIDENCED by the provided text.
   Do NOT infer, generalise, or invent problems not explicitly stated.
   Reason: LLMs hallucinate plausible-sounding but fabricated issues without this guard.

2. verbatim_quote MUST be a near-exact excerpt from the provided content.
   Minor typo fixes are allowed; do NOT paraphrase or summarise.
   Reason: Product teams need real user language, not sanitised versions.

3. Rate severity (1-10) using ALL THREE signals:
   a) Emotional intensity of the language (caps, profanity, exclamations)
   b) Upvote score of the comment (higher score = more people agree)
   c) How many distinct users express the same sentiment
   Reason: A single furious comment may be an outlier; many calm complaints are systemic.

4. mention_count must reflect how many DIFFERENT users (not comments from the same user)
   express the same pain. Default to 1 if you only see it once.
   Reason: Prevents inflating frequency from a single vocal user.

5. confidence should be LOW (< 0.5) for:
   - Complaints about a specific bug that seems fixed
   - Single mentions with low score and no replies
   - Sarcasm or clearly joking comments
   Reason: Filters noise before the pain points reach the product roadmap.

CATEGORY DEFINITIONS (pick the best fit):
- Pricing: cost, plans, paywalls, value-for-money complaints
- UX/Design: confusing UI, poor workflows, visual clutter
- Performance: slow, crashes, timeouts, lag
- Missing Feature: explicitly requested feature that does not exist
- Customer Support: bad support, no response, unhelpful agents
- Onboarding: hard to set up, poor documentation for getting started
- Integration: broken or missing integrations with other tools
- Reliability: data loss, sync issues, unexpected downtime
- Documentation: missing or wrong docs, unclear API references
- Other: anything that doesn't fit above categories

OUTPUT FORMAT — respond with ONLY valid JSON matching this schema:
{{
  "pain_points": [
    {{
      "pain_text": "Users cannot export their data to CSV without upgrading to the $99/month Business plan, forcing small teams to pay for enterprise features they do not need.",
      "severity": 8,
      "category": "Pricing",
      "verbatim_quote": "why the hell is CSV export behind a paywall?? we're a 3-person startup we can't afford $99/mo just to get our own data out",
      "source_url": "https://www.reddit.com/r/CRM/comments/abc123",
      "mention_count": 4,
      "confidence": 0.92
    }}
  ]
}}

NEGATIVE EXAMPLE — do NOT extract something like this:
{{
  "pain_text": "Users find the software difficult to use.",
  "verbatim_quote": "it's just not great overall",
  "severity": 5,
  "confidence": 0.8
}}
Why it's wrong: pain_text is vague and unactionable; the quote is too short and \
non-specific; confidence is too high for such weak evidence.

Return ONLY the JSON object. No markdown fences, no explanation text.
"""

# The user prompt is intentionally simple — all intelligence lives in the system
# prompt. This separation makes it easy to swap niche/content without touching
# the carefully-engineered system instructions.
EXTRACTION_USER_PROMPT = """\
Product niche: {niche}

Analyse the Reddit threads below and extract all distinct pain points according \
to your instructions. Every pain point must be evidenced by the content below.

{thread_content}
"""


# ---------------------------------------------------------------------------
# Formatting helper
# ---------------------------------------------------------------------------

def _format_threads(threads: list[Thread]) -> str:
    """
    Convert a list of Thread objects into a structured plain-text block that
    the LLM can parse unambiguously.

    Design choice: indentation (4 spaces) to signal comment hierarchy rather
    than JSON/XML — LLMs read indented text more reliably in chat contexts, and
    it keeps the token count lower than JSON wrapping.
    """
    lines: list[str] = []
    for t in threads:
        lines.append(f"POST | score={t.score} | url={t.url}")
        lines.append(f"Title: {t.title}")
        if t.selftext:
            lines.append(f"Body: {t.selftext[:600]}")  # cap body to keep tokens bounded
        lines.append("Comments:")
        for c in t.comments:
            indent = "    " * (c.depth + 1)
            lines.append(f"{indent}[score={c.score}] {c.body[:400]}")
        lines.append("")  # blank line between threads
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deduplication helper
# ---------------------------------------------------------------------------

def _keyword_overlap(a: str, b: str) -> float:
    """
    Simple token-overlap similarity between two strings.
    Returns Jaccard similarity on lowercased, stop-word-stripped word sets.

    Why not embeddings?  Embeddings need a model call.  For same-batch
    deduplication this O(n²) keyword check is fast enough and avoids
    an extra API dependency on Day 2.  Day 3+ can upgrade to cosine similarity.
    """
    STOPWORDS = {
        "the","a","an","is","it","in","on","of","to","and","or","but",
        "for","with","this","that","are","was","were","be","been","have",
        "has","they","their","we","our","you","your","i","my","me","us",
    }
    def tokenise(s: str) -> set[str]:
        tokens = re.findall(r"[a-z]+", s.lower())
        return {t for t in tokens if t not in STOPWORDS and len(t) > 2}

    set_a, set_b = tokenise(a), tokenise(b)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)  # Jaccard index


def _merge_pain_points(
    all_points: list[PainPoint],
    similarity_threshold: float = 0.85,
) -> list[PainPoint]:
    """
    Merge semantically duplicate pain points extracted across batches.

    Algorithm:
    - For each new pain point, check if it is similar (Jaccard > threshold)
      to any pain point already in the merged list.
    - If similar: keep the one with higher confidence; sum mention_counts;
      take the max severity.
    - If not similar: add it as a new entry.

    This is O(n²) but n is at most ~50 pain points per run — negligible cost.
    """
    merged: list[PainPoint] = []
    for candidate in all_points:
        matched = False
        for existing in merged:
            sim = _keyword_overlap(candidate.pain_text, existing.pain_text)
            if sim >= similarity_threshold:
                # In-place merge: accumulate evidence into the better entry.
                existing.mention_count += candidate.mention_count
                existing.severity = max(existing.severity, candidate.severity)
                existing.confidence = max(existing.confidence, candidate.confidence)
                matched = True
                break
        if not matched:
            merged.append(candidate.model_copy())
    return merged


# ---------------------------------------------------------------------------
# Main extractor class
# ---------------------------------------------------------------------------

class PainPointExtractor:
    """
    Orchestrates batched LLM extraction of pain points from Reddit Thread data.

    Batching rationale:
    - GPT-4o's context window is large (128k tokens) but larger prompts
      degrade instruction-following quality.
    - 5 threads × ~30 comments × ~100 words ≈ 15,000 tokens per batch,
      well within the sweet spot for reliable structured output.
    """

    BATCH_SIZE = 5

    def __init__(self) -> None:
        # temperature=0 is critical for structured extraction — we want
        # deterministic, conservative output, not creative paraphrasing.
        self._llm = ChatOpenAI(
            model="gpt-4o",
            temperature=0,
            api_key=config.OPENAI_API_KEY,
        )

        # PydanticOutputParser wraps ExtractionResult so LangChain can
        # validate and parse the raw JSON string from the model into a
        # typed Python object automatically.
        self._parser = PydanticOutputParser(pydantic_object=ExtractionResult)

        self._prompt = ChatPromptTemplate.from_messages([
            SystemMessagePromptTemplate.from_template(EXTRACTION_SYSTEM_PROMPT),
            HumanMessagePromptTemplate.from_template(EXTRACTION_USER_PROMPT),
        ])

    # ------------------------------------------------------------------
    def _extract_batch(self, threads: list[Thread], niche: str) -> list[PainPoint]:
        """Run extraction on a single batch of threads."""
        thread_content = _format_threads(threads)
        messages = self._prompt.format_messages(
            niche=niche,
            thread_content=thread_content,
        )

        logger.info("Calling GPT-4o for batch of %d threads…", len(threads))
        response = self._llm.invoke(messages)
        raw: str = response.content

        # The system prompt instructs the model to return raw JSON, but
        # occasionally it wraps in ```json ... ``` fences despite instructions.
        # Strip markdown fences defensively before parsing.
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw)

        try:
            data = json.loads(raw)
            # Inject niche so ExtractionResult validates fully.
            data["niche"] = niche
            result = ExtractionResult.model_validate(data)
            logger.info("Batch produced %d pain points.", len(result.pain_points))
            return result.pain_points
        except Exception as exc:
            logger.error("Failed to parse LLM output: %s\nRaw output:\n%s", exc, raw[:500])
            return []

    # ------------------------------------------------------------------
    def extract(self, threads: list[Thread], niche: str) -> ExtractionResult:
        """
        Main entry point.  Batches threads, extracts, deduplicates, returns.

        Parameters
        ----------
        threads : list of Thread objects (with comments populated)
        niche   : product niche string, passed into the prompt for context

        Returns
        -------
        ExtractionResult with deduplicated, merged pain_points sorted by severity desc.
        """
        all_points: list[PainPoint] = []

        for i in range(0, len(threads), self.BATCH_SIZE):
            batch = threads[i : i + self.BATCH_SIZE]
            logger.info(
                "Processing batch %d/%d (%d threads)…",
                i // self.BATCH_SIZE + 1,
                -(-len(threads) // self.BATCH_SIZE),  # ceiling division
                len(batch),
            )
            batch_points = self._extract_batch(batch, niche)
            all_points.extend(batch_points)

        merged = _merge_pain_points(all_points)
        # Sort by severity descending so highest-impact issues appear first.
        merged.sort(key=lambda p: (p.severity, p.confidence), reverse=True)

        logger.info(
            "Extraction complete: %d raw → %d merged pain points.",
            len(all_points),
            len(merged),
        )
        return ExtractionResult(niche=niche, pain_points=merged)


# ---------------------------------------------------------------------------
# __main__ — smoke test with hardcoded fake CRM comments
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    from reddit_fetcher import Comment, Thread

    # Realistic-sounding synthetic Reddit data for offline testing.
    # These mimic the structure fetch_comments() would produce.
    sample_threads = [
        Thread(
            post_id="t1abc",
            title="Why is HubSpot's free tier so crippled now?",
            selftext=(
                "Used to love HubSpot for small teams but the recent changes to "
                "the free tier have made it basically unusable. Reporting is now "
                "locked behind the Starter plan at $50/month. For a 4-person "
                "startup that's insane."
            ),
            url="https://www.reddit.com/r/CRM/comments/t1abc",
            subreddit="CRM",
            score=312,
            num_comments=47,
            comments=[
                Comment(
                    comment_id="c001",
                    body=(
                        "Same experience here. We moved off HubSpot after they locked "
                        "email sequences behind Professional. $800/month for email "
                        "automation is absolutely ridiculous for a startup. "
                        "Salesforce at least gives you a real product for enterprise money."
                    ),
                    score=189,
                    depth=0,
                    post_title="Why is HubSpot's free tier so crippled now?",
                    post_url="https://www.reddit.com/r/CRM/comments/t1abc",
                ),
                Comment(
                    comment_id="c002",
                    body=(
                        "The pricing jumps are insane. Free → Starter is $50, but "
                        "Starter → Professional is $890/month. There's no middle ground. "
                        "Small businesses get completely screwed."
                    ),
                    score=143,
                    depth=0,
                    post_title="Why is HubSpot's free tier so crippled now?",
                    post_url="https://www.reddit.com/r/CRM/comments/t1abc",
                ),
                Comment(
                    comment_id="c003",
                    body="100% agree. The cliff between tiers is a dealbreaker for us.",
                    score=67,
                    depth=1,
                    post_title="Why is HubSpot's free tier so crippled now?",
                    post_url="https://www.reddit.com/r/CRM/comments/t1abc",
                ),
            ],
        ),
        Thread(
            post_id="t2def",
            title="Salesforce data sync with Slack is completely broken",
            selftext="Has anyone else had Salesforce → Slack notifications just stop working randomly? Third time this month.",
            url="https://www.reddit.com/r/salesforce/comments/t2def",
            subreddit="salesforce",
            score=88,
            num_comments=23,
            comments=[
                Comment(
                    comment_id="c004",
                    body=(
                        "Yes! The Slack integration breaks every time Salesforce does a "
                        "patch release. I've opened 3 support tickets this quarter and "
                        "none of them were resolved — just closed as 'cannot reproduce'. "
                        "Absolutely maddening when your whole sales team relies on those alerts."
                    ),
                    score=74,
                    depth=0,
                    post_title="Salesforce data sync with Slack is completely broken",
                    post_url="https://www.reddit.com/r/salesforce/comments/t2def",
                ),
                Comment(
                    comment_id="c005",
                    body=(
                        "Support told me to reinstall the Slack app. That's their answer "
                        "to everything. We pay $150 per seat and can't get a real engineer "
                        "to look at the issue."
                    ),
                    score=55,
                    depth=1,
                    post_title="Salesforce data sync with Slack is completely broken",
                    post_url="https://www.reddit.com/r/salesforce/comments/t2def",
                ),
            ],
        ),
        Thread(
            post_id="t3ghi",
            title="Pipedrive onboarding is a nightmare — anyone else?",
            selftext=(
                "Signed up for Pipedrive trial. The UI is clean but figuring out "
                "how to import contacts from a CSV with custom fields took me 3 hours "
                "and 2 YouTube videos. The in-app guide just… doesn't cover it."
            ),
            url="https://www.reddit.com/r/smallbusiness/comments/t3ghi",
            subreddit="smallbusiness",
            score=201,
            num_comments=34,
            comments=[
                Comment(
                    comment_id="c006",
                    body=(
                        "Their onboarding docs are so outdated. Half the screenshots in "
                        "the help center show a UI from 2 versions ago. I spent an entire "
                        "afternoon mapping fields that don't exist anymore."
                    ),
                    score=112,
                    depth=0,
                    post_title="Pipedrive onboarding is a nightmare — anyone else?",
                    post_url="https://www.reddit.com/r/smallbusiness/comments/t3ghi",
                ),
                Comment(
                    comment_id="c007",
                    body=(
                        "Switched from Pipedrive to Attio after 6 months. The CSV import "
                        "issues you describe never got fixed despite being reported on their "
                        "community forum since 2021. No acknowledgement, no ETA."
                    ),
                    score=88,
                    depth=0,
                    post_title="Pipedrive onboarding is a nightmare — anyone else?",
                    post_url="https://www.reddit.com/r/smallbusiness/comments/t3ghi",
                ),
            ],
        ),
    ]

    print("\n" + "="*60)
    print("  Pain Point Extractor — Smoke Test")
    print("  Niche: CRM software")
    print("="*60 + "\n")

    extractor = PainPointExtractor()
    result = extractor.extract(sample_threads, niche="CRM software")

    print(json.dumps(result.model_dump(), indent=2))
    print(f"\n✅  Extracted {len(result.pain_points)} pain points.\n")
