"""Search strategy: every EDGAR query the scraper runs is built from the lists below.

How queries are built
- Each term is an exact phrase (wrapped in quotes). Two terms in one query = BOTH must
  appear in the same document (EDGAR full-text search uses implicit AND, case-insensitive).
- Most families are a grid: every CONTRACT term x every AI term. Pairing matters because a
  contract word alone ("master services agreement") matches >10,000 non-AI documents, and an
  AI word alone ("artificial intelligence") matches most modern 10-Ks.
- A term that already contains quotes is used as-is (lets one term hold two phrases).

Known false-positive traps (observed in probes, kept but expect noise)
- "GPU" also means "ground power unit" (aircraft purchase agreements).
- "LLM" is also a law degree (Master of Laws) in executive bios.
- Bare "inference" is avoided (legal boilerplate: "no inference should be drawn").

Removed after run 1 (2026-11-10): these were the ONLY AI-term match for many Priority-1 rows,
and the matching companies showed they meant something else.
- "Blackwell" (800 rows: surname/place, UK mortgage trusts)  -> "NVIDIA Blackwell"
- bare "AI" (428 rows: abbreviations, e.g. car rental, vaccine contracts) -> dropped; "AI model" etc. remain
- "training data" (131 rows: airline pilot training)  -> "AI training data"
- "fine-tuning" (61 rows: generic English)            -> "model fine-tuning"
- "B200" (28 rows: part numbers)                      -> dropped; "NVIDIA", "GB200" cover it
- "TPU" (15 rows: thermoplastic polyurethane)         -> "Tensor Processing Unit"
Removed after precision sample (2026-11-10), STACK family:
- "ASIC" (494 rows: Australian Securities & Investments Commission) -> "AI ASIC", "custom ASIC"
- "HBM" (103 rows: ambiguous abbreviation)              -> dropped; "high bandwidth memory" stays
- "semiconductor" + "intellectual property" (918 rows: generic) -> "semiconductor IP"

Edit these lists to change the search. Query IDs are assigned in list order, so IDs are
only comparable between runs of the same keywords.py version (run_info.json stores its hash).
"""

# --- AI context terms (paired with contract terms) -------------------------------------
AI_CORE = [
    "artificial intelligence", "machine learning", "deep learning", "neural network",
    "generative AI", "generative artificial intelligence", "large language model", "LLM",
    "foundation model", "AI model", "machine learning model", "natural language processing",
    "computer vision", "conversational AI", "chatbot", "AI agent", "agentic",
    "AI platform", "AI services", "AI solution",
]
AI_COMPUTE = [
    "GPU", "graphics processing unit", "NVIDIA", "H100", "H200", "GB200", "GB300",
    "NVIDIA Blackwell", "Tensor Processing Unit", "accelerated computing", "AI compute", "compute capacity",
    "GPU capacity", "GPU cluster", "AI infrastructure", "AI data center", "AI cloud",
    "high performance computing", "AI inference", "model training",
]
AI_DATA = [
    "AI training data", "data labeling", "data annotation", "synthetic data", "model fine-tuning",
    "model weights", "retrieval-augmented generation", "vector database",
]
AI_ALL = AI_CORE + AI_COMPUTE + AI_DATA
AI_SHORT = [  # smaller set for broad deal terms, to keep query count sane
    "artificial intelligence", "machine learning", "generative AI", "large language model",
    "foundation model", "AI model", "deep learning", "neural network", "AI platform",
    "AI infrastructure", "GPU",
]

# --- Family MSA (top priority): Master Services Agreement and its synonyms --------------
# Service/infrastructure contracts under which AI models, compute or data are delivered.
MSA_TERMS = [
    "master services agreement", "master service agreement", "master agreement",
    "master subscription agreement", "master cloud services agreement", "cloud services agreement",
    "services agreement", "professional services agreement", "technology services agreement",
    "statement of work", "order form", "service order", "subscription services agreement",
    "software as a service", "enterprise agreement", "framework agreement", "customer agreement",
    "platform agreement", "outsourcing agreement", "hosting agreement", "colocation agreement",
    "lease agreement", "capacity agreement",
]
# Standalone phrases specific enough to search without pairing (mentor list + extensions).
MSA_PHRASES = [
    "AI services agreement", "artificial intelligence services agreement", "GPU services agreement",
    "compute services agreement", "cloud compute agreement", "GPU as a service",
    "AI cloud services", "AI infrastructure services", "GPU capacity agreement",
    "compute capacity agreement", "AI as a service", "inference services agreement",
    "model hosting", "data center services agreement",
]

# --- Family LICENSE: licensing / distribution of AI technology, models, data ------------
LICENSE_TERMS = [
    "license agreement", "licence agreement", "software license agreement",
    "technology license agreement", "data license agreement", "content license agreement",
    "license and services agreement", "sublicense agreement", "API",
    "application programming interface", "reseller agreement", "distribution agreement",
    "OEM agreement", "marketplace agreement", "partner agreement", "referral agreement",
    "royalty",
]
LICENSE_AI = AI_CORE + AI_DATA
LICENSE_PHRASES = [  # mentor list + extensions
    "AI license", "artificial intelligence license", "model license", "training data license",
    "foundation model license", "generative AI license", "enterprise AI license", "API license",
    "LLM license", "large language model license", "AI license agreement", "model license agreement",
    "data licensing agreement", "content licensing agreement", "open weights", "model weights license",
    "license to use the model", "AI training data",
]

# --- Family DEAL: M&A and partnership contracts (mentor: pair with an AI term) ----------
DEAL_TERMS = [
    "agreement and plan of merger", "merger agreement", "asset purchase agreement",
    "stock purchase agreement", "share purchase agreement", "purchase and sale agreement",
    "strategic investment agreement", "investment agreement", "commercial agreement",
    "technology license and collaboration agreement", "collaboration agreement",
    "joint development agreement", "co-development agreement", "development agreement",
    "joint venture agreement", "strategic partnership agreement", "strategic alliance agreement",
    "research agreement", "sponsored research agreement",
]

# --- Family PARTY: legal / brand names of AI developers, neoclouds, AI chip makers ------
# Contracts name parties by legal entity; searching names was the most precise method
# in prior team tests ("OpenAI OpCo", "Anthropic PBC").
PARTIES = [
    # model developers
    "OpenAI OpCo", "OpenAI Global", "OpenAI, L.L.C.", "OpenAI Group PBC", "OpenAI, Inc.",
    "Anthropic PBC", "Anthropic, PBC", "X.AI Corp", "X.AI LLC", "xAI", "Mistral AI", "Cohere Inc",
    "AI21 Labs", "Inflection AI", "Stability AI", "Perplexity AI", "Character Technologies",
    "Scale AI", "Hugging Face", "Together Computer", "Adept AI", "Aleph Alpha", "Databricks",
    "Midjourney", "ElevenLabs", "Runway AI", "Safe Superintelligence", "Thinking Machines Lab",
    # GPU cloud / AI data center operators ("neoclouds")
    "CoreWeave", "Lambda Labs", "Crusoe Energy", "Nebius", "Nscale", "Fluidstack", "Vultr",
    "Voltage Park", "TensorWave", "Applied Digital", "IREN Limited", "Iris Energy",
    "Core Scientific", "TeraWulf", "Cipher Mining", "Hut 8", "Bit Digital", "WhiteFiber",
    "Northern Data", "Sharon AI", "QumulusAI", "Galaxy Digital", "Stargate",
    # AI chip makers
    "Cerebras Systems", "Groq", "SambaNova", "Tenstorrent", "Graphcore", "Lightmatter",
    "Ayar Labs", "Etched",
    # hyperscaler AI products (product names appear in reseller/cloud contracts)
    "Azure OpenAI", "Amazon Bedrock", "Vertex AI", "ChatGPT", "GPT-4", "Microsoft Copilot",
]

# --- Family STACK: mentor's AI-stack / technical terms x common contract types -----------
STACK_TERMS = [
    "AI ASIC", "custom ASIC", "Tensor Processing Unit", "semiconductor IP", "silicon photonics", "chiplet",
    "wafer-scale", "high bandwidth memory", "AI accelerator", "AI chip", "custom silicon",
    "vector database", "synthetic data", "data labeling", "embeddings", "retrieval-augmented generation",
    "\"web scraping\" \"artificial intelligence\"", "foundation model", "transformer architecture",
    "open weights", "model weights", "diffusion model", "model fine-tuning", "autonomous agents",
    "agentic AI", "multi-agent", "function calling", "tool calling", "prompt engineering",
    "LLM observability", "AI guardrails", "inference optimization", "model quantization", "MLOps",
    "\"containerized\" \"artificial intelligence\"",
]
STACK_CONTRACTS = [
    "master services agreement", "services agreement", "license agreement", "supply agreement",
    "development agreement", "purchase agreement", "statement of work", "collaboration agreement",
]


# --- Narrowed queries: these 30 returned >=10,000 hits in run 3 (the API's cap), so their
# results were incomplete and they were skipped. Each now gets a third phrase, "in witness
# whereof": the signature-block wording found in almost every signed contract. This keeps
# only documents that ARE contracts (not 10-Ks or fund reports that mention both terms).
# Same query ID as before; every narrowed version has <6,000 hits (probed 2026-11-10).
CONTRACT_SIGNAL = '"in witness whereof"'
NARROWED = {
    '"master agreement" "NVIDIA"', '"services agreement" "artificial intelligence"',
    '"services agreement" "machine learning"', '"services agreement" "NVIDIA"',
    '"software as a service" "artificial intelligence"', '"software as a service" "machine learning"',
    '"lease agreement" "artificial intelligence"', '"lease agreement" "machine learning"',
    '"license agreement" "artificial intelligence"', '"license agreement" "machine learning"',
    '"API" "artificial intelligence"', '"API" "machine learning"',
    '"distribution agreement" "artificial intelligence"', '"royalty" "artificial intelligence"',
    '"royalty" "machine learning"', '"agreement and plan of merger" "artificial intelligence"',
    '"agreement and plan of merger" "machine learning"', '"merger agreement" "artificial intelligence"',
    '"merger agreement" "machine learning"', '"stock purchase agreement" "artificial intelligence"',
    '"xAI"', '"CoreWeave"', '"Nebius"', '"Applied Digital"', '"Core Scientific"', '"TeraWulf"',
    '"Cipher Mining"', '"Hut 8"', '"Bit Digital"', '"Galaxy Digital"',
}


def _q(term):
    return term if '"' in term else f'"{term}"'


def build_queries():
    """Return [(query_id, family, query_text)] in fixed order; exact duplicates dropped."""
    plan = [
        ("MSA", [(a, b) for a in MSA_TERMS for b in AI_ALL] + [(p,) for p in MSA_PHRASES]),
        ("LICENSE", [(a, b) for a in LICENSE_TERMS for b in LICENSE_AI] + [(p,) for p in LICENSE_PHRASES]),
        ("DEAL", [(a, b) for a in DEAL_TERMS for b in AI_SHORT]),
        ("PARTY", [(p,) for p in PARTIES]),
        ("STACK", [(a, b) for a in STACK_TERMS for b in STACK_CONTRACTS]),
    ]
    out, seen = [], set()
    for family, combos in plan:
        for n, combo in enumerate(combos, 1):
            text = " ".join(_q(t) for t in combo)
            if text in NARROWED:
                text += " " + CONTRACT_SIGNAL
            if text.lower() not in seen:
                seen.add(text.lower())
                out.append((f"{family}-{n:04d}", family, text))
    return out
