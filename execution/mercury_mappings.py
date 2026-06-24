# Mercury Spend Mappings
# ======================
# Card -> person attribution and merchant categorization for the
# "AI & Software Spend" tracker (fetch_mercury.py). Finance-only: this does NOT
# use the activity CORE_TEAM gate, because some tracked cardholders aren't on the
# activity roster — the finance team decides which cards count via the map below.

# Each Mercury card is a dedicated "SW & Subscription" card. The account holder
# (Areeba / James) is just the owner; the card's LAST 4 identifies the person.
CARD_LAST4_TO_PERSON = {
    '8094': 'Prameeth Kotian',
    '3371': 'Clarissa',
    '5585': 'Victor Cheung',
    '0532': 'Roman Naidenko',
    '2201': 'David Andres',
    '8428': 'Cherry Aznar',
    '1918': 'Rafael Casabianca',
    '9483': 'JP Casabianca',
    '7475': 'Dana Hetté',
    '7360': 'Muhammad Farhan',
    '1831': 'Areeba Akhlaque',
    '2971': 'Saifullah Khan',
    '3014': 'Saymond Montoya',
    '2701': 'Alexander Pavelko',
    '4518': 'David Andres',     # "David - Claude Card" (his 2nd card)
}

# Cards explicitly excluded by the finance team.
SKIP_LAST4 = {
    '0964',   # "Rafio" — project/product, not a person
    '7470',   # "Lucah's" — not tracked
}


def person_for_card(last4):
    """Return the tracked person for a card's last4, or None to skip."""
    if not last4:
        return None
    last4 = str(last4)[-4:]
    if last4 in SKIP_LAST4:
        return None
    return CARD_LAST4_TO_PERSON.get(last4)


# Merchant normalization: matched as a substring (case-insensitive) against
# counterpartyName + bankDescription. First match wins.
MERCHANT_ALIASES = [
    ('anthropic', 'Claude (Anthropic)'),
    ('claude', 'Claude (Anthropic)'),
    ('openai', 'OpenAI'),
    ('chatgpt', 'OpenAI'),
    ('cursor', 'Cursor'),
    ('perplexity', 'Perplexity'),
    ('midjourney', 'Midjourney'),
    ('elevenlabs', 'ElevenLabs'),
    ('replicate', 'Replicate'),
    ('huggingface', 'Hugging Face'),
    ('github', 'GitHub'),
    ('vercel', 'Vercel'),
    ('figma', 'Figma'),
    ('depot', 'Depot'),
    ('craftmypdf', 'CraftMyPDF'),
    ('privateinternet', 'Private Internet Access'),
    ('notion', 'Notion'),
    ('linear', 'Linear'),
    ('supabase', 'Supabase'),
    ('railway', 'Railway'),
    ('render', 'Render'),
    ('netlify', 'Netlify'),
    ('cloudflare', 'Cloudflare'),
    ('google', 'Google Cloud / Workspace'),
    ('aws', 'AWS'),
    ('amazon web', 'AWS'),
]

# Merchants we treat as "AI tools" (used to optionally narrow the email view).
AI_MERCHANTS = {
    'Claude (Anthropic)', 'OpenAI', 'Cursor', 'Perplexity', 'Midjourney',
    'ElevenLabs', 'Replicate', 'Hugging Face',
}


def normalize_merchant(counterparty, bank_desc=''):
    """Friendly product name from Mercury's counterparty + bank description."""
    text = f"{counterparty or ''} {bank_desc or ''}".lower()
    for key, name in MERCHANT_ALIASES:
        if key in text:
            return name
    return (counterparty or bank_desc or 'Unknown').strip().title()


def classify(merchant_name, amount):
    """Best-effort (Type, Tier) for a charge. Tiers are approximate (real charges
    carry tax/proration), so callers should present them with a '≈' qualifier.

    Type  : 'Subscription' | 'Usage'
    Tier  : human label or '' (e.g. 'Pro', 'Max 5x', 'API/Usage')
    """
    a = abs(amount or 0)
    if merchant_name == 'Claude (Anthropic)':
        if 15 <= a <= 30:
            return ('Subscription', 'Pro')
        if 80 <= a <= 135:
            return ('Subscription', 'Max 5x')
        if 180 <= a <= 235:
            return ('Subscription', 'Max 20x')
        return ('Usage', 'API/Usage')   # odd amounts ($5, $45, etc.) = credits/overage
    if merchant_name == 'OpenAI':
        if 18 <= a <= 30:
            return ('Subscription', 'Plus')
        if 180 <= a <= 220:
            return ('Subscription', 'Pro')
        return ('Usage', 'API/Usage')   # small repeated top-ups = ChatGPT credits
    # Generic: small fixed amounts look like subscriptions, larger/odd = usage.
    if a <= 60:
        return ('Subscription', '')
    return ('Usage', '')


def is_ai_merchant(merchant_name):
    return merchant_name in AI_MERCHANTS
