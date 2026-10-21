#!/usr/bin/env python3
"""One definition of "this address belongs to a person, not a shared mailbox".

Why it exists: sponsorship intelligence is bought by a named GTM, growth or marketing person.
A first touch that lands in support@ or info@ is read by whoever watches the ticket queue, if
anyone, and the 2026-10-15 batch showed the cost: thirteen first touches went to role mailboxes
(support@anam.ai, hello@factor.ai, info@cakewallet.com, media@f2.com and the rest) while the
CRM held the buyer's own name for nearly every one of them. Anam answered from its helpdesk
within twenty seconds with an automated acknowledgement, and the CRM recorded that as a reply.

The rule is stated once, here, and every stage imports it: the gate holds a draft addressed to a
role mailbox, address discovery refuses to store one, and the name pass will not let one fill an
empty email column. A second copy of this list is a second thing to get wrong.

The match is on the local part only, punctuation-free, and consumes the whole string: the local
has to be a role word, or a run of them ("customer.service", "support.team", "hello.info"). So
`info`, `support`, `hello`, `hi`, `media`, `sales`, `team`, `customer.service` are role boxes,
while `info@` style false alarms do not fire on real names: `informed`, `Hillary`, `Prasad` and
`careers` are checked by whole-word consumption, not by prefix.

Not a role box, deliberately: a personal address at another domain, a free-mail address, or a
brand-named mailbox that is not a role word (`zikoko@bigcabal.com`). Those are separate rules
(the gate's domain check, and the CRM's own contact record).
"""
import re

# Words that name a function, a team or a queue. Nothing here is a person's name.
ROLE_WORDS = (
    "advertising", "advertisers", "sponsorship", "sponsorships", "partnerships",
    "partnership", "marketing", "newsletter", "editorial", "customerservice", "customercare",
    "hello", "contactus", "support", "service", "customer", "enquiries", "enquiry", "inquiries",
    "inquiry", "accounts", "billing", "careers", "office", "general", "contact", "info",
    "press", "media", "sales", "admin", "team", "mail", "email", "help", "sponsor",
    "partners", "partner", "editor", "events", "jobs", "care", "pr", "ask", "hi", "outreach",
    # chat@ and legal@ are the same failure as info@ under another name: a shared inbox that
    # nobody buys from. Both turned up as the only address two ICP leads had.
    "chat", "legal",
)
# Digits are dropped before the match: `support1` and `accounts2` are still the queue.
ROLE_LOCAL = re.compile(r"^(" + "|".join(ROLE_WORDS) + r")$", re.I)
# no-reply and its spellings only make sense as one token, so they are matched on the raw local.
ROLE_RAW = re.compile(r"^(no-?reply|donotreply|do-not-reply|postmaster|webmaster|noreply)$", re.I)
_WORDS_BY_LENGTH = sorted(set(ROLE_WORDS), key=len, reverse=True)


def address_domain(address: "str | None") -> str:
    """The registrable host an address sits on, lowercased, without a leading www."""
    addr = str(address or "").strip().lower()
    if "@" not in addr:
        return ""
    host = addr.rsplit("@", 1)[-1].strip().strip(".")
    return host[4:] if host.startswith("www.") else host


def belongs_to_domain(address: "str | None", domain: "str | None") -> bool:
    """True when the address is on the lead's own host, or a subdomain of it.

    A shared mailbox is not the only way an address can be the wrong person: a namesake
    provider record is another. Asking the free lanes about alphasense.com returned a person
    whose address was sensors.alphasense@ametek.com, and the name pass wrote it into the CRM
    and queued a stranger's mailbox. So an address may only fill a lead's contact column when
    its host IS the lead's host. With no domain recorded there is nothing to match against and
    the address is refused rather than guessed; a free-mail address never matches either.
    """
    host = address_domain(address)
    dom = str(domain or "").strip().lower()
    if "//" in dom:
        dom = dom.split("//", 1)[-1]
    dom = dom.split("/", 1)[0].split(":", 1)[0].strip().strip(".")
    if dom.startswith("www."):
        dom = dom[4:]
    if not host or "." not in host or not dom or "." not in dom:
        return False
    # Both directions, exactly as the send gate compares them: an address at the parent host of
    # a subdomain lead (aws.amazon.com and someone@amazon.com) is still the same company.
    return host == dom or host.endswith("." + dom) or dom.endswith("." + host)


def is_role_address(address: "str | None") -> bool:
    """True for a shared/functional mailbox: support@, info@, hello@, customer.service@.

    Deliberately conservative in the other direction: a real person's address must not be
    refused, so the local part has to be consumed entirely by role words.
    """
    addr = str(address or "").strip().lower()
    if "@" not in addr:
        return False
    local = addr.split("@", 1)[0]
    if ROLE_RAW.match(local):
        return True
    key = re.sub(r"[^a-z]", "", local.split("+", 1)[0])
    if not key:
        return False
    rest = key
    while rest:
        for w in _WORDS_BY_LENGTH:
            if rest.startswith(w):
                rest = rest[len(w):]
                break
        else:
            return False
    return True
