from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "pdf" / "M2_Wallet_Product_Features_Bilingual.pdf"

GREEN = colors.HexColor("#00B84A")
GREEN_DARK = colors.HexColor("#083D2A")
GREEN_SOFT = colors.HexColor("#EAF8EF")
INK = colors.HexColor("#17201C")
MUTED = colors.HexColor("#63706A")
LINE = colors.HexColor("#DDE6E1")
PANEL = colors.HexColor("#F5F8F6")
BLUE = colors.HexColor("#2875CA")
ORANGE = colors.HexColor("#D88400")
RED = colors.HexColor("#C93B3B")


pdfmetrics.registerFont(TTFont("M2CJK", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"))
pdfmetrics.registerFontFamily(
    "M2CJK",
    normal="M2CJK",
    bold="M2CJK",
    italic="M2CJK",
    boldItalic="M2CJK",
)


def styles_for(font: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "eyebrow": ParagraphStyle(
            "Eyebrow",
            parent=base["Normal"],
            fontName=font,
            fontSize=8,
            leading=11,
            textColor=GREEN,
            spaceAfter=4,
        ),
        "title": ParagraphStyle(
            "Title",
            parent=base["Title"],
            fontName=font,
            fontSize=24,
            leading=29,
            textColor=INK,
            alignment=TA_LEFT,
            spaceAfter=7,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle",
            parent=base["Normal"],
            fontName=font,
            fontSize=11,
            leading=16,
            textColor=MUTED,
            spaceAfter=12,
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=base["Heading2"],
            fontName=font,
            fontSize=14,
            leading=18,
            textColor=INK,
            spaceBefore=5,
            spaceAfter=7,
        ),
        "h3": ParagraphStyle(
            "H3",
            parent=base["Heading3"],
            fontName=font,
            fontSize=10.5,
            leading=14,
            textColor=INK,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName=font,
            fontSize=8.5,
            leading=13,
            textColor=INK,
            spaceAfter=6,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base["BodyText"],
            fontName=font,
            fontSize=7.2,
            leading=10.5,
            textColor=MUTED,
        ),
        "bullet": ParagraphStyle(
            "Bullet",
            parent=base["BodyText"],
            fontName=font,
            fontSize=8,
            leading=12,
            leftIndent=9,
            firstLineIndent=-7,
            textColor=INK,
            spaceAfter=3,
        ),
        "card_title": ParagraphStyle(
            "CardTitle",
            parent=base["Normal"],
            fontName=font,
            fontSize=9,
            leading=12,
            textColor=INK,
            spaceAfter=4,
        ),
        "card_body": ParagraphStyle(
            "CardBody",
            parent=base["Normal"],
            fontName=font,
            fontSize=7.2,
            leading=10.5,
            textColor=MUTED,
        ),
        "table_head": ParagraphStyle(
            "TableHead",
            parent=base["Normal"],
            fontName=font,
            fontSize=7.2,
            leading=9,
            textColor=colors.white,
        ),
        "table": ParagraphStyle(
            "Table",
            parent=base["Normal"],
            fontName=font,
            fontSize=7,
            leading=10,
            textColor=INK,
        ),
        "code": ParagraphStyle(
            "Code",
            parent=base["Code"],
            fontName="Courier",
            fontSize=6.6,
            leading=9.5,
            textColor=colors.HexColor("#DDF5E5"),
        ),
    }


EN = styles_for("Helvetica")
ZH = styles_for("M2CJK")


class SectionRule(Flowable):
    def __init__(self, width: float = 34 * mm):
        super().__init__()
        self.width = width
        self.height = 4

    def draw(self) -> None:
        self.canv.setStrokeColor(GREEN)
        self.canv.setLineWidth(3)
        self.canv.line(0, 1, self.width, 1)


def p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(text, style)


def bullets(items: list[str], s: dict[str, ParagraphStyle]) -> list[Paragraph]:
    return [p(f"• {item}", s["bullet"]) for item in items]


def feature_cards(cards: list[tuple[str, str]], s: dict[str, ParagraphStyle]) -> Table:
    cells = []
    for title, body in cards:
        cells.append([p(title, s["card_title"]), p(body, s["card_body"])])
    rows = []
    for index in range(0, len(cells), 2):
        left = cells[index]
        right = cells[index + 1] if index + 1 < len(cells) else [p("", s["body"])]
        rows.append([left, right])
    table = Table(rows, colWidths=[83 * mm, 83 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PANEL),
                ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.white),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    return table


def data_table(headers: list[str], rows: list[list[str]], s: dict[str, ParagraphStyle], widths=None) -> Table:
    content = [[p(value, s["table_head"]) for value in headers]]
    content.extend([[p(value, s["table"]) for value in row] for row in rows])
    table = Table(content, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), GREEN_DARK),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PANEL]),
                ("GRID", (0, 0), (-1, -1), 0.35, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def flow_table(items: list[tuple[str, str]], s: dict[str, ParagraphStyle]) -> Table:
    row = []
    for number, (title, body) in enumerate(items, 1):
        row.append(
            [
                p(f"<font color='#00B84A'>{number:02d}</font>", s["h3"]),
                p(title, s["card_title"]),
                p(body, s["card_body"]),
            ]
        )
    table = Table([row], colWidths=[166 * mm / len(row)] * len(row))
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PANEL),
                ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def page_title(story: list, eyebrow: str, title: str, subtitle: str, s: dict[str, ParagraphStyle]) -> None:
    story.extend(
        [
            p(eyebrow.upper(), s["eyebrow"]),
            p(title, s["title"]),
            SectionRule(),
            Spacer(1, 4 * mm),
            p(subtitle, s["subtitle"]),
        ]
    )


def finish_page(story: list) -> None:
    story.append(PageBreak())


def cover(story: list) -> None:
    story.extend(
        [
            Spacer(1, 25 * mm),
            p("M2", ParagraphStyle("CoverLogo", fontName="Helvetica-Bold", fontSize=25, leading=28, textColor=colors.white)),
            Spacer(1, 10 * mm),
            p(
                "M2 Wallet",
                ParagraphStyle("CoverTitle", fontName="Helvetica-Bold", fontSize=36, leading=41, textColor=colors.white),
            ),
            p(
                "Stablecoin Wallet and Payment Operations Platform",
                ParagraphStyle("CoverSub", fontName="Helvetica", fontSize=16, leading=22, textColor=colors.HexColor("#CFE7D8")),
            ),
            Spacer(1, 15 * mm),
            p(
                "PRODUCT FEATURES GUIDE  |  产品功能介绍",
                ParagraphStyle("CoverEyebrow", fontName="M2CJK", fontSize=9, leading=12, textColor=colors.HexColor("#7BE5A4")),
            ),
            Spacer(1, 10 * mm),
            Table(
                [[p("Payments", EN["card_title"]), p("Payouts", EN["card_title"]), p("Sweeping", EN["card_title"]), p("Risk Control", EN["card_title"])]],
                colWidths=[39 * mm] * 4,
                style=TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#124F37")),
                        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#31745A")),
                        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#31745A")),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("TOPPADDING", (0, 0), (-1, -1), 10),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                        ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
                    ]
                ),
            ),
            Spacer(1, 42 * mm),
            p(
                "Internal interactive demo - July 2026",
                ParagraphStyle("CoverDate", fontName="Helvetica", fontSize=9, leading=12, textColor=colors.HexColor("#A9CDB8")),
            ),
            p(
                "English edition followed by 中文版",
                ParagraphStyle("CoverDateZh", fontName="M2CJK", fontSize=9, leading=12, textColor=colors.HexColor("#A9CDB8")),
            ),
            PageBreak(),
        ]
    )


def english_pages(story: list) -> None:
    s = EN
    page_title(
        story,
        "01 / Product overview",
        "A control plane for stablecoin money movement",
        "M2 Wallet combines treasury wallets, merchant payments, payout approvals, automated sweeping, WaaS APIs, risk controls, and financial operations in one internal workspace.",
        s,
    )
    story.append(feature_cards([
        ("One operating system", "A shared interface for operations, finance, developers, and administrators. Each role sees the actions and evidence relevant to its responsibilities."),
        ("Stablecoin first", "The current demo focuses on USDT on TRON and USDC on Polygon, the two corridors required for the first internal release."),
        ("Human control where it matters", "Customer withdrawals remain pending until Finance or Admin approves them. Signing and broadcast begin only after policy checks pass."),
        ("Traceable by design", "Every payment, payout, sweep, callback, approval, and configuration change leaves a queryable operational record."),
    ], s))
    story.extend([Spacer(1, 5 * mm), p("Primary users", s["h2"])])
    story.append(data_table(
        ["Role", "Primary jobs", "Typical permissions"],
        [
            ["Operations", "Create payment orders, monitor transfers, run sweeps, retry callbacks", "Create and operate, no finance approval"],
            ["Finance", "Review customer withdrawals and confirm release of funds", "Approve or reject payouts, view audit evidence"],
            ["Developers", "Integrate merchant systems and verify lifecycle events", "Scoped API keys, status queries, callbacks"],
            ["Administrators", "Configure wallets, limits, allowlists, roles, and project settings", "Full configuration and security management"],
        ],
        s,
        [26 * mm, 75 * mm, 65 * mm],
    ))
    story.extend([Spacer(1, 5 * mm), p("Current demo boundary", s["h2"]), *bullets([
        "Interactive simulation: no real private key, mnemonic, or spendable signing material is stored in the application.",
        "A production boundary is already defined for external MPC/HSM signing, chain RPC broadcasting, and confirmed transaction monitoring.",
        "Hosted team demo data is isolated per browser; the local backend uses a persistent SQLite operational store.",
    ], s)])
    finish_page(story)

    page_title(story, "02 / Platform map", "Eight connected workspaces", "The product follows a Cregis-style navigation model while focusing first on the workflows needed by an internal payment company.", s)
    story.append(feature_cards([
        ("Wallets", "Multi-wallet asset view, USDT/USDC balances, processing and sweepable amounts, send, receive, addresses, and wallet transaction history."),
        ("Workflow", "Pending approval, pending signing, approved, signed, all items, initiated items, approval drawer, policy evidence, and operator timeline."),
        ("WaaS Projects", "API withdrawals, exception callbacks, callback history, developer settings, API Console, IP allowlist, callback policy, and notifications."),
        ("Payment Engine", "Stablecoin orders, hosted checkout, payment addresses, payment status, exception states, fee calculation, and settlement records."),
        ("Transactions", "Unified inbound, outbound, and internal-sweep history with asset, network, address, wallet, business type, hash, amount, and status."),
        ("Risk Control", "Payout pause, single and daily limits, address allowlist/blocklist, automation, policy evidence, and operational risk logs."),
        ("Management", "Demo acceptance, team plan, accounts, members, roles, team security, API keys, and audit logs."),
        ("App Marketplace", "Extension point for AML, TRON energy, notifications, finance reporting, and future cross-border settlement capabilities."),
    ], s))
    story.extend([Spacer(1, 5 * mm), p("System flow", s["h2"]), flow_table([
        ("Merchant", "Creates payment or payout request"),
        ("M2 Wallet", "Validates, records, and applies policy"),
        ("Finance / Chain", "Approves, signs, broadcasts, confirms"),
        ("Merchant", "Receives signed callback and reconciles"),
    ], s)])
    finish_page(story)

    page_title(story, "03 / Wallet and treasury", "Asset visibility and controlled wallet operations", "Wallet pages provide an operational treasury view rather than a simple consumer wallet balance.", s)
    story.append(feature_cards([
        ("Multi-wallet navigation", "Switch between main, regional settlement, merchant collection, and test wallets. Wallet type and key-share indicators remain visible."),
        ("Asset state", "For each asset, show available, processing, and sweepable balances. Expand by network for a chain-level breakdown."),
        ("Guided receive flow", "Choose asset and network, display a receiving address and QR-style representation, and show explicit network safety guidance."),
        ("Guided send flow", "Choose asset and network, enter recipient and amount, validate address format and risk policy, then create a finance approval request."),
        ("Address management", "Search by address or alias, display default payment/collection context, and support allowlist and blocklist controls."),
        ("Transaction detail", "Inspect business reference, direction, wallet, network, address, timestamp, transaction hash, and lifecycle status."),
    ], s))
    story.extend([Spacer(1, 5 * mm), p("Treasury data model", s["h2"])])
    story.append(data_table(
        ["Balance state", "Meaning", "Operational action"],
        [
            ["Available", "Confirmed balance available to the wallet", "Eligible for controlled payout"],
            ["Processing", "Payment order is open or transfer is not final", "Monitor confirmations and expiry"],
            ["Sweepable", "Confirmed funds remain on collection addresses", "Run automatic or manual sweep"],
            ["Collected", "Funds moved into the designated treasury wallet", "Reconcile internal transfer and hash"],
        ], s, [32 * mm, 78 * mm, 56 * mm]))
    finish_page(story)

    page_title(story, "04 / Stablecoin payment engine", "Accept USDT and USDC with a complete order lifecycle", "The payment engine gives a merchant one reference, one checkout, one payment address, and a durable status model from creation through settlement.", s)
    story.append(flow_table([
        ("Create", "Merchant submits order, customer, amount, asset, network, return URL, and metadata."),
        ("Present", "M2 Wallet generates an address, expiry time, and hosted checkout page."),
        ("Observe", "Chain listener matches a confirmed token transfer to the open order."),
        ("Book", "Fee and merchant net amount are posted to a balanced ledger."),
        ("Notify", "A signed callback reports the final business state."),
    ], s))
    story.extend([Spacer(1, 5 * mm), p("Payment states", s["h2"])])
    story.append(data_table(
        ["State", "Trigger", "Merchant meaning"],
        [
            ["PENDING", "No confirmed payment", "Checkout remains payable until expiry"],
            ["PARTIAL", "Paid below the 1% tolerance", "Customer can submit the remaining amount"],
            ["CONFIRMED", "Paid within tolerance", "Order is successful and ledger is posted"],
            ["OVERPAID", "Paid above tolerance", "Full receipt is booked and exception remains visible"],
            ["EXPIRED", "Payment window elapsed", "Order is closed and an expiry callback is created"],
        ], s, [30 * mm, 62 * mm, 74 * mm]))
    story.extend([Spacer(1, 5 * mm), p("Commercially important details", s["h2"]), *bullets([
        "merchant_order_id is an idempotency key; conflicting reuse with a different amount, asset, or network is rejected.",
        "Platform commission is calculated per order and posted separately from merchant payable funds.",
        "Public checkout exposes payment-safe fields only; callback URL and internal metadata remain private.",
        "Merchant systems can query by either the M2 Wallet ID or their own order reference.",
    ], s)])
    finish_page(story)

    page_title(story, "05 / Payouts and finance approval", "From customer withdrawal request to confirmed transfer", "Payouts are deliberately split between API initiation, finance authorization, signing, chain broadcast, confirmation, and merchant notification.", s)
    story.append(flow_table([
        ("Request", "Merchant submits customer, asset, amount, network, recipient, callback, and metadata."),
        ("Validate", "M2 checks project state, pair, address, blocklist/allowlist, limits, and idempotency."),
        ("Approve", "Finance or Admin reviews the request, policy evidence, and optional merchant validation."),
        ("Sign", "A separate signing boundary receives a transaction intent; the app never stores a private key."),
        ("Broadcast", "Signed payload is sent to TRON or Polygon and receives a transaction hash."),
        ("Confirm", "Final status, ledger entry, audit evidence, timeline, and signed callback are recorded."),
    ], s))
    story.extend([Spacer(1, 5 * mm), p("Finance workspace", s["h2"]), feature_cards([
        ("Pending approval", "Shows amount, asset, network, recipient, source system, customer reference, matching policies, and validation status."),
        ("Approval evidence", "Records reviewer identity, review time, external validation response, signing reference, hash, and final result."),
        ("Reject safely", "A rejected request never reaches signing. The decision remains available in the event timeline and audit log."),
        ("Automatic continuation", "After approval, the demo automatically performs simulated signing, broadcast, and confirmation for a fast internal demonstration."),
    ], s)])
    finish_page(story)

    page_title(story, "06 / Sweeping, settlement, and reconciliation", "Turn many payment addresses into controlled treasury liquidity", "Collection automation groups confirmed customer receipts and moves them to designated treasury wallets without losing order-level traceability.", s)
    story.append(feature_cards([
        ("Sweep candidates", "Aggregate confirmed, uncollected balances by network and asset. Show source-address count, total amount, threshold, and eligibility."),
        ("Threshold policy", "Configure independent USDT and USDC thresholds. Pause collection globally without disabling incoming payments."),
        ("Destination policy", "Bind TRON and Polygon collection destinations to controlled treasury wallets."),
        ("Collection task", "Create one task with source items, amount, destination, operator, status, and transaction hash."),
        ("Internal ledger", "Book collection as an internal movement from collection addresses to the hot/treasury wallet, keeping debit and credit balanced."),
        ("Settlement view", "Summarize gross receipts, platform fee, and net merchant settlement separately for USDT and USDC."),
    ], s))
    story.extend([Spacer(1, 5 * mm), p("Reconciliation controls", s["h2"]), data_table(
        ["Control", "Evidence"],
        [
            ["Balanced journal", "Debit total must equal credit total for every business journal"],
            ["Order vs ledger", "Confirmed and overpaid orders must have matching ledger postings"],
            ["Payout vs ledger", "Confirmed withdrawals must reduce merchant payable and wallet balance"],
            ["Sweep completeness", "Every collected payment address belongs to one collection task"],
            ["Exception counters", "Open payouts, failed callbacks, and pending/failed collections remain visible"],
        ], s, [50 * mm, 116 * mm])])
    finish_page(story)

    page_title(story, "07 / WaaS and merchant integration", "APIs that connect M2 Wallet to an existing payment platform", "The WaaS project area provides both configuration and live operational evidence for developers and operations teams.", s)
    story.append(feature_cards([
        ("Scoped API keys", "Create hash-only credentials with payments, withdrawals, and operations scopes. Rotate or disable without exposing stored secrets."),
        ("IP allowlist", "Restrict API-key requests to approved host addresses or CIDR ranges."),
        ("API Console", "Run safe payment, payout, status, and timeline requests from the UI and inspect the exact JSON request and response."),
        ("Status queries", "Retrieve payment or withdrawal state using either the M2 reference or merchant reference."),
        ("Callback operations", "Review pending, delivered, skipped, retry, and failed callbacks; manually deliver or retry when required."),
        ("Pre-payout verification", "Call the merchant before signing to confirm that the customer withdrawal remains valid."),
    ], s))
    story.extend([Spacer(1, 5 * mm), p("Signed callback contract", s["h2"]), Table(
        [[p("POST /webhooks/m2-wallet", s["code"])], [p("X-M2-Event: withdrawal.confirmed<br/>X-M2-Event-Id: EVT-...<br/>X-M2-Timestamp: 1784750000<br/>X-M2-Signature: sha256=&lt;HMAC&gt;<br/><br/>{ event_id, event_type, merchant_withdraw_id, status, tx_hash, amount, asset, network, occurred_at }", s["code"])]],
        colWidths=[166 * mm],
        style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#101613")), ("BOX", (0, 0), (-1, -1), 0.5, GREEN_DARK), ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))])
    story.extend([Spacer(1, 4 * mm), *bullets([
        "HMAC-SHA256 signs timestamp + '.' + the raw request body.",
        "Event ID is stable across retries and must be stored uniquely by the merchant before applying a business effect.",
        "The local merchant sandbox verifies signatures and persists deduplicated receipts for demonstration.",
    ], s)])
    finish_page(story)

    page_title(story, "08 / Risk, security, and administration", "Operational safeguards around every transfer", "Risk controls are embedded into the payment and payout workflow instead of being a separate reporting-only module.", s)
    story.append(data_table(
        ["Control", "Behavior", "Evidence"],
        [
            ["Global payout pause", "Blocks new approvals before signing", "Policy state and failed approval audit"],
            ["Single payout limit", "Rejects a request above the configured amount", "Matched policy and failure reason"],
            ["Daily payout limit", "Uses confirmed daily volume before releasing funds", "Used vs configured daily amount"],
            ["Address blocklist", "Stops withdrawal creation to a blocked recipient", "Address-book record and API error"],
            ["Address allowlist", "Optionally requires every recipient to be pre-approved", "Policy toggle and matched address"],
            ["Role permissions", "Separates view, operate, approve, and administer actions", "Session role and audit actor"],
            ["Signing boundary", "Private keys stay outside the application process", "Signature reference only"],
            ["Callback controls", "HTTPS, host allowlist, timestamp window, HMAC, bounded retry", "Delivery attempts and receipt"],
        ], s, [38 * mm, 72 * mm, 56 * mm]))
    story.extend([Spacer(1, 5 * mm), p("Management features", s["h2"]), feature_cards([
        ("Members and roles", "Admin, Finance, Operations, and Viewer demo accounts with a visible permission model."),
        ("Team API", "Credential name, prefix, scopes, creator, creation time, last use, state, rotation, and disable actions."),
        ("Audit log", "Actor, role, action, resource, outcome, timestamp, and detail for sensitive operations."),
        ("Demo Acceptance", "Eight evidence-based checks aggregate live payment, payout, sweeping, callback, risk, and reconciliation data."),
    ], s)])
    finish_page(story)

    page_title(story, "09 / Delivery and roadmap", "What is ready now and what comes next", "The current package is designed for internal demonstration and integration preparation, with a clear route to a commercial deployment.", s)
    story.append(data_table(
        ["Area", "Internal demo now", "Commercial next step"],
        [
            ["Stablecoin payments", "USDT-TRON and USDC-Polygon, hosted checkout, exception states", "Rates, refunds, more chains, merchant branding"],
            ["Payouts", "Finance approval, simulated signing/broadcast, callbacks", "MPC/HSM cluster, production RPC, confirmation workers"],
            ["Sweeping", "Threshold candidates, manual/automatic policy, ledger", "Gas/energy orchestration and batched treasury policy"],
            ["Risk", "Limits, pause, allowlist, blocklist, role separation", "AML provider, velocity rules, dual approval"],
            ["Integration", "Scoped keys, API Console, status/timeline, signed callback sandbox", "Merchant test environment and production secret rotation"],
            ["Deployment", "Local persistent backend plus installable team PWA", "Managed database, workers, monitoring, backup, disaster recovery"],
        ], s, [36 * mm, 65 * mm, 65 * mm]))
    story.extend([Spacer(1, 5 * mm), p("Demo access", s["h2"]), feature_cards([
        ("Team link", "Deploy the static browser sandbox to your organization's approved hosting environment."),
        ("Installable app", "Install from desktop Chrome or add to the home screen on iOS/Android as a PWA."),
        ("Local environment", "http://127.0.0.1:8787 - persistent operational data and full backend API behavior."),
        ("Verification", "30 backend tests plus hosted/PWA checks, build validation, and browser-based workflow acceptance."),
    ], s)])
    story.extend([Spacer(1, 5 * mm), p("Inputs required for real platform integration", s["h2"]), *bullets([
        "Test API base URL, authentication method, and callback endpoint.",
        "Example payment-order and customer-withdrawal payloads.",
        "Commission model, customer-specific rates, and settlement rules.",
        "Named finance approvers and dedicated test wallets. Never share mnemonics or private keys in chat or source control.",
    ], s)])
    finish_page(story)


def chinese_pages(story: list) -> None:
    s = ZH
    page_title(story, "01 / 产品概览", "稳定币资金流转的一体化控制平台", "M2 Wallet 将企业钱包、商户收款、提现审批、自动归集、WaaS API、风险控制和财务运营整合到一个内部工作台中。", s)
    story.append(feature_cards([
        ("统一运营系统", "运营、财务、开发和管理员在同一个平台协作，每个角色只看到与职责相关的操作和证据。"),
        ("稳定币优先", "当前 Demo 聚焦 USDT-TRON 与 USDC-Polygon，覆盖首个内部版本最需要的两条资金通道。"),
        ("关键资金动作人工控制", "客户提现必须由财务或管理员确认；通过策略检查后，系统才进入签名与链上广播。"),
        ("全流程可追溯", "支付、提现、归集、回调、审批和配置变更均形成可查询的运营记录。"),
    ], s))
    story.extend([Spacer(1, 5 * mm), p("主要使用角色", s["h2"])])
    story.append(data_table(
        ["角色", "主要工作", "典型权限"],
        [
            ["运营人员", "创建收款订单、监控交易、执行归集、重试回调", "可创建和操作，但不能执行财务审批"],
            ["财务人员", "审核客户提现并决定是否放款", "批准或拒绝提现，查看审计证据"],
            ["开发人员", "对接商户系统并验证订单生命周期", "最小权限 API Key、状态查询、回调"],
            ["管理员", "配置钱包、限额、名单、角色和项目参数", "完整配置和安全管理权限"],
        ], s, [26 * mm, 75 * mm, 65 * mm]))
    story.extend([Spacer(1, 5 * mm), p("当前 Demo 边界", s["h2"]), *bullets([
        "当前为交互式模拟环境，应用内不保存真实私钥、助记词或可花费的签名材料。",
        "已经预留外部 MPC/HSM 签名、链 RPC 广播和交易确认监听的生产边界。",
        "在线团队 Demo 按浏览器隔离数据；本机后端使用持久化 SQLite 运营数据库。",
    ], s)])
    finish_page(story)

    page_title(story, "02 / 产品地图", "八个相互联动的功能区", "产品采用接近 Cregis 的信息架构，优先覆盖支付公司内部最需要的稳定币资金运营流程。", s)
    story.append(feature_cards([
        ("钱包", "多钱包资产、USDT/USDC 余额、处理中和可归集金额、发送、接收、地址及钱包交易记录。"),
        ("协作", "待审批、待签名、已审批、已签名、全部事项、我发起的事项、审批详情及策略证据。"),
        ("WaaS 项目", "API 提现、异常回调、历史回调、开发者设置、API Console、IP 白名单、回调策略和通知。"),
        ("支付引擎", "稳定币订单、托管收银台、支付地址、支付状态、异常状态、佣金计算和结算记录。"),
        ("交易记录", "统一展示转入、转出和内部归集，包含币种、网络、地址、钱包、业务类型、哈希、金额和状态。"),
        ("风控", "出款总开关、单笔与日累计限额、地址白名单/黑名单、自动化、策略证据和风险日志。"),
        ("管理", "Demo 验收、团队套餐、账户、成员、角色、团队安全、API Key 和审计日志。"),
        ("应用市场", "为 AML、TRON 能量、通知、财务报表及后续跨境结算能力预留扩展入口。"),
    ], s))
    story.extend([Spacer(1, 5 * mm), p("系统主流程", s["h2"]), flow_table([
        ("商户系统", "创建支付订单或提现申请"),
        ("M2 Wallet", "校验、落库并执行策略"),
        ("财务与链上", "审批、签名、广播和确认"),
        ("商户系统", "接收签名回调并完成对账"),
    ], s)])
    finish_page(story)

    page_title(story, "03 / 钱包与资金管理", "从余额展示升级为企业资金运营视图", "钱包模块不仅展示余额，还帮助团队识别处理中资产、可归集资产及各网络上的资金状态。", s)
    story.append(feature_cards([
        ("多钱包切换", "支持主钱包、区域结算钱包、商户归集钱包和测试钱包，并展示钱包类型与密钥分片标识。"),
        ("资产状态", "每个币种展示可用、处理中和可归集余额，并可展开查看网络级明细。"),
        ("分步接收", "选择币种和网络，展示收款地址与二维码式视图，同时明确网络安全提示。"),
        ("分步发送", "选择币种和网络，填写接收方和金额，完成地址与策略校验后创建财务审批任务。"),
        ("地址管理", "支持地址和别名检索，展示默认付款/归集属性，并连接白名单及黑名单控制。"),
        ("交易详情", "查看业务编号、方向、钱包、网络、地址、时间、交易哈希和生命周期状态。"),
    ], s))
    story.extend([Spacer(1, 5 * mm), p("资金状态模型", s["h2"]), data_table(
        ["状态", "含义", "运营动作"],
        [
            ["可用", "钱包中已经确认且可使用的余额", "在风控和审批约束下用于出款"],
            ["处理中", "订单未完成或链上交易尚未最终确认", "监控确认数和订单到期时间"],
            ["可归集", "已经确认但仍停留在收款子地址的资金", "执行自动归集或人工归集"],
            ["已归集", "资金已经进入指定财资钱包", "核对内部划转和交易哈希"],
        ], s, [32 * mm, 78 * mm, 56 * mm])])
    finish_page(story)

    page_title(story, "04 / 稳定币支付引擎", "用完整订单生命周期接收 USDT 与 USDC", "商户只需要一个业务订单号，即可获得收银台、支付地址、状态跟踪、佣金记账和签名回调。", s)
    story.append(flow_table([
        ("创建订单", "商户提交订单号、客户、金额、币种、网络、返回地址和扩展字段。"),
        ("展示收银台", "系统生成收款地址、到期时间和托管支付页面。"),
        ("监听到账", "链上监听器将已确认的代币转账匹配到待支付订单。"),
        ("记账", "平台佣金和商户净额分别写入平衡的复式账本。"),
        ("通知商户", "系统通过签名回调报告最终业务状态。"),
    ], s))
    story.extend([Spacer(1, 5 * mm), p("支付状态", s["h2"]), data_table(
        ["状态", "触发条件", "商户含义"],
        [
            ["PENDING", "尚未发现确认到账", "到期前仍可继续支付"],
            ["PARTIAL", "实付低于 1% 容差下限", "客户可继续补足剩余金额"],
            ["CONFIRMED", "实付金额处于容差范围", "订单成功并完成账本记账"],
            ["OVERPAID", "实付超过容差上限", "按完整实收记账，同时保留异常状态"],
            ["EXPIRED", "支付时间窗口结束", "关闭订单并生成过期回调"],
        ], s, [30 * mm, 62 * mm, 74 * mm])])
    story.extend([Spacer(1, 5 * mm), p("商业接入关键点", s["h2"]), *bullets([
        "merchant_order_id 是幂等键；同一个编号若被不同金额、币种或网络复用，系统会明确拒绝。",
        "平台佣金按订单计算，并与商户应付资金分开记账。",
        "公开收银台只暴露支付所需字段，回调地址和内部 Metadata 不对客户开放。",
        "商户既可使用 M2 Wallet 编号，也可使用自身订单号查询状态。",
    ], s)])
    finish_page(story)

    page_title(story, "05 / 提现与财务审批", "从客户提现申请到链上确认的完整闭环", "提现链路被拆分为 API 发起、财务授权、签名、链上广播、确认和商户通知，避免系统自动无控制放款。", s)
    story.append(flow_table([
        ("提交申请", "商户提交客户、币种、金额、网络、接收方、回调和扩展字段。"),
        ("系统校验", "检查项目状态、币种组合、地址、黑白名单、限额和幂等键。"),
        ("财务审批", "财务或管理员查看业务信息、策略证据和可选的商户二次校验结果。"),
        ("独立签名", "签名服务只接收交易意图，应用本身不保存真实私钥。"),
        ("链上广播", "将签名交易发送至 TRON 或 Polygon 并取得交易哈希。"),
        ("确认回写", "记录最终状态、账本、审计、时间线和签名回调。"),
    ], s))
    story.extend([Spacer(1, 5 * mm), p("财务工作台", s["h2"]), feature_cards([
        ("待审批", "展示金额、币种、网络、接收方、来源系统、客户编号、命中策略和校验状态。"),
        ("审批证据", "记录审批人、审批时间、外部校验结果、签名引用、交易哈希和最终结果。"),
        ("安全拒绝", "被拒绝的申请不会进入签名；决定仍保存在事件时间线和审计日志中。"),
        ("自动续接", "审批通过后，Demo 自动完成模拟签名、广播和确认，便于内部快速展示完整链路。"),
    ], s)])
    finish_page(story)

    page_title(story, "06 / 归集、结算与对账", "把分散收款地址转化为可控财资流动性", "归集自动化按网络和币种聚合已确认资金，并在不丢失订单级追踪能力的前提下转入指定财资钱包。", s)
    story.append(feature_cards([
        ("待归集资产", "按网络和币种汇总已确认、尚未归集的余额，展示来源地址数、总金额、阈值和可执行状态。"),
        ("阈值策略", "分别配置 USDT 和 USDC 阈值；可以暂停归集而不影响继续收款。"),
        ("目标地址", "为 TRON 和 Polygon 绑定受控的财资归集钱包。"),
        ("归集任务", "每个任务记录来源项、金额、目标、操作人、状态和交易哈希。"),
        ("内部账本", "将归集记为收款地址到热钱包/财资钱包的内部划转，保持借贷平衡。"),
        ("结算视图", "按 USDT 与 USDC 分别汇总总收款、平台佣金和商户净结算金额。"),
    ], s))
    story.extend([Spacer(1, 5 * mm), p("对账控制", s["h2"]), data_table(
        ["控制项", "校验证据"],
        [
            ["日记账平衡", "每一个业务 Journal 的借方总额必须等于贷方总额"],
            ["订单与账本", "已确认和超额支付订单必须存在对应账本分录"],
            ["提现与账本", "已确认提现必须同时减少商户应付与钱包余额"],
            ["归集完整性", "每个已归集的支付地址只能属于一个归集任务"],
            ["异常统计", "待处理提现、失败回调、待执行/失败归集持续可见"],
        ], s, [50 * mm, 116 * mm])])
    finish_page(story)

    page_title(story, "07 / WaaS 与商户系统对接", "把 M2 Wallet 接入现有支付平台", "WaaS 项目区同时提供开发配置和实时运营证据，方便开发、运营与财务共同完成联调。", s)
    story.append(feature_cards([
        ("最小权限 API Key", "按支付、提现和运营范围授权；只保存哈希，可轮换或停用，不回显历史密钥。"),
        ("IP 白名单", "将 API Key 请求限制在指定 IP 或 CIDR 网段。"),
        ("API Console", "直接在界面运行支付、提现、状态和时间线示例，并查看完整 JSON 请求与响应。"),
        ("状态查询", "使用 M2 编号或商户自己的订单号/提现单号获取当前状态。"),
        ("回调运营", "查看待推送、已送达、已忽略、重试和失败回调，并支持人工推送或重试。"),
        ("出款前二次校验", "签名前回调商户平台，确认客户提现申请仍然有效。"),
    ], s))
    story.extend([Spacer(1, 5 * mm), p("签名回调协议", s["h2"]), Table(
        [[p("POST /webhooks/m2-wallet", EN["code"])], [p("X-M2-Event: withdrawal.confirmed<br/>X-M2-Event-Id: EVT-...<br/>X-M2-Timestamp: 1784750000<br/>X-M2-Signature: sha256=&lt;HMAC&gt;<br/><br/>{ event_id, event_type, merchant_withdraw_id, status, tx_hash, amount, asset, network, occurred_at }", EN["code"])]],
        colWidths=[166 * mm],
        style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#101613")), ("BOX", (0, 0), (-1, -1), 0.5, GREEN_DARK), ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))])
    story.extend([Spacer(1, 4 * mm), *bullets([
        "使用 HMAC-SHA256 对 timestamp + '.' + 原始请求体进行签名。",
        "Event ID 在重试期间保持不变；商户必须先唯一落库，再执行入账或状态更新。",
        "本机商户沙箱会验证签名并保存去重回执，便于完整演示。",
    ], s)])
    finish_page(story)

    page_title(story, "08 / 风控、安全与管理", "让每一笔资金动作都有边界和证据", "风控不是独立的报表模块，而是直接嵌入支付、提现、审批和回调流程。", s)
    story.append(data_table(
        ["控制项", "系统行为", "可查看证据"],
        [
            ["出款总开关", "在签名前阻止新的审批放款", "策略状态和失败审批审计"],
            ["单笔限额", "金额超过配置值时拒绝放款", "命中策略和失败原因"],
            ["日累计限额", "结合当日已确认出款量控制放款", "当日已用金额与限额"],
            ["地址黑名单", "禁止创建向风险地址的提现", "地址簿记录和 API 错误"],
            ["地址白名单", "可要求所有接收方必须提前审批", "策略开关和命中地址"],
            ["角色权限", "分离查看、运营、审批和管理动作", "会话角色和审计操作人"],
            ["签名边界", "私钥始终留在应用进程之外", "只保存签名引用"],
            ["回调安全", "HTTPS、主机白名单、时间窗、HMAC、有限重试", "投递次数和验签回执"],
        ], s, [38 * mm, 72 * mm, 56 * mm]))
    story.extend([Spacer(1, 5 * mm), p("管理能力", s["h2"]), feature_cards([
        ("成员与角色", "管理员、财务、运营和只读演示账号，并展示可理解的权限模型。"),
        ("团队 API", "展示凭据名称、前缀、权限、创建人、创建时间、最后使用、状态、轮换和停用。"),
        ("审计日志", "记录敏感操作的操作人、角色、动作、资源、结果、时间和详细信息。"),
        ("Demo Acceptance", "用八类实时证据聚合支付、提现、归集、回调、风控和对账验收结果。"),
    ], s)])
    finish_page(story)

    page_title(story, "09 / 交付状态与路线图", "当前可展示的能力及下一阶段", "现有版本用于内部展示和真实联调准备，并已经规划从 Demo 走向商业部署的演进路径。", s)
    story.append(data_table(
        ["领域", "当前内部 Demo", "商业化下一步"],
        [
            ["稳定币收款", "USDT-TRON、USDC-Polygon、收银台和异常状态", "汇率、退款、更多链、商户品牌"],
            ["提现", "财务审批、模拟签名/广播、状态回调", "MPC/HSM 集群、生产 RPC、确认 Worker"],
            ["归集", "阈值候选、人工/自动策略、内部账本", "Gas/能量调度和批量财资策略"],
            ["风控", "限额、暂停、白名单、黑名单和角色分离", "AML 服务、频率规则、大额双人审批"],
            ["系统对接", "权限 Key、API Console、状态/时间线、签名回调沙箱", "商户测试环境及生产密钥轮换"],
            ["部署", "本机持久化后端与可安装团队 PWA", "托管数据库、Worker、监控、备份和灾备"],
        ], s, [36 * mm, 65 * mm, 65 * mm]))
    story.extend([Spacer(1, 5 * mm), p("Demo 使用方式", s["h2"]), feature_cards([
        ("团队链接", "可将静态浏览器沙箱部署至组织批准的托管环境，供团队成员访问。"),
        ("可安装 App", "桌面 Chrome 可直接安装；iOS/Android 可添加到主屏幕，作为 PWA 使用。"),
        ("本机环境", "http://127.0.0.1:8787，包含持久化运营数据和完整后端 API 行为。"),
        ("验证状态", "30 项后端测试、托管/PWA 测试、构建验证及浏览器端完整流程验收。"),
    ], s)])
    story.extend([Spacer(1, 5 * mm), p("真实平台联调所需资料", s["h2"]), *bullets([
        "测试环境 API Base URL、认证方式和回调地址。",
        "收款订单与客户提现单的字段示例。",
        "佣金模式、客户差异费率和结算规则。",
        "指定财务审批人和专用测试钱包；不要在聊天或代码中提供助记词及私钥。",
    ], s)])


def draw_page(canvas, doc) -> None:
    width, height = A4
    canvas.saveState()
    if doc.page == 1:
        canvas.setFillColor(GREEN_DARK)
        canvas.rect(0, 0, width, height, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#0C583A"))
        canvas.circle(width - 24 * mm, height - 25 * mm, 45 * mm, fill=1, stroke=0)
        canvas.setStrokeColor(colors.HexColor("#1A6C4B"))
        canvas.setLineWidth(1)
        for offset in range(0, 7):
            canvas.circle(width - 24 * mm, height - 25 * mm, (18 + offset * 6) * mm, fill=0, stroke=1)
    else:
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(20 * mm, height - 14 * mm, width - 20 * mm, height - 14 * mm)
        canvas.setFont("Helvetica-Bold", 7)
        canvas.setFillColor(GREEN_DARK)
        canvas.drawString(20 * mm, height - 10 * mm, "M2 WALLET")
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(width - 20 * mm, height - 10 * mm, "PRODUCT FEATURES GUIDE")
        canvas.line(20 * mm, 13 * mm, width - 20 * mm, 13 * mm)
        canvas.drawString(20 * mm, 8 * mm, "Internal demo - July 2026")
        canvas.drawRightString(width - 20 * mm, 8 * mm, f"{doc.page:02d}")
    canvas.restoreState()


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    story: list = []
    cover(story)
    english_pages(story)
    chinese_pages(story)
    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        title="M2 Wallet Product Features Guide - English and Chinese",
        author="M2 Wallet",
        subject="Stablecoin wallet, payments, payouts, sweeping, WaaS, risk control, and operations demo",
    )
    doc.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    print(OUTPUT)


if __name__ == "__main__":
    main()
