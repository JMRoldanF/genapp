#!/usr/bin/env python3
"""Generate large volumes of realistic COBOL/CICS/Db2 source for stress-testing
static analysis, explanation and call-graph tooling.

The generator builds a *topology* first (layers, domains, hubs, cycles, long
chains, orphans, dynamic dispatch) and then emits source that materialises it,
writing the exact expected graph to manifest.json so tool output can be scored
against ground truth instead of eyeballed.

Style is modelled on the GenApp base application (base/src): fixed-format
COBOL, eyecatchers in WORKING-STORAGE, commarea copybooks, EXEC CICS LINK to a
shared error hub, EXEC SQL in the data layer, VSAM in the file layer.

Nothing here is meant to run on z/OS -- it is meant to be *read* by tools.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import sys
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Fixed-format COBOL emission
# ---------------------------------------------------------------------------
# cols 1-6   sequence area
# col  7     indicator ('*' comment, '-' continuation)
# cols 8-11  area A    (divisions, sections, paragraphs, 01 levels)
# cols 12-72 area B    (statements)
# cols 73-80 identification (ignored by the compiler)

SEQ = " " * 6
AREA_A = " " * 7
AREA_B = " " * 11
MAX_COL = 72


def cmt(text: str = "") -> str:
    """Comment line: '*' in column 7."""
    return (SEQ + "*" + text)[:MAX_COL]


def banner(text: str) -> list[str]:
    return [cmt("*" * 65), cmt(f" {text:<63}*"), cmt("*" * 65)]


def rule() -> str:
    return cmt("-" * 64 + "*")


def a(text: str) -> str:
    """Area A line."""
    return (AREA_A + text)[:MAX_COL]


def b(text: str, indent: int = 0) -> str:
    """Area B line."""
    return (AREA_B + " " * indent + text)[:MAX_COL]


# ---------------------------------------------------------------------------
# Vocabulary -- keeps generated text lexically diverse
# ---------------------------------------------------------------------------

# Two-letter subsystem codes. The first ten are GenApp's own; the rest extend
# the vocabulary so a large estate is not forced into ten giant blobs. None may
# start with K, which is reserved for the ZK copybook prefix.
DOMAINS = {
    "MT": ("MOTOR", "motor policy"),
    "HO": ("HOUSE", "house policy"),
    "EN": ("ENDOW", "endowment policy"),
    "CU": ("CUSTOMER", "customer master"),
    "CL": ("CLAIM", "claims handling"),
    "BI": ("BILLING", "billing and dunning"),
    "RE": ("REINS", "reinsurance cession"),
    "AG": ("AGENT", "agent and broker"),
    "PA": ("PAYMENT", "premium payment"),
    "UW": ("UNDERWR", "underwriting rules"),
    "LI": ("LIFE", "life assurance"),
    "HE": ("HEALTH", "health cover"),
    "TR": ("TRAVEL", "travel cover"),
    "PE": ("PET", "pet cover"),
    "MA": ("MARINE", "marine cargo and hull"),
    "AV": ("AVIATION", "aviation cover"),
    "FL": ("FLEET", "commercial fleet"),
    "CO": ("COMMRCL", "commercial lines"),
    "LB": ("LIABLTY", "liability cover"),
    "PR": ("PROPRTY", "property cover"),
    "AN": ("ANNUITY", "annuity contracts"),
    "PN": ("PENSION", "pension administration"),
    "IV": ("INVEST", "investment funds"),
    "TX": ("TAX", "premium tax and levies"),
    "RG": ("REGLTRY", "regulatory reporting"),
    "CP": ("COMPLNC", "compliance checks"),
    "FR": ("FRAUD", "fraud detection"),
    "AC": ("ACTUARL", "actuarial modelling"),
    "RS": ("RESERVE", "technical reserving"),
    "TY": ("TREATY", "treaty management"),
    "BR": ("BROKER", "broker settlement"),
    "PT": ("PARTNER", "partner integration"),
    "DO": ("DOCUMNT", "document production"),
    "CR": ("CORRESP", "customer correspondence"),
    "NT": ("NOTIFY", "notification dispatch"),
    "SC": ("SCHEDUL", "job scheduling"),
    "WF": ("WORKFLW", "case workflow"),
    "AU": ("AUDIT", "audit trail"),
    "AR": ("ARCHIVE", "archival and retention"),
    "BA": ("BATCH", "batch control"),
    "IF": ("INTRFCE", "external interfaces"),
    "GW": ("GATEWAY", "service gateway"),
    "SE": ("SECURTY", "security services"),
    "AZ": ("AUTHZ", "authorisation rules"),
    "QU": ("QUOTE", "quotation engine"),
    "RN": ("RENEWAL", "renewal processing"),
    "ED": ("ENDORSE", "policy endorsement"),
    "CN": ("CANCEL", "cancellation handling"),
    "SU": ("SURRNDR", "surrender and maturity"),
    "MB": ("MEMBER", "group membership"),
    "PL": ("POLADMN", "policy administration"),
    "RT": ("RATING", "rating tables"),
    "DI": ("DISCONT", "discount schemes"),
    "EX": ("EXCESS", "excess and deductible"),
    "ST": ("SETTLE", "claim settlement"),
    "RC": ("RECOVER", "recovery actions"),
    "SL": ("SALVAGE", "salvage disposal"),
    "SB": ("SUBROG", "subrogation"),
    "LT": ("LITIGTN", "litigation tracking"),
    "VA": ("VALUATN", "asset valuation"),
}

ENTITIES = [
    "POLICY", "COVER", "RISK", "PREMIUM", "CLAIM", "PAYMENT", "ENDORSE",
    "RENEWAL", "SCHEDULE", "EXCESS", "DISCOUNT", "SURCHARGE", "COMMISSION",
    "SETTLEMENT", "RESERVE", "LEDGER", "ADDRESS", "CONTACT", "HISTORY",
]

FIELD_NOUNS = [
    "POSTCODE", "PREMIUM", "EXCESS", "MAKE", "MODEL", "VALUE", "COLOUR",
    "CC-RATING", "REG-NUMBER", "SUM-ASSURED", "WITH-PROFITS", "EQUITIES",
    "MANAGED-FUND", "TERM", "BEDROOMS", "HOUSE-TYPE", "ROOF-TYPE",
    "STATUS-CODE", "BROKER-ID", "AGENT-CODE", "TAX-BAND", "NCD-YEARS",
]

VERBS = [
    "VALIDATE", "CHECK", "DERIVE", "COMPUTE", "FORMAT", "NORMALISE",
    "RESOLVE", "APPLY", "EXPAND", "AUDIT", "REFRESH", "RECONCILE",
]

ABEND_CODES = ["LGCA", "LGSQ", "LGVS", "LGDL", "LGRC", "LGTS"]

# Measured over generated output: ~45 characters per emitted line. Used to turn
# a token budget into a line count for the context-window boundary cohort.
CHARS_PER_LINE = 45

# Multiples of the context-window budget to generate, so the point where a
# tool starts chunking (and whether it chunks correctly) is locatable.
BOUNDARY_MULTIPLES = [0.50, 0.80, 0.95, 1.02, 1.15, 1.50, 2.00]

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

# Edge kinds and whether a purely static analyser can resolve the target.
KIND_LINK = "CICS_LINK"                 # EXEC CICS LINK PROGRAM('LITERAL')
KIND_LINK_DYN = "CICS_LINK_DYNAMIC"     # EXEC CICS LINK PROGRAM(WS-VAR)
KIND_XCTL = "CICS_XCTL"
KIND_START = "CICS_START"               # via TRANSID -> needs CSD to resolve
KIND_CALL = "CALL_STATIC"               # CALL 'LITERAL'
KIND_CALL_DYN = "CALL_DYNAMIC"          # CALL WS-VAR
KIND_JCL = "JCL_EXEC_PGM"

RESOLVABLE = {
    KIND_LINK: True,
    KIND_XCTL: True,
    KIND_CALL: True,
    KIND_JCL: True,
    KIND_START: False,      # resolvable only by cross-referencing the CSD
    KIND_LINK_DYN: False,
    KIND_CALL_DYN: False,
}


@dataclass
class Call:
    target: str
    kind: str
    line: int = 0          # filled in while emitting
    via: str | None = None  # working-storage field for dynamic dispatch
    transid: str | None = None


@dataclass
class Prog:
    name: str
    ptype: str
    domain: str
    layer: int
    target_lines: int
    transid: str | None = None
    calls: list[Call] = field(default_factory=list)
    copybooks: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    vsam_files: list[str] = field(default_factory=list)
    mapset: str | None = None
    tags: list[str] = field(default_factory=list)
    path: str = ""
    lines: int = 0
    paragraphs: list[str] = field(default_factory=list)
    dead_paragraphs: list[str] = field(default_factory=list)


@dataclass
class Copybook:
    name: str
    domain: str
    kind: str                 # commarea | record | constants
    copies: list[str] = field(default_factory=list)
    path: str = ""
    lines: int = 0


# Layer / type mix. Data layer dominates, mirroring real insurance estates.
TYPE_MIX = [
    # (ptype, layer, share)
    ("driver", 0, 0.08),
    ("business", 1, 0.20),
    ("data_db2", 2, 0.22),
    ("data_vsam", 2, 0.13),
    ("subroutine", 3, 0.22),
    ("batch", 1, 0.13),
    ("util", 3, 0.02),
]


# ---------------------------------------------------------------------------
# Topology construction
# ---------------------------------------------------------------------------


class Topology:
    def __init__(self, rng: random.Random, cfg: argparse.Namespace):
        self.rng = rng
        self.cfg = cfg
        # Usable characters per program when every file must fit one context
        # window. None when --fit-window is off (the default).
        self.window_budget_chars: int | None = None
        if cfg.fit_window:
            self.window_budget_chars = int(
                cfg.window_tokens * cfg.chars_per_token * (1.0 - cfg.window_reserve)
            )
        self.progs: dict[str, Prog] = {}
        self.order: list[Prog] = []
        self.copybooks: dict[str, Copybook] = {}
        self.domains = list(DOMAINS)[: max(1, min(cfg.domains, len(DOMAINS)))]
        self._serial = 0
        self._transid = 0
        # Program names are Z + 2-char domain + 5-char serial = 8 chars, the
        # z/OS member-name limit. Five decimal digits run out at 99,999, so
        # switch the serial to base 36 (60M names) once the estate needs it.
        # Estates that fit in decimal keep decimal, so smaller corpora stay
        # byte-identical to earlier runs with the same seed.
        planned = cfg.programs + cfg.pathological + cfg.boundary
        self._serial_base36 = planned > 99_999

    # -- naming ------------------------------------------------------------
    ALPHABET36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    @classmethod
    def _base36(cls, n: int, width: int) -> str:
        s = ""
        for _ in range(width):
            s = cls.ALPHABET36[n % 36] + s
            n //= 36
        return s

    def _pname(self, domain: str) -> str:
        self._serial += 1
        if self._serial_base36:
            return f"Z{domain}{self._base36(self._serial, 5)}"
        return f"Z{domain}{self._serial:05d}"

    def _tranid(self) -> str:
        self._transid += 1
        return "Z" + self._base36(self._transid, 3)

    # -- size distribution -------------------------------------------------
    def _size(self) -> int:
        """Log-normal-ish: many small programs, a long tail of big ones."""
        mu = self.cfg.avg_lines
        v = self.rng.lognormvariate(0.0, 0.75)
        n = max(90, min(6000, int(mu * 0.65 * v)))
        return min(n, self._window_max_lines()) if self.window_budget_chars else n

    def _window_max_lines(self) -> int:
        """Largest line count that still fits the window budget, with margin."""
        assert self.window_budget_chars is not None
        return max(120, int(self.window_budget_chars * 0.95 / CHARS_PER_LINE))

    def build(self) -> None:
        self._build_partners()
        self._build_copybooks()
        self._build_programs()
        self._wire_calls()
        # chains and cycles claim their nodes before hub edges are sprayed
        # across the estate, otherwise there are no free subroutines left
        self._inject_chains()
        self._inject_cycles()
        self._inject_hubs()
        self._inject_dynamic()
        if self.cfg.pathological:
            self._inject_pathological()
        if self.cfg.boundary:
            self._inject_boundary()
        self._build_jobs()

    # -- copybooks ---------------------------------------------------------
    def _build_copybooks(self) -> None:
        # One estate-wide commarea, copied by nearly everything: the LGCMAREA
        # equivalent. High fan-in include resolution is a common tool failure.
        shared = Copybook("ZKCOMMON", "XX", "commarea")
        self.copybooks[shared.name] = shared
        self.shared_commarea = shared

        consts = Copybook("ZKCONST0", "XX", "constants")
        self.copybooks[consts.name] = consts
        shared.copies.append(consts.name)  # nested COPY, 2 levels

        self.domain_books: dict[str, list[str]] = {}
        for i, dom in enumerate(self.domains):
            books = []
            for j in range(self.cfg.copybooks_per_domain):
                cb = Copybook(f"ZK{dom}{j:04d}", dom, "record")
                # a third of the record copybooks nest the shared constants
                if j % 3 == 1:
                    cb.copies.append(consts.name)
                self.copybooks[cb.name] = cb
                books.append(cb.name)
            self.domain_books[dom] = books

    # -- programs ----------------------------------------------------------
    def _build_programs(self) -> None:
        total = self.cfg.programs
        counts: list[tuple[str, int, int]] = []
        assigned = 0
        for ptype, layer, share in TYPE_MIX:
            n = int(round(total * share))
            counts.append((ptype, layer, n))
            assigned += n
        # give the rounding remainder to the data layer
        if assigned != total:
            counts = [
                (t, l, n + (total - assigned) if t == "data_db2" else n)
                for t, l, n in counts
            ]

        # Hubs must not scale with the estate. A fixed 2% share would give 200
        # "utilities" at n=10000, each with a middling fan-in -- a flat
        # distribution. Real estates have a handful of modules that everything
        # calls, so cap the count and give the remainder to subroutines.
        util_planned = next((n for t, _, n in counts if t == "util"), 0)
        spare = max(0, util_planned - self.cfg.hubs)
        counts = [
            (t, l, min(n, self.cfg.hubs) if t == "util"
             else n + spare if t == "subroutine"
             else n)
            for t, l, n in counts
        ]

        self.by_type: dict[str, list[Prog]] = {t: [] for t, _, _ in counts}
        self.by_domain_type: dict[tuple[str, str], list[Prog]] = {}

        # Real estates have a few large core subsystems and a long tail of small
        # ones. Round-robin assignment instead produces domains of identical
        # size, which is both unrealistic and uninformative to cluster on.
        weights = [1.0 / (1 + i) ** 0.6 for i in range(len(self.domains))]
        self.mapset_seq: dict[str, int] = {d: 0 for d in self.domains}

        for ptype, layer, n in counts:
            for i in range(max(0, n)):
                # Seed every domain once so none is left empty, then weight.
                if i < len(self.domains):
                    dom = self.domains[i]
                else:
                    dom = self.rng.choices(self.domains, weights=weights)[0]
                p = Prog(
                    name=self._pname(dom),
                    ptype=ptype,
                    domain=dom,
                    layer=layer,
                    target_lines=self._size(),
                )
                if ptype == "driver":
                    p.transid = self._tranid()
                    # Mapsets are shared, but the count has to grow with the
                    # number of drivers -- a fixed 9 per domain meant ~89
                    # drivers per mapset at 100k programs.
                    self.mapset_seq[dom] += 1
                    slot = (self.mapset_seq[dom] - 1) // self.cfg.drivers_per_mapset
                    p.mapset = f"Z{dom}MAP{slot % 100:02d}"
                if ptype != "batch":
                    p.copybooks.append(self.shared_commarea.name)
                books = self.domain_books[dom]
                if books:
                    k = self.rng.randint(1, min(3, len(books)))
                    p.copybooks.extend(self.rng.sample(books, k))
                if ptype == "data_db2":
                    p.tables = [
                        f"GENA{dom}.{e}"
                        for e in self.rng.sample(ENTITIES, self.rng.randint(1, 3))
                    ]
                if ptype == "data_vsam":
                    p.vsam_files = [
                        f"KSDS{dom}{self.rng.randint(1, 99):02d}"
                        for _ in range(self.rng.randint(1, 2))
                    ]
                if ptype == "batch":
                    p.tables = [f"GENA{dom}.{self.rng.choice(ENTITIES)}"]
                    # DD names are fixed here, not at emission time, so the
                    # generated JCL and the FILE-CONTROL entries agree
                    p.vsam_files = [
                        f"DD{dom}IN{i % 90 + 1:02d}",
                        f"DD{dom}OT{i % 90 + 1:02d}",
                        f"DD{dom}RP{i % 90 + 1:02d}",
                    ]
                self.progs[p.name] = p
                self.order.append(p)
                self.by_type[ptype].append(p)
                self.by_domain_type.setdefault((dom, ptype), []).append(p)

    def _build_partners(self) -> None:
        """Give each domain a few partner domains, instead of talking to all.

        Choosing cross-domain targets uniformly at random produces a *complete*
        domain-coupling graph with near-uniform weights -- every domain pair
        connected, all about equally. That carries no signal: community
        detection recovers the blocks trivially, and impact analysis answers
        "everything, equally" for any change. Real estates couple sparsely and
        unevenly, so restrict each domain to a handful of partners.
        """
        self.partners: dict[str, list[str]] = {}
        for dom in self.domains:
            others = [d for d in self.domains if d != dom]
            if not others:
                self.partners[dom] = []
                continue
            k = min(self.rng.randint(*self.cfg.partners), len(others))
            self.partners[dom] = self.rng.sample(others, k)

    def _pick(self, ptype: str, domain: str, n: int) -> list[Prog]:
        """Same-domain by default; otherwise only into a partner domain."""
        same = self.by_domain_type.get((domain, ptype), [])
        pool = self.by_type.get(ptype, [])
        partners = self.partners.get(domain, [])
        out: list[Prog] = []
        for _ in range(n):
            if same and self.rng.random() < 0.8:
                out.append(self.rng.choice(same))
                continue
            # Cross-domain edges stay inside the partner set.
            candidates: list[Prog] = []
            if partners:
                for _try in range(4):
                    pdom = self.rng.choice(partners)
                    candidates = self.by_domain_type.get((pdom, ptype), [])
                    if candidates:
                        break
            if candidates:
                out.append(self.rng.choice(candidates))
            elif same:
                # No partner has a program of this type. Fall back to our own
                # domain, NOT to the global pool -- a uniform global draw here
                # silently rebuilds the complete coupling graph this method
                # exists to avoid. Small domains hit this often.
                out.append(self.rng.choice(same))
            elif pool:
                out.append(self.rng.choice(pool))
        return out

    # -- edges -------------------------------------------------------------
    def _wire_calls(self) -> None:
        rng = self.rng
        for p in self.order:
            if p.ptype == "driver":
                for t in self._pick("business", p.domain, rng.randint(2, 5)):
                    p.calls.append(Call(t.name, KIND_LINK))
                # Menu-to-menu transfers go through _pick like everything else:
                # a uniform draw over all drivers was the last source of
                # complete, uniform domain coupling.
                if rng.random() < 0.35:
                    for t in self._pick("driver", p.domain, 1):
                        if t.name != p.name:
                            p.calls.append(Call(t.name, KIND_XCTL))
                if rng.random() < 0.25:
                    for t in self._pick("driver", p.domain, 1):
                        if t.name != p.name and t.transid:
                            p.calls.append(
                                Call(t.name, KIND_START, transid=t.transid))
            elif p.ptype == "business":
                for t in self._pick("data_db2", p.domain, rng.randint(1, 3)):
                    p.calls.append(Call(t.name, KIND_LINK))
                for t in self._pick("data_vsam", p.domain, rng.randint(0, 2)):
                    p.calls.append(Call(t.name, KIND_LINK))
                for t in self._pick("subroutine", p.domain, rng.randint(0, 2)):
                    p.calls.append(Call(t.name, KIND_CALL))
            elif p.ptype in ("data_db2", "data_vsam"):
                for t in self._pick("subroutine", p.domain, rng.randint(0, 2)):
                    p.calls.append(Call(t.name, KIND_CALL))
            elif p.ptype == "batch":
                for t in self._pick("subroutine", p.domain, rng.randint(2, 6)):
                    p.calls.append(Call(t.name, KIND_CALL))
                for t in self._pick("data_db2", p.domain, rng.randint(0, 1)):
                    p.calls.append(Call(t.name, KIND_CALL))
            elif p.ptype == "subroutine":
                # occasional subroutine -> subroutine, deepens the graph
                if rng.random() < 0.3:
                    for t in self._pick("subroutine", p.domain, 1):
                        if t.name != p.name:
                            p.calls.append(Call(t.name, KIND_CALL))

    def _inject_hubs(self) -> None:
        """A handful of utilities called from almost everywhere (LGSTSQ-like)."""
        utils = self.by_type.get("util", [])
        if not utils:
            return
        self.hubs = [u.name for u in utils]
        for p in self.order:
            if p.ptype == "util" or p.ptype == "batch":
                continue
            if self.rng.random() < self.cfg.hub_density:
                p.calls.append(Call(self.rng.choice(self.hubs), KIND_LINK))
        for u in utils:
            u.tags.append("hub")

    def _free_subs_by_domain(self) -> list[list[Prog]]:
        """Free subroutines grouped by domain, largest group first.

        Chains and rings are built inside one domain. Drawing them from a
        globally shuffled list makes a single chain hop across twenty
        subsystems, which no real estate does and which quietly inflates the
        domain-coupling graph that clustering is scored against.
        """
        groups: dict[str, list[Prog]] = {}
        for p in self.by_type.get("subroutine", []):
            if not p.calls:
                groups.setdefault(p.domain, []).append(p)
        for g in groups.values():
            self.rng.shuffle(g)
        return sorted(groups.values(), key=len, reverse=True)

    def _inject_chains(self) -> None:
        """Long linear chains to test traversal depth beyond the layer count."""
        groups = self._free_subs_by_domain()
        depth = max(2, self.cfg.chain_depth)
        # Flatten domain-by-domain so each chain's slice never straddles two.
        subs: list[Prog] = []
        for g in groups:
            usable = (len(g) // depth) * depth
            subs.extend(g[:usable])
        want = self.cfg.chains
        if want * depth > len(subs):
            want = len(subs) // depth
            if want < self.cfg.chains:
                print(
                    f"note: only {want} of {self.cfg.chains} requested chains fit"
                    f" ({len(subs)} free subroutines, depth {depth});"
                    " raise -n or lower --chain-depth",
                    file=sys.stderr,
                )
        made = 0
        i = 0
        while made < want and i + depth <= len(subs):
            chain = subs[i: i + depth]
            i += depth
            for src, dst in zip(chain, chain[1:]):
                src.calls.append(Call(dst.name, KIND_CALL))
                src.tags.append("chain")
            chain[0].tags.append("chain-head")
            chain[-1].tags.append("chain-tail")
            made += 1
        self.chain_count = made

    def _inject_cycles(self) -> None:
        """Explicit recursion: self-calls and multi-node rings."""
        self.cycles: list[list[str]] = []
        # Chain members are off limits: a back-edge onto a chain would fuse
        # every injected ring into one giant SCC instead of N small ones.
        chain_tags = {"chain", "chain-head", "chain-tail"}
        # Rings stay inside one domain, for the same reason chains do.
        groups: dict[str, list[Prog]] = {}
        for p in self.by_type.get("subroutine", []):
            if chain_tags.intersection(p.tags):
                continue
            groups.setdefault(p.domain, []).append(p)
        for g in groups.values():
            self.rng.shuffle(g)
        pools = sorted(groups.values(), key=len, reverse=True)
        cursor = 0
        for _ in range(self.cfg.cycles):
            size = self.rng.choice([1, 2, 2, 3, 4])
            # Round-robin across domains, so rings spread over the estate
            # instead of piling into whichever domain happens to be largest.
            ring: list[Prog] = []
            for _probe in range(len(pools) or 1):
                pool = pools[cursor % len(pools)] if pools else []
                cursor += 1
                if len(pool) >= size:
                    ring = [pool.pop() for _ in range(size)]
                    break
            if not ring:
                break
            if size == 1:
                ring[0].calls.append(Call(ring[0].name, KIND_CALL))
                ring[0].tags.append("recursive")
                self.cycles.append([ring[0].name])
                continue
            for j, src in enumerate(ring):
                dst = ring[(j + 1) % size]
                src.calls.append(Call(dst.name, KIND_CALL))
                src.tags.append("cycle")
            self.cycles.append([p.name for p in ring])

    def _inject_dynamic(self) -> None:
        """Rewrite a share of static edges as dynamic dispatch.

        The true target stays in the manifest, so the tool can be scored on how
        much of the dynamic surface it resolves rather than just on the easy
        literal edges.
        """
        rng = self.rng
        n = 0
        for p in self.order:
            for c in p.calls:
                if c.kind == KIND_LINK and rng.random() < self.cfg.dynamic_rate:
                    c.kind = KIND_LINK_DYN
                    c.via = f"WS-PROGNAME-{n % 8 + 1}"
                    n += 1
                elif c.kind == KIND_CALL and rng.random() < self.cfg.dynamic_rate:
                    c.kind = KIND_CALL_DYN
                    c.via = f"WS-SUBNAME-{n % 8 + 1}"
                    n += 1
        self.dynamic_edges = n

    def _inject_boundary(self) -> None:
        """Programs sized to straddle a model's context window.

        A 200K-window model is barely exercised by an ordinary size
        distribution -- almost everything fits, so the chunking path never
        runs. These programs bracket the limit at known multiples so the tool
        can be scored on where chunking starts and whether it stays correct
        across it.

        The chars-per-token ratio is a parameter, not a guess: measure it on
        your own corpus with count_tokens and pass the real value.
        """
        self.boundary: list[dict] = []
        budget_chars = self.cfg.window_tokens * self.cfg.chars_per_token
        # leave room for the system prompt and the model's own output
        budget_chars *= 1.0 - self.cfg.window_reserve
        multiples = BOUNDARY_MULTIPLES
        if self.window_budget_chars:
            multiples = [m for m in BOUNDARY_MULTIPLES if m <= 1.0]
            print(
                "note: --fit-window is on, so the boundary cohort keeps only the"
                f" multiples that fit ({', '.join(str(m) for m in multiples)});"
                " drop the flag once the over-window fallback exists",
                file=sys.stderr,
            )
        for i in range(self.cfg.boundary):
            mult = multiples[i % len(multiples)]
            dom = self.domains[i % len(self.domains)]
            target_chars = int(budget_chars * mult)
            p = Prog(
                name=self._pname(dom),
                ptype="boundary",
                domain=dom,
                layer=3,
                target_lines=max(120, int(target_chars / CHARS_PER_LINE)),
                tags=[f"boundary-{int(mult * 100):03d}pct", "boundary"],
            )
            p.copybooks.append(self.shared_commarea.name)
            books = self.domain_books[dom]
            if books:
                p.copybooks.extend(self.rng.sample(books, min(2, len(books))))
            # a few real edges so the program is not an inert wall of filler
            for t in self._pick("subroutine", dom, 2):
                p.calls.append(Call(t.name, KIND_CALL))
            self.progs[p.name] = p
            self.order.append(p)
            self.by_type.setdefault("boundary", []).append(p)
            self.boundary.append({
                "program": p.name,
                "multiple_of_window": mult,
                "target_chars": target_chars,
                "fits_window": mult <= 1.0,
            })

    def _build_jobs(self) -> None:
        """Group batch programs into multi-step jobs.

        JCL is a first-class part of the graph: EXEC PGM= is often the only
        thing that makes a batch module reachable, and DD names are the only
        link between a SELECT ... ASSIGN TO and a real dataset.
        """
        self.jobs: list[dict] = []
        batches = [p for p in self.order if p.ptype == "batch"]
        for i in range(0, len(batches), 3):
            chunk = batches[i: i + 3]
            self.jobs.append({
                "name": f"VJOB{i // 3:04d}",
                "steps": [
                    {"step": f"STEP{j + 1:02d}", "program": p.name,
                     "dd_names": list(p.vsam_files)}
                    for j, p in enumerate(chunk)
                ],
            })

    # -- deliberately hostile inputs ---------------------------------------
    def _inject_pathological(self) -> None:
        self.pathological: list[str] = []
        specs = [
            ("deep-nesting", "IF nesting 120 levels deep"),
            ("goto-spaghetti", "GO TO DEPENDING ON with unstructured flow"),
            ("altered-goto", "obsolete ALTER ... TO PROCEED TO"),
            ("fan-out", "300 CALL statements in one program"),
            ("dispatch-table", "CALL resolved from an OCCURS table of names"),
            ("giant", "single program of ~20000 lines"),
            ("lowercase-tabs", "lower case source with embedded tabs"),
            ("dynamic-sql", "EXEC SQL PREPARE from a host variable"),
            ("no-callers", "orphan with a large body"),
            ("duplicate-name", "same PROGRAM-ID emitted in two directories"),
        ]
        for tag, _desc in specs[: self.cfg.pathological]:
            dom = self.domains[0]
            if tag == "giant":
                # Under --fit-window the giant is shrunk to the largest file
                # that still fits rather than dropped, so there is still a
                # biggest-file stress case. Drop the flag to get the real one.
                giant_lines = (
                    self._window_max_lines() if self.window_budget_chars else 20000
                )
            p = Prog(
                name=self._pname(dom),
                ptype="pathological",
                domain=dom,
                layer=3,
                target_lines=giant_lines if tag == "giant" else 400,
                tags=[tag, "pathological"],
            )
            p.copybooks.append(self.shared_commarea.name)
            if tag == "fan-out":
                pool = self.by_type.get("subroutine", [])
                for t in self.rng.sample(pool, min(300, len(pool))):
                    p.calls.append(Call(t.name, KIND_CALL))
            if tag == "dispatch-table":
                pool = self.by_type.get("subroutine", [])
                for t in self.rng.sample(pool, min(12, len(pool))):
                    p.calls.append(
                        Call(t.name, KIND_CALL_DYN, via="WS-DISPATCH-NAME")
                    )
            if tag == "dynamic-sql":
                p.tables = [f"GENA{dom}.{self.rng.choice(ENTITIES)}"]
            self.progs[p.name] = p
            self.order.append(p)
            self.by_type.setdefault("pathological", []).append(p)
            self.pathological.append(p.name)


# ---------------------------------------------------------------------------
# COBOL emission
# ---------------------------------------------------------------------------


class Emitter:
    def __init__(self, rng: random.Random, topo: Topology, cfg):
        self.rng = rng
        self.topo = topo
        self.cfg = cfg

    # -- working storage ---------------------------------------------------
    def working_storage(self, p: Prog) -> list[str]:
        rng = self.rng
        L: list[str] = []
        L.append(a("WORKING-STORAGE SECTION."))
        L.append(cmt(" Run time (debug) information for this invocation"))
        L.append(a("01  WS-HEADER."))
        L.append(b(f"03 WS-EYECATCHER          PIC X(16)", 2))
        L.append(b(f"                           VALUE '{p.name}------WS'.", 2))
        L.append(b("03 WS-TRANSID             PIC X(4).", 2))
        L.append(b("03 WS-TERMID              PIC X(4).", 2))
        L.append(b("03 WS-TASKNUM             PIC 9(7).", 2))
        L.append(b("03 WS-CALEN               PIC S9(4) COMP.", 2))
        L.append(b("03 WS-ADDR-COMMAREA       USAGE IS POINTER.", 2))
        L.append(rule())
        L.append(a("01  WS-RESP                   PIC S9(8) COMP VALUE +0."))
        L.append(a("01  WS-RESP2                  PIC S9(8) COMP VALUE +0."))
        L.append(a("01  ABS-TIME                  PIC S9(15) COMP-3 VALUE +0."))
        L.append(a("01  TIME1                     PIC X(8)  VALUE SPACES."))
        L.append(a("01  DATE1                     PIC X(10) VALUE SPACES."))
        L.append("")
        L.append(cmt(" Error message structure"))
        L.append(a("01  ERROR-MSG."))
        L.append(b("03 EM-DATE                PIC X(8)  VALUE SPACES.", 2))
        L.append(b("03 FILLER                 PIC X     VALUE SPACES.", 2))
        L.append(b("03 EM-TIME                PIC X(6)  VALUE SPACES.", 2))
        L.append(b(f"03 FILLER                 PIC X(9)  VALUE ' {p.name}'.", 2))
        L.append(b("03 EM-VARIABLE            PIC X(21) VALUE SPACES.", 2))
        L.append("")

        # condition names and packed decimal, to exercise data-division parsing
        L.append(a("01  WS-STATUS-CODE            PIC X(2)  VALUE SPACES."))
        L.append(b("88 WS-STATUS-OK             VALUE '00'.", 4))
        L.append(b("88 WS-STATUS-NOTFND         VALUE '01'.", 4))
        L.append(b("88 WS-STATUS-DUPKEY         VALUE '02'.", 4))
        L.append(b("88 WS-STATUS-FAILED         VALUE '90' THRU '99'.", 4))
        L.append(a("01  WS-PREMIUM-TOTAL          PIC S9(9)V99 COMP-3 VALUE +0."))
        L.append(a("01  WS-PREMIUM-BAND           PIC 9(2)  COMP-5 VALUE 0."))
        L.append(a("01  WS-SUB                    PIC S9(4) COMP VALUE +1."))
        L.append(a("01  WS-IX                     PIC S9(4) COMP VALUE +1."))
        L.append(a("01  WS-ENTRY-COUNT            PIC S9(4) COMP VALUE +0."))
        L.append("")

        # a REDEFINES pair plus OCCURS DEPENDING ON
        L.append(a("01  WS-KEY-AREA."))
        L.append(b("03 WS-KEY-CUSTOMER        PIC 9(10).", 2))
        L.append(b("03 WS-KEY-POLICY          PIC 9(10).", 2))
        L.append(a("01  WS-KEY-FLAT REDEFINES WS-KEY-AREA."))
        L.append(b("03 WS-KEY-CHAR            PIC X(20).", 2))
        L.append(a("01  WS-TABLE-AREA."))
        L.append(b("03 WS-TABLE-COUNT         PIC S9(4) COMP VALUE +0.", 2))
        L.append(b("03 WS-TABLE-ENTRY OCCURS 1 TO 250 TIMES", 2))
        L.append(b("        DEPENDING ON WS-TABLE-COUNT.", 5))
        for n in rng.sample(FIELD_NOUNS, 4):
            L.append(b(f"05 WS-T-{n[:14]:<14} PIC X(12).", 5))
        L.append(b("05 WS-T-AMOUNT           PIC S9(7)V99 COMP-3.", 5))
        L.append("")

        # literals backing every static call target
        statics = [c for c in p.calls if c.kind in (KIND_LINK, KIND_XCTL, KIND_CALL)]
        if statics:
            L.append(cmt(" Called module names"))
            seen = set()
            for c in statics:
                if c.target in seen:
                    continue
                seen.add(c.target)
                L.append(a(f"01  MOD-{c.target}              PIC X(8) VALUE '{c.target}'."))
            L.append("")

        # variables backing dynamic dispatch
        dyns = [c for c in p.calls if c.via]
        if dyns:
            L.append(cmt(" Dynamically resolved module names"))
            for v in sorted({c.via for c in dyns if c.via}):
                L.append(a(f"01  {v:<24}  PIC X(8) VALUE SPACES."))
            if "dispatch-table" in p.tags:
                L.append(a("01  WS-DISPATCH-TABLE."))
                for i, c in enumerate(dyns, start=1):
                    L.append(b(f"03 FILLER                 PIC X(8) VALUE '{c.target}'.", 2))
                L.append(a("01  WS-DISPATCH REDEFINES WS-DISPATCH-TABLE."))
                L.append(b(f"03 WS-DISPATCH-ENT        PIC X(8) OCCURS {len(dyns)}.", 2))
            L.append("")

        if p.ptype == "data_db2":
            L.append(cmt(" SQL communication area"))
            L.append(b("EXEC SQL INCLUDE SQLCA END-EXEC."))
            L.append("")
            L.append(cmt(" Host variables"))
            L.append(a("01  HV-CUSTOMER-NUM           PIC S9(9) COMP."))
            L.append(a("01  HV-POLICY-NUM             PIC S9(9) COMP."))
            L.append(a("01  HV-ISSUE-DATE             PIC X(10)."))
            L.append(a("01  HV-EXPIRY-DATE            PIC X(10)."))
            L.append(a("01  HV-BROKERID               PIC S9(9) COMP."))
            L.append(a("01  HV-PAYMENT                PIC S9(7)V99 COMP-3."))
            L.append(a("01  HV-LASTCHANGED            PIC X(26)."))
            if "dynamic-sql" in p.tags:
                L.append(a("01  HV-STMT-TEXT              PIC X(254) VALUE SPACES."))
            L.append("")

        if p.ptype == "data_vsam":
            L.append(cmt(" VSAM record areas"))
            for f in p.vsam_files:
                L.append(a(f"01  {f}-REC."))
                L.append(b("03 REC-KEY                PIC 9(10).", 2))
                L.append(b("03 REC-CUSTOMER           PIC 9(10).", 2))
                L.append(b("03 REC-DATA               PIC X(160).", 2))
            L.append(a("01  WS-FILE-LEN               PIC S9(4) COMP VALUE +180."))
            L.append("")

        if p.ptype == "driver" and p.mapset:
            L.append(cmt(" BMS mapset copy"))
            L.append(b(f"COPY {p.mapset}."))
            L.append("")

        return L

    # -- linkage / file section -------------------------------------------
    def linkage(self, p: Prog) -> list[str]:
        L: list[str] = []
        L.extend(banner("L I N K A G E     S E C T I O N"))
        L.append(a("LINKAGE SECTION."))
        L.append(a("01  DFHCOMMAREA."))
        for cb in p.copybooks:
            L.append(b(f"COPY {cb}.", 4))
        return L

    def file_section(self, p: Prog) -> list[str]:
        """Batch programs get real FILE-CONTROL entries -> DDNAME edges."""
        L: list[str] = []
        dds = p.vsam_files or [
            f"DD{p.domain}IN01", f"DD{p.domain}OT01", f"DD{p.domain}RP01"
        ]
        L.append(a("ENVIRONMENT DIVISION."))
        L.append(a("CONFIGURATION SECTION."))
        L.append(a("INPUT-OUTPUT SECTION."))
        L.append(a("FILE-CONTROL."))
        L.append(b(f"SELECT INPUT-FILE  ASSIGN TO {dds[0]}", 4))
        L.append(b("     ORGANIZATION IS SEQUENTIAL", 9))
        L.append(b("     FILE STATUS  IS WS-FILE-STATUS.", 9))
        L.append(b(f"SELECT OUTPUT-FILE ASSIGN TO {dds[1]}", 4))
        L.append(b("     ORGANIZATION IS SEQUENTIAL", 9))
        L.append(b("     FILE STATUS  IS WS-FILE-STATUS.", 9))
        L.append(b(f"SELECT REPORT-FILE ASSIGN TO {dds[2]}", 4))
        L.append(b("     ORGANIZATION IS SEQUENTIAL", 9))
        L.append(b("     FILE STATUS  IS WS-FILE-STATUS.", 9))
        L.append(a("DATA DIVISION."))
        L.append(a("FILE SECTION."))
        for fd, rec in (("INPUT-FILE", "IN-REC"), ("OUTPUT-FILE", "OUT-REC")):
            L.append(a(f"FD  {fd}"))
            L.append(b("RECORDING MODE IS F", 4))
            L.append(b("RECORD CONTAINS 200 CHARACTERS.", 4))
            L.append(a(f"01  {rec}."))
            L.append(b("03 REC-KEY               PIC 9(10).", 2))
            L.append(b("03 REC-CUSTOMER          PIC 9(10).", 2))
            L.append(b("03 REC-PAYLOAD           PIC X(180).", 2))
        L.append(a("FD  REPORT-FILE"))
        L.append(b("RECORDING MODE IS F", 4))
        L.append(b("RECORD CONTAINS 133 CHARACTERS.", 4))
        L.append(a("01  RPT-REC                   PIC X(133)."))
        return L

    # -- procedure paragraphs ---------------------------------------------
    def call_paragraph(self, p: Prog, c: Call, idx: int) -> list[str]:
        """One paragraph per outbound edge, so edges are locatable in source."""
        L: list[str] = []
        para = f"CALL-{c.target}-{idx:03d}"
        L.append(rule())
        L.append(a(f"{para}."))
        if c.kind == KIND_LINK:
            L.append(b(f"EXEC CICS LINK PROGRAM('{c.target}')", 4))
            L.append(b("     COMMAREA(DFHCOMMAREA)", 9))
            L.append(b("     LENGTH(WS-CALEN)", 9))
            L.append(b("     RESP(WS-RESP)", 9))
            L.append(b("END-EXEC.", 4))
        elif c.kind == KIND_LINK_DYN:
            L.append(b(f"MOVE '{c.target}' TO {c.via}", 4))
            L.append(b(f"EXEC CICS LINK PROGRAM({c.via})", 4))
            L.append(b("     COMMAREA(DFHCOMMAREA)", 9))
            L.append(b("     LENGTH(WS-CALEN)", 9))
            L.append(b("     RESP(WS-RESP)", 9))
            L.append(b("END-EXEC.", 4))
        elif c.kind == KIND_XCTL:
            L.append(b(f"EXEC CICS XCTL PROGRAM('{c.target}')", 4))
            L.append(b("     COMMAREA(DFHCOMMAREA)", 9))
            L.append(b("     LENGTH(WS-CALEN)", 9))
            L.append(b("END-EXEC.", 4))
        elif c.kind == KIND_START:
            L.append(b(f"EXEC CICS START TRANSID('{c.transid}')", 4))
            L.append(b("     FROM(WS-KEY-AREA)", 9))
            L.append(b("     LENGTH(20)", 9))
            L.append(b("     RESP(WS-RESP)", 9))
            L.append(b("END-EXEC.", 4))
            L.append(cmt(f" TRANSID {c.transid} is defined against {c.target}"))
        elif c.kind == KIND_CALL:
            L.append(b(f"CALL '{c.target}' USING DFHCOMMAREA", 4))
            L.append(b("     WS-STATUS-CODE.", 9))
        elif c.kind == KIND_CALL_DYN:
            if "dispatch-table" in p.tags:
                L.append(b(f"MOVE {idx} TO WS-SUB", 4))
                L.append(b(f"MOVE WS-DISPATCH-ENT(WS-SUB) TO {c.via}", 4))
            else:
                L.append(b(f"MOVE '{c.target}' TO {c.via}", 4))
            L.append(b(f"CALL {c.via} USING DFHCOMMAREA", 4))
            L.append(b("     WS-STATUS-CODE.", 9))
        L.append(b("IF WS-RESP NOT = DFHRESP(NORMAL)", 4))
        L.append(b(f"   MOVE ' LINK {c.target} FAILED' TO EM-VARIABLE", 4))
        L.append(b("   PERFORM WRITE-ERROR-MESSAGE", 4))
        L.append(b("END-IF.", 4))
        p.paragraphs.append(para)
        return L

    def filler_paragraph(self, p: Prog, n: int) -> list[str]:
        rng = self.rng
        verb = rng.choice(VERBS)
        noun = rng.choice(FIELD_NOUNS)
        para = f"{verb}-{noun}-{n:04d}"
        L = [rule(), a(f"{para}.")]
        style = rng.randint(0, 8)
        if style == 0:
            L.append(b("IF WS-KEY-CUSTOMER = ZERO", 4))
            L.append(b(f"   MOVE ' NO {noun[:14]}' TO EM-VARIABLE", 4))
            L.append(b("   MOVE '01' TO WS-STATUS-CODE", 4))
            L.append(b("ELSE", 4))
            L.append(b("   MOVE '00' TO WS-STATUS-CODE", 4))
            L.append(b("END-IF.", 4))
        elif style == 1:
            L.append(b("EVALUATE TRUE", 4))
            for lo, hi, band in ((0, 999, 1), (1000, 4999, 2), (5000, 24999, 3)):
                L.append(b(f"   WHEN WS-PREMIUM-TOTAL < {hi}", 4))
                L.append(b(f"        MOVE {band} TO WS-PREMIUM-BAND", 4))
            L.append(b("   WHEN OTHER", 4))
            L.append(b("        MOVE 9 TO WS-PREMIUM-BAND", 4))
            L.append(b("END-EVALUATE.", 4))
        elif style == 2:
            L.append(b("COMPUTE WS-PREMIUM-TOTAL ROUNDED =", 4))
            L.append(b("        WS-PREMIUM-TOTAL * 1.075", 8))
            L.append(b(f"      + WS-T-AMOUNT(WS-SUB) / {rng.randint(2, 12)}", 8))
            L.append(b("      - WS-PREMIUM-BAND.", 8))
            L.append(b("IF WS-PREMIUM-TOTAL < ZERO", 4))
            L.append(b("   MOVE ZERO TO WS-PREMIUM-TOTAL", 4))
            L.append(b("END-IF.", 4))
        elif style == 3:
            L.append(b("PERFORM VARYING WS-IX FROM 1 BY 1", 4))
            L.append(b("        UNTIL WS-IX > WS-TABLE-COUNT", 8))
            L.append(b("   ADD WS-T-AMOUNT(WS-IX) TO WS-PREMIUM-TOTAL", 4))
            L.append(b("   IF WS-T-AMOUNT(WS-IX) = ZERO", 4))
            L.append(b("      ADD 1 TO WS-ENTRY-COUNT", 4))
            L.append(b("   END-IF", 4))
            L.append(b("END-PERFORM.", 4))
        elif style == 4:
            L.append(b("MOVE SPACES TO WS-KEY-CHAR.", 4))
            L.append(b("STRING WS-KEY-CUSTOMER DELIMITED BY SIZE", 4))
            L.append(b("       '/'              DELIMITED BY SIZE", 7))
            L.append(b("       WS-KEY-POLICY    DELIMITED BY SIZE", 7))
            L.append(b("       INTO WS-KEY-CHAR", 7))
            L.append(b("END-STRING.", 4))
        elif style == 5:
            L.append(b("UNSTRING WS-KEY-CHAR DELIMITED BY '/'", 4))
            L.append(b("         INTO WS-KEY-CUSTOMER", 9))
            L.append(b("              WS-KEY-POLICY", 9))
            L.append(b("END-UNSTRING.", 4))
        elif style == 6:
            L.append(b("EXEC CICS ASKTIME ABSTIME(ABS-TIME)", 4))
            L.append(b("END-EXEC.", 4))
            L.append(b("EXEC CICS FORMATTIME ABSTIME(ABS-TIME)", 4))
            L.append(b("     MMDDYYYY(DATE1)", 9))
            L.append(b("     TIME(TIME1)", 9))
            L.append(b("END-EXEC.", 4))
        elif style == 7:
            L.append(b(f"MOVE '{noun[:10]}' TO WS-T-AMOUNT(1)", 4))
            L.append(b("SEARCH ALL WS-TABLE-ENTRY", 4))
            L.append(b("   AT END MOVE '01' TO WS-STATUS-CODE", 4))
            L.append(b("   WHEN WS-T-AMOUNT(WS-IX) = WS-PREMIUM-TOTAL", 4))
            L.append(b("        CONTINUE", 4))
            L.append(b("END-SEARCH.", 4))
        else:
            L.append(b("INSPECT WS-KEY-CHAR REPLACING ALL SPACES BY '0'.", 4))
            L.append(b("IF WS-STATUS-FAILED", 4))
            L.append(b("   PERFORM WRITE-ERROR-MESSAGE", 4))
            L.append(b("END-IF.", 4))
        p.paragraphs.append(para)
        return L

    def sql_paragraph(self, p: Prog, n: int) -> list[str]:
        rng = self.rng
        table = rng.choice(p.tables) if p.tables else f"GENA{p.domain}.POLICY"
        para = f"SQL-ACCESS-{n:04d}"
        L = [rule(), a(f"{para}.")]
        mode = rng.randint(0, 3)
        if mode == 0:
            L.append(b("EXEC SQL", 4))
            L.append(b("   SELECT POLICYNUMBER, ISSUEDATE, EXPIRYDATE,", 7))
            L.append(b("          BROKERID, PAYMENT, LASTCHANGED", 7))
            L.append(b("     INTO :HV-POLICY-NUM, :HV-ISSUE-DATE,", 7))
            L.append(b("          :HV-EXPIRY-DATE, :HV-BROKERID,", 7))
            L.append(b("          :HV-PAYMENT, :HV-LASTCHANGED", 7))
            L.append(b(f"     FROM {table}", 7))
            L.append(b("    WHERE CUSTOMERNUMBER = :HV-CUSTOMER-NUM", 7))
            L.append(b("END-EXEC.", 4))
        elif mode == 1:
            L.append(b("EXEC SQL", 4))
            L.append(b(f"   DECLARE C{n:04d} CURSOR FOR", 7))
            L.append(b("   SELECT POLICYNUMBER, PAYMENT", 7))
            L.append(b(f"     FROM {table} A", 7))
            L.append(b(f"     JOIN GENA{p.domain}.CUSTOMER B", 7))
            L.append(b("       ON A.CUSTOMERNUMBER = B.CUSTOMERNUMBER", 7))
            L.append(b("    WHERE A.EXPIRYDATE > :HV-EXPIRY-DATE", 7))
            L.append(b("    ORDER BY A.POLICYNUMBER", 7))
            L.append(b("END-EXEC.", 4))
            L.append(b(f"EXEC SQL OPEN C{n:04d} END-EXEC.", 4))
            L.append(b("PERFORM UNTIL SQLCODE NOT = 0", 4))
            L.append(b(f"   EXEC SQL FETCH C{n:04d}", 4))
            L.append(b("        INTO :HV-POLICY-NUM, :HV-PAYMENT", 9))
            L.append(b("   END-EXEC", 4))
            L.append(b("   ADD HV-PAYMENT TO WS-PREMIUM-TOTAL", 4))
            L.append(b("END-PERFORM.", 4))
            L.append(b(f"EXEC SQL CLOSE C{n:04d} END-EXEC.", 4))
        elif mode == 2:
            L.append(b("EXEC SQL", 4))
            L.append(b(f"   UPDATE {table}", 7))
            L.append(b("      SET PAYMENT = :HV-PAYMENT,", 7))
            L.append(b("          LASTCHANGED = CURRENT TIMESTAMP", 7))
            L.append(b("    WHERE POLICYNUMBER = :HV-POLICY-NUM", 7))
            L.append(b("END-EXEC.", 4))
            L.append(b("IF SQLCODE NOT = 0", 4))
            L.append(b("   MOVE ' SQL UPDATE FAILED' TO EM-VARIABLE", 4))
            L.append(b("   PERFORM WRITE-ERROR-MESSAGE", 4))
            L.append(b("END-IF.", 4))
        else:
            L.append(b("EXEC SQL", 4))
            L.append(b(f"   INSERT INTO {table}", 7))
            L.append(b("          (CUSTOMERNUMBER, POLICYNUMBER,", 7))
            L.append(b("           ISSUEDATE, EXPIRYDATE, PAYMENT)", 7))
            L.append(b("   VALUES (:HV-CUSTOMER-NUM, :HV-POLICY-NUM,", 7))
            L.append(b("           :HV-ISSUE-DATE, :HV-EXPIRY-DATE,", 7))
            L.append(b("           :HV-PAYMENT)", 7))
            L.append(b("END-EXEC.", 4))
        p.paragraphs.append(para)
        return L

    def vsam_paragraph(self, p: Prog, n: int) -> list[str]:
        rng = self.rng
        f = rng.choice(p.vsam_files) if p.vsam_files else f"KSDS{p.domain}01"
        para = f"FILE-ACCESS-{n:04d}"
        L = [rule(), a(f"{para}.")]
        op = rng.choice(["READ", "WRITE", "REWRITE", "DELETE", "BROWSE"])
        if op == "BROWSE":
            L.append(b(f"EXEC CICS STARTBR FILE('{f}')", 4))
            L.append(b("     RIDFLD(WS-KEY-AREA)", 9))
            L.append(b("     RESP(WS-RESP)", 9))
            L.append(b("END-EXEC.", 4))
            L.append(b("PERFORM UNTIL WS-RESP NOT = DFHRESP(NORMAL)", 4))
            L.append(b(f"   EXEC CICS READNEXT FILE('{f}')", 4))
            L.append(b(f"        INTO({f}-REC)", 9))
            L.append(b("        RIDFLD(WS-KEY-AREA)", 9))
            L.append(b("        RESP(WS-RESP)", 9))
            L.append(b("   END-EXEC", 4))
            L.append(b("END-PERFORM.", 4))
            L.append(b(f"EXEC CICS ENDBR FILE('{f}') END-EXEC.", 4))
        else:
            L.append(b(f"EXEC CICS {op} FILE('{f}')", 4))
            if op in ("READ", "REWRITE", "WRITE"):
                kw = "INTO" if op == "READ" else "FROM"
                L.append(b(f"     {kw}({f}-REC)", 9))
                L.append(b("     LENGTH(WS-FILE-LEN)", 9))
            L.append(b("     RIDFLD(WS-KEY-AREA)", 9))
            L.append(b("     RESP(WS-RESP)", 9))
            L.append(b("END-EXEC.", 4))
            L.append(b("EVALUATE WS-RESP", 4))
            L.append(b("   WHEN DFHRESP(NORMAL)", 4))
            L.append(b("        MOVE '00' TO WS-STATUS-CODE", 4))
            L.append(b("   WHEN DFHRESP(NOTFND)", 4))
            L.append(b("        MOVE '01' TO WS-STATUS-CODE", 4))
            L.append(b("   WHEN DFHRESP(DUPREC)", 4))
            L.append(b("        MOVE '02' TO WS-STATUS-CODE", 4))
            L.append(b("   WHEN OTHER", 4))
            L.append(b("        MOVE '90' TO WS-STATUS-CODE", 4))
            L.append(b("        PERFORM WRITE-ERROR-MESSAGE", 4))
            L.append(b("END-EVALUATE.", 4))
        p.paragraphs.append(para)
        return L

    def map_paragraph(self, p: Prog, n: int) -> list[str]:
        para = f"SEND-RECEIVE-MAP-{n:04d}"
        L = [rule(), a(f"{para}.")]
        L.append(b(f"EXEC CICS SEND MAP('{p.mapset[:7]}I')", 4))
        L.append(b(f"     MAPSET('{p.mapset}')", 9))
        L.append(b("     ERASE", 9))
        L.append(b("     RESP(WS-RESP)", 9))
        L.append(b("END-EXEC.", 4))
        L.append(b(f"EXEC CICS RECEIVE MAP('{p.mapset[:7]}I')", 4))
        L.append(b(f"     MAPSET('{p.mapset}')", 9))
        L.append(b("     RESP(WS-RESP)", 9))
        L.append(b("END-EXEC.", 4))
        p.paragraphs.append(para)
        return L

    def error_paragraph(self, p: Prog) -> list[str]:
        hub = getattr(self.topo, "hubs", None)
        L = [rule(), a("WRITE-ERROR-MESSAGE.")]
        L.append(b("EXEC CICS ASKTIME ABSTIME(ABS-TIME) END-EXEC.", 4))
        L.append(b("EXEC CICS FORMATTIME ABSTIME(ABS-TIME)", 4))
        L.append(b("     MMDDYYYY(EM-DATE)", 9))
        L.append(b("     TIME(EM-TIME)", 9))
        L.append(b("END-EXEC.", 4))
        if hub and p.ptype != "util":
            L.append(b(f"EXEC CICS LINK PROGRAM('{hub[0]}')", 4))
            L.append(b("     COMMAREA(ERROR-MSG)", 9))
            L.append(b("     LENGTH(45)", 9))
            L.append(b("END-EXEC.", 4))
        else:
            L.append(b("EXEC CICS WRITEQ TD QUEUE('CSMT')", 4))
            L.append(b("     FROM(ERROR-MSG)", 9))
            L.append(b("     LENGTH(45)", 9))
            L.append(b("END-EXEC.", 4))
        p.paragraphs.append("WRITE-ERROR-MESSAGE")
        return L

    # -- pathological bodies ----------------------------------------------
    def pathological_body(self, p: Prog) -> list[str]:
        tag = p.tags[0]
        L: list[str] = []
        if tag == "deep-nesting":
            L.extend([rule(), a("DEEP-NESTING.")])
            depth = 120
            for i in range(depth):
                L.append(b(f"IF WS-T-AMOUNT({(i % 250) + 1}) > {i}", 4 + min(i, 40)))
            L.append(b("CONTINUE", 4 + 40))
            for _ in range(depth):
                L.append(b("END-IF", 4 + 40))
            L.append(b(".", 4))
            p.paragraphs.append("DEEP-NESTING")
        elif tag == "goto-spaghetti":
            for i in range(40):
                L.extend([rule(), a(f"SPAGHETTI-{i:03d}.")])
                L.append(b(f"ADD {i} TO WS-ENTRY-COUNT.", 4))
                nxt = (i * 7 + 3) % 40
                L.append(b(f"IF WS-ENTRY-COUNT > {i * 3}", 4))
                L.append(b(f"   GO TO SPAGHETTI-{nxt:03d}", 4))
                L.append(b("END-IF.", 4))
                p.paragraphs.append(f"SPAGHETTI-{i:03d}")
            L.extend([rule(), a("SPAGHETTI-SWITCH.")])
            L.append(b("GO TO SPAGHETTI-000 SPAGHETTI-007 SPAGHETTI-014", 4))
            L.append(b("       SPAGHETTI-021 SPAGHETTI-028", 4))
            L.append(b("       DEPENDING ON WS-PREMIUM-BAND.", 4))
            p.paragraphs.append("SPAGHETTI-SWITCH")
        elif tag == "altered-goto":
            L.extend([rule(), a("ALTERED-ENTRY.")])
            L.append(b("GO TO ALT-TARGET-A.", 4))
            L.extend([rule(), a("ALT-TARGET-A."), b("CONTINUE.", 4)])
            L.extend([rule(), a("ALT-TARGET-B."), b("CONTINUE.", 4)])
            L.extend([rule(), a("ALTER-CONTROL.")])
            L.append(b("ALTER ALTERED-ENTRY TO PROCEED TO ALT-TARGET-B.", 4))
            p.paragraphs.extend(
                ["ALTERED-ENTRY", "ALT-TARGET-A", "ALT-TARGET-B", "ALTER-CONTROL"]
            )
        elif tag == "dynamic-sql":
            L.extend([rule(), a("DYNAMIC-SQL.")])
            table = p.tables[0] if p.tables else "GENAMT.POLICY"
            L.append(b("MOVE 'SELECT COUNT(*) FROM ' TO HV-STMT-TEXT.", 4))
            L.append(b(f"STRING HV-STMT-TEXT DELIMITED BY SPACE", 4))
            L.append(b(f"       '{table}' DELIMITED BY SIZE", 7))
            L.append(b("       INTO HV-STMT-TEXT", 7))
            L.append(b("END-STRING.", 4))
            L.append(b("EXEC SQL", 4))
            L.append(b("   PREPARE DYNSTMT FROM :HV-STMT-TEXT", 7))
            L.append(b("END-EXEC.", 4))
            L.append(b("EXEC SQL", 4))
            L.append(b("   EXECUTE DYNSTMT", 7))
            L.append(b("END-EXEC.", 4))
            p.paragraphs.append("DYNAMIC-SQL")
        return L

    # -- whole program -----------------------------------------------------
    def program(self, p: Prog) -> str:
        rng = self.rng
        L: list[str] = []
        dom_name, dom_desc = DOMAINS[p.domain]
        L.extend(banner(f"{p.name} - {dom_desc.upper()}"))
        L.append(cmt())
        L.append(cmt(f"  Generated volume-test source. Layer {p.layer},"))
        L.append(cmt(f"  type {p.ptype}, domain {dom_name}."))
        if p.tags:
            L.append(cmt(f"  Tags: {', '.join(p.tags)}"))
        L.append(cmt("*" * 65))
        L.append(a("IDENTIFICATION DIVISION."))
        L.append(a(f"PROGRAM-ID. {p.name}."))
        L.append(a("AUTHOR. VOLUME GENERATOR."))
        if p.ptype == "batch":
            L.extend(self.file_section(p))
        else:
            L.append(a("ENVIRONMENT DIVISION."))
            L.append(a("CONFIGURATION SECTION."))
            L.append(a("DATA DIVISION."))
        L.extend(self.working_storage(p))
        if p.ptype == "batch":
            L.append(a("01  WS-FILE-STATUS            PIC X(2) VALUE SPACES."))
            L.append(a("01  WS-EOF-FLAG               PIC X    VALUE 'N'."))
            L.append(b("88 WS-EOF                   VALUE 'Y'.", 4))
        else:
            L.extend(self.linkage(p))

        # ---- procedure division
        L.extend(banner("P R O C E D U R E S"))
        L.append(a("PROCEDURE DIVISION."))
        L.append(rule())
        L.append(a("MAINLINE SECTION."))
        L.append(b("INITIALIZE WS-HEADER.", 4))
        if p.ptype != "batch":
            L.append(b("MOVE EIBTRNID TO WS-TRANSID.", 4))
            L.append(b("MOVE EIBTRMID TO WS-TERMID.", 4))
            L.append(b("MOVE EIBTASKN TO WS-TASKNUM.", 4))
            L.append(b("IF EIBCALEN IS EQUAL TO ZERO", 4))
            L.append(b("   MOVE ' NO COMMAREA RECEIVED' TO EM-VARIABLE", 4))
            L.append(b("   PERFORM WRITE-ERROR-MESSAGE", 4))
            L.append(b(f"   EXEC CICS ABEND ABCODE('{rng.choice(ABEND_CODES)}')", 4))
            L.append(b("        NODUMP END-EXEC", 9))
            L.append(b("END-IF.", 4))
            L.append(b("MOVE EIBCALEN TO WS-CALEN.", 4))
            L.append(b("SET WS-ADDR-COMMAREA TO ADDRESS OF DFHCOMMAREA.", 4))
        else:
            L.append(b("OPEN INPUT  INPUT-FILE.", 4))
            L.append(b("OPEN OUTPUT OUTPUT-FILE.", 4))
            L.append(b("OPEN OUTPUT REPORT-FILE.", 4))

        # body paragraphs are emitted after mainline; collect PERFORM list
        body: list[str] = []
        performs: list[str] = []

        if p.ptype == "pathological":
            body.extend(self.pathological_body(p))

        for i, c in enumerate(p.calls, start=1):
            para_lines = self.call_paragraph(p, c, i)
            # record the source line of the CICS/CALL verb for ground truth
            c.line = 0  # patched below once the full listing is assembled
            body.append(f"\x00CALL{i}")   # placeholder marker, replaced later
            body.extend(para_lines)
            performs.append(p.paragraphs[-1])

        n = 0
        while True:
            approx = len(L) + len(body) + len(performs) + 40
            if approx >= p.target_lines:
                break
            n += 1
            if p.ptype == "data_db2" and n % 3 == 0:
                body.extend(self.sql_paragraph(p, n))
            elif p.ptype == "data_vsam" and n % 3 == 0:
                body.extend(self.vsam_paragraph(p, n))
            elif p.ptype == "driver" and p.mapset and n % 7 == 0:
                body.extend(self.map_paragraph(p, n))
            else:
                body.extend(self.filler_paragraph(p, n))
            performs.append(p.paragraphs[-1])

        # leave a slice of paragraphs unreferenced -> genuine dead code
        live = list(performs)
        dead: list[str] = []
        if len(live) > 6 and self.cfg.dead_code_rate > 0:
            k = max(1, int(len(live) * self.cfg.dead_code_rate))
            for _ in range(k):
                dead.append(live.pop(rng.randrange(len(live))))
        p.dead_paragraphs = dead

        for para in live:
            L.append(b(f"PERFORM {para}.", 4))

        if p.ptype == "batch":
            L.append(b("PERFORM UNTIL WS-EOF", 4))
            L.append(b("   READ INPUT-FILE", 4))
            L.append(b("        AT END MOVE 'Y' TO WS-EOF-FLAG", 4))
            L.append(b("   END-READ", 4))
            L.append(b("   IF NOT WS-EOF", 4))
            L.append(b("      WRITE OUT-REC FROM IN-REC", 4))
            L.append(b("      ADD 1 TO WS-ENTRY-COUNT", 4))
            L.append(b("   END-IF", 4))
            L.append(b("END-PERFORM.", 4))
            L.append(b("CLOSE INPUT-FILE OUTPUT-FILE REPORT-FILE.", 4))
            L.append(b("GOBACK.", 4))
        else:
            L.append(b("EXEC CICS RETURN END-EXEC.", 4))

        L.extend(body)
        L.extend(self.error_paragraph(p))
        L.append(rule())
        L.append(a(f"END PROGRAM {p.name}."))

        # resolve placeholder markers into real line numbers
        out: list[str] = []
        for line in L:
            if line.startswith("\x00CALL"):
                idx = int(line[5:])
                # the verb sits 2 lines after the paragraph header we are about
                # to emit (rule + header), i.e. current length + 3
                p.calls[idx - 1].line = len(out) + 3
                continue
            out.append(line)

        if self.cfg.seqnums:
            out = [
                f"{(i + 1) * 100 % 1000000:06d}{ln[6:]:<66}"[:80].rstrip()
                if ln else ln
                for i, ln in enumerate(out)
            ]

        if "lowercase-tabs" in p.tags:
            out = [
                ln.lower().replace("          ", "\t", 1) if i % 3 == 0 else ln
                for i, ln in enumerate(out)
            ]

        p.lines = len(out)
        return "\n".join(out) + "\n"

    # -- copybooks ---------------------------------------------------------
    def copybook(self, cb: Copybook) -> str:
        rng = self.rng
        L: list[str] = []
        L.extend(banner(f"COPYBOOK {cb.name} ({cb.kind})"))
        for dep in cb.copies:
            L.append(b(f"COPY {dep}.", 4))
        if cb.kind == "commarea":
            L.append(b("03 CA-REQUEST-ID            PIC X(6).", 4))
            L.append(b("03 CA-RETURN-CODE           PIC 9(2).", 4))
            L.append(b("03 CA-CUSTOMER-NUM          PIC 9(10).", 4))
            L.append(b("03 CA-REQUEST-SPECIFIC      PIC X(32482).", 4))
            L.append(b("03 CA-POLICY-REQUEST REDEFINES", 4))
            L.append(b("                       CA-REQUEST-SPECIFIC.", 4))
            L.append(b("05 CA-POLICY-NUM         PIC 9(10).", 7))
            L.append(b("05 CA-POLICY-COMMON.", 7))
            L.append(b("07 CA-ISSUE-DATE      PIC X(10).", 10))
            L.append(b("07 CA-EXPIRY-DATE     PIC X(10).", 10))
            L.append(b("07 CA-LASTCHANGED     PIC X(26).", 10))
            L.append(b("07 CA-BROKERID        PIC 9(10).", 10))
            L.append(b("07 CA-PAYMENT         PIC 9(6).", 10))
            L.append(b("05 CA-POLICY-FILLER      PIC X(32400).", 7))
        elif cb.kind == "constants":
            L.append(a("01  WS-CONSTANTS."))
            for i, e in enumerate(ENTITIES):
                L.append(b(f"03 K-{e:<12}         PIC X(10) VALUE '{e[:10]}'.", 2))
            L.append(a("01  K-VAT-RATE                PIC S9V999 VALUE +0.210."))
            L.append(a("01  K-MAX-ROWS                PIC S9(4) COMP VALUE +250."))
        else:
            dom_name = DOMAINS.get(cb.domain, ("GEN", ""))[0]
            L.append(b(f"03 {cb.name}-REC.", 4))
            for nf in rng.sample(FIELD_NOUNS, min(10, len(FIELD_NOUNS))):
                nm = nf[:16]
                pic = rng.choice(
                    ["PIC X(20).", "PIC 9(8).", "PIC S9(7)V99 COMP-3.",
                     "PIC X(10).", "PIC S9(4) COMP."]
                )
                L.append(b(f"05 {dom_name[:4]}-{nm:<16} {pic}", 7))
            L.append(b(f"05 {dom_name[:4]}-TABLE OCCURS 12 TIMES.", 7))
            L.append(b("07 TAB-MONTH             PIC 9(2).", 10))
            L.append(b("07 TAB-AMOUNT            PIC S9(7)V99 COMP-3.", 10))
        cb.lines = len(L)
        return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# Non-COBOL artefacts: BMS, JCL, CSD, DDL
# ---------------------------------------------------------------------------


def _bms_cont(line: str) -> str:
    """Mark an assembler statement as continued: non-blank in column 72.

    HLASM requires this; without it a statement whose operands run onto the next
    line is a syntax error. The reference ssmap.bms puts an X there.
    """
    if len(line) >= 71:
        line = line[:71]
    return f"{line:<71}X"


def bms_mapset(name: str) -> str:
    L = [
        _bms_cont(f"{name:<9}DFHMSD TYPE=MAP,MODE=INOUT,CTRL=(FREEKB,FRSET),"),
        "               LANG=COBOL,STORAGE=AUTO,TIOAPFX=YES",
        f"{name[:7]}I DFHMDI SIZE=(24,80)",
        _bms_cont("         DFHMDF POS=(1,1),LENGTH=20,ATTRB=(PROT),"),
        "               INITIAL='GENERATED VOLUME TEST'",
        # Deliberately NOT named TITLE. TITLE is an HLASM instruction, so a
        # lexer that matches keywords without regard to column position
        # mis-reads it -- a real bug class, but one the reference application
        # never triggers, which makes it an accidental trap rather than a
        # documented test. See the mnemonic-collision mapset below for the
        # labelled version.
        "HDRTTL   DFHMDF POS=(3,1),LENGTH=40,ATTRB=(PROT)",
        "CUSTNO   DFHMDF POS=(5,20),LENGTH=10,ATTRB=(UNPROT,IC)",
        "POLNO    DFHMDF POS=(6,20),LENGTH=10,ATTRB=(UNPROT)",
        "PREMIUM  DFHMDF POS=(7,20),LENGTH=9,ATTRB=(UNPROT)",
        "ERRMSG   DFHMDF POS=(23,1),LENGTH=70,ATTRB=(PROT,BRT)",
        "         DFHMSD TYPE=FINAL",
        "         END",
    ]
    return "\n".join(L) + "\n"


def bms_mnemonic_collision(name: str) -> str:
    """A mapset whose field names collide with HLASM instruction mnemonics.

    Field names live in the name field (column 1); the operation field follows.
    A lexer that recognises keywords by token match rather than by column
    position will mis-read every one of these. Real shops do produce names like
    this, so it is worth testing -- but as a labelled case, not by accident.
    """
    L = [
        _bms_cont(f"{name:<9}DFHMSD TYPE=MAP,MODE=INOUT,CTRL=(FREEKB,FRSET),"),
        "               LANG=COBOL,STORAGE=AUTO,TIOAPFX=YES",
        f"{name[:7]}I DFHMDI SIZE=(24,80)",
        "TITLE    DFHMDF POS=(1,1),LENGTH=20,ATTRB=(PROT)",
        "START    DFHMDF POS=(2,1),LENGTH=10,ATTRB=(UNPROT)",
        "END      DFHMDF POS=(3,1),LENGTH=10,ATTRB=(UNPROT)",
        "COPY     DFHMDF POS=(4,1),LENGTH=10,ATTRB=(UNPROT)",
        "EQU      DFHMDF POS=(5,1),LENGTH=10,ATTRB=(UNPROT)",
        "USING    DFHMDF POS=(6,1),LENGTH=10,ATTRB=(UNPROT)",
        "SPACE    DFHMDF POS=(7,1),LENGTH=10,ATTRB=(UNPROT)",
        "PRINT    DFHMDF POS=(8,1),LENGTH=10,ATTRB=(UNPROT)",
        "         DFHMSD TYPE=FINAL",
        "         END",
    ]
    return "\n".join(L) + "\n"


def jcl_job(job: str, steps: list[tuple[str, str, list[str]]]) -> str:
    L = [
        f"//{job:<8} JOB (ACCT),'VOLUME TEST',CLASS=A,MSGCLASS=H,",
        "//         NOTIFY=&SYSUID,REGION=0M",
        "//*",
        "//* Generated volume-test JCL",
        "//*",
    ]
    for step, pgm, dds in steps:
        L.append(f"//{step:<8} EXEC PGM={pgm}")
        L.append("//STEPLIB  DD DISP=SHR,DSN=GENAPP.LOADLIB")
        for dd in dds:
            L.append(f"//{dd:<8} DD DISP=SHR,DSN=GENAPP.{dd}.DATA")
        L.append("//SYSOUT   DD SYSOUT=*")
        L.append("//SYSPRINT DD SYSOUT=*")
        L.append("//*")
    L.append("//")
    return "\n".join(L) + "\n"


def csd_defines(progs: list[Prog], group: str = "GENAVOL") -> str:
    L = [
        "*" * 60,
        "* Generated CSD input: transaction -> program bindings",
        "*" * 60,
        f"Add    Group({group})        List(GENAVLST)",
        "",
        "***** Transactions",
    ]
    for p in progs:
        if p.transid:
            L.append(f"Define Transaction({p.transid}) Group({group})")
            L.append(f"       Program({p.name}) TaskDataLoc(Any) TaskDataKey(User)")
    L.append("")
    L.append("***** Programs")
    for p in progs:
        if p.ptype in ("driver", "business", "data_db2", "data_vsam", "util"):
            L.append(f"Define Program({p.name}) Group({group})")
            L.append("       Language(Cobol) DataLocation(Any) Concurrency(Threadsafe)")
    return "\n".join(L) + "\n"


def ddl(tables: set[str]) -> str:
    L = ["-- Generated DDL for volume-test schema", ""]
    schemas = sorted({t.split(".")[0] for t in tables})
    for s in schemas:
        L.append(f"CREATE DATABASE {s};")
    L.append("")
    for t in sorted(tables):
        L.append(f"CREATE TABLE {t} (")
        L.append("  CUSTOMERNUMBER  INTEGER      NOT NULL,")
        L.append("  POLICYNUMBER    INTEGER      NOT NULL,")
        L.append("  ISSUEDATE       DATE         NOT NULL,")
        L.append("  EXPIRYDATE      DATE         NOT NULL,")
        L.append("  BROKERID        INTEGER               ,")
        L.append("  PAYMENT         DECIMAL(9,2)          ,")
        L.append("  LASTCHANGED     TIMESTAMP    NOT NULL,")
        L.append("  PRIMARY KEY (POLICYNUMBER)")
        L.append(");")
        L.append(f"CREATE INDEX {t.split('.')[1][:8]}X1")
        L.append(f"  ON {t} (CUSTOMERNUMBER);")
        L.append("")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# Graph metrics for the ground-truth manifest
# ---------------------------------------------------------------------------


def graph_metrics(topo: Topology) -> dict:
    jobs = getattr(topo, "jobs", [])
    # JCL jobs are graph nodes too: without the EXEC PGM= edge a batch module
    # looks unreachable, which would make the orphan count meaningless.
    names = list(topo.progs) + [j["name"] for j in jobs]
    idx = {n: i for i, n in enumerate(names)}
    adj: list[list[int]] = [[] for _ in names]
    edge_kinds: dict[str, int] = {}
    edges = 0
    for p in topo.order:
        for c in p.calls:
            if c.target in idx:
                adj[idx[p.name]].append(idx[c.target])
            edge_kinds[c.kind] = edge_kinds.get(c.kind, 0) + 1
            edges += 1
    for j in jobs:
        for st in j["steps"]:
            if st["program"] in idx:
                adj[idx[j["name"]]].append(idx[st["program"]])
            edge_kinds[KIND_JCL] = edge_kinds.get(KIND_JCL, 0) + 1
            edges += 1

    indeg = [0] * len(names)
    for u, vs in enumerate(adj):
        for v in vs:
            indeg[v] += 1

    # Tarjan SCC, iterative to survive deep graphs
    index = [None] * len(names)
    low = [0] * len(names)
    on_stack = [False] * len(names)
    stack: list[int] = []
    counter = 0
    sccs: list[list[int]] = []
    for root in range(len(names)):
        if index[root] is not None:
            continue
        work = [(root, 0)]
        while work:
            v, pi = work[-1]
            if pi == 0:
                index[v] = low[v] = counter
                counter += 1
                stack.append(v)
                on_stack[v] = True
            recurse = False
            for i in range(pi, len(adj[v])):
                w = adj[v][i]
                if index[w] is None:
                    work[-1] = (v, i + 1)
                    work.append((w, 0))
                    recurse = True
                    break
                elif on_stack[w]:
                    low[v] = min(low[v], index[w])
            if recurse:
                continue
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    comp.append(w)
                    if w == v:
                        break
                sccs.append(comp)
            work.pop()
            if work:
                u, _ = work[-1]
                low[u] = min(low[u], low[v])

    self_loops = {u for u, vs in enumerate(adj) if u in vs}
    cycles = [
        sorted(names[i] for i in comp)
        for comp in sccs
        if len(comp) > 1 or comp[0] in self_loops
    ]

    # longest path (depth) with memoisation; cycle members capped
    in_cycle = {i for comp in sccs if len(comp) > 1 for i in comp} | self_loops
    depth_memo: dict[int, int] = {}

    def depth(v: int, seen: frozenset) -> int:
        if v in depth_memo and v not in in_cycle:
            return depth_memo[v]
        best = 0
        for w in adj[v]:
            if w in seen:
                continue
            best = max(best, 1 + depth(w, seen | {w}))
        if v not in in_cycle:
            depth_memo[v] = best
        return best

    sys.setrecursionlimit(20000)
    roots = [i for i in range(len(names)) if indeg[i] == 0]
    max_depth = 0
    for r in roots[:400]:  # sampling keeps this cheap on huge estates
        max_depth = max(max_depth, depth(r, frozenset({r})))

    order_in = sorted(range(len(names)), key=lambda i: -indeg[i])
    hubs = [
        {"program": names[i], "fan_in": indeg[i]}
        for i in order_in[:15]
        if indeg[i] > 0
    ]
    # An online driver reached only via its TRANSID, and a job, are entry
    # points by design. Everything else with no callers is genuinely dead.
    job_names = {j["name"] for j in jobs}
    entry_points = sorted(
        p.name for p in topo.order if p.ptype == "driver" and p.transid
    )
    entry_set = set(entry_points) | job_names
    unreached = [names[i] for i in range(len(names)) if indeg[i] == 0]
    orphans = sorted(n for n in unreached if n not in entry_set)

    cb_fan_in: dict[str, int] = {}
    for p in topo.order:
        for cb in p.copybooks:
            cb_fan_in[cb] = cb_fan_in.get(cb, 0) + 1

    # Domain coupling, reported twice: with and without edges into a shared
    # utility. Hub edges are legitimately dense -- an error-logging module
    # called from everywhere is the LGSTSQ pattern -- but they are
    # infrastructure, not subsystem coupling, and mixing them in makes the
    # coupling graph look complete. A tool worth its salt separates them.
    hub_set = set(getattr(topo, "hubs", []))
    dom_of = {p.name: p.domain for p in topo.order}
    dom_size: dict[str, int] = {}
    for p in topo.order:
        dom_size[p.domain] = dom_size.get(p.domain, 0) + 1
    pairs_all: dict[str, int] = {}
    pairs_biz: dict[str, int] = {}
    intra = inter = 0
    for p in topo.order:
        for c in p.calls:
            target_dom = dom_of.get(c.target)
            if target_dom is None:
                continue
            if target_dom == p.domain:
                intra += 1
                continue
            inter += 1
            key = "|".join(sorted((p.domain, target_dom)))
            pairs_all[key] = pairs_all.get(key, 0) + 1
            if c.target not in hub_set:
                pairs_biz[key] = pairs_biz.get(key, 0) + 1
    n_dom = len(dom_size)
    possible = n_dom * (n_dom - 1) // 2 or 1
    coupling = {
        "domains": n_dom,
        "domain_sizes": dict(sorted(dom_size.items(), key=lambda kv: -kv[1])),
        "intra_domain_edges": intra,
        "inter_domain_edges": inter,
        "possible_domain_pairs": possible,
        "connected_pairs_all": len(pairs_all),
        "connected_pairs_excluding_hubs": len(pairs_biz),
        "density_all": round(len(pairs_all) / possible, 4),
        "density_excluding_hubs": round(len(pairs_biz) / possible, 4),
        "pair_weights_excluding_hubs": dict(
            sorted(pairs_biz.items(), key=lambda kv: -kv[1])),
        "note": "density_all is inflated by shared utilities, which every "
                "domain calls by design. Score subsystem coupling against "
                "density_excluding_hubs; the hub list is injected.hubs.",
    }

    resolvable = sum(n for k, n in edge_kinds.items() if RESOLVABLE.get(k, True))
    return {
        "nodes": len(names),
        "program_nodes": len(topo.progs),
        "job_nodes": len(jobs),
        "edges": edges,
        "edges_by_kind": edge_kinds,
        "edges_statically_resolvable": resolvable,
        "edges_requiring_dataflow_or_csd": edges - resolvable,
        "cycles": cycles,
        "cycle_count": len(cycles),
        "entry_points": entry_points,
        "entry_point_count": len(entry_set),
        "orphans_count": len(orphans),
        "orphans_sample": orphans[:50],
        "max_call_depth_sampled": max_depth,
        "hubs": hubs,
        "copybook_fan_in_top": sorted(
            ({"copybook": k, "fan_in": v} for k, v in cb_fan_in.items()),
            key=lambda d: -d["fan_in"],
        )[:10],
        "coupling": coupling,
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Generate volumes of realistic COBOL for analysis-tool testing."
    )
    ap.add_argument("-n", "--programs", type=int, default=2000)
    ap.add_argument("-o", "--out", default="generated")
    ap.add_argument("--seed", type=int, default=20260729)
    ap.add_argument("--avg-lines", type=int, default=600,
                    help="median-ish program size; distribution is log-normal")
    ap.add_argument("--domains", type=int, default=10,
                    help=f"business subsystems, max {len(DOMAINS)}. Scale with "
                         "-n: ten domains at 100k programs means ten blobs of "
                         "10.000, which no real estate looks like")
    ap.add_argument("--partners", type=int, nargs=2, default=(2, 5),
                    metavar=("MIN", "MAX"),
                    help="how many other domains each domain calls into. Keeps "
                         "the coupling graph sparse and uneven instead of "
                         "complete and uniform")
    ap.add_argument("--drivers-per-mapset", type=int, default=8,
                    help="online drivers sharing one BMS mapset")
    ap.add_argument("--copybooks-per-domain", type=int, default=12)
    ap.add_argument("--hub-density", type=float, default=0.55,
                    help="fraction of programs that LINK to a shared utility")
    ap.add_argument("--hubs", type=int, default=6,
                    help="number of high-fan-in utilities; deliberately does "
                         "NOT scale with -n, so hubs stay real hubs")
    ap.add_argument("--dynamic-rate", type=float, default=0.12,
                    help="fraction of edges rewritten as dynamic dispatch")
    ap.add_argument("--dead-code-rate", type=float, default=0.08)
    ap.add_argument("--cycles", type=int, default=12)
    ap.add_argument("--chains", type=int, default=6)
    ap.add_argument("--chain-depth", type=int, default=18)
    ap.add_argument("--pathological", type=int, default=10,
                    help="number of deliberately hostile programs (0 disables)")
    ap.add_argument("--boundary", type=int, default=0,
                    help="programs sized to straddle a context window "
                         "(e.g. 14 for two passes over the 7 multiples)")
    ap.add_argument("--window-tokens", type=int, default=200_000,
                    help="target model context window, in tokens")
    ap.add_argument("--chars-per-token", type=float, default=3.5,
                    help="MEASURE THIS on your corpus with count_tokens; the "
                         "default is a placeholder, not a calibrated value")
    ap.add_argument("--window-reserve", type=float, default=0.15,
                    help="share of the window reserved for prompt and output")
    ap.add_argument("--fit-window", action="store_true",
                    help="cap EVERY program below --window-tokens: shrinks the "
                         "giant and keeps only the boundary multiples that fit. "
                         "Use until an over-window fallback exists, then drop it")
    ap.add_argument("--layout", choices=["domain", "flat"], default="domain")
    ap.add_argument("--seqnums", action="store_true",
                    help="emit sequence numbers in cols 73-80 (z/OS export style)")
    ap.add_argument("--no-artefacts", action="store_true",
                    help="skip JCL/BMS/CSD/DDL, emit COBOL only")
    ap.add_argument("--gzip-manifest", action="store_true",
                    help="write manifest.json.gz instead of manifest.json. "
                         "Needed above ~90k programs, where the plain manifest "
                         "exceeds GitHub's 100 MiB per-file limit")
    cfg = ap.parse_args(argv)

    rng = random.Random(cfg.seed)
    topo = Topology(rng, cfg)
    topo.build()

    em = Emitter(rng, topo, cfg)
    out = os.path.abspath(cfg.out)
    total_lines = 0
    total_bytes = 0

    # copybooks first: programs reference them
    for cb in topo.copybooks.values():
        sub = "copybook" if cfg.layout == "flat" else f"copybook/{cb.domain}"
        cb.path = os.path.join(sub, f"{cb.name}.cpy")
        text = em.copybook(cb)
        write(os.path.join(out, cb.path), text)
        total_lines += cb.lines
        total_bytes += len(text)

    for i, p in enumerate(topo.order, start=1):
        sub = "src" if cfg.layout == "flat" else f"src/{p.domain}"
        p.path = os.path.join(sub, f"{p.name}.cbl")
        text = em.program(p)
        write(os.path.join(out, p.path), text)
        total_lines += p.lines
        total_bytes += len(text)
        if i % 250 == 0:
            print(f"  {i}/{len(topo.order)} programs, {total_lines:,} lines",
                  file=sys.stderr)

    # duplicate PROGRAM-ID in a second directory: a real-world trap
    dup = [p for p in topo.order if "duplicate-name" in p.tags]
    for p in dup:
        with open(os.path.join(out, p.path), encoding="utf-8") as fh:
            body = fh.read()
        write(os.path.join(out, "src/attic", f"{p.name}.cbl"), body)

    if not cfg.no_artefacts:
        mapsets = sorted({p.mapset for p in topo.order if p.mapset})
        for ms in mapsets:
            write(os.path.join(out, "bms", f"{ms}.bms"), bms_mapset(ms))
        if mapsets and cfg.pathological:
            # One labelled mnemonic-collision mapset, recorded in the manifest.
            write(os.path.join(out, "bms", "ZZMNEMON.bms"),
                  bms_mnemonic_collision("ZZMNEMON"))
        for j in topo.jobs:
            steps = [
                (st["step"], st["program"], st["dd_names"]) for st in j["steps"]
            ]
            write(os.path.join(out, "jcl", f"{j['name']}.jcl"),
                  jcl_job(j["name"], steps))
        write(os.path.join(out, "cntl", "csdvol.txt"), csd_defines(topo.order))
        tables = {t for p in topo.order for t in p.tables}
        if tables:
            write(os.path.join(out, "ddl", "schema.sql"), ddl(tables))

    metrics = graph_metrics(topo)
    hub_names = set(getattr(topo, "hubs", []))
    manifest = {
        "config": vars(cfg),
        "totals": {
            "programs": len(topo.order),
            "copybooks": len(topo.copybooks),
            "source_lines": total_lines,
            "source_bytes": total_bytes,
        },
        "expected_graph": metrics,
        "injected": {
            "cycles": getattr(topo, "cycles", []),
            "chains": getattr(topo, "chain_count", 0),
            "chain_depth": cfg.chain_depth,
            "dynamic_edges": getattr(topo, "dynamic_edges", 0),
            "hubs": getattr(topo, "hubs", []),
            "pathological": getattr(topo, "pathological", []),
            "boundary": getattr(topo, "boundary", []),
            "bms_mnemonic_collision": (
                "bms/ZZMNEMON.bms" if not cfg.no_artefacts and cfg.pathological
                else None
            ),
        },
        "window_cap": {
            "applied": bool(topo.window_budget_chars),
            "window_tokens": cfg.window_tokens,
            "chars_per_token": cfg.chars_per_token,
            "budget_chars": topo.window_budget_chars,
            "note": (
                "every program fits one context window; no over-window case is"
                " present" if topo.window_budget_chars else
                "corpus is uncapped and contains over-window programs"
            ),
        },
        "programs": [
            {
                "name": p.name,
                "file": p.path,
                "type": p.ptype,
                "domain": p.domain,
                "layer": p.layer,
                "lines": p.lines,
                "transid": p.transid,
                "mapset": p.mapset,
                "copybooks": p.copybooks,
                "tables": p.tables,
                "files": p.vsam_files,
                "tags": p.tags,
                "paragraph_count": len(p.paragraphs),
                "dead_paragraphs": p.dead_paragraphs,
                "calls": [
                    {
                        "target": c.target,
                        "kind": c.kind,
                        "line": c.line,
                        "via": c.via,
                        "transid": c.transid,
                        "statically_resolvable": RESOLVABLE.get(c.kind, True),
                        # Infrastructure edge, not subsystem coupling. Filter
                        # these out when scoring clustering or impact analysis.
                        "to_hub": c.target in hub_names,
                    }
                    for c in p.calls
                ],
            }
            for p in topo.order
        ],
        "copybooks": [
            {"name": cb.name, "file": cb.path, "kind": cb.kind,
             "domain": cb.domain, "copies": cb.copies, "lines": cb.lines}
            for cb in topo.copybooks.values()
        ],
        "jobs": topo.jobs,
    }
    manifest_text = json.dumps(manifest, indent=2) + "\n"
    if cfg.gzip_manifest:
        # At 100k programs the manifest is ~106 MiB, over GitHub's 100 MiB
        # hard file limit. It compresses ~13:1 and json.load(gzip.open(...))
        # reads it just as fast. mtime=0 keeps the output byte-reproducible.
        manifest_name = "manifest.json.gz"
        path = os.path.join(out, manifest_name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                               compresslevel=9, mtime=0) as fh:
                fh.write(manifest_text.encode("utf-8"))
    else:
        manifest_name = "manifest.json"
        write(os.path.join(out, manifest_name), manifest_text)

    g = metrics
    print(f"\nwrote {out}")
    print(f"  programs           {len(topo.order):,}")
    print(f"  copybooks          {len(topo.copybooks):,}")
    print(f"  source lines       {total_lines:,}")
    print(f"  source size        {total_bytes / 1_048_576:.1f} MiB")
    print(f"  jcl jobs           {len(topo.jobs):,}")
    print(f"  call-graph edges   {g['edges']:,}"
          f" ({g['edges_statically_resolvable']:,} literal,"
          f" {g['edges_requiring_dataflow_or_csd']:,} need dataflow/CSD)")
    print(f"  cycles             {g['cycle_count']}")
    print(f"  entry points       {g['entry_point_count']:,}")
    print(f"  orphan programs    {g['orphans_count']:,}")
    print(f"  max call depth     {g['max_call_depth_sampled']}"
          " (sampled from roots)")
    if topo.window_budget_chars:
        biggest = max(p.lines for p in topo.order)
        print(f"  window cap         every program fits {cfg.window_tokens:,}"
              f" tokens ({topo.window_budget_chars:,} chars);"
              f" largest is {biggest:,} lines")
    print(f"  ground truth       {os.path.join(cfg.out, manifest_name)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
