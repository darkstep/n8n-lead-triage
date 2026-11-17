"""Builds workflow/discard-lead-triage.json (the n8n workflow export).

python build.py              full workflow (Form, Claude, Google Sheets, Gmail draft)
python build.py --no-google  stops after the lead row, for testing the AI step alone
"""
import json
import sys
from pathlib import Path

NO_GOOGLE = "--no-google" in sys.argv
ROOT = Path(__file__).parent

CARD_OPTIONS = ["1", "2-4", "5-20", "21-100", "100+"]

SYSTEM_PROMPT = """You triage inbound leads for Discard (discard.bg), a Bulgarian company that sells NFC business cards with a digital profile.

Facts you may use (never invent others):
- Metal cards: Black, Silver, Rose Gold, Gold at 44.90 EUR each; White Metal 49.90 EUR.
- Wood cards: Dark Wood (walnut), Light Wood, Maple at 39.90 EUR each.
- Every card includes the NFC chip and a digital profile; the profile is 19.90 EUR per year after checkout. Logo printing or engraving is free.
- A digital profile without a card is 19.90 EUR per year. A card without a profile (chip opens the customer's own URL) costs the card price, no subscription.
- Orders of 5 or more cards are team orders: the team prepares a custom quote with a volume discount on the cards. Never quote a team price yourself.
- Custom card designs are always possible. Shipping takes 5-7 business days in the EU, 7-14 elsewhere.
- Order online at https://discard.bg or ask for a quote at contact@discard.bg.

Categories:
- team_order: a company or group wants 5 or more cards.
- single_order: one person wants 1-4 cards or asks prices for themselves.
- design_request: wants us to design the card or asks what a custom design looks like.
- support: an existing customer with a problem (order status, profile edit, card not scanning).
- partnership: reseller, agency, supplier, media or collaboration offer.
- spam: SEO/marketing pitches, link offers, gibberish, or anything unrelated to buying or using Discard.

Priority:
- hot: team_order, or a clear intent to buy now (dates, quantities, budget, logo ready).
- warm: genuine interest without urgency, design requests, partnership offers that fit.
- cold: vague questions, support, or low fit.

The reply draft:
- Write in the language the message itself is written in, never one guessed from the sender's name, company or country. Write as "we", never "I". In Bulgarian use the polite form and capitalise every form of it, mid-sentence too: Вие, Вас, Ви, Ваш, Вашия, Вашата, Вашето, Вашите ("Вашия сайт", "логото Ви").
- 60-140 words, plain text, no markdown, no emoji, no em dashes.
- Answer what they asked using only the facts above, then one clear next step.
- If they ask about anything the facts do not cover (wholesale chips, blank cards, reseller terms, integrations, exact team prices), do not say yes or no: say the team will reply with the details.
- Team orders: ask for quantity, material and whether they have a logo file, and say a quote follows within one business day.
- Sign off exactly as "Екипът на Discard" in Bulgarian or "The Discard team" in other languages. Never use a personal name.
- For spam, reply_draft is an empty string."""

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": ["team_order", "single_order", "design_request", "support", "partnership", "spam"],
        },
        "priority": {"type": "string", "enum": ["hot", "warm", "cold"]},
        "language": {"type": "string", "description": "ISO 639-1 code of the language the message text is written in, e.g. bg, en, de"},
        "estimated_cards": {"type": "number", "description": "Best estimate of how many cards they want, 0 if unknown"},
        "summary": {"type": "string", "description": "One sentence in English: who they are and what they want"},
        "reply_subject": {"type": "string", "description": "Email subject for the reply, in the lead's language"},
        "reply_draft": {"type": "string", "description": "The reply email body, following the rules"},
    },
    "required": ["category", "priority", "language", "estimated_cards", "summary", "reply_subject", "reply_draft"],
}

LEAD_TEXT = (
    "=Name: {{ $json.Name }}\n"
    "Email: {{ $json.Email }}\n"
    "Company: {{ $json.Company }}\n"
    "Cards wanted (form dropdown): {{ $json['How many cards?'] }}\n"
    "Message:\n{{ $json.Message }}"
)


def node(name, ntype, version, position, parameters, **extra):
    n = {
        "parameters": parameters,
        "id": name.lower().replace(" ", "-"),
        "name": name,
        "type": ntype,
        "typeVersion": version,
        "position": position,
    }
    n.update(extra)
    return n


def field(label, ftype=None, required=False, options=None, placeholder=None):
    f = {"fieldLabel": label}
    if ftype:
        f["fieldType"] = ftype
    if options:
        f["fieldOptions"] = {"values": [{"option": o} for o in options]}
    if placeholder:
        f["placeholder"] = placeholder
    if required:
        f["requiredField"] = True
    return f


def assignment(name, value, vtype="string"):
    return {"id": name, "name": name, "value": value, "type": vtype}


nodes = [
    node(
        "Lead form",
        "n8n-nodes-base.formTrigger",
        2.3,
        [0, 0],
        {
            "formTitle": "Discard NFC cards: ask for an offer",
            "formDescription": "Tell us what you need and we reply within one business day.",
            "formFields": {
                "values": [
                    field("Name", required=True),
                    field("Email", "email", required=True),
                    field("Company"),
                    field("How many cards?", "dropdown", required=True, options=CARD_OPTIONS),
                    field("Message", "textarea", required=True),
                ]
            },
            "options": {
                "path": "discard-lead",
                "appendAttribution": False,
                "respondWithOptions": {
                    "values": {"formSubmittedText": "Thank you. We received your request and will reply by email."}
                },
            },
        },
        webhookId="discard-lead-form",
    ),
    node(
        "Classify and draft reply",
        "@n8n/n8n-nodes-langchain.informationExtractor",
        1.2,
        [260, 0],
        {
            "text": LEAD_TEXT,
            "schemaType": "manual",
            "inputSchema": json.dumps(INPUT_SCHEMA, indent=2),
            "options": {"systemPromptTemplate": SYSTEM_PROMPT},
        },
    ),
    node(
        "Claude Haiku",
        "@n8n/n8n-nodes-langchain.lmChatAnthropic",
        1.6,
        [260, 220],
        {
            "model": {"__rl": True, "mode": "id", "value": "claude-haiku-5-5"},
            "options": {"maxTokensToSample": 2048, "temperature": 0},
        },
        credentials={"anthropicApi": {"id": "anthropicLeadTriage", "name": "Anthropic (lead triage)"}},
    ),
    node(
        "Lead row",
        "n8n-nodes-base.set",
        3.4,
        [520, 0],
        {
            "mode": "manual",
            "assignments": {
                "assignments": [
                    assignment("received_at", "={{ $now.setZone('Europe/Sofia').toFormat('yyyy-MM-dd HH:mm') }}"),
                    assignment("name", "={{ $('Lead form').item.json.Name }}"),
                    assignment("email", "={{ $('Lead form').item.json.Email }}"),
                    assignment("company", "={{ $('Lead form').item.json.Company }}"),
                    assignment("cards_form", "={{ $('Lead form').item.json['How many cards?'] }}"),
                    assignment("message", "={{ $('Lead form').item.json.Message }}"),
                    assignment("category", "={{ $json.output.category }}"),
                    assignment("priority", "={{ $json.output.priority }}"),
                    assignment("language", "={{ $json.output.language }}"),
                    assignment("estimated_cards", "={{ $json.output.estimated_cards }}", "number"),
                    assignment("summary", "={{ $json.output.summary }}"),
                    assignment("reply_subject", "={{ $json.output.reply_subject }}"),
                    assignment("reply_draft", "={{ $json.output.reply_draft }}"),
                ]
            },
            "options": {},
        },
    ),
]

connections = {
    "Lead form": {"main": [[{"node": "Classify and draft reply", "type": "main", "index": 0}]]},
    "Claude Haiku": {"ai_languageModel": [[{"node": "Classify and draft reply", "type": "ai_languageModel", "index": 0}]]},
    "Classify and draft reply": {"main": [[{"node": "Lead row", "type": "main", "index": 0}]]},
}

if not NO_GOOGLE:
    nodes += [
        node(
            "Spam?",
            "n8n-nodes-base.if",
            2.2,
            [780, 0],
            {
                "conditions": {
                    "options": {"caseSensitive": True, "typeValidation": "strict", "version": 2},
                    "conditions": [
                        {
                            "id": "is-spam",
                            "leftValue": "={{ $json.category }}",
                            "rightValue": "spam",
                            "operator": {"type": "string", "operation": "equals"},
                        }
                    ],
                    "combinator": "and",
                },
                "options": {},
            },
        ),
        node("Ignore spam", "n8n-nodes-base.noOp", 1, [1040, -160], {}),
        node(
            "Log lead in Sheet",
            "n8n-nodes-base.googleSheets",
            4.7,
            [1040, 60],
            {
                "operation": "append",
                "documentId": {"__rl": True, "mode": "id", "value": "SHEET_ID"},
                "sheetName": {"__rl": True, "mode": "name", "value": "Leads"},
                "columns": {
                    "mappingMode": "autoMapInputData",
                    "value": {},
                    "matchingColumns": [],
                    "schema": [],
                },
                "options": {},
            },
            credentials={"googleSheetsOAuth2Api": {"id": "googleSheetsLeadTriage", "name": "Google Sheets (lead triage)"}},
        ),
        node(
            "Draft reply in Gmail",
            "n8n-nodes-base.gmail",
            2.1,
            [1300, 60],
            {
                "resource": "draft",
                "subject": "={{ $('Lead row').item.json.reply_subject }}",
                "message": "={{ $('Lead row').item.json.reply_draft }}",
                "options": {"sendTo": "={{ $('Lead row').item.json.email }}"},
            },
            credentials={"gmailOAuth2": {"id": "gmailLeadTriage", "name": "Gmail (lead triage)"}},
        ),
    ]
    connections["Lead row"] = {"main": [[{"node": "Spam?", "type": "main", "index": 0}]]}
    connections["Spam?"] = {
        "main": [
            [{"node": "Ignore spam", "type": "main", "index": 0}],
            [{"node": "Log lead in Sheet", "type": "main", "index": 0}],
        ]
    }
    connections["Log lead in Sheet"] = {"main": [[{"node": "Draft reply in Gmail", "type": "main", "index": 0}]]}

workflow = {
    "id": "discardLeadTriage",
    "name": "Discard lead triage (Claude + Google Sheets + Gmail)",
    "nodes": nodes,
    "connections": connections,
    "settings": {"executionOrder": "v1", "timezone": "Europe/Sofia"},
    "active": False,
    "pinData": {},
}

out = ROOT / "workflow" / "discard-lead-triage.json"
out.write_text(json.dumps(workflow, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"wrote {out} ({len(nodes)} nodes, google={'off' if NO_GOOGLE else 'on'})")
